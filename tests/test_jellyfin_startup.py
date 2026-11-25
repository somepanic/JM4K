"""
JellyfinServer, lib/jellyfin.py start-up (init, initAfterSwitch, signIn), Quick Connect sign-in card,
Switch server menu, and session-end handling.
"""

import json
import os
from collections import deque
from unittest import mock

import requests
from jellynet import auth
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.discovery import Found
from jellynet.server import JellyfinServer
from jellynet.servermanager import JellyfinServerManager
from kodienv import ENV

ENV.abort_requested = True
from plexnet import plexapp, plexlibrary
from plexnet import util as pnUtil

from lib import jellyfin
from lib.windows import library, signin

from .base import KodiTestCase, fixture
from .jellynet_fakes import FakeResponse, FakeSession, problem

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')
STORED = {'base_url': 'http://jellyfin.lan:8096', 'server_id': 'srv-1', 'name': 'Home Jellyfin'}

USER_VIEWS = FakeResponse(
    200,
    {
        'Items': [
            {
                'Id': 'view-movies',
                'Name': 'Movies',
                'CollectionType': 'movies',
                'ImageTags': {'Primary': 'tag-m'},
            },
            {'Id': 'view-shows', 'Name': 'TV Shows', 'CollectionType': 'tvshows', 'ImageTags': {}},
            {'Id': 'view-boxsets', 'Name': 'Collections', 'CollectionType': 'boxsets', 'ImageTags': {}},
            {'Id': 'view-music', 'Name': 'Music', 'CollectionType': 'music', 'ImageTags': {}},
            {'Id': 'view-livetv', 'Name': 'Live TV', 'CollectionType': 'livetv', 'ImageTags': {}},
            {'Id': 'view-playlists', 'Name': 'Playlists', 'CollectionType': 'playlists', 'ImageTags': {}},
        ],
        'TotalRecordCount': 6,
    },
)

PUBLIC_USERS = FakeResponse(
    200,
    [
        {
            'Id': 'u1',
            'Name': 'alice',
            'ServerId': 'srv-1',
            'HasPassword': True,
            'Policy': {'IsAdministrator': True},
        }
    ],
)

ME_ALICE = FakeResponse(
    200,
    {
        'Id': 'u1',
        'Name': 'alice',
        'ServerId': 'srv-1',
        'HasPassword': True,
        'Policy': {'IsAdministrator': True},
    },
)


def probe_ok(server_id='srv-1', name='Home Jellyfin'):
    data = json.loads(fixture('jellynet', 'system_info_public_10_11.json'))
    data['Id'] = server_id
    data['ServerName'] = name
    return FakeResponse(200, data)


def auth_result(token='TOK-1', uid='u1', name='alice', is_admin=True, server_id='srv-1'):
    return auth.AuthResult(token, uid, name, is_admin, server_id)


def make_server(session, base_url='http://jellyfin.lan:8096', token='TOK-1'):
    client = JellyfinClient(base_url, IDENTITY, token=token, username='alice', session=session)
    return JellyfinServer(jellyfin.StoredServer(base_url, 'srv-1', 'Home Jellyfin'), client)


def pin_window():
    window = signin.PinLoginWindow.__new__(signin.PinLoginWindow)
    window._closing = False
    window._winID = None
    window.windowId = 13001
    window.abort = False
    window.submitted = False
    window.change = False
    window.username = ''
    window.password = ''
    window.closed = False
    window.setFocusId = lambda control_id: setattr(window, 'focused', control_id)
    window.doClose = lambda: setattr(window, 'closed', True)
    return window


class JellyfinServerTest(KodiTestCase):
    def test_library_sections_come_from_user_views(self):
        session = FakeSession(USER_VIEWS)
        srv = make_server(session)

        sections = plexlibrary.Library(None, server=srv).sections()

        self.assertEqual(
            [(s.key, s.title, s.type) for s in sections],
            [('view-movies', 'Movies', 'movie'), ('view-shows', 'TV Shows', 'show')],
        )
        self.assertEqual(session.calls[0]['url'], 'http://jellyfin.lan:8096/UserViews')
        self.assertEqual(srv.platform, 'jellyfin')

        # Image URL uses /Items/{itemId}/Images/{imageType} with maxWidth, maxHeight, tag, and never carries a token.
        thumb_url = srv.getImageTranscodeURL(sections[0].thumb, 300, 450)
        self.assertIn('/Items/view-movies/Images/Primary', thumb_url)
        self.assertIn('maxWidth=300', thumb_url)
        self.assertIn('maxHeight=450', thumb_url)
        self.assertIn('tag=tag-m', thumb_url)
        self.assertNotIn('TOK-1', thumb_url)
        self.assertNotIn('Token=', thumb_url)

    def test_music_library_hidden_until_phase_10(self):
        srv = make_server(FakeSession(USER_VIEWS))

        sections = plexlibrary.Library(None, server=srv).sections()

        self.assertNotIn('view-music', [s.key for s in sections])
        self.assertNotIn('Music', [s.title for s in sections])
        self.assertEqual(srv.hubs(), [])

    def test_every_server_method_the_screens_call_exists(self):
        required = [
            'query',
            'buildUrl',
            'getImageTranscodeURL',
            'isReachable',
            'isLocalConnection',
            'anyLANConnection',
            'supportsFeature',
            'startPlayback',
            'fileId',
            'getObject',
            'watchInfo',
            'hasPreviews',
            'trickplayIndex',
            'isFavorite',
            'setFavorite',
            'themeUrl',
            'rememberStream',
            'searchSubtitles',
            'downloadSubtitle',
            'findVideoSession',
            'reportTimeline',
            'updateReachability',
            'close',
            'dropIdleConnections',
            'hubs',
            'homeCapabilities',
            'playlists',
            'library',
            'libraryLocations',
        ]
        srv = make_server(FakeSession())

        self.assertEqual(len(required), 29)
        for name in required:
            with self.subTest(method=name):
                self.assertTrue(hasattr(srv, name), f'JellyfinServer missing {name}')

    def test_unmapped_path_returns_empty_container_and_logs_once(self):
        srv = make_server(FakeSession())

        first = srv.query('/hubs/sections/home')
        second = srv.query('/hubs/sections/home')

        self.assertEqual(first.tag, 'MediaContainer')
        self.assertEqual(first.get('size'), '0')
        self.assertEqual(len(list(first)), 0)
        self.assertEqual(len(list(second)), 0)
        self.assertEqual(sum('/hubs/sections/home' in msg for msg, _ in ENV.log_lines), 1)

    def test_add_on_exit_closes_connections(self):
        from plexnet import http

        session = FakeSession()
        with (
            mock.patch.object(plexapp, 'SERVERMANAGER', JellyfinServerManager([make_server(session)])),
            mock.patch.object(http.HttpRequest, '_cancel', False),
        ):
            plexapp.App().preShutdown()

        self.assertTrue(session.closed)


class JellyfinStartupTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.found = []
        for patch in (
            mock.patch.object(plexapp, 'ACCOUNT', None),
            mock.patch.object(plexapp, 'SERVERMANAGER', None),
            mock.patch.object(pnUtil, 'ACCOUNT', None, create=True),
            mock.patch.object(pnUtil, 'SERVERMANAGER', None, create=True),
            mock.patch.object(jellyfin.discovery, 'discover', side_effect=lambda: self.found),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_first_run_asks_for_server_then_sign_in(self):
        ENV.dialog_answers = deque(['jellyfin.lan:8096'])
        session = FakeSession(probe_ok(), PUBLIC_USERS)

        with mock.patch.object(jellyfin, 'signIn', return_value=auth_result()) as do_sign_in:
            self.assertTrue(jellyfin.init(session=session))

        do_sign_in.assert_called_once()
        self.assertEqual([s.serverId for s in jellyfin.storedServers()], ['srv-1'])
        self.assertEqual(plexapp.ACCOUNT.ID, 'u1')
        self.assertTrue(plexapp.ACCOUNT.isAdmin)
        self.assertTrue(plexapp.ACCOUNT.isAuthenticated)
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-1')
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.platform, 'jellyfin')

        stored = jellyfin.storedSessions()['srv-1']
        self.assertEqual(stored['active_user_id'], 'u1')
        self.assertEqual(stored['users']['u1']['access_token'], 'TOK-1')

    def test_stored_session_relocates_moved_server_and_recovers_on_401(self):
        ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps([STORED])
        jellyfin.saveSessions(
            'srv-1',
            {'u1': {'user_id': 'u1', 'username': 'alice', 'access_token': 'TOK-OLD', 'is_admin': True}},
            'u1',
        )
        self.found = [Found('http://192.168.1.250:8096', 'srv-1', 'Home Jellyfin')]
        session = FakeSession(
            requests.ConnectionError('down'),
            probe_ok('srv-1'),
            problem(401, 'Unauthorized'),
            PUBLIC_USERS,
        )

        with mock.patch.object(jellyfin, 'signIn', return_value=auth_result('TOK-NEW')) as do_sign_in:
            self.assertTrue(jellyfin.init(session=session))

        do_sign_in.assert_called_once()
        self.assertEqual([s.baseUrl for s in jellyfin.storedServers()], ['http://192.168.1.250:8096'])
        self.assertEqual(plexapp.ACCOUNT.authToken, 'TOK-NEW')

    def test_switch_server_restarts_on_the_new_one(self):
        other = {'base_url': 'http://b.lan:8096', 'server_id': 'srv-2', 'name': 'Jellyfin B'}
        ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps([STORED, other])
        jellyfin.saveSessions(
            'srv-1',
            {'u1': {'user_id': 'u1', 'username': 'alice', 'access_token': 'TOK-1', 'is_admin': True}},
            'u1',
        )

        # User menu offers Switch server on platform == 'jellyfin' and closes session with {'switch_server': prev}.
        account = mock.Mock(isSignedIn=True, homeUsers=[mock.Mock()])
        selected = mock.Mock(spec=['platform'], platform='jellyfin')
        with (
            mock.patch.object(library.plexapp, 'ACCOUNT', account),
            mock.patch.object(library.plexapp, 'SERVERMANAGER', mock.Mock(selectedServer=selected)),
            mock.patch.object(library, 'showOptionsDialog', return_value=None) as dialog,
            mock.patch.object(jellyfin, 'pickServer', return_value='srv-1'),
        ):
            win = library.LibraryWindow.__new__(library.LibraryWindow)
            win._closeSessionWithOption = mock.Mock()
            win.showUserMenu()
            self.assertIn(('server', 'Switch server'), dialog.call_args[0][1])
            win.doUserOption('server')
            win._closeSessionWithOption.assert_called_once_with({'switch_server': 'srv-1'})

        # Completing sign-in on srv-2 restarts on srv-2.
        with mock.patch.object(jellyfin, 'signIn', return_value=auth_result('TOK-2', server_id='srv-2')):
            self.assertTrue(jellyfin.initAfterSwitch('srv-1', session=FakeSession(PUBLIC_USERS)))
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-2')

        # Giving up on srv-2's sign-in falls back to srv-1.
        jellyfin.saveSessions('srv-2', {}, None)
        with mock.patch.object(jellyfin, 'signIn', return_value=None):
            self.assertTrue(jellyfin.initAfterSwitch('srv-1', session=FakeSession(ME_ALICE, PUBLIC_USERS)))
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-1')
        self.assertEqual(jellyfin.storedServers()[-1].serverId, 'srv-1')

    def test_session_end_closes_home_once(self):
        win = library.LibraryWindow.__new__(library.LibraryWindow)
        win._shuttingDown = False
        win._closeSessionWithOption = mock.Mock()

        win.onSessionEnded()
        win.onSessionEnded()

        win._closeSessionWithOption.assert_called_once_with('session_ended')

    def test_sign_in_card_shows_quick_connect_code(self):
        # Quick Connect enabled: shows 6-digit code, link to /web/#/quickconnect, QR with ?code=<CODE>, and polls to auth.
        qc_pending = FakeResponse(
            200, {'Code': '482910', 'Secret': 'QC-SEC-1', 'Authenticated': False, 'DateAdded': '2026-11-25T07:00:00Z'}
        )
        qc_done = FakeResponse(
            200, {'Code': '482910', 'Secret': 'QC-SEC-1', 'Authenticated': True, 'DateAdded': '2026-11-25T07:00:00Z'}
        )
        auth_ok = FakeResponse(
            200,
            {
                'AccessToken': 'TOK-QC',
                'ServerId': 'srv-1',
                'User': {'Id': 'u1', 'Name': 'alice', 'ServerId': 'srv-1', 'Policy': {'IsAdministrator': True}},
                'SessionInfo': {'UserId': 'u1', 'UserName': 'alice'},
            },
        )
        session = FakeSession(
            FakeResponse(200, True),
            FakeResponse(200, {'LoginDisclaimer': 'Welcome home'}),
            qc_pending,
            qc_done,
            auth_ok,
        )
        client = JellyfinClient('http://jellyfin.lan:8096', IDENTITY, session=session)
        window = pin_window()

        with (
            mock.patch.object(signin.PinLoginWindow, 'create', return_value=window),
            mock.patch.object(jellyfin, 'WAIT_STEP', 0.001),
            mock.patch.object(jellyfin.util.MONITOR, 'waitForAbort', return_value=False),
            mock.patch.object(jellyfin.auth, 'POLL_INTERVAL', 0.001),
        ):
            result = jellyfin.signIn(client, 'http://jellyfin.lan:8096', server_name='Home Jellyfin')

        self.assertEqual(result.access_token, 'TOK-QC')
        self.assertTrue(window.closed)
        props = ENV.window_props[13001]
        self.assertEqual(props.get('server.name'), 'Home Jellyfin')
        self.assertEqual(props.get('subtitle'), 'Welcome home')
        self.assertEqual(props.get('quickconnect.enabled'), '1')
        self.assertEqual(props.get('pin'), '482910')
        self.assertIn('http://jellyfin.lan:8096/web/#/quickconnect', props.get('instructions'))
        expected_qr = signin.qrImage('http://jellyfin.lan:8096/web/#/quickconnect?code=482910')
        self.assertEqual(props.get('qr'), expected_qr)
        self.assertTrue(os.path.exists(expected_qr))

        # Quick Connect disabled: hides code and QR cleanly.
        ENV.window_props.clear()
        off_session = FakeSession(
            FakeResponse(200, False),
            FakeResponse(200, {'LoginDisclaimer': ''}),
        )
        off_client = JellyfinClient('http://jellyfin.lan:8096', IDENTITY, session=off_session)
        off_window = pin_window()
        off_window.abort = True

        with mock.patch.object(signin.PinLoginWindow, 'create', return_value=off_window):
            self.assertIsNone(jellyfin.signIn(off_client, 'http://jellyfin.lan:8096', server_name='Home Jellyfin'))

        off_props = ENV.window_props[13001]
        self.assertEqual(off_props.get('quickconnect.enabled'), '')
        self.assertEqual(off_props.get('pin', ''), '')
        self.assertEqual(off_props.get('qr', ''), '')

    def test_sign_in_card_password_login_and_change_and_unreachable(self):
        # Password sign-in when Quick Connect is disabled: wrong password shows error, then valid password signs in.
        window = pin_window()
        ENV.dialog_answers = deque(['alice', 'bad', 'good'])

        def step_wait(_seconds):
            if not hasattr(step_wait, 'stage'):
                step_wait.stage = 0
            if step_wait.stage == 0:
                window.onClick(window.USERNAME_ID)
                window.onClick(window.PASSWORD_ID)
                window.onClick(window.SIGN_IN_ID)
                step_wait.stage = 1
            elif step_wait.stage == 1:
                window.onClick(window.PASSWORD_ID)
                window.onClick(window.SIGN_IN_ID)
                step_wait.stage = 2
            return False

        auth_ok = FakeResponse(
            200,
            {
                'AccessToken': 'TOK-PW',
                'ServerId': 'srv-1',
                'User': {'Id': 'u1', 'Name': 'alice', 'ServerId': 'srv-1', 'Policy': {'IsAdministrator': False}},
                'SessionInfo': {'UserId': 'u1', 'UserName': 'alice'},
            },
        )
        session = FakeSession(
            FakeResponse(200, False),
            FakeResponse(200, {'LoginDisclaimer': ''}),
            FakeResponse(401, raw='Invalid username or password.'),
            auth_ok,
        )
        client = JellyfinClient('http://jellyfin.lan:8096', IDENTITY, session=session)
        with (
            mock.patch.object(signin.PinLoginWindow, 'create', return_value=window),
            mock.patch.object(jellyfin, 'WAIT_STEP', 0.5),
            mock.patch.object(jellyfin.util.MONITOR, 'waitForAbort', side_effect=step_wait),
        ):
            result = jellyfin.signIn(client, 'http://jellyfin.lan:8096', server_name='Home Jellyfin')

        self.assertEqual(result.access_token, 'TOK-PW')
        self.assertEqual(ENV.settings.get(jellyfin.USERNAME_SETTING), 'alice')
        self.assertNotIn('good', ENV.settings.values())

        # Clicking Change returns jellyfin.CHANGE; unreachable focuses CHANGE_ID without calling server.
        ENV.window_props.clear()
        change_win = pin_window()
        change_win.onClick(change_win.CHANGE_ID)
        with mock.patch.object(signin.PinLoginWindow, 'create', return_value=change_win):
            self.assertIs(
                jellyfin.signIn(
                    JellyfinClient('http://jellyfin.lan:8096', IDENTITY, session=FakeSession()),
                    'http://jellyfin.lan:8096',
                    reachable=False,
                ),
                jellyfin.CHANGE,
            )
        self.assertEqual(change_win.focused, change_win.CHANGE_ID)
        self.assertIn('Could not reach a Jellyfin server', ENV.window_props[13001].get('error'))
