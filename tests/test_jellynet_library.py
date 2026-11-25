"""
Library browsing against Jellyfin 10.11.5: BaseItemDto -> Plex XML mapping (jellynet.items),
paginated library grids, Uno sort mapping, and A-Z jump bar counts via nameLessThan (jellynet.library).
Responses are written inline in the shape of Jellyfin 10.11.5 OpenAPI (BaseItemDto, BaseItemDtoQueryResult).
"""

from jellynet import items as jf_items
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.server import JellyfinServer
from plexnet import plexlibrary, video  # noqa: F401 (video registers plexnet item types)

from lib.jellyfin import StoredServer

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')
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
            {
                'Id': 'view-shows',
                'Name': 'TV Shows',
                'CollectionType': 'tvshows',
                'ImageTags': {},
            },
        ],
        'TotalRecordCount': 2,
    },
)


def movie_dto(n, **extra):
    dto = {
        'Id': f'm{n}',
        'Name': f'Film {n}',
        'OriginalTitle': f'Original Film {n}',
        'SortName': f'film {n:04d}',
        'Type': 'Movie',
        'ProductionYear': 2000 + (n % 25),
        'PremiereDate': f'{2000 + (n % 25)}-06-15T00:00:00.0000000Z',
        'DateCreated': '2025-01-10T12:00:00.0000000Z',
        'Overview': f'Plot of Film {n}.',
        'OfficialRating': 'PG-13',
        'CommunityRating': 7.8,
        'RunTimeTicks': 72_000_000_000,  # 7200s = 7,200,000 ms
        'Genres': ['Action', 'Sci-Fi'],
        'ImageTags': {'Primary': f'ptag-{n}'},
        'BackdropImageTags': [f'btag-{n}'],
        'UserData': {
            'PlaybackPositionTicks': 0,
            'PlayCount': 0,
            'Played': False,
            'IsFavorite': False,
        },
    }
    dto.update(extra)
    return dto


def series_dto(n, **extra):
    dto = {
        'Id': f's{n}',
        'Name': f'Show {n}',
        'SortName': f'show {n:04d}',
        'Type': 'Series',
        'ProductionYear': 2020,
        'PremiereDate': '2020-02-01T00:00:00.0000000Z',
        'Overview': f'About Show {n}.',
        'OfficialRating': 'TV-MA',
        'CommunityRating': 8.4,
        'ChildCount': 2,
        'RecursiveItemCount': 10,
        'Genres': ['Drama'],
        'ImageTags': {'Primary': f'stag-{n}'},
        'BackdropImageTags': [f'sbtag-{n}'],
        'UserData': {
            'UnplayedItemCount': 4,
            'Played': False,
        },
    }
    dto.update(extra)
    return dto


def items_page(items, total=None, start=0):
    return FakeResponse(
        200,
        {
            'Items': items,
            'TotalRecordCount': len(items) if total is None else total,
            'StartIndex': start,
        },
    )


def make_server(session, version='10.11.5'):
    client = JellyfinClient('http://jellyfin.lan:8096', IDENTITY, token='TOK-1', username='alice', session=session)
    return JellyfinServer(StoredServer('http://jellyfin.lan:8096', 'srv-1', 'Home Jellyfin', version), client)


def make_section(session, index=0):
    return plexlibrary.Library(None, server=make_server(session)).sections()[index]


class CountingJumpSession(FakeSession):
    """FakeSession that answers GET /Items?limit=0&nameLessThan=... dynamically from a sorted title list."""

    def __init__(self, sort_names, *initial_responses):
        super().__init__(*initial_responses)
        self.sort_names = list(sort_names)

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        if not self.responses and url.endswith('/Items') and params and str(params.get('limit')) == '0':
            self.calls.append(
                {
                    'method': method,
                    'url': url,
                    'params': params,
                    'json': json,
                    'headers': dict(headers or {}),
                    'timeout': timeout,
                }
            )
            less_than = params.get('nameLessThan')
            if less_than is None:
                count = len(self.sort_names)
            else:
                count = sum(1 for name in self.sort_names if name.upper() < str(less_than).upper())
            return FakeResponse(200, {'Items': [], 'TotalRecordCount': count, 'StartIndex': 0})
        return super().request(method, url, params=params, json=json, headers=headers, timeout=timeout)


class JellyfinItemsMappingTest(KodiTestCase):
    def test_item_fields_map_to_plex_attributes(self):
        # Tick conversion helpers
        self.assertEqual(jf_items.TICKS_PER_MS, 10_000)
        self.assertEqual(jf_items.TICKS_PER_SECOND, 10_000_000)
        self.assertEqual(jf_items.ticks_to_ms(72_000_000_000), 7_200_000)
        self.assertEqual(jf_items.ticks_to_ms(0), 0)
        self.assertEqual(jf_items.ticks_to_ms(None), 0)
        self.assertEqual(jf_items.ms_to_ticks(7_200_000), 72_000_000_000)
        self.assertEqual(jf_items.ms_to_ticks(0), 0)

        # Movie -> <Video type="movie">
        movie_el = jf_items.itemElement(
            movie_dto(
                1,
                UserData={
                    'PlaybackPositionTicks': 12_345_000_000,
                    'PlayCount': 2,
                    'Played': True,
                    'LastPlayedDate': '2025-02-01T08:30:00.0000000Z',
                },
            )
        )
        self.assertEqual(movie_el.tag, 'Video')
        self.assertEqual(movie_el.get('type'), 'movie')
        self.assertEqual(movie_el.get('ratingKey'), 'm1')
        self.assertEqual(movie_el.get('key'), '/library/metadata/m1')
        self.assertEqual(movie_el.get('title'), 'Film 1')
        self.assertEqual(movie_el.get('originalTitle'), 'Original Film 1')
        self.assertEqual(movie_el.get('sortTitle'), 'film 0001')
        self.assertEqual(movie_el.get('titleSort'), 'film 0001')
        self.assertEqual(movie_el.get('year'), '2001')
        self.assertEqual(movie_el.get('originallyAvailableAt'), '2001-06-15')
        self.assertEqual(movie_el.get('summary'), 'Plot of Film 1.')
        self.assertEqual(movie_el.get('contentRating'), 'PG-13')
        # Plex's rating is the critic score, its audienceRating the audience one: Jellyfin's CommunityRating
        self.assertEqual(movie_el.get('audienceRating'), '7.8')
        self.assertEqual(movie_el.get('audienceRatingImage'), 'themoviedb://image.rating')
        self.assertIsNone(movie_el.get('rating'))  # no CriticRating
        critic_el = jf_items.itemElement(dict(movie_dto(1), CriticRating=75))
        self.assertEqual(
            (critic_el.get('rating'), critic_el.get('ratingImage')), ('7.5', 'rottentomatoes://image.rating.ripe')
        )
        rotten_el = jf_items.itemElement(dict(movie_dto(1), CriticRating=40))
        self.assertEqual(rotten_el.get('ratingImage'), 'rottentomatoes://image.rating.rotten')
        self.assertEqual(movie_el.get('duration'), '7200000')
        self.assertEqual(movie_el.get('viewOffset'), '1234500')
        self.assertEqual(movie_el.get('viewCount'), '2')
        self.assertEqual(movie_el.get('thumb'), '/Items/m1/Images/Primary?tag=ptag-1')
        self.assertEqual(movie_el.get('art'), '/Items/m1/Images/Backdrop?tag=btag-1')
        self.assertNotIn('TOK-1', movie_el.get('thumb') or '')
        self.assertNotIn('Token', movie_el.get('thumb') or '')
        self.assertEqual([g.get('tag') for g in movie_el.findall('Genre')], ['Action', 'Sci-Fi'])

        # Played=True with PlayCount=0 still sets viewCount='1'
        played_once = jf_items.itemElement(movie_dto(2, UserData={'Played': True, 'PlayCount': 0}))
        self.assertEqual(played_once.get('viewCount'), '1')
        unplayed = jf_items.itemElement(movie_dto(3, UserData={'Played': False, 'PlayCount': 0}))
        self.assertIsNone(unplayed.get('viewCount'))

        # Series -> <Directory type="show">
        show_el = jf_items.itemElement(series_dto(1))
        self.assertEqual(show_el.tag, 'Directory')
        self.assertEqual(show_el.get('type'), 'show')
        self.assertEqual(show_el.get('ratingKey'), 's1')
        self.assertEqual(show_el.get('key'), '/library/metadata/s1/children')
        self.assertEqual(show_el.get('leafCount'), '10')
        self.assertEqual(show_el.get('viewedLeafCount'), '6')
        self.assertEqual(show_el.get('childCount'), '2')
        self.assertEqual([g.get('tag') for g in show_el.findall('Genre')], ['Drama'])

        # Season -> <Directory type="season">
        season_el = jf_items.itemElement(
            {
                'Id': 'sea-1',
                'Name': 'Season 1',
                'Type': 'Season',
                'IndexNumber': 1,
                'SeriesId': 's1',
                'SeriesName': 'Show 1',
                'SeriesPrimaryImageTag': 'stag-1',
                'ChildCount': 8,
                'ImageTags': {'Primary': 'seatag-1'},
                'ParentBackdropItemId': 's1',
                'ParentBackdropImageTags': ['sbtag-1'],
                'UserData': {'UnplayedItemCount': 3, 'Played': False},
            }
        )
        self.assertEqual(season_el.tag, 'Directory')
        self.assertEqual(season_el.get('type'), 'season')
        self.assertEqual(season_el.get('key'), '/library/metadata/sea-1/children')
        self.assertEqual(season_el.get('index'), '1')
        self.assertEqual(season_el.get('parentRatingKey'), 's1')
        self.assertEqual(season_el.get('parentTitle'), 'Show 1')
        self.assertEqual(season_el.get('leafCount'), '8')
        self.assertEqual(season_el.get('viewedLeafCount'), '5')
        self.assertEqual(season_el.get('art'), '/Items/s1/Images/Backdrop?tag=sbtag-1')

        # Episode -> <Video type="episode"> with parent backdrop fallback
        ep_el = jf_items.itemElement(
            {
                'Id': 'ep-1',
                'Name': 'Pilot',
                'Type': 'Episode',
                'IndexNumber': 1,
                'ParentIndexNumber': 1,
                'SeriesId': 's1',
                'SeriesName': 'Show 1',
                'SeriesPrimaryImageTag': 'stag-1',
                'SeasonId': 'sea-1',
                'SeasonName': 'Season 1',
                'RunTimeTicks': 27_000_000_000,
                'ImageTags': {'Primary': 'eptag-1'},
                'ParentBackdropItemId': 's1',
                'ParentBackdropImageTags': ['sbtag-1'],
                'UserData': {'PlaybackPositionTicks': 600_000_000, 'Played': False},
            }
        )
        self.assertEqual(ep_el.tag, 'Video')
        self.assertEqual(ep_el.get('type'), 'episode')
        self.assertEqual(ep_el.get('key'), '/library/metadata/ep-1')
        self.assertEqual(ep_el.get('parentIndex'), '1')
        self.assertEqual(ep_el.get('index'), '1')
        self.assertEqual(ep_el.get('grandparentRatingKey'), 's1')
        self.assertEqual(ep_el.get('grandparentTitle'), 'Show 1')
        self.assertEqual(ep_el.get('parentRatingKey'), 'sea-1')
        self.assertEqual(ep_el.get('parentTitle'), 'Season 1')
        self.assertEqual(ep_el.get('duration'), '2700000')
        self.assertEqual(ep_el.get('viewOffset'), '60000')
        self.assertEqual(ep_el.get('art'), '/Items/s1/Images/Backdrop?tag=sbtag-1')

        # BoxSet -> <Directory type="collection">
        box_el = jf_items.itemElement(
            {
                'Id': 'box-1',
                'Name': 'Alien Collection',
                'Type': 'BoxSet',
                'ChildCount': 4,
                'Overview': 'Xenomorphs.',
                'ImageTags': {'Primary': 'boxtag-1'},
            }
        )
        self.assertEqual(box_el.tag, 'Directory')
        self.assertEqual(box_el.get('type'), 'collection')
        self.assertEqual(box_el.get('ratingKey'), 'box-1')
        self.assertEqual(box_el.get('key'), '/library/metadata/box-1/children')
        self.assertEqual(box_el.get('childCount'), '4')
        self.assertEqual(box_el.get('thumb'), '/Items/box-1/Images/Primary?tag=boxtag-1')

        # Unsupported types return None
        self.assertIsNone(jf_items.itemElement({'Id': 'x1', 'Name': 'Song', 'Type': 'Audio'}))


class JellyfinLibraryGridTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_grid_pages_with_total(self):
        # Count-only request (size=0) asks Jellyfin for limit=0&enableTotalRecordCount=true
        session = FakeSession(
            USER_VIEWS,
            items_page([], total=216),
            items_page([movie_dto(n) for n in range(100, 150)], total=216, start=100),
        )
        movies = make_section(session, index=0)

        count_only = movies.all(0, 0)
        self.assertEqual(count_only.totalSize.asInt(), 216)
        self.assertEqual(len(count_only), 0)
        count_call = session.calls[1]
        self.assertEqual(count_call['url'], 'http://jellyfin.lan:8096/Items')
        self.assertEqual(count_call['params']['parentId'], 'view-movies')
        self.assertEqual(count_call['params']['includeItemTypes'], 'Movie')
        self.assertEqual(count_call['params']['recursive'], 'true')
        self.assertEqual(count_call['params']['limit'], 0)
        self.assertEqual(count_call['params']['enableTotalRecordCount'], 'true')

        # Paged chunk request
        page = movies.all(100, 50)
        self.assertEqual(page.totalSize.asInt(), 216)
        self.assertEqual(page.offset.asInt(), 100)
        self.assertEqual([i.title for i in page], [f'Film {n}' for n in range(100, 150)])
        self.assertEqual(page[0].type, 'movie')
        self.assertEqual(page[0].get('librarySectionID'), 'view-movies')
        page_call = session.calls[2]
        self.assertEqual(page_call['params']['startIndex'], 100)
        self.assertEqual(page_call['params']['limit'], 50)
        thumb_url = page[0].defaultThumb.asTranscodedImageURL(300, 450)
        self.assertIn('/Items/m100/Images/Primary', thumb_url)
        self.assertNotIn('TOK-1', thumb_url)

        # Series library asks for includeItemTypes=Series
        show_session = FakeSession(USER_VIEWS, items_page([series_dto(1), series_dto(2)], total=2))
        shows = make_section(show_session, index=1).all(0, 50)
        self.assertEqual([(s.type, s.title) for s in shows], [('show', 'Show 1'), ('show', 'Show 2')])
        self.assertEqual(show_session.calls[1]['params']['includeItemTypes'], 'Series')

        # Failed listing returns None from server.query()
        err_srv = make_server(FakeSession(problem(503, 'Unavailable')))
        self.assertIsNone(
            err_srv.query('/library/sections/view-movies/all?X-Plex-Container-Start=0&X-Plex-Container-Size=0')
        )

    def test_each_uno_sort_maps(self):
        expected = [
            (('titleSort', 'asc'), ('SortName', 'Ascending')),
            (('titleSort', 'desc'), ('SortName', 'Descending')),
            (('addedAt', 'desc'), ('DateCreated', 'Descending')),
            (('originallyAvailableAt', 'desc'), ('PremiereDate', 'Descending')),
            (('rating', 'desc'), ('CriticRating', 'Descending')),
            (('audienceRating', 'desc'), ('CommunityRating', 'Descending')),
            (('contentRating', 'asc'), ('OfficialRating', 'Ascending')),
            (('duration', 'desc'), ('Runtime', 'Descending')),
            (('lastViewedAt', 'desc'), ('DatePlayed', 'Descending')),
            (('random', 'desc'), ('Random', 'Descending')),
        ]
        session = FakeSession(USER_VIEWS, *[items_page([movie_dto(1)], total=1) for _ in expected])
        movies = make_section(session, index=0)

        for idx, (plex_sort, (jf_field, jf_order)) in enumerate(expected):
            with self.subTest(sort=plex_sort):
                movies.all(0, 50, sort=plex_sort)
                params = session.calls[1 + idx]['params']
                self.assertEqual(params['sortBy'], jf_field)
                self.assertEqual(params['sortOrder'], jf_order)

        # /library/sections/{id}/sorts exposes Uno sorts
        sorts = make_section(FakeSession(USER_VIEWS)).listSorts(libtype='movie')
        sort_keys = [str(s.key) for s in sorts]
        for key in (
            'titleSort',
            'addedAt',
            'originallyAvailableAt',
            'rating',
            'contentRating',
            'duration',
            'lastViewedAt',
            'random',
        ):
            self.assertIn(key, sort_keys)

        # Unknown sort leaves default order without sending invalid sortBy
        unknown_session = FakeSession(USER_VIEWS, items_page([movie_dto(1)], total=1))
        make_section(unknown_session).all(0, 50, sort=('unknownSort', 'desc'))
        self.assertNotIn('sortBy', unknown_session.calls[1]['params'])

    def test_jump_bar_counts(self):
        titles = ['2001', '300', 'Alien', 'Arrival', 'Blade Runner', 'Matrix', 'Zodiac']
        session = CountingJumpSession(titles, USER_VIEWS)
        movies = make_section(session, index=0)

        bar = movies.jumpList(sort=('titleSort', 'asc'))

        self.assertEqual(
            [(str(j.title), j.size.asInt()) for j in bar],
            [('#', 2), ('A', 2), ('B', 1), ('M', 1), ('Z', 1)],
        )
        self.assertEqual(sum(j.size.asInt() for j in bar), len(titles))
        # Every Jellyfin call used limit=0 & enableTotalRecordCount=true
        item_calls = [c for c in session.calls if c['url'].endswith('/Items')]
        self.assertTrue(all(c['params']['limit'] == 0 for c in item_calls))
        self.assertTrue(all(c['params']['enableTotalRecordCount'] == 'true' for c in item_calls))
        self.assertTrue(any('nameLessThan' in c['params'] for c in item_calls))

        # Cached for the same (section_id, sort, filters): no additional HTTP calls
        calls_before = len(session.calls)
        bar_again = movies.jumpList(sort=('titleSort', 'asc'))
        self.assertEqual(
            [(str(j.title), j.size.asInt()) for j in bar_again], [('#', 2), ('A', 2), ('B', 1), ('M', 1), ('Z', 1)]
        )
        self.assertEqual(len(session.calls), calls_before)

        # Descending sort reverses bucket order and still sums to total
        desc_bar = movies.jumpList(sort=('titleSort', 'desc'))
        self.assertEqual(
            [(str(j.title), j.size.asInt()) for j in desc_bar],
            [('Z', 1), ('M', 1), ('B', 1), ('A', 2), ('#', 2)],
        )
        self.assertEqual(sum(j.size.asInt() for j in desc_bar), len(titles))

        # Empty library returns empty bar after a single total=0 count request
        empty_session = CountingJumpSession([], USER_VIEWS)
        empty_bar = make_section(empty_session).jumpList(sort=('titleSort', 'asc'))
        self.assertEqual(list(empty_bar), [])
        self.assertEqual(len([c for c in empty_session.calls if c['url'].endswith('/Items')]), 1)
