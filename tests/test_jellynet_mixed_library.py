"""
Mixed libraries (films and shows together), as SiloMod shows them (its D24): Jellyfin gives such a library's view no
CollectionType. It is one grid, a film library's marked mixed="1" (the grid's count says "Movies & Shows"), listing
films and shows together; each tile opens as its own type. Home's Latest rows, the library's Recommended rows and a
search in it include both kinds.
"""

from jellynet import home as jf_home
from jellynet import library as jf_library
from jellynet import search as jf_search
from plexnet import plexlibrary, video  # noqa: F401 (video registers plexnet item types)

from lib.windows import library_grid

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession
from .test_jellynet_library import items_page, make_section, make_server, movie_dto, series_dto

USER_VIEWS_MIXED = FakeResponse(
    200,
    {
        'Items': [
            {'Id': 'view-movies', 'Name': 'Movies', 'CollectionType': 'movies', 'Type': 'CollectionFolder'},
            {'Id': 'view-mixed', 'Name': 'Test-Files', 'CollectionType': None, 'Type': 'CollectionFolder'},
            {'Id': 'view-live', 'Name': 'Live TV', 'CollectionType': 'livetv', 'Type': 'UserView'},
        ],
        'TotalRecordCount': 3,
    },
)


class MixedLibraryTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_listed_as_a_film_library_marked_mixed(self):
        sections = plexlibrary.Library(None, server=make_server(FakeSession(USER_VIEWS_MIXED))).sections()

        self.assertEqual([(s.key, s.type) for s in sections], [('view-movies', 'movie'), ('view-mixed', 'movie')])
        self.assertFalse(sections[0].get('mixed').asBool())
        self.assertTrue(sections[1].get('mixed').asBool())
        self.assertEqual(library_grid.typePlural(sections[1], 'movie'), library_grid.TYPE_PLURAL['movies_shows'])

    def test_grid_lists_films_and_shows_together(self):
        session = FakeSession(USER_VIEWS_MIXED, items_page([movie_dto(1), series_dto(2)], total=2))
        mixed = make_section(session, index=1)

        items = mixed.all(0, 50, type_=1)  # the grid asks for its film type

        self.assertEqual([(i.TYPE, i.ratingKey) for i in items], [('movie', 'm1'), ('show', 's2')])
        params = session.calls[1]['params']
        self.assertEqual((params['parentId'], params['includeItemTypes']), ('view-mixed', 'Movie,Series'))
        self.assertEqual(params['recursive'], 'true')

    def test_filters_offer_both_kinds_choices(self):
        session = FakeSession(USER_VIEWS_MIXED, FakeResponse(200, {'Genres': [{'Name': 'Drama'}], 'Tags': []}))
        srv = make_server(session)
        plexlibrary.Library(None, server=srv).sections()

        genres = srv.query('/library/sections/view-mixed/genre')

        self.assertEqual([g.get('title') for g in genres], ['Drama'])
        self.assertEqual(session.calls[1]['params']['includeItemTypes'], 'Movie,Series')

    def test_home_has_its_latest_row(self):
        session = FakeSession(FakeResponse(200, {'Id': 'u1', 'Configuration': {}}), USER_VIEWS_MIXED)
        srv = make_server(session)

        self.assertEqual(jf_home.latestLibraries(srv.client), [('view-movies', 'Movies'), ('view-mixed', 'Test-Files')])

    def test_recommended_rows_include_next_up(self):
        session = FakeSession(items_page([]), items_page([]), FakeResponse(200, []))
        srv = make_server(session)

        jf_library.libraryRows(
            srv.client, 'view-mixed', count=10, section_type='movie', collection_type=jf_library.MIXED
        )

        asked = [(c['url'].rsplit('8096', 1)[1], c['params']['parentId']) for c in session.calls]
        self.assertEqual(
            asked,
            [('/UserItems/Resume', 'view-mixed'), ('/Shows/NextUp', 'view-mixed'), ('/Items/Latest', 'view-mixed')],
        )

    def test_search_in_it_finds_films_and_shows(self):
        session = FakeSession(items_page([movie_dto(1), series_dto(2)], total=2))
        srv = make_server(session)

        jf_search.searchHubs(
            srv.client,
            'film',
            section_ids=['view-mixed'],
            library_types={'view-mixed': 'movie'},
            collection_types={'view-mixed': jf_library.MIXED},
        )

        params = session.calls[0]['params']
        self.assertEqual(params['includeItemTypes'], 'Movie,Series,Episode')
        self.assertEqual(params['parentId'], 'view-mixed')
