"""
A library's grid from Silo's catalog: SiloServer.query() answers Uno's /library/sections/{key}/all (LibrarySection.all(),
the grid's count and its chunks) from GET /api/v2/catalog?source=query&library_id=... A chunk is Silo's explicit window
jump (seek, limit): the grid asks for chunks out of order, and a chunk past Silo's 200-item page is asked in two.
Responses are written inline in the shape of /api/v2/openapi.json (Silo build-2987).
"""

from plexnet import plexlibrary, video  # noqa: F401 (video registers the item types, as the add-on's imports do)
from silonet.client import Credentials, DeviceIdentity, SiloClient
from silonet.server import SiloServer

from lib.silo import StoredServer

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')
LIBRARIES = FakeResponse(
    200,
    {
        'items': [
            {'id': '2', 'name': '2160p Movies', 'type': 'movies'},
            {'id': '5', 'name': '2160p Shows', 'type': 'series'},
        ]
    },
)


def movie(n):
    return {
        'content_id': f'm{n}',
        'type': 'movie',
        'title': f'Film {n}',
        'year': 2000 + n % 20,
        'poster_url': f'/api/v2/artwork/tmdb/movies/{n}/poster/w500.x',
        'user_state': {'played': False},
    }


def series(n):
    return {
        'content_id': f's{n}',
        'type': 'series',
        'title': f'Show {n}',
        'user_state': {'watched_count': 1, 'unplayed_count': 9},
    }


def page(items, total, start=0):
    return FakeResponse(
        200,
        {
            'items': items,
            'total': total,
            'total_exact': True,
            'page': {'has_more': start + len(items) < total, 'next_cursor': 'opaque'},
        },
    )


def server(session):
    client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(accessToken='A1', profileId='p1'), session=session)
    return SiloServer(StoredServer('http://silo.lan:8090', 'srv-1', 'Silo'), client)


def section(session, index=0):
    return plexlibrary.Library(None, server=server(session)).sections()[index]


class SiloLibraryGridTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()  # LibrarySection reads the add-on's preferences through plexnet's interface

    def test_the_grids_count_is_silos_total(self):
        session = FakeSession(LIBRARIES, page([movie(0)], 216))

        listing = section(session).all(0, 0)

        self.assertEqual(listing.totalSize.asInt(), 216)
        self.assertEqual(len(listing), 0)
        call = session.calls[1]
        self.assertEqual(call['url'], 'http://silo.lan:8090/api/v2/catalog')
        self.assertEqual((call['params']['source'], call['params']['library_id']), ('query', '2'))

    def test_a_chunk_is_exactly_those_positions(self):
        session = FakeSession(LIBRARIES, page([movie(n) for n in range(100, 150)], 216, 100))

        items = section(session).all(100, 50)

        self.assertEqual([i.title for i in items], [f'Film {n}' for n in range(100, 150)])
        self.assertEqual((session.calls[1]['params']['seek'], session.calls[1]['params']['limit']), (100, 50))
        self.assertEqual(items[0].type, 'movie')
        self.assertEqual(
            items[0].defaultThumb.asTranscodedImageURL(300, 450),
            'http://silo.lan:8090/api/v2/artwork/tmdb/movies/100/poster/w500.x',
        )

    def test_a_chunk_past_silos_page_size_is_asked_in_two(self):
        session = FakeSession(
            LIBRARIES,
            page([movie(n) for n in range(200)], 216),
            page([movie(n) for n in range(200, 216)], 216, 200),
        )

        items = section(session).all(0, 240)

        self.assertEqual([i.title for i in items], [f'Film {n}' for n in range(216)])
        self.assertEqual(
            [(c['params']['seek'], c['params']['limit']) for c in session.calls[1:]], [(0, 200), (200, 40)]
        )

    def test_a_jump_far_down_asks_for_that_window_only(self):
        session = FakeSession(LIBRARIES, page([movie(n) for n in range(4800, 4840)], 4840, 4800))

        items = section(session).all(4800, 240)

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[1]['params']['seek'], 4800)
        self.assertEqual(len(items), 40)

    def test_a_series_librarys_items_are_shows(self):
        session = FakeSession(LIBRARIES, page([series(1), series(2)], 2))

        items = section(session, index=1).all(0, 50)

        self.assertEqual([(i.type, i.title) for i in items], [('show', 'Show 1'), ('show', 'Show 2')])
        self.assertEqual(session.calls[1]['params']['library_id'], '5')

    def test_a_listing_silo_cant_answer_is_none(self):
        srv = server(FakeSession(problem(503, 'dependency_unavailable')))

        self.assertIsNone(srv.query('/library/sections/2/all?X-Plex-Container-Start=0&X-Plex-Container-Size=0'))


class SiloLibrarySortTest(KodiTestCase):
    """The grid's sort menu (LibraryWindow.serverSortOptions(): /library/sections/<id>/sorts) offers what Silo's
    catalog sorts by, and a sorted chunk asks Silo for that sort ('field' or '-field')."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_the_menu_offers_what_silo_sorts_by(self):
        sorts = section(FakeSession(LIBRARIES)).listSorts(libtype='movie')

        self.assertEqual(
            [(s.key, s.defaultDirection) for s in sorts],
            [
                ('titleSort', 'asc'),
                ('addedAt', 'desc'),
                ('originallyAvailableAt', 'desc'),
                ('year', 'desc'),
                ('rating', 'desc'),
                ('audienceRating', 'desc'),
                ('contentRating', 'asc'),
                ('duration', 'desc'),
                ('viewOffset', 'desc'),
            ],
        )

    def test_a_sorted_chunk_asks_silo_for_that_sort(self):
        session = FakeSession(LIBRARIES, page([movie(1)], 1))

        section(session).all(0, 50, sort=('addedAt', 'desc'))

        self.assertEqual(session.calls[1]['params']['sort'], '-added_at')

    def test_ascending_is_the_bare_field(self):
        session = FakeSession(LIBRARIES, page([movie(1)], 1))

        section(session).all(0, 50, sort=('titleSort', 'asc'))

        self.assertEqual(session.calls[1]['params']['sort'], 'title')

    def test_a_sort_silo_lacks_leaves_silos_own_order(self):
        session = FakeSession(LIBRARIES, page([movie(1)], 1))

        section(session).all(0, 50, sort=('lastViewedAt', 'desc'))

        self.assertNotIn('sort', session.calls[1]['params'])


def letters(runs, total):
    """POST /api/v2/catalog/letters: runs as (letter, position)."""
    return FakeResponse(200, {'total': total, 'letters': [], 'runs': [{'letter': l, 'position': p} for l, p in runs]})


class SiloLibraryJumpBarTest(KodiTestCase):
    """The grid's A-Z bar (LibrarySection.jumpList(): /library/sections/<id>/firstCharacter) from Silo's letters: one
    entry per block of titles under a letter, in list order, sized so each starts where Silo says it does."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_letters_in_list_order_sized_to_their_positions(self):
        session = FakeSession(LIBRARIES, letters([('#', 0), ('A', 4), ('B', 16), ('C', 19)], 33))

        bar = section(session).jumpList(sort=('titleSort', 'asc'))

        self.assertEqual([(str(j.title), j.size.asInt()) for j in bar], [('#', 4), ('A', 12), ('B', 3), ('C', 14)])
        call = session.calls[1]
        self.assertEqual((call['method'], call['url']), ('POST', 'http://silo.lan:8090/api/v2/catalog/letters'))
        self.assertEqual(call['json'], {'library_id': '2', 'order': 'asc'})

    def test_descending_titles_ask_for_descending_letters(self):
        session = FakeSession(LIBRARIES, letters([('Z', 0), ('A', 5)], 9))

        section(session).jumpList(sort=('titleSort', 'desc'))

        self.assertEqual(session.calls[1]['json']['order'], 'desc')

    def test_a_letter_in_two_blocks_keeps_each_blocks_place(self):
        session = FakeSession(LIBRARIES, letters([('A', 0), ('B', 3), ('A', 5)], 8))

        bar = section(session).jumpList(sort=('titleSort', 'asc'))

        self.assertEqual([(str(j.title), j.size.asInt()) for j in bar], [('A', 3), ('B', 2), ('A', 3)])
        self.assertEqual(len({str(j.key) for j in bar}), 3)

    def test_no_letters_means_no_bar(self):
        bar = section(FakeSession(LIBRARIES, problem(503, 'dependency_unavailable'))).jumpList(
            sort=('titleSort', 'asc')
        )

        self.assertFalse(bar)


def librarySection(sid, kind, title, count):
    return {
        'id': sid,
        'section_type': kind,
        'title': title,
        'items': [movie(n) for n in range(count)],
        'collections': [],
        'total_count': count,
        'has_more': None,
        'item_limit': 100,
    }


class SiloLibraryRowsTest(KodiTestCase):
    """A library's Recommended rows (library_hubs: server.hubs(section.key, count)) from Silo's own rows for that
    library (GET /api/v2/library/{id}/sections, D8), and each row's See more from that row's items."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def rows(self):
        return FakeResponse(
            200,
            {
                'sections': [
                    librarySection('152896895625322561', 'recently_added', 'Recently Added Movies', 20),
                    librarySection('152896895625584705', 'random', 'Random Picks', 4),
                ]
            },
        )

    def test_a_librarys_rows_in_silos_order(self):
        session = FakeSession(self.rows())

        hubs = server(session).hubs('2', count=20)

        self.assertEqual([(h.title, len(h.items)) for h in hubs], [('Recently Added Movies', 20), ('Random Picks', 4)])
        self.assertEqual(session.calls[0]['url'], 'http://silo.lan:8090/api/v2/library/2/sections')
        self.assertEqual(session.calls[0]['params'], {'item_limit': 20})
        self.assertEqual(len({h.getCleanHubIdentifier() for h in hubs}), 2)

    def test_a_full_row_says_it_has_more(self):
        hubs = server(FakeSession(self.rows())).hubs('2', count=20)

        self.assertEqual([h.more.asBool() for h in hubs], [True, False])

    def test_a_rows_see_more_lists_that_rows_items(self):
        session = FakeSession(
            self.rows(), FakeResponse(200, librarySection('152896895625322561', 'recently_added', 'x', 100))
        )
        srv = server(session)
        hub = srv.hubs('2', count=20)[0]

        answer = srv.query(hub.key, limit=0)

        self.assertEqual(answer.get('totalSize'), '100')
        self.assertEqual(
            session.calls[1]['url'], 'http://silo.lan:8090/api/v2/library/2/sections/152896895625322561/items'
        )

    def test_no_rows_when_silo_cant_answer(self):
        self.assertEqual(server(FakeSession(problem(503, 'dependency_unavailable'))).hubs('2', count=20), [])


def curated(cid, title, count):
    return {
        'id': cid,
        'title': title,
        'item_count': count,
        'poster_url': f'/api/v2/artwork/collections/{cid}.webp',
        'backdrop_url': '',
        'description': f'About {title}',
        'library_id': '2',
        'group_id': None,
    }


COLLECTIONS = FakeResponse(
    200,
    {
        'library_id': '2',
        'collections': [curated('c1', 'Alien', 4), curated('c2', 'Bond', 25)],
        'groups': [
            {
                'id': 'g1',
                'kind': 'user_collections',
                'name': 'Shared',
                'collections': [curated('c2', 'Bond', 25), curated('u1', 'Sam picks', 7)],
            }
        ],
    },
)


class SiloCollectionsTest(KodiTestCase):
    """A library's Collections tab (the grid's type=18 listing) from GET /api/v2/library/{id}/collections, and a
    collection's titles (Collection.all(): its key) from the catalog's library_collection source."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_the_tab_lists_the_librarys_collections_then_personal_ones(self):
        session = FakeSession(LIBRARIES, COLLECTIONS)

        items = section(session).all(0, 50, type_=18)

        self.assertEqual(
            [(i.type, i.title, i.childCount.asInt()) for i in items],
            [('collection', 'Alien', 4), ('collection', 'Bond', 25), ('collection', 'Sam picks', 7)],
        )
        self.assertEqual(session.calls[1]['url'], 'http://silo.lan:8090/api/v2/library/2/collections')
        self.assertEqual(items[0].memberType(), 'movie')

    def test_the_tabs_count(self):
        listing = section(FakeSession(LIBRARIES, COLLECTIONS)).all(0, 0, type_=18)

        self.assertEqual(listing.totalSize.asInt(), 3)

    def test_a_collection_lists_its_titles(self):
        session = FakeSession(LIBRARIES, COLLECTIONS, page([movie(n) for n in range(4)], 4))
        alien = section(session).all(0, 50, type_=18)[0]

        titles = alien.all(0, 50)

        self.assertEqual([t.title for t in titles], ['Film 0', 'Film 1', 'Film 2', 'Film 3'])
        params = session.calls[2]['params']
        self.assertEqual(
            (params['source'], params['collection_id'], params['seek'], params['limit']),
            ('library_collection', 'c1', 0, 50),
        )

    def test_no_letters_on_the_collections_tab(self):
        session = FakeSession(LIBRARIES)

        bar = section(session).jumpList(sort=('titleSort', 'asc'), type_=18)

        self.assertFalse(bar)
        self.assertEqual(len(session.calls), 1)


FACETS = FakeResponse(200, {'genres': ['Action', 'Comedy'], 'content_ratings': ['PG', 'PG-13'], 'studios': ['A24']})


class SiloLibraryFilterTest(KodiTestCase):
    """The grid's Filter menu (LibrarySection.listFilters(): /library/sections/<id>/filters; values from
    listChoices(): /library/sections/<id>/<filter>) offers the filters Silo's catalog takes, with values from
    GET /api/v2/catalog/filters; a filtered chunk sends Silo's parameter."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_the_menu_offers_genre_and_content_rating(self):
        filters = section(FakeSession(LIBRARIES)).listFilters(libtype='movie')

        self.assertEqual(
            [(f.filter, f.filterType) for f in filters], [('genre', 'string'), ('contentRating', 'string')]
        )

    def test_a_filters_values_are_silos_facets_for_the_library(self):
        session = FakeSession(LIBRARIES, FACETS)

        values = section(session).listChoices('genre', libtype='movie')

        self.assertEqual([(str(v.key), str(v.title)) for v in values], [('Action', 'Action'), ('Comedy', 'Comedy')])
        call = session.calls[1]
        self.assertEqual(call['url'], 'http://silo.lan:8090/api/v2/catalog/filters')
        self.assertEqual((call['params']['source'], call['params']['library_id']), ('query', '2'))
        # Silo takes JSON booleans in a query: 'True' (what requests makes of True) is 422
        self.assertEqual(call['params']['skip_technical'], 'true')

    def test_a_filtered_chunk_and_count_send_silos_parameter(self):
        session = FakeSession(LIBRARIES, page([movie(1)], 105), page([movie(1)], 105))

        movies = section(session)

        listing = movies.all(0, 0, filter_=('genre', 'Action'))
        movies.all(0, 50, filter_=('contentRating', 'PG-13'))

        self.assertEqual(listing.totalSize.asInt(), 105)
        self.assertEqual(session.calls[1]['params']['genre'], 'Action')
        self.assertEqual(session.calls[2]['params']['content_rating'], 'PG-13')

    def test_no_letters_while_filtered(self):
        session = FakeSession(LIBRARIES)

        bar = section(session).jumpList(filter_=('genre', 'Action'), sort=('titleSort', 'asc'))

        self.assertFalse(bar)
        self.assertEqual(len(session.calls), 1)
