"""
Library filters, per-library Recommended rows, collections (both the Jellyfin boxsets library
and the Collections tab / BoxSet contents), and Play All / Shuffle local queues on Jellyfin 10.11.5.
"""

import random
from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from jellynet import lists as jf_lists
from plexnet import playlist, playqueue, plexlibrary, video  # noqa: F401

from lib.windows import library, library_grid

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem
from .test_jellynet_library import (
    CountingJumpSession,
    items_page,
    make_server,
    movie_dto,
    series_dto,
)

USER_VIEWS_WITH_BOXSETS = FakeResponse(
    200,
    {
        'Items': [
            {
                'Id': 'view-movies',
                'Name': 'Movies',
                'CollectionType': 'movies',
                'ImageTags': {'Primary': 'tag-m'},
            },
            {
                'Id': 'view-shows',
                'Name': 'TV Shows',
                'CollectionType': 'tvshows',
                'ImageTags': {},
            },
            {
                'Id': 'view-boxsets',
                'Name': 'Collections',
                'CollectionType': 'boxsets',
                'ImageTags': {},
            },
        ],
        'TotalRecordCount': 3,
    },
)


def episode_dto(series_id, season_num, ep_num, played=False, position_ticks=0):
    return {
        'Id': f'{series_id}-{season_num}-{ep_num}',
        'Name': f'Episode {season_num}x{ep_num}',
        'Type': 'Episode',
        'SeriesId': series_id,
        'SeriesName': f'Show {series_id}',
        'SeasonId': f'{series_id}-sea-{season_num}',
        'SeasonName': f'Season {season_num}',
        'ParentIndexNumber': season_num,
        'IndexNumber': ep_num,
        'RunTimeTicks': 27_000_000_000,
        'UserData': {
            'Played': played,
            'PlayCount': 1 if played else 0,
            'PlaybackPositionTicks': position_ticks,
        },
    }


def boxset_dto(bid, name, count):
    return {
        'Id': bid,
        'Name': name,
        'Type': 'BoxSet',
        'ChildCount': count,
        'Overview': f'About {name}',
        'ImageTags': {'Primary': f'btag-{bid}'},
    }


def make_section(session, index=0):
    return plexlibrary.Library(None, server=make_server(session)).sections()[index]


def queue_keys(pq):
    return [str(item.ratingKey) for item in pq]


class JellyfinLibraryFiltersTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_filters_from_filters2(self):
        filters2 = FakeResponse(
            200,
            {
                'Genres': [
                    {'Name': 'Action', 'Id': 'g-1'},
                    {'Name': 'Sci-Fi', 'Id': 'g-2'},
                ],
                'Tags': ['4k', 'cyberpunk'],
            },
        )
        filters_legacy = FakeResponse(
            200,
            {
                'Genres': ['Action', 'Sci-Fi'],
                'Tags': ['4k', 'cyberpunk'],
                'OfficialRatings': ['PG', 'PG-13', 'R'],
                'Years': [2024, 2023, 1999],
            },
        )
        session = FakeSession(
            USER_VIEWS_WITH_BOXSETS,
            filters2,
            filters2,
            filters_legacy,
            filters_legacy,
            items_page([movie_dto(1)], total=1),
            items_page([movie_dto(2)], total=1),
            items_page([movie_dto(3)], total=1),
            items_page([movie_dto(4)], total=1),
            items_page([movie_dto(5)], total=1),
        )
        movies = make_section(session, index=0)

        # /filters exposes boolean (unwatched, favorite) and value (genre, year, contentRating, label) filters
        filters = movies.listFilters(libtype='movie')
        self.assertEqual(
            [(str(f.filter), str(f.filterType)) for f in filters],
            [
                ('unwatched', 'boolean'),
                ('favorite', 'boolean'),
                ('genre', 'string'),
                ('year', 'integer'),
                ('contentRating', 'string'),
                ('label', 'string'),
            ],
        )

        # Genres and tags come from GET /Items/Filters2?parentId=...&recursive=true
        genres = movies.listChoices('genre', libtype='movie')
        self.assertEqual([(str(g.key), str(g.title)) for g in genres], [('Action', 'Action'), ('Sci-Fi', 'Sci-Fi')])
        genre_call = session.calls[1]
        self.assertEqual(genre_call['url'], 'http://jellyfin.lan:8096/Items/Filters2')
        self.assertEqual(genre_call['params']['parentId'], 'view-movies')
        self.assertEqual(genre_call['params']['recursive'], 'true')
        self.assertEqual(genre_call['params']['includeItemTypes'], 'Movie')

        labels = movies.listChoices('label', libtype='movie')
        self.assertEqual([(str(t.key), str(t.title)) for t in labels], [('4k', '4k'), ('cyberpunk', 'cyberpunk')])
        self.assertEqual(session.calls[2]['url'], 'http://jellyfin.lan:8096/Items/Filters2')

        # OfficialRatings and Years come from GET /Items/Filters (QueryFiltersLegacy; Filters2 omits them in 10.11.5)
        ratings = movies.listChoices('contentRating', libtype='movie')
        self.assertEqual([str(r.key) for r in ratings], ['PG', 'PG-13', 'R'])
        self.assertEqual(session.calls[3]['url'], 'http://jellyfin.lan:8096/Items/Filters')
        self.assertEqual(session.calls[3]['params']['parentId'], 'view-movies')

        years = movies.listChoices('year', libtype='movie')
        self.assertEqual([str(y.key) for y in years], ['2024', '2023', '1999'])
        self.assertEqual(session.calls[4]['url'], 'http://jellyfin.lan:8096/Items/Filters')

        # Applying each filter sends the corresponding Jellyfin /Items parameter
        movies.all(0, 50, filter_=('genre', 'Action'))
        movies.all(0, 50, filter_=('year', '2024'))
        movies.all(0, 50, filter_=('contentRating', 'PG-13'))
        movies.all(0, 50, filter_=('label', 'cyberpunk'))
        movies.all(0, 50, bool_filters={'favorite': True})

        self.assertEqual(session.calls[5]['params']['genres'], 'Action')
        self.assertEqual(session.calls[6]['params']['years'], '2024')
        self.assertEqual(session.calls[7]['params']['officialRatings'], 'PG-13')
        self.assertEqual(session.calls[8]['params']['tags'], 'cyberpunk')
        self.assertEqual(session.calls[9]['params']['isFavorite'], 'true')

        # Filtered jump bar passes filter params and caches independently of unfiltered jump bar
        jump_session = CountingJumpSession(['Alien', 'Matrix'], USER_VIEWS_WITH_BOXSETS)
        jump_movies = make_section(jump_session, index=0)
        jump_movies.jumpList(sort=('titleSort', 'asc'))
        calls_unfiltered = len(jump_session.calls)
        jump_movies.jumpList(filter_=('genre', 'Action'), sort=('titleSort', 'asc'))
        self.assertGreater(len(jump_session.calls), calls_unfiltered)
        filtered_calls = jump_session.calls[calls_unfiltered:]
        self.assertTrue(all(c['params'].get('genres') == 'Action' for c in filtered_calls))

    def test_unwatched_filter(self):
        session = FakeSession(
            USER_VIEWS_WITH_BOXSETS,
            items_page([movie_dto(1)], total=12),
            items_page([series_dto(1)], total=5),
            items_page([episode_dto('s1', 1, 1)], total=8),
        )
        srv = make_server(session)
        sections = plexlibrary.Library(None, server=srv).sections()
        movies, shows = sections[0], sections[1]

        movie_res = movies.all(0, 50, bool_filters={'unwatched': True})
        show_res = shows.all(0, 50, bool_filters={'unwatched': True})
        ep_res = shows.all(0, 50, type_=4, bool_filters={'unwatched': True})

        self.assertEqual(movie_res.totalSize.asInt(), 12)
        self.assertEqual(show_res.totalSize.asInt(), 5)
        self.assertEqual(ep_res.totalSize.asInt(), 8)
        self.assertEqual(session.calls[1]['params']['isPlayed'], 'false')
        self.assertEqual(session.calls[2]['params']['isPlayed'], 'false')
        self.assertEqual(session.calls[3]['params']['isPlayed'], 'false')
        self.assertEqual(session.calls[3]['params']['includeItemTypes'], 'Episode')


class JellyfinLibraryRecommendedTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_recommended_rows_for_one_library(self):
        # Movie library: Resume + Latest (no NextUp); empty rows omitted
        movie_resume = items_page(
            [movie_dto(1, UserData={'PlaybackPositionTicks': 6_000_000_000, 'Played': False})], total=3
        )
        movie_latest = FakeResponse(200, [movie_dto(2), movie_dto(3)])
        movie_resume_more = items_page([movie_dto(1), movie_dto(4), movie_dto(5)], total=3)

        session = FakeSession(USER_VIEWS_WITH_BOXSETS, movie_resume, movie_latest, movie_resume_more)
        srv = make_server(session)
        plexlibrary.Library(None, server=srv).sections()

        hubs = srv.hubs('view-movies', count=2)
        self.assertEqual([(h.title, len(h.items)) for h in hubs], [('Continue Watching', 1), ('Recently Added', 2)])
        self.assertEqual([h.more.asBool() for h in hubs], [True, True])
        self.assertEqual(len({h.getCleanHubIdentifier() for h in hubs}), 2)

        resume_call = session.calls[1]
        self.assertEqual(resume_call['url'], 'http://jellyfin.lan:8096/UserItems/Resume')
        self.assertEqual(resume_call['params']['parentId'], 'view-movies')
        self.assertEqual(resume_call['params']['mediaTypes'], 'Video')
        self.assertEqual(resume_call['params']['limit'], 2)

        latest_call = session.calls[2]
        self.assertEqual(latest_call['url'], 'http://jellyfin.lan:8096/Items/Latest')
        self.assertEqual(latest_call['params']['parentId'], 'view-movies')
        self.assertEqual(latest_call['params']['groupItems'], 'true')
        self.assertEqual(latest_call['params']['limit'], 2)

        # See more on Continue Watching row
        see_more = srv.query(hubs[0].key, offset=0, limit=50)
        self.assertEqual(see_more.get('totalSize'), '3')
        self.assertEqual(len(list(see_more)), 3)

        # TV Show library: Resume (empty -> omitted), Next Up, Latest
        show_session = FakeSession(
            USER_VIEWS_WITH_BOXSETS,
            items_page([], total=0),
            items_page([episode_dto('s1', 1, 2)], total=1),
            FakeResponse(200, [series_dto(1)]),
        )
        show_srv = make_server(show_session)
        plexlibrary.Library(None, server=show_srv).sections()

        show_hubs = show_srv.hubs('view-shows', count=20)
        self.assertEqual([(h.title, len(h.items)) for h in show_hubs], [('Next Up', 1), ('Recently Added', 1)])
        self.assertEqual(show_session.calls[2]['url'], 'http://jellyfin.lan:8096/Shows/NextUp')
        self.assertEqual(show_session.calls[2]['params']['parentId'], 'view-shows')
        self.assertEqual(show_hubs[0].items[0].type, 'episode')
        self.assertEqual(show_hubs[1].items[0].type, 'show')

        # Direct /hubs/sections/{id} query returns <MediaContainer> of <Hub> elements
        direct_session = FakeSession(
            USER_VIEWS_WITH_BOXSETS,
            items_page([], total=0),
            FakeResponse(200, [movie_dto(9)]),
        )
        direct_srv = make_server(direct_session)
        plexlibrary.Library(None, server=direct_srv).sections()
        xml = direct_srv.query('/hubs/sections/view-movies?count=10')
        self.assertEqual(xml.tag, 'MediaContainer')
        self.assertEqual(len(list(xml)), 1)
        self.assertEqual(xml[0].tag, 'Hub')
        self.assertEqual(xml[0].get('title'), 'Recently Added')

        # Error from Jellyfin omits failed rows cleanly
        err_srv = make_server(FakeSession(USER_VIEWS_WITH_BOXSETS, problem(503, 'Down'), problem(503, 'Down')))
        plexlibrary.Library(None, server=err_srv).sections()
        self.assertEqual(err_srv.hubs('view-movies', count=20), [])


class JellyfinCollectionsTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_collections_grid_and_contents(self):
        boxsets = items_page([boxset_dto('c1', 'Alien', 4), boxset_dto('c2', 'Bond', 25)], total=2)
        alien_items = items_page([movie_dto(0), movie_dto(1), movie_dto(2), movie_dto(3)], total=4)

        # 1. Dedicated Collections (boxsets) library section
        session = FakeSession(USER_VIEWS_WITH_BOXSETS, boxsets, alien_items)
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

        colls = sections[2].all(0, 50)
        self.assertEqual(
            [(c.type, c.title, c.childCount.asInt()) for c in colls],
            [('collection', 'Alien', 4), ('collection', 'Bond', 25)],
        )
        self.assertEqual(colls[0].memberType(), 'movie')
        self.assertEqual(session.calls[1]['params']['parentId'], 'view-boxsets')
        self.assertEqual(session.calls[1]['params']['includeItemTypes'], 'BoxSet')
        self.assertEqual(session.calls[1]['params']['recursive'], 'true')

        # Collection.all() fetches members via /library/metadata/{id}/children -> GET /Items?parentId=c1
        titles = colls[0].all(0, 50)
        self.assertEqual([t.title for t in titles], ['Film 0', 'Film 1', 'Film 2', 'Film 3'])
        self.assertEqual(session.calls[2]['url'], 'http://jellyfin.lan:8096/Items')
        self.assertEqual(session.calls[2]['params']['parentId'], 'c1')

        # 2. Collections tab inside Movies library (type=18) AND /library/collections/{id}/children route
        tab_session = FakeSession(USER_VIEWS_WITH_BOXSETS, boxsets, alien_items, items_page([movie_dto(7)], total=1))
        tab_srv = make_server(tab_session)
        movies = plexlibrary.Library(None, server=tab_srv).sections()[0]

        tab_colls = movies.all(0, 50, type_=18)
        self.assertEqual([(c.type, c.title) for c in tab_colls], [('collection', 'Alien'), ('collection', 'Bond')])
        self.assertEqual(tab_session.calls[1]['params']['includeItemTypes'], 'BoxSet')
        self.assertEqual(tab_session.calls[1]['params']['recursive'], 'true')
        self.assertNotIn('parentId', tab_session.calls[1]['params'])

        # Direct /library/collections/{id}/children route also returns collection contents
        direct = tab_srv.query('/library/collections/c1/children', offset=0, limit=50)
        self.assertEqual(len(list(direct)), 4)
        self.assertEqual(tab_session.calls[2]['params']['parentId'], 'c1')

        # Direct /library/sections/{id}/collections route also lists BoxSets
        coll_route_session = FakeSession(USER_VIEWS_WITH_BOXSETS, boxsets)
        coll_route_srv = make_server(coll_route_session)
        plexlibrary.Library(None, server=coll_route_srv).sections()
        coll_xml = coll_route_srv.query('/library/sections/view-movies/collections', offset=0, limit=50)
        self.assertEqual(len(list(coll_xml)), 2)

        # lists.favorites helper queries isFavorite=true
        fav_xml = jf_lists.favorites(tab_srv.client)
        self.assertEqual(len(list(fav_xml)), 1)
        self.assertEqual(tab_session.calls[3]['params']['isFavorite'], 'true')

    def test_jellyfin_12_lists_a_librarys_own_collections(self):
        # Jellyfin 12 answers a library's own BoxSets by parentId; both ways Uno asks for the Collections tab use it.
        boxsets = items_page([boxset_dto('c1', 'Alien', 4)], total=1)
        session = FakeSession(USER_VIEWS_WITH_BOXSETS, boxsets, boxsets)
        srv = make_server(session, version='12.4.0')
        movies = plexlibrary.Library(None, server=srv).sections()[0]

        tab_colls = movies.all(0, 50, type_=18)
        self.assertEqual([(c.type, c.title) for c in tab_colls], [('collection', 'Alien')])
        coll_xml = srv.query('/library/sections/view-movies/collections', offset=0, limit=50)
        self.assertEqual(len(list(coll_xml)), 1)

        for call in session.calls[1:]:
            self.assertEqual(call['params']['parentId'], 'view-movies')
            self.assertEqual(call['params']['includeItemTypes'], 'BoxSet')
            self.assertEqual(call['params']['recursive'], 'true')
        self.assertEqual(len(session.calls), 3)  # the stored version is used: no /System/Info/Public probe


class JellyfinPlayAllAndShuffleTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_play_all_builds_a_local_queue(self):
        # Movie library Play All keeps grid sort order
        movie_session = FakeSession(
            USER_VIEWS_WITH_BOXSETS,
            items_page([movie_dto(0), movie_dto(1), movie_dto(2)], total=3),
        )
        movies = make_section(movie_session, index=0)

        pq = playqueue.createPlayQueueForItem(
            movies, options={'shuffle': False}, args={'sourceType': '1', 'sort': 'titleSort:desc'}
        )

        self.assertIsInstance(pq, playlist.LocalPlaylist)
        self.assertEqual(queue_keys(pq), ['m0', 'm1', 'm2'])
        self.assertEqual(movie_session.calls[1]['params']['sortBy'], 'SortName')
        self.assertEqual(movie_session.calls[1]['params']['sortOrder'], 'Descending')

        # TV Show library Play All expands each show's episodes in (season, episode) order via includeItemTypes=Episode
        episodes = [
            episode_dto('s0', 1, 2),
            episode_dto('s1', 2, 1),
            episode_dto('s9', 1, 1),  # filtered-out show must be excluded
            episode_dto('s0', 1, 1),
            episode_dto('s1', 1, 1),
        ]
        show_session = FakeSession(
            USER_VIEWS_WITH_BOXSETS,
            items_page([series_dto(1), series_dto(0)], total=2),
            items_page(episodes, total=5),
        )
        shows = make_section(show_session, index=1)

        show_pq = playqueue.createPlayQueueForItem(shows, options={'shuffle': False}, args={'sourceType': '2'})

        self.assertIsInstance(show_pq, playlist.LocalPlaylist)
        self.assertEqual(queue_keys(show_pq), ['s1-1-1', 's1-2-1', 's0-1-1', 's0-1-2'])
        ep_params = show_session.calls[2]['params']
        self.assertEqual(ep_params['parentId'], 'view-shows')
        self.assertEqual(ep_params['includeItemTypes'], 'Episode')
        self.assertEqual(ep_params['recursive'], 'true')

        # Collection Play All queues the collection's titles
        coll_session = FakeSession(
            USER_VIEWS_WITH_BOXSETS,
            items_page([boxset_dto('c1', 'Alien', 2)], total=1),
            items_page([movie_dto(0), movie_dto(1)], total=2),
        )
        alien = make_section(coll_session, index=2).all(0, 50)[0]
        coll_pq = playqueue.createPlayQueueForItem(alien, options={'shuffle': False})
        self.assertEqual(queue_keys(coll_pq), ['m0', 'm1'])

    def test_shuffle_is_random_order(self):
        session = FakeSession(
            USER_VIEWS_WITH_BOXSETS,
            items_page([movie_dto(n) for n in range(10)], total=10),
        )
        movies = make_section(session, index=0)

        rng_state = random.getstate()
        try:
            random.seed(42)
            pq = playqueue.createPlayQueueForItem(
                movies,
                options={'shuffle': True},
                args={'sourceType': '1', 'sort': 'titleSort:asc'},
            )
        finally:
            random.setstate(rng_state)

        self.assertIsInstance(pq, playlist.LocalPlaylist)
        expected = [f'm{n}' for n in range(10)]
        self.assertEqual(sorted(queue_keys(pq)), expected)
        self.assertNotEqual(queue_keys(pq), expected)
        self.assertEqual(session.calls[1]['params']['sortBy'], 'Random')

    def test_nothing_left_to_play_says_so(self):
        watched = movie_dto(0, UserData={'Played': True, 'PlayCount': 1, 'PlaybackPositionTicks': 0})

        # Local queue itself is None when unwatched filter leaves nothing
        pq_session = FakeSession(USER_VIEWS_WITH_BOXSETS, items_page([watched], total=1))
        pq = playqueue.createPlayQueueForItem(
            make_section(pq_session, index=0),
            options={'shuffle': False},
            args={'sourceType': '1', 'unwatched': '1'},
        )
        self.assertIsNone(pq)

        # LibraryWindow.playButtonClicked() shows messageDialog, doesn't open player, resets playBtnClicked
        win = library.LibraryWindow.__new__(library.LibraryWindow)
        win.section = make_section(FakeSession(USER_VIEWS_WITH_BOXSETS, items_page([watched], total=1)), index=0)
        win.boolFilters, win.playBtnClicked = {'unwatched': True}, False
        win.getFilterOpts = win.getSortOpts = lambda: None
        win._watchGridPlayback = mock.Mock()

        dialog = mock.patch.object(library_grid.util, 'messageDialog').start()
        open_ = mock.patch.object(library_grid.opener, 'open').start()
        self.addCleanup(mock.patch.stopall)

        win.playButtonClicked()

        dialog.assert_called_once()
        open_.assert_not_called()
        self.assertFalse(win.playBtnClicked)
