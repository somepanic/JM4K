"""
silonet.server: SiloServer answers the Plex paths Uno's object model and screens ask for, from Silo's API, as
Plex-shaped XML; and SiloServerManager holds the Silo servers the way plexapp.SERVERMANAGER held Plex's.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True  # importing lib.windows.* starts lib.player's monitor thread otherwise
from plexnet import plexlibrary
from silonet.client import Credentials, DeviceIdentity, SiloClient
from silonet.server import SiloServer
from silonet.servermanager import SiloServerManager

from lib.silo import StoredServer
from lib.windows import home, library_grid, section_ids, sidebar_model

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')
LIBRARIES = FakeResponse(
    200,
    {
        'items': [
            {
                'id': 'lib-m',
                'name': '1080p Movies',
                'type': 'movies',
                'sort_order': 1,
                'box_sets_enabled': False,
                'poster_url': 'https://silo.lan/posters/lib-m.webp',
            },
            {'id': 'lib-s', 'name': 'Shows', 'type': 'series', 'sort_order': 2, 'box_sets_enabled': False},
            {'id': 'lib-x', 'name': 'test-files', 'type': 'mixed', 'sort_order': 3, 'box_sets_enabled': False},
        ]
    },
)


def server(session):
    client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(accessToken='A1', profileId='p1'), session=session)
    return SiloServer(StoredServer('http://silo.lan:8090', 'srv-1', 'Silo'), client)


class SiloServerTest(KodiTestCase):
    def test_library_sections_come_from_user_libraries(self):
        session = FakeSession(LIBRARIES)

        sections = plexlibrary.Library(None, server=server(session)).sections()

        self.assertEqual(
            [(s.key, s.title, s.type) for s in sections],
            [('lib-m', '1080p Movies', 'movie'), ('lib-s', 'Shows', 'show'), ('lib-x', 'test-files', 'movie')],
        )
        self.assertEqual(session.calls[0]['url'], 'http://silo.lan:8090/api/v2/user/libraries')
        self.assertEqual(session.calls[0]['headers']['X-Profile-Id'], 'p1')

    def test_a_mixed_library_is_one_grid(self):
        # D24: movies and series together, as one grid (a film library's, whose tiles open as their own type)
        cards = {
            'items': [
                {'content_id': 'movie-1', 'type': 'movie', 'title': 'Dune'},
                {'content_id': 'series-1', 'type': 'series', 'title': 'The Crocodile Hunter'},
            ],
            'total': 2,
        }
        session = FakeSession(LIBRARIES, FakeResponse(200, cards))
        srv = server(session)

        sections = {s.key: s for s in plexlibrary.Library(None, server=srv).sections()}
        grid = srv.query('/library/sections/lib-x/all', offset=0, limit=50)

        mixed = sections['lib-x']
        self.assertEqual(mixed.TYPE, 'movie')
        self.assertTrue(mixed.get('mixed').asBool())
        # its count and type picker say "Movies & Shows", not "Movies"
        self.assertEqual(library_grid.typePlural(mixed, None), 'Movies & Shows')
        self.assertEqual(library_grid.typePlural(sections['lib-m'], None), 'Movies')
        self.assertEqual(session.calls[1]['params']['library_id'], 'lib-x')
        self.assertEqual(
            [(e.get('type'), e.get('title')) for e in grid], [('movie', 'Dune'), ('show', 'The Crocodile Hunter')]
        )

    def test_unmapped_path_returns_empty_container_and_logs_once(self):
        srv = server(FakeSession())

        first = srv.query('/hubs/sections/home')
        srv.query('/hubs/sections/home')

        self.assertEqual(first.tag, 'MediaContainer')
        self.assertEqual(len(list(first)), 0)
        self.assertEqual(sum('/hubs/sections/home' in msg for msg, _ in ENV.log_lines), 1)

    def test_an_item_by_its_key(self):
        session = FakeSession(FakeResponse(200, {'content_id': 'movie-1', 'type': 'movie', 'title': 'Dune'}))

        item = server(session).getObject('/library/metadata/movie-1')

        self.assertEqual((item.TYPE, item.title), ('movie', 'Dune'))

    def test_pre_plays_more_menu_has_what_silo_does(self):
        # an admin's More menu read allowsMediaDeletion, which a Silo server lacked: the menu never opened
        session = FakeSession(FakeResponse(202, {}))
        srv = server(session)

        srv.query('/library/metadata/movie-1/refresh', method='put')

        self.assertFalse(srv.allowsMediaDeletion)
        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url']),
            ('POST', 'http://silo.lan:8090/api/v2/admin/items/movie-1/refresh-metadata'),
        )

    def test_identity_and_urls(self):
        srv = server(FakeSession())

        self.assertEqual((srv.uuid, srv.name), ('srv-1', 'Silo'))
        self.assertEqual(srv.buildUrl('/api/v2/x'), 'http://silo.lan:8090/api/v2/x')
        self.assertEqual(srv.getImageTranscodeURL('https://silo.lan/p.webp', 300, 450), 'https://silo.lan/p.webp')


class SiloServerOnHomeTest(KodiTestCase):
    """Home and its sidebar read a Plex server's connection state before they ask it anything (activeConnection,
    pendingReachabilityRequests, gone): a Silo server has its one address and is asked directly."""

    def test_sidebar_lists_its_libraries(self):
        srv = server(FakeSession(LIBRARIES))

        with (
            mock.patch.object(sidebar_model.SectionsFetchTask, 'start', lambda task: task.run()),
            mock.patch.dict(sidebar_model._serverSections, clear=True),
            mock.patch.object(sidebar_model, '_sectionsFetching', set()),
        ):
            sidebar_model.serverSections(srv)
            listed = sidebar_model.knownSections('srv-1')

        self.assertEqual([s.title for s in listed], ['1080p Movies', 'Shows', 'test-files'])

    def test_home_binds_its_rows(self):
        got = []
        session = FakeSession(
            FakeResponse(200, {'state': 'available'}),
            FakeResponse(
                200, {'sections': [{'id': 's2', 'section_type': 'recently_added', 'title': 'Recently Added'}]}
            ),
            FakeResponse(200, {'items': [{'content_id': 'm1', 'type': 'movie', 'title': 'Dune'}], 'has_more': False}),
            FakeResponse(200, {'items': []}),  # no favorites
        )

        home.HomeHubsTask().setup(
            home.home_section, lambda section, rows: got.append(rows), [(server(session), ['lib-m'])]
        ).run()

        self.assertEqual([(h.title, [i.title for i in h.items]) for h in got[0]], [('Recently Added', ['Dune'])])
        self.assertEqual(list(got[0].answers), ['srv-1'])

    def test_a_library_does_not_wait_for_a_connection_test(self):
        self.assertTrue(section_ids.awaitConnection(server(FakeSession()), 0.1))

    def test_sidebar_has_no_playlists_entry(self):
        # Silo has no playlists (D4); the sidebar asks on the main thread as it builds
        with mock.patch.dict(sidebar_model._hasPlaylists, clear=True):
            self.assertFalse(sidebar_model.hasPlaylists(server(FakeSession())))

    def test_waking_from_sleep_drops_no_connections(self):
        srv = server(FakeSession())

        srv.dropIdleConnections()

        self.assertTrue(section_ids.awaitConnection(srv, 0.1))


class SiloServerManagerTest(KodiTestCase):
    def test_holds_the_selected_server(self):
        srv = server(FakeSession())
        manager = SiloServerManager([srv])

        self.assertIs(manager.selectedServer, srv)
        self.assertEqual(manager.getServers(), [srv])
        self.assertIs(manager.getServer('srv-1'), srv)
        self.assertEqual(manager.serversByUuid, {'srv-1': srv})
        self.assertTrue(manager.serversKnown())


class SiloShutdownTest(KodiTestCase):
    def test_add_on_exit_closes_its_connections(self):
        """main.py's shutdown runs plexapp.App.preShutdown(), which closes every server. On the user's Kodi box a
        SiloServer without close() stopped the shutdown there, and Kodi killed the add-on after 5 s."""
        from plexnet import http, plexapp

        session = FakeSession()
        with (
            mock.patch.object(plexapp, 'SERVERMANAGER', SiloServerManager([server(session)])),
            mock.patch.object(http.HttpRequest, '_cancel', False),
        ):
            plexapp.App().preShutdown()

        self.assertTrue(session.closed)

    def test_add_on_exit_before_sign_in(self):
        """Back on the sign-in screen ends the add-on before silo.init() sets a server manager. On the user's Kodi box
        preShutdown() then raised AttributeError, leaving the add-on's running markers set, so its next start was
        refused."""
        from plexnet import http, plexapp

        with (
            mock.patch.object(plexapp, 'SERVERMANAGER', None),
            mock.patch.object(http.HttpRequest, '_cancel', False),
        ):
            plexapp.App().preShutdown()  # raises nothing
