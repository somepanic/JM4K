"""
lib/syncplay and lib/windows/syncplaydialog: Watch together via Jellyfin SyncPlay (Task 9.2).
Covers group creation and joining, queue startup, Unpause/Pause/Seek/Stop command convergence
on a keyframe-snapping player, local pause/seek/start forwarding, echo suppression, live dialog
row refreshes on group updates, session teardown on Home close, and menu entries in Library,
PrePlay, and Episodes windows.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True

from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.items import itemElement
from jellynet.server import JellyfinServer
from plexnet import plexobjects, video

from lib import syncplay
from lib.jellyfin import StoredServer
from lib.windows import episodes, library, preplay, syncplaydialog, windowutils

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession

BASE = 'http://jellyfin.lan:8096'
IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Kodi', 'device-uuid-1')
GROUP = {
    'GroupId': 'grp-1',
    'GroupName': 'Movie Night',
    'State': 'Idle',
    'Participants': ['alice'],
    'LastUpdatedAt': '2026-11-25T10:00:00.000Z',
}


def ok(body=None, status=200):
    return FakeResponse(status, body)


def make_server(session):
    client = JellyfinClient(BASE, IDENTITY, token='tok-1', username='alice', session=session)
    stored = StoredServer('srv-1', BASE, 'Jellyfin')
    return JellyfinServer(stored, client)


def make_video_item(srv, dto):
    elem = itemElement(dto)
    return plexobjects.buildItem(srv, elem, '/library/metadata/' + dto['Id'])


class FakePlayer:
    def __init__(self, key=None, position=0.0, paused=False):
        self.key = key
        self.at = position
        self.paused = paused
        self.calls = []
        self.notes = []

    def playingKey(self):
        return self.key

    def position(self):
        return self.at

    def isPaused(self):
        return self.paused

    def seek(self, seconds):
        self.calls.append(('seek', round(seconds, 2)))
        self.at = seconds

    def setPaused(self, paused):
        self.calls.append(('paused', paused))
        self.paused = paused

    def stop(self):
        self.calls.append(('stop',))
        self.key = None

    def notify(self, text):
        self.notes.append(text)


class KeyframePlayer(FakePlayer):
    """Simulates Kodi landing seeks on 10-second keyframes while time advances when unpaused."""

    def __init__(self, clock, keyframe=10, **kwargs):
        super().__init__(**kwargs)
        self.clock = clock
        self.keyframe = keyframe

    def seek(self, seconds):
        self.calls.append(('seek', round(seconds, 2)))
        self.at = (seconds // self.keyframe) * self.keyframe

    def run(self, seconds):
        if not self.paused:
            self.at += seconds


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now
        self.player = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.player is not None:
            self.player.run(seconds)


class FakeLink:
    def __init__(self, now=1000.0):
        self.now = now
        self.ended = None
        self.stopped = False

    def server_time(self, now=None):
        return self.now

    def start(self):
        pass

    def stop(self):
        self.stopped = True


def at_iso(seconds):
    minutes, rest = divmod(int(seconds), 60)
    return f'1970-01-01T00:{minutes:02d}:{rest:02d}.000Z'


def make_controller(group=GROUP, player=None, session=None, clock=None, link=None, playlist=None, state=None):
    clk = clock or FakeClock(1000.0)
    pl = player or FakePlayer('mov-1', position=0.0, paused=False)
    lk = link or FakeLink(clk.now)
    if clock is not None and link is None:
        lk.server_time = lambda now=None: clk.now
    client = make_server(session).client if session is not None else None
    g = dict(group)
    if state is not None:
        g['State'] = state
    ctrl = syncplay.SyncPlayController(g, pl, client=client, link=lk, later=lambda delay, fn: fn())
    ctrl.clock = clk
    ctrl.sleep = clk.sleep
    if playlist is not None:
        ctrl.on_group_update(
            {
                'GroupId': g['GroupId'],
                'Type': 'PlayQueue',
                'Data': {
                    'Reason': 'NewPlaylist',
                    'Playlist': playlist,
                    'PlayingItemIndex': 0,
                    'StartPositionTicks': 0,
                    'IsPlaying': g.get('State') == 'Playing',
                },
            }
        )
    return ctrl, clk


class SyncPlayFlowTest(KodiTestCase):
    def tearDown(self):
        syncplay.CURRENT = None
        super().tearDown()

    def test_create_and_start_item_sets_new_queue(self):
        session = FakeSession(
            ok(GROUP),  # POST /SyncPlay/New
            ok(None, 204),  # POST /SyncPlay/SetNewQueue
        )
        srv = make_server(session)
        movie = make_video_item(srv, {'Id': 'mov-42', 'Type': 'Movie', 'Name': 'Arrival'})
        ran = []

        def fake_begin(server, group):
            fake_sess = mock.Mock(server=server, active=True)
            fake_sess.controller = syncplay.SyncPlayController(group, FakePlayer(), client=server.client)
            syncplay.CURRENT = fake_sess
            return fake_sess

        def play(vid, sec):
            pass

        with (
            mock.patch.object(syncplay, '_begin', side_effect=fake_begin),
            mock.patch.object(syncplay, 'run_group', side_effect=lambda s, p: ran.append((s, p))),
        ):
            syncplay.start_with_item(movie, play)

        self.assertEqual(len(session.calls), 2)
        self.assertEqual((session.calls[0]['method'], session.calls[0]['url']), ('POST', BASE + '/SyncPlay/New'))
        self.assertEqual(
            (session.calls[1]['method'], session.calls[1]['url'], session.calls[1]['json']),
            (
                'POST',
                BASE + '/SyncPlay/SetNewQueue',
                {'PlayingQueue': ['mov-42'], 'PlayingItemPosition': 0, 'StartPositionTicks': 0},
            ),
        )
        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0][0].controller.selection(), ('mov-42', '', 0.0))

    def test_join_existing_group_from_list(self):
        groups = [
            {
                'GroupId': 'grp-7',
                'GroupName': 'Friday Film',
                'State': 'Playing',
                'Participants': ['alice', 'bob'],
            }
        ]
        session = FakeSession(
            ok(groups),  # GET /SyncPlay/List
            ok(None, 204),  # POST /SyncPlay/Join
        )
        srv = make_server(session)
        picked_options = []
        ran = []

        def pick_group(heading, options):
            picked_options.extend(options)
            return 'join:grp-7'

        def fake_begin(server, group):
            fake_sess = mock.Mock(server=server, active=True)
            fake_sess.controller = syncplay.SyncPlayController(group, FakePlayer(), client=server.client)
            syncplay.CURRENT = fake_sess
            return fake_sess

        with (
            mock.patch.object(syncplay, '_begin', side_effect=fake_begin),
            mock.patch.object(syncplay, 'run_group', side_effect=lambda s, p: ran.append(s)),
        ):
            syncplay.open_syncplay(srv, lambda vid, sec: None, pick_group=pick_group)

        self.assertEqual([o[0] for o in picked_options], ['join:grp-7', 'create'])
        self.assertEqual(picked_options[0][1], ('Friday Film', '2 participants'))
        self.assertEqual(
            (session.calls[1]['method'], session.calls[1]['url'], session.calls[1]['json']),
            ('POST', BASE + '/SyncPlay/Join', {'GroupId': 'grp-7'}),
        )
        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0].controller.group_id, 'grp-7')


class SyncPlayControllerTest(KodiTestCase):
    def test_command_unpause_pause_seek_stop_converges_player(self):
        # 1. Unpause behind a playing group -> converges to projected position and unpauses
        clock = FakeClock(1000.0)
        player = KeyframePlayer(clock, keyframe=10, key='mov-1', position=20.0, paused=True)
        clock.player = player
        http_session = FakeSession(ok(None, 204))  # Ready call on Waiting seek
        ctrl, _ = make_controller(
            player=player,
            session=http_session,
            clock=clock,
            playlist=[{'ItemId': 'mov-1', 'PlaylistItemId': 'pli-1'}],
            state='Playing',
        )

        ctrl.on_command(
            {
                'GroupId': 'grp-1',
                'PlaylistItemId': 'pli-1',
                'When': at_iso(998),  # 2s ago -> projected to 307s at clock=1000
                'PositionTicks': 3_050_000_000,
                'Command': 'Unpause',
                'EmittedAt': at_iso(998),
            }
        )
        expected_now = 305.0 + (clock.now - 998.0)
        self.assertAlmostEqual(player.at, expected_now, delta=1.0)
        self.assertFalse(player.paused)

        # 2. Pause within IN_STEP (1.0s) pauses without seeking
        player.calls.clear()
        player.at = 100.4
        player.paused = False
        ctrl.on_command(
            {
                'GroupId': 'grp-1',
                'PlaylistItemId': 'pli-1',
                'When': at_iso(clock.now),
                'PositionTicks': 1_000_000_000,
                'Command': 'Pause',
                'EmittedAt': at_iso(clock.now),
            }
        )
        self.assertEqual(player.calls, [('paused', True)])
        self.assertTrue(player.paused)

        # 3. Seek while Waiting converges to exact target and sends /SyncPlay/Ready
        ctrl.on_group_update({'GroupId': 'grp-1', 'Type': 'StateUpdate', 'Data': {'State': 'Waiting'}})
        player.at = 40.0
        player.paused = True
        ctrl.on_command(
            {
                'GroupId': 'grp-1',
                'PlaylistItemId': 'pli-1',
                'When': at_iso(clock.now),
                'PositionTicks': 3_050_000_000,
                'Command': 'Seek',
                'EmittedAt': at_iso(clock.now),
            }
        )
        self.assertAlmostEqual(player.at, 305.0, delta=0.3)
        self.assertTrue(player.paused)
        self.assertEqual(http_session.calls[0]['url'], BASE + '/SyncPlay/Ready')
        self.assertEqual(http_session.calls[0]['json']['PlaylistItemId'], 'pli-1')

        # 4. Stop stops player and sets state to Idle
        player.calls.clear()
        ctrl.on_command(
            {
                'GroupId': 'grp-1',
                'PlaylistItemId': 'pli-1',
                'When': at_iso(clock.now),
                'PositionTicks': 0,
                'Command': 'Stop',
                'EmittedAt': at_iso(clock.now),
            }
        )
        self.assertEqual(player.calls, [('stop',)])
        self.assertEqual(ctrl.state, 'Idle')
        self.assertIsNone(ctrl.selection())

    def test_local_pause_and_seek_send_syncplay_requests(self):
        http_session = FakeSession(ok(None, 204), ok(None, 204), ok(None, 204), ok(None, 204))
        player = FakePlayer('mov-1', position=42.5, paused=False)
        ctrl, _ = make_controller(
            player=player,
            session=http_session,
            playlist=[{'ItemId': 'mov-1', 'PlaylistItemId': 'pli-1'}],
            state='Playing',
        )

        ctrl.on_local_pause(True)
        ctrl.on_local_pause(False)
        ctrl.on_local_seek(120.5)
        ctrl.on_local_start()

        self.assertEqual(
            [(c['method'], c['url'].replace(BASE, ''), c['json']) for c in http_session.calls],
            [
                ('POST', '/SyncPlay/Pause', None),
                ('POST', '/SyncPlay/Unpause', None),
                ('POST', '/SyncPlay/Seek', {'PositionTicks': 1_205_000_000}),
                (
                    'POST',
                    '/SyncPlay/Ready',
                    {
                        'When': '1970-01-01T00:16:40.000Z',
                        'PositionTicks': 425_000_000,
                        'IsPlaying': True,
                        'PlaylistItemId': 'pli-1',
                    },
                ),
            ],
        )

    def test_echo_suppression_prevents_command_loop(self):
        http_session = FakeSession(ok(None, 204))
        clock = FakeClock(1000.0)
        player = FakePlayer('mov-1', position=100.0, paused=False)
        ctrl, _ = make_controller(
            player=player,
            session=http_session,
            clock=clock,
            playlist=[{'ItemId': 'mov-1', 'PlaylistItemId': 'pli-1'}],
            state='Playing',
        )

        # Remote Pause command arms echo window
        ctrl.on_command(
            {
                'GroupId': 'grp-1',
                'PlaylistItemId': 'pli-1',
                'When': at_iso(1000),
                'PositionTicks': 1_000_000_000,
                'Command': 'Pause',
                'EmittedAt': at_iso(1000),
            }
        )
        # Player fires callbacks during echo window -> suppressed
        ctrl.on_local_pause(True)
        ctrl.on_local_seek(100.0)
        self.assertEqual(http_session.calls, [])

        # After echo window expires, user pause/unpause is forwarded again
        clock.now += syncplay.ECHO + 0.5
        ctrl.on_local_pause(False)
        self.assertEqual(len(http_session.calls), 1)
        self.assertEqual(http_session.calls[0]['url'], BASE + '/SyncPlay/Unpause')

    def test_group_updates_refresh_dialog_rows(self):
        ctrl, _ = make_controller(player=FakePlayer('mov-1'))
        ctrl.title_source = lambda item_id: {'mov-1': 'Arrival', 'mov-2': 'Dune'}.get(item_id, '')

        initial_version = ctrl.version
        rows_idle = syncplay.group_rows(ctrl)
        self.assertEqual(rows_idle[0][1][0], 'Nothing playing yet')
        self.assertEqual([r[1][0] for r in rows_idle if r[0] == 'participant'], ['alice'])

        ctrl.on_group_update({'GroupId': 'grp-1', 'Type': 'UserJoined', 'Data': 'bob'})
        self.assertGreater(ctrl.version, initial_version)
        rows_joined = syncplay.group_rows(ctrl)
        self.assertEqual([r[1][0] for r in rows_joined if r[0] == 'participant'], ['alice', 'bob'])

        ctrl.on_group_update(
            {
                'GroupId': 'grp-1',
                'Type': 'PlayQueue',
                'Data': {
                    'Reason': 'NewPlaylist',
                    'Playlist': [
                        {'ItemId': 'mov-1', 'PlaylistItemId': 'pli-1'},
                        {'ItemId': 'mov-2', 'PlaylistItemId': 'pli-2'},
                    ],
                    'PlayingItemIndex': 0,
                    'StartPositionTicks': 0,
                    'IsPlaying': True,
                },
            }
        )
        ctrl.on_group_update({'GroupId': 'grp-1', 'Type': 'StateUpdate', 'Data': {'State': 'Playing'}})
        rows_playing = syncplay.group_rows(ctrl)
        row_keys = [r[0] for r in rows_playing]
        self.assertEqual(rows_playing[0][1][0], 'Playing: Arrival')
        self.assertIn('pause', row_keys)
        self.assertIn('stop', row_keys)
        self.assertIn('next', row_keys)
        self.assertNotIn('previous', row_keys)
        self.assertIn('leave', row_keys)

        # Verify SyncPlayDialog.tick() redraws on version change while idle
        ctrl.on_group_update({'GroupId': 'grp-1', 'Type': 'StateUpdate', 'Data': {'State': 'Idle'}})
        dlg = syncplaydialog.SyncPlayDialog.__new__(syncplaydialog.SyncPlayDialog)
        dlg.session = mock.Mock(active=True, controller=ctrl)
        dlg.shownVersion = ctrl.version
        dlg.optionsList = mock.Mock(getSelectedPosition=mock.Mock(return_value=0))
        dlg.showOptions = mock.Mock()
        dlg.doClose = mock.Mock()

        ctrl.on_group_update({'GroupId': 'grp-1', 'Type': 'UserLeft', 'Data': 'bob'})
        dlg.tick()
        dlg.showOptions.assert_called_once()
        self.assertEqual(dlg.shownVersion, ctrl.version)
        self.assertEqual([r[1][0] for r in dlg.options if r[0] == 'participant'], ['alice'])


class SyncPlaySessionAndMenusTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def tearDown(self):
        syncplay.CURRENT = None
        super().tearDown()

    def test_leave_current_on_session_close(self):
        active_session = mock.Mock()
        with (
            mock.patch.object(syncplay, 'CURRENT', active_session),
            mock.patch.object(windowutils, 'HOME', mock.Mock()),
        ):
            win = library.LibraryWindow.__new__(library.LibraryWindow)
            win.navigate = mock.Mock()

            for option in ('signout', 'switch', {'switch_server': 'srv-1'}, 'session_ended', 'exit'):
                with self.subTest(option=option):
                    active_session.reset_mock()
                    win._closeSessionWithOption(option)
                    active_session.leave.assert_called_once_with()

            active_session.reset_mock()
            win._closeSessionWithOption('recompile')
            active_session.leave.assert_not_called()

    def test_preplay_and_episodes_menu_offer_watch_together(self):
        srv = make_server(FakeSession())
        movie = make_video_item(srv, {'Id': 'mov-1', 'Type': 'Movie', 'Name': 'Arrival'})
        episode = make_video_item(
            srv,
            {
                'Id': 'ep-2',
                'Type': 'Episode',
                'Name': 'Pilot',
                'SeriesId': 'ser-1',
                'SeriesName': 'Andor',
                'SeasonId': 'sea-1',
                'ParentIndexNumber': 1,
                'IndexNumber': 2,
            },
        )
        self.assertIsInstance(movie, video.Movie)
        self.assertIsInstance(episode, video.Episode)

        # PrePlayWindow
        pre = preplay.PrePlayWindow.__new__(preplay.PrePlayWindow)
        pre.video = movie
        with mock.patch.object(preplay.plexapp, 'ACCOUNT', mock.Mock(isAdmin=False)):
            pre_keys = [a[0] for a in pre.moreActions()]
        self.assertIn('watch_together', pre_keys)
        with mock.patch.object(preplay.syncplay, 'start_with_item') as start_pre:
            pre.runMoreAction('watch_together')
        start_pre.assert_called_once()
        self.assertIs(start_pre.call_args[0][0], movie)

        # EpisodesWindow (episode card yes, season card no)
        ep_win = episodes.EpisodesWindow.__new__(episodes.EpisodesWindow)
        ep_win.season = None
        ep_win.show_ = episode
        mli = mock.Mock(dataSource=episode)
        with mock.patch.object(episodes.plexapp, 'ACCOUNT', mock.Mock(isAdmin=False)):
            ep_keys = [a[0] for a in ep_win.moreActions(mli, False)]
            season_keys = [a[0] for a in ep_win.moreActions(mli, True)]
        self.assertIn('watch_together', ep_keys)
        self.assertNotIn('watch_together', season_keys)
        with mock.patch.object(episodes.syncplay, 'start_with_item') as start_ep:
            ep_win.runMoreAction('watch_together', mli, False)
        start_ep.assert_called_once()
        self.assertIs(start_ep.call_args[0][0], episode)

    def test_terminal_group_update_stops_playback_and_notifies(self):
        player = FakePlayer('mov-1', position=55.0, paused=False)
        ctrl, _ = make_controller(
            player=player,
            playlist=[{'ItemId': 'mov-1', 'PlaylistItemId': 'pli-1'}],
            state='Playing',
        )

        ctrl.on_group_update({'GroupId': 'grp-1', 'Type': 'GroupDoesNotExist', 'Data': ''})

        self.assertEqual(ctrl.ended, 'group_does_not_exist')
        self.assertEqual(player.calls, [('stop',)])
        self.assertEqual(len(player.notes), 1)

    def test_run_group_stop_for_everyone_and_choose_controls(self):
        http_session = FakeSession(
            ok(None, 204),  # POST /SyncPlay/Stop from run_group STOP_FOR_EVERYONE
            ok(None, 204),  # POST /SyncPlay/Pause from choose
            ok(None, 204),  # POST /SyncPlay/NextItem from choose
            ok(None, 204),  # POST /SyncPlay/Leave from session.leave()
        )
        srv = make_server(http_session)
        srv.getObject = mock.Mock(return_value='video-mov-1')
        sess = syncplay.SyncPlaySession.__new__(syncplay.SyncPlaySession)
        sess.server = srv
        sess.player = FakePlayer('mov-1', position=10.0)
        sess.controller = syncplay.SyncPlayController(GROUP, sess.player, client=srv.client)
        sess.link = FakeLink()
        sess.controller.link = sess.link
        sess.controller.adopt_queue(['mov-1'], playing_position=0, start_seconds=0.0)
        # Server PlayQueue fills in PlaylistItemId for the same item without bumping queue_revision
        rev_before = sess.controller.queue_revision
        sess.controller.on_group_update(
            {
                'GroupId': 'grp-1',
                'Type': 'PlayQueue',
                'Data': {
                    'Reason': 'NewPlaylist',
                    'Playlist': [
                        {'ItemId': 'mov-1', 'PlaylistItemId': 'pli-1'},
                        {'ItemId': 'mov-2', 'PlaylistItemId': 'pli-2'},
                    ],
                    'PlayingItemIndex': 0,
                    'StartPositionTicks': 0,
                    'IsPlaying': True,
                },
            }
        )
        self.assertEqual(sess.controller.queue_revision, rev_before)

        played = []
        screens = []

        def screen(s):
            screens.append(s.controller.state)
            return 'leave'

        syncplay.CURRENT = sess
        syncplay.run_group(
            sess,
            lambda vid, sec: played.append((vid, sec)),
            screen=screen,
            ask=lambda: syncplay.STOP_FOR_EVERYONE,
        )

        self.assertEqual(played, [('video-mov-1', 0.0)])
        self.assertEqual(screens, ['Idle'])
        self.assertIsNone(syncplay.CURRENT)
        self.assertIsNone(sess.player.controller)
        self.assertTrue(sess.link.stopped)

        # Verify choose() dispatches transport and queue requests cleanly
        sess2 = mock.Mock(server=srv, controller=sess.controller)
        self.assertTrue(syncplay.choose(sess2, 'pause'))
        self.assertTrue(syncplay.choose(sess2, 'next'))
        self.assertFalse(syncplay.choose(sess2, 'leave'))
        self.assertEqual(
            [c['url'].replace(BASE, '') for c in http_session.calls],
            ['/SyncPlay/Stop', '/SyncPlay/Leave', '/SyncPlay/Pause', '/SyncPlay/NextItem'],
        )

    def test_library_user_menu_shows_watch_together_and_opens_syncplay(self):
        srv = make_server(FakeSession())
        win = library.LibraryWindow.__new__(library.LibraryWindow)
        shown_options = []

        def fake_dialog(heading, options, **kwargs):
            shown_options.extend(options)
            return 'syncplay'

        with (
            mock.patch.object(library.plexapp, 'ACCOUNT', mock.Mock(isSignedIn=True, homeUsers=['u1'])),
            mock.patch.object(library.plexapp, 'SERVERMANAGER', mock.Mock(selectedServer=srv)),
            mock.patch.object(library, 'showOptionsDialog', side_effect=fake_dialog),
            mock.patch.object(library.syncplay, 'open_syncplay') as open_sp,
        ):
            win.showUserMenu()

        self.assertIn(('syncplay', 'Watch together'), shown_options)
        open_sp.assert_called_once()
        self.assertIs(open_sp.call_args[0][0], srv)
