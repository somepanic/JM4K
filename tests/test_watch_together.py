"""
lib/watch_together: a watch party (decision D26) keeps Kodi's player with its room. The room's transport commands
run at their execute_at on the server's clock (a late one projected forward, an in-step player left alone); reports
go out while the room's item plays (attach first, then state reports, marked ready while the room waits); a pause or
seek here becomes the room's request, or is undone for a guest the host hasn't given control; the end of the room
stops its item. The player and the room socket are stand-ins.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from lib import watch_together

from .base import KodiTestCase

PLAYING = {
    'room_id': 'r1',
    'phase': 'playing',
    'playback_state': 'playing',
    'selection_revision': 2,
    'selected_content_id': 'movie-tmdb-238',
    'selected_file_id': 4711,
    'anchor_position_seconds': 0,
    'generation': 5,
    'self_role': 'guest',
    'self_can_control_transport': False,
    'members': [{'profile_id': 'p1', 'is_self': True, 'is_ready': False}],
}


class FakePlayer:
    def __init__(self, key=None, position=0.0, paused=False):
        self.key, self.at, self.paused = key, position, paused
        self.calls, self.notes = [], []

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

    def sessionId(self, key):
        return 'sess-1' if key == 'movie-tmdb-238' else None

    def notify(self, text):
        self.notes.append(text)


class FakeLink:
    def __init__(self, now=1000.0):
        self.sent, self.now = [], now

    def send(self, message):
        self.sent.append(message)
        return True

    def serverTime(self, now=None):
        return self.now


def party(snapshot=PLAYING, player=None, now=1000.0, **changes):
    p = watch_together.Party(
        dict(snapshot, **changes), player or FakePlayer(), FakeLink(now), later=lambda delay, fn: fn()
    )
    p.clock = lambda: now
    p.sleep = lambda seconds: None
    return p


def command(action, position, state='playing', at='1970-01-01T00:16:38Z', cid='c1'):
    # 00:16:38 is 998 s after the epoch: two seconds before the room's clock in these tests (1000)
    return {
        'type': 'transport_command',
        'command': {
            'command_id': cid,
            'selection_revision': 2,
            'action': action,
            'position_seconds': position,
            'execute_at': at,
            'playback_state': state,
        },
    }


class PartyCommandsTest(KodiTestCase):
    def test_a_late_play_runs_projected_forward(self):
        p, clock = converging(50.0, paused=True, keyframe=1)

        p.onMessage(command('play', 100.0, at=at(998)))

        self.assertAlmostEqual(p.player.at - (102.0 + clock.now - 1000), 0.0, delta=1.0)
        self.assertFalse(p.player.paused)

    def test_a_pause_lands_where_the_room_is(self):
        p, _clock = converging(100.4, paused=False, keyframe=1)

        p.onMessage(command('pause', 100.0, state='paused', at=at(1000)))

        self.assertEqual(p.player.calls, [('paused', True)])  # within a second: no seek

    def test_a_seek_within_a_second_stays(self):
        p, _clock = converging(99.8, paused=True, keyframe=1)

        p.onMessage(command('seek', 100.0, state='waiting', at=at(1000)))

        self.assertEqual(p.player.calls, [])

    def test_nothing_runs_while_another_item_plays(self):
        p = party(player=FakePlayer('movie-other', position=5.0))

        p.onMessage(command('play', 100.0))

        self.assertEqual(p.player.calls, [])


class PartyReportsTest(KodiTestCase):
    def test_its_session_is_attached_first(self):
        p = party(player=FakePlayer('movie-tmdb-238', position=3.0))

        p.tick()

        self.assertEqual(p.link.sent, [{'type': 'attach_session', 'session_id': 'sess-1'}])

    def test_then_it_reports_where_it_is(self):
        p = party(player=FakePlayer('movie-tmdb-238', position=3.0), attached_session_id='sess-1')
        p.onMessage(command('play', 1.0, cid='c7'))
        p.link.sent.clear()

        p.tick()

        self.assertEqual(
            p.link.sent,
            [
                {
                    'type': 'state_report',
                    'session_id': 'sess-1',
                    'position_seconds': 3.0,
                    'is_paused': False,
                    'command_id': 'c7',
                }
            ],
        )

    def test_while_the_room_waits_it_says_it_is_ready(self):
        p = party(
            player=FakePlayer('movie-tmdb-238', position=0.0, paused=True),
            attached_session_id='sess-1',
            playback_state='waiting',
        )
        p.onMessage(command('seek', 0.0, state='waiting', cid='c8'))
        p.link.sent.clear()

        p.tick()

        self.assertTrue(p.link.sent[0]['is_ready'])
        self.assertEqual(p.link.sent[0]['command_id'], 'c8')

    def test_a_host_short_of_the_seek_is_ready_where_it_landed(self):
        # Kodi lands a seek on a keyframe, here 1.9 s short: Silo accepts the host within 15 s and re-anchors to it
        p = party(
            player=FakePlayer('movie-tmdb-238', position=598.1, paused=True),
            attached_session_id='sess-1',
            playback_state='waiting',
            self_role='host',
        )
        p.onMessage(command('seek', 600.0, state='waiting', cid='c9'))
        p.player.at = 598.1  # where the seek really landed
        p.link.sent.clear()

        p.tick()

        self.assertTrue(p.link.sent[0]['is_ready'])

    def test_a_guest_short_of_the_seek_is_not_ready_yet(self):
        p = party(
            player=FakePlayer('movie-tmdb-238', position=598.1, paused=True),
            attached_session_id='sess-1',
            playback_state='waiting',
        )
        p.onMessage(command('seek', 600.0, state='waiting', cid='c9'))
        p.player.at = 598.1
        p.link.sent.clear()

        p.tick()

        self.assertNotIn('is_ready', p.link.sent[0])

    def test_a_negative_time_at_the_start_is_reported_as_zero(self):
        # Kodi can report a time below zero as a stream starts; Silo refuses one (position is invalid)
        p = party(player=FakePlayer('movie-tmdb-238', position=-0.04), attached_session_id='sess-1')

        p.tick()

        self.assertEqual(p.link.sent[0]['position_seconds'], 0.0)

    def test_no_reports_without_the_rooms_item(self):
        p = party(player=FakePlayer(None))

        p.tick()

        self.assertEqual(p.link.sent, [])


class PartyLocalActionsTest(KodiTestCase):
    def test_the_host_pausing_pauses_the_room(self):
        p = party(
            player=FakePlayer('movie-tmdb-238', position=42.0, paused=True),
            self_role='host',
            self_can_control_transport=True,
        )

        p.onLocalPause(True)

        self.assertEqual(
            p.link.sent, [{'type': 'transport_request', 'action': 'pause', 'position_seconds': 42.0, 'is_paused': True}]
        )

    def test_the_hosts_seek_asks_the_room(self):
        p = party(
            player=FakePlayer('movie-tmdb-238', position=600.0), self_role='host', self_can_control_transport=True
        )

        p.onLocalSeek(600.0)

        self.assertEqual(p.link.sent[0]['action'], 'seek')
        self.assertEqual(p.link.sent[0]['position_seconds'], 600.0)

    def test_what_a_command_did_is_not_sent_back(self):
        p = party(player=FakePlayer('movie-tmdb-238', position=100.0), self_can_control_transport=True)
        p.onMessage(command('pause', 100.0, state='paused'))
        p.link.sent.clear()

        p.onLocalPause(True)

        self.assertEqual(p.link.sent, [])

    def test_a_guest_without_control_is_put_back(self):
        p = party(player=FakePlayer('movie-tmdb-238', position=10.0, paused=True))
        p.onMessage(command('play', 10.0, at='1970-01-01T00:16:40Z'))
        p.player.calls.clear()
        p.clock = lambda: 1010.0  # well after the command

        p.onLocalPause(True)

        self.assertEqual(p.link.sent, [])
        self.assertEqual(p.player.calls, [('paused', False)])
        self.assertEqual(len(p.player.notes), 1)


class KeyframePlayer(FakePlayer):
    """A player whose seeks land on the keyframe before the target (every 10 s), and whose time runs while it plays."""

    def __init__(self, clock, keyframe=10, **kwargs):
        super().__init__(**kwargs)
        self.clock, self.keyframe = clock, keyframe

    def seek(self, seconds):
        self.calls.append(('seek', round(seconds, 2)))
        self.at = (seconds // self.keyframe) * self.keyframe

    def run(self, seconds):
        if not self.paused:
            self.at += seconds


class Clock:
    def __init__(self, now):
        self.now = now
        self.player = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.player is not None:
            self.player.run(seconds)


def converging(position, paused, keyframe=10, **snapshot):
    clock = Clock(1000.0)
    player = KeyframePlayer(clock, keyframe, key='movie-tmdb-238', position=position, paused=paused)
    clock.player = player
    link = FakeLink()
    link.serverTime = lambda now=None: clock.now
    p = watch_together.Party(dict(PLAYING, **snapshot), player, link, later=lambda delay, fn: fn())
    p.clock, p.sleep = clock, clock.sleep
    return p, clock


def at(seconds):
    """execute_at, seconds after the epoch (the tests' room clock starts at 1000)."""
    minutes, rest = divmod(seconds, 60)
    return f'1970-01-01T00:{minutes:02d}:{rest:02d}Z'


class PartyConvergesTest(KodiTestCase):
    """Kodi lands a seek on a keyframe: a party gets to the room's position by holding still or playing up to it."""

    def test_a_seek_in_a_waiting_room_ends_on_its_destination(self):
        p, _clock = converging(40.0, paused=True, playback_state='waiting')

        p.onMessage(command('seek', 305.0, state='waiting', at=at(1000)))

        self.assertAlmostEqual(p.player.at, 305.0, delta=0.3)
        self.assertTrue(p.player.paused)

    def test_ahead_of_a_playing_room_it_holds_still(self):
        p, clock = converging(105.0, paused=False)

        p.onMessage(command('play', 100.0, at=at(1000)))

        self.assertNotIn('seek', [c[0] for c in p.player.calls])
        self.assertAlmostEqual(p.player.at - (100.0 + clock.now - 1000), 0.0, delta=0.6)
        self.assertFalse(p.player.paused)

    def test_behind_a_playing_room_it_catches_up(self):
        p, clock = converging(20.0, paused=False)

        p.onMessage(command('play', 305.0, at=at(1000)))

        self.assertAlmostEqual(p.player.at - (305.0 + clock.now - 1000), 0.0, delta=1.0)
        self.assertFalse(p.player.paused)

    def test_its_own_holds_are_not_sent_as_requests(self):
        p, _clock = converging(105.0, paused=False, self_can_control_transport=True)
        sent = []
        p.player.setPaused = lambda paused, f=p.player.setPaused: (f(paused), p.onLocalPause(paused))

        p.onMessage(command('play', 100.0, at=at(1000)))

        self.assertEqual([m for m in p.link.sent if m['type'] == 'transport_request'], sent)


LOBBY = {
    'room_id': 'r1',
    'code': 'ABCD',
    'phase': 'lobby',
    'playback_state': 'idle',
    'selection_revision': 1,
    'selected_content_id': 'movie-tmdb-238',
    'selected_file_id': 4711,
    'guest_control_policy': 'host_only',
    'generation': 3,
    'members': [
        {'display_name': 'Claude', 'is_host': True, 'is_self': True, 'lobby_ready': False},
        {'display_name': 'Sam', 'is_host': False, 'is_self': False, 'lobby_ready': True},
        {'display_name': 'Alex', 'is_host': False, 'is_self': False, 'lobby_ready': False},
    ],
}
GUEST_LOBBY = dict(
    LOBBY,
    members=[
        {'display_name': 'Claude', 'is_host': True, 'is_self': False, 'lobby_ready': False},
        {'display_name': 'Sam', 'is_host': False, 'is_self': True, 'lobby_ready': False},
    ],
)


class FakeRoom:
    def __init__(self, party):
        self.party, self.calls = party, []

    def start(self, client):
        self.calls.append('start')
        self.snapshot = dict(self.party.snapshot, phase='playing', playback_state='waiting', generation=9)

    def stop(self, client):
        self.calls.append('stop')
        self.snapshot = dict(self.party.snapshot, phase='lobby', playback_state='idle', generation=10)

    def setGuestControl(self, client, allowed):
        self.calls.append(('guests', allowed))
        policy = 'guest_play_pause' if allowed else 'host_only'
        self.snapshot = dict(self.party.snapshot, guest_control_policy=policy, generation=9)


class FakeSession:
    def __init__(self, snapshot, role):
        self.party = party(dict(snapshot, self_role=role))
        self.party.titleSource = lambda contentId: {'movie-tmdb-238': 'The Godfather'}.get(contentId, '')
        self.room = FakeRoom(self.party)
        self.server = type('Server', (), {'client': None, 'pinnedFiles': {}, 'getObject': lambda s, key: key})()
        self.left = False

    @property
    def active(self):
        return not self.left and self.party.ended is None

    def leave(self):
        self.left = True


def keys(rows):
    return [r[0] for r in rows]


class PartyScreenTest(KodiTestCase):
    """The party screen (lib/windows/partydialog.py) shows watch_together.partyRows() and runs choose()."""

    def test_the_host_starts_what_is_up_next_and_sees_who_is_ready(self):
        session = FakeSession(LOBBY, 'host')

        rows = watch_together.partyRows(session.party)

        self.assertEqual(rows[0][1], ('Up next: The Godfather', ''))
        start = rows[keys(rows).index('start')]
        self.assertEqual(start[1], ('Start for everyone', '1 of 2 ready'))
        self.assertEqual(rows[keys(rows).index('guest_control')][2], False)
        self.assertIn('end', keys(rows))
        self.assertEqual(
            [r[1] for r in rows if r[0] == 'member'], [('Claude', 'Host'), ('Sam', 'Ready'), ('Alex', 'Not ready')]
        )

    def test_a_guest_says_it_is_ready(self):
        session = FakeSession(GUEST_LOBBY, 'guest')

        rows = watch_together.partyRows(session.party)
        stays = watch_together.choose(session, 'ready')

        self.assertNotIn('start', keys(rows))
        self.assertEqual(rows[keys(rows).index('ready')][2], False)
        self.assertIn('leave', keys(rows))
        self.assertTrue(stays)
        self.assertEqual(session.party.link.sent, [{'type': 'lobby_ready', 'ready': True}])

    def test_nothing_up_next_yet(self):
        session = FakeSession(dict(GUEST_LOBBY, selected_content_id=None), 'guest')

        rows = watch_together.partyRows(session.party)

        self.assertEqual(rows[0][1][0], 'Nothing picked yet')
        self.assertNotIn('ready', keys(rows))

    def test_the_host_lets_guests_play_and_pause(self):
        session = FakeSession(LOBBY, 'host')

        stays = watch_together.choose(session, 'guest_control')

        self.assertTrue(stays)
        self.assertEqual(session.room.calls, [('guests', True)])
        self.assertEqual(session.party.snapshot['guest_control_policy'], 'guest_play_pause')

    def test_starting_closes_the_screen_for_playback(self):
        session = FakeSession(LOBBY, 'host')

        stays = watch_together.choose(session, 'start')

        self.assertFalse(stays)
        self.assertEqual(session.room.calls, ['start'])
        self.assertIsNotNone(session.party.selection())

    def test_a_guest_with_play_and_pause_still_cannot_seek(self):
        p = party(player=FakePlayer('movie-tmdb-238', position=600.0), self_can_control_transport=True)
        p.lastTarget = 100.0

        p.onLocalSeek(600.0)

        self.assertEqual(p.link.sent, [])
        self.assertEqual(p.player.calls, [('seek', 100.0)])


VOTE_LOBBY = dict(LOBBY, selection_mode='vote', selected_content_id=None)
SUGGESTIONS = [
    {'id': 's1', 'title': 'Heat', 'subtitle': '', 'vote_count': 1, 'created_at': '2026-11-24T10:00:00.000Z'},
    {'id': 's2', 'title': 'Dune', 'subtitle': '', 'vote_count': 2, 'created_at': '2026-11-24T10:01:00.000Z'},
]


class VotingRoom(FakeRoom):
    def setVoting(self, client, voting):
        self.calls.append(('voting', voting))
        self.snapshot = dict(self.party.snapshot, selection_mode='vote' if voting else 'host_pick', generation=11)

    def suggestions(self, client):
        return [dict(s, voted_by_me=s['id'] == 's1') for s in SUGGESTIONS]

    def vote(self, client, suggestionId, on):
        self.calls.append(('vote', suggestionId, on))

    def promote(self, client, suggestionId):
        self.calls.append(('promote', suggestionId))
        self.snapshot = dict(self.party.snapshot, phase='playing', selected_content_id='movie-dune', generation=12)

    def suggest(self, client, contentId, contentType, title, subtitle=''):
        self.calls.append(('suggest', contentId, contentType, title, subtitle))


def voting(role, snapshot=VOTE_LOBBY):
    session = FakeSession(snapshot, role)
    session.room = VotingRoom(session.party)
    session.party.suggestionSource = lambda: session.room.suggestions(None)
    session.party.loadSuggestions()
    return session


class PartyVotingTest(KodiTestCase):
    """A voting room: members suggest things and vote; the host plays a suggestion for everyone."""

    def test_suggestions_by_votes_with_my_own_ticked(self):
        rows = watch_together.partyRows(voting('guest').party)

        suggested = [r for r in rows if isinstance(r[0], str) and r[0].startswith('suggestion:')]
        self.assertEqual(
            [(r[0], r[1], r[2]) for r in suggested],
            [('suggestion:s2', ('Dune', '2 votes'), False), ('suggestion:s1', ('Heat', '1 vote'), True)],
        )
        self.assertNotIn('ready', keys(rows))

    def test_a_guest_votes(self):
        session = voting('guest')

        self.assertTrue(watch_together.choose(session, 'suggestion:s2'))
        self.assertTrue(watch_together.choose(session, 'suggestion:s1'))

        self.assertEqual(session.room.calls, [('vote', 's2', True), ('vote', 's1', False)])
        self.assertEqual(session.party.myVotes, {'s2'})

    def test_the_host_plays_a_suggestion(self):
        session = voting('host')

        stays = watch_together.choose(session, 'suggestion:s1')

        self.assertFalse(stays)
        self.assertEqual(session.room.calls, [('promote', 's1')])
        self.assertIsNotNone(session.party.selection())

    def test_the_host_switches_voting_on(self):
        session = FakeSession(LOBBY, 'host')
        session.room = VotingRoom(session.party)
        session.party.suggestionSource = lambda: session.room.suggestions(None)

        self.assertIn('voting', keys(watch_together.partyRows(session.party)))
        watch_together.choose(session, 'voting')

        self.assertEqual(session.room.calls, [('voting', True)])
        self.assertEqual([s['id'] for s in session.party.suggestions], ['s1', 's2'])
        self.assertNotIn('start', keys(watch_together.partyRows(session.party)))

    def test_suggestions_update_keeps_my_votes(self):
        session = voting('guest')

        session.party.onMessage(
            {'type': 'suggestions_update', 'suggestions': [dict(s, voted_by_me=False) for s in SUGGESTIONS]}
        )

        self.assertEqual(session.party.myVotes, {'s1'})
        self.assertEqual(len(session.party.suggestions), 2)

    def test_watch_together_in_a_voting_party_suggests_it(self):
        session = voting('guest')
        episode = mock.Mock(
            type='episode', ratingKey='episode-1', title='Pilot', grandparentTitle='Show', parentIndex='1', index='2'
        )

        with (
            mock.patch.object(watch_together, 'current', return_value=session),
            mock.patch.object(watch_together, 'runParty') as run,
        ):
            watch_together.pickItem(episode, None, lambda video, seconds: None)

        self.assertEqual(session.room.calls, [('suggest', 'episode-1', 'episode', 'Pilot', 'Show · S1E2')])
        run.assert_called_once()

    def test_watch_together_as_a_guest_of_a_host_picked_party_opens_its_screen(self):
        session = FakeSession(GUEST_LOBBY, 'guest')
        movie = mock.Mock(type='movie', ratingKey='movie-1', title='Heat')

        with (
            mock.patch.object(watch_together, 'current', return_value=session),
            mock.patch.object(watch_together, 'runParty') as run,
            mock.patch.object(watch_together, '_begin') as begin,
        ):
            watch_together.pickItem(movie, None, lambda video, seconds: None)

        begin.assert_not_called()  # stays in this party, no room of its own
        run.assert_called_once()


class PartyLoopTest(KodiTestCase):
    """watch_together.runParty(): the party screen while the room plays nothing, the room's item while it plays."""

    def test_a_guest_plays_what_the_room_starts_then_leaves_when_it_stops_here(self):
        session = FakeSession(GUEST_LOBBY, 'guest')
        played = []

        def screen(s):
            s.party.adopt(dict(s.party.snapshot, phase='playing', playback_state='playing', generation=20))
            return 'play'

        watch_together.runParty(
            session, lambda video, seconds: played.append(video), screen=screen, ask=lambda *a: True
        )

        self.assertEqual(played, ['/library/metadata/movie-tmdb-238'])
        self.assertEqual(session.server.pinnedFiles, {'movie-tmdb-238': 4711})
        self.assertTrue(session.left)

    def test_the_host_going_back_leaves_the_party_running(self):
        session = FakeSession(LOBBY, 'host')

        watch_together.runParty(session, lambda video, seconds: None, screen=lambda s: None, ask=lambda *a: True)

        self.assertFalse(session.left)

    def test_the_host_stopping_can_stop_it_for_everyone(self):
        session = FakeSession(dict(LOBBY, phase='playing', playback_state='playing'), 'host')
        screens = []

        watch_together.runParty(
            session,
            lambda video, seconds: None,
            screen=lambda s: screens.append(s.party.snapshot['phase']),
            ask=lambda *a: True,
            askHost=lambda: watch_together.STOP_FOR_EVERYONE,
        )

        self.assertEqual(session.room.calls, ['stop'])
        self.assertEqual(screens, ['lobby'])  # back on the party screen, the item up next again


class PartyEndTest(KodiTestCase):
    def test_the_room_closing_stops_its_item(self):
        p = party(player=FakePlayer('movie-tmdb-238'))

        p.onMessage({'type': 'room_closed', 'reason': 'host_left'})

        self.assertEqual(p.player.calls, [('stop',)])
        self.assertEqual(p.ended, 'host_left')

    def test_another_item_starting_is_stopped(self):
        p = party(player=FakePlayer('movie-next-episode'))

        p.onLocalStart()

        self.assertEqual(p.player.calls, [('stop',)])
        self.assertEqual(len(p.player.notes), 1)

    def test_after_the_room_closes_other_items_play(self):
        # the session stays the add-on's last (PARTY) after the room ends: its player's events go on arriving
        p = party(player=FakePlayer('movie-tmdb-238'))
        p.onMessage({'type': 'room_closed', 'reason': 'host_left'})
        p.player.calls, p.player.key = [], 'movie-other'

        p.onLocalStart()

        self.assertEqual(p.player.calls, [])
        self.assertEqual(len(p.player.notes), 1)  # only the "has ended" notice

    def test_after_the_room_closes_pauses_and_seeks_are_the_users(self):
        p = party(player=FakePlayer('movie-tmdb-238'))
        p.onMessage({'type': 'room_closed', 'reason': 'host_left'})
        p.player.calls, p.player.key = [], 'movie-tmdb-238'  # played again, on its own
        p.lastTarget = 30.0

        p.onLocalPause(True)
        p.onLocalSeek(600.0)

        self.assertEqual((p.player.calls, p.link.sent), ([], []))

    def test_leaving_lets_go_of_the_player(self):
        session = watch_together.Session.__new__(watch_together.Session)
        session.server, session.room = mock.Mock(pinnedFiles={}), mock.Mock()
        session.party = party(player=FakePlayer())
        session.link = mock.Mock()
        session.player = mock.Mock(party=session.party)

        session.leave()

        self.assertIsNone(session.player.party)  # Kodi's callbacks reach no party once it's left

    def test_the_rooms_selection(self):
        p = party(anchor_position_seconds=61.5)

        self.assertEqual(p.selection(), ('movie-tmdb-238', 4711, 61.5))
        self.assertIsNone(party(phase='lobby').selection())
