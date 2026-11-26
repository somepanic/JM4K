"""
JellyfinServer, lib/jellyfin.py start-up (init, initAfterSwitch, signIn), Quick Connect sign-in card,
Switch server menu, and session-end handling.
"""

import json
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
from lib.windows import library

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


class JellyfinServerTest(KodiTestCase):
    def test_library_sections_come_from_user_views(self):
        session = FakeSession(USER_VIEWS)
        srv = make_server(session)

        sections = plexlibrary.Library(None, server=srv).sections()

        self.assertEqual(
            [(s.key, s.title, s.type) for s in sections],
            [
                ('view-movies', 'Movies', 'movie'),
                ('view-shows', 'TV Shows', 'show'),
                ('view-boxsets', 'Collections', 'movie'),
            ],
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
        session = FakeSession(
            USER_VIEWS,
            FakeResponse(200, {'CustomPrefs': {}}),
            FakeResponse(200, {'Items': [], 'TotalRecordCount': 0}),
            FakeResponse(200, {'Items': [], 'TotalRecordCount': 0}),
            ME_ALICE,
            USER_VIEWS,
            FakeResponse(200, []),
            FakeResponse(200, []),
            FakeResponse(200, {'Items': [], 'TotalRecordCount': 0}),
        )
        srv = make_server(session)

        sections = plexlibrary.Library(None, server=srv).sections()

        self.assertNotIn('view-music', [s.key for s in sections])
        self.assertNotIn('Music', [s.title for s in sections])
        self.assertEqual(srv.hubs(), [])
        self.assertNotIn('view-music', [c['params'].get('parentId') for c in session.calls if c.get('params')])

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
            'playlists',
            'library',
            'libraryLocations',
        ]
        srv = make_server(FakeSession())

        self.assertEqual(len(required), 28)
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

    def signed_in(self, token='TOK-1', uid='u1', name='alice', is_admin=True, server_id='srv-1'):
        """Sign in to continue as the person uses it: someone signs in, which the screen records on the account."""

        def screen():
            plexapp.ACCOUNT.recordAuth(auth_result(token, uid, name, is_admin, server_id))
            plexapp.ACCOUNT.isAuthenticated = True
            return True

        return screen

    def test_first_run_connects_to_a_server_then_signs_in(self):
        session = FakeSession(probe_ok(), PUBLIC_USERS)

        def connect(identity, session=None, **kwargs):
            return jellyfin.addServer('jellyfin.lan:8096', identity, session=session)

        with (
            mock.patch.object(jellyfin, 'connectToServer', side_effect=connect) as connect_screen,
            mock.patch.object(jellyfin, 'signInScreen', side_effect=self.signed_in()) as sign_in_screen,
        ):
            self.assertTrue(jellyfin.init(session=session))

        connect_screen.assert_called_once()
        sign_in_screen.assert_called_once()
        self.assertEqual([s.serverId for s in jellyfin.storedServers()], ['srv-1'])
        self.assertEqual(plexapp.ACCOUNT.ID, 'u1')
        self.assertTrue(plexapp.ACCOUNT.isAdmin)
        self.assertTrue(plexapp.ACCOUNT.isAuthenticated)
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-1')
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.platform, 'jellyfin')

        stored = jellyfin.storedSessions()['srv-1']
        self.assertEqual(stored['active_user_id'], 'u1')
        self.assertEqual(stored['users']['u1']['access_token'], 'TOK-1')

    def test_sign_in_screen_lists_the_servers_users_before_anyone_signs_in(self):
        ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps([STORED])
        seen = []

        def screen():
            seen.append(([u.username for u in plexapp.ACCOUNT.homeUsers], plexapp.SERVERMANAGER.selectedServer.uuid))
            # Back: no one signed in

        with mock.patch.object(jellyfin, 'signInScreen', side_effect=screen):
            self.assertFalse(jellyfin.init(session=FakeSession(PUBLIC_USERS)))

        self.assertEqual(seen, [([u['Name'] for u in PUBLIC_USERS.json()], 'srv-1')])

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

        with mock.patch.object(jellyfin, 'signInScreen', side_effect=self.signed_in('TOK-NEW')) as screen:
            self.assertTrue(jellyfin.init(session=session))

        screen.assert_called_once()
        self.assertEqual([s.baseUrl for s in jellyfin.storedServers()], ['http://192.168.1.250:8096'])
        self.assertEqual(plexapp.ACCOUNT.authToken, 'TOK-NEW')

    def test_a_server_that_stopped_answering_opens_connect_to_server(self):
        ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps([STORED])

        with mock.patch.object(jellyfin, 'connectToServer', return_value=None) as connect:
            self.assertFalse(jellyfin.init(session=FakeSession(requests.ConnectionError('down'))))

        self.assertEqual(connect.call_args.kwargs['address'], STORED['base_url'])
        self.assertEqual(
            connect.call_args.kwargs['error'], 'Could not reach a Jellyfin server at {}.'.format(STORED['base_url'])
        )

    def test_switch_server_on_the_sign_in_screen_starts_on_the_new_server(self):
        ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps([STORED])
        new = {'base_url': 'http://b.lan:8096', 'server_id': 'srv-2', 'name': 'Jellyfin B'}
        answers = iter([jellyfin.SWITCH_SERVER, True])

        def screen():
            answer = next(answers)
            if answer == jellyfin.SWITCH_SERVER:
                # Connect to Server stored srv-2 last
                ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps([STORED, new])
                return answer
            return self.signed_in('TOK-2', server_id='srv-2')()

        with mock.patch.object(jellyfin, 'signInScreen', side_effect=screen):
            self.assertTrue(jellyfin.init(session=FakeSession(PUBLIC_USERS, PUBLIC_USERS)))

        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-2')
        self.assertEqual(plexapp.ACCOUNT.authToken, 'TOK-2')

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

        # Signing in on srv-2 restarts on srv-2.
        with mock.patch.object(jellyfin, 'signInScreen', side_effect=self.signed_in('TOK-2', server_id='srv-2')):
            self.assertTrue(jellyfin.initAfterSwitch('srv-1', session=FakeSession(PUBLIC_USERS)))
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-2')

        # Giving up on srv-2's sign-in falls back to srv-1.
        jellyfin.saveSessions('srv-2', {}, None)
        with mock.patch.object(jellyfin, 'signInScreen', return_value=None):
            self.assertTrue(
                jellyfin.initAfterSwitch('srv-1', session=FakeSession(PUBLIC_USERS, ME_ALICE, PUBLIC_USERS))
            )
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-1')
        self.assertEqual(jellyfin.storedServers()[-1].serverId, 'srv-1')

    def test_session_end_closes_home_once(self):
        win = library.LibraryWindow.__new__(library.LibraryWindow)
        win._shuttingDown = False
        win._closeSessionWithOption = mock.Mock()

        win.onSessionEnded()
        win.onSessionEnded()

        win._closeSessionWithOption.assert_called_once_with('session_ended')
