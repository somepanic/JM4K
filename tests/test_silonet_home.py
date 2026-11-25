"""
Home's rows from Silo's home layout: SiloServer.hubs() answers Uno's Home (home.HomeHubsTask) with plexlibrary.Hub
rows built from GET /api/v2/home/layout and /api/v2/home/sections/{id}/items. Responses are written inline in the
shape of /api/v2/openapi.json (Silo build-2987); nothing is copied from Silo's sources.
"""

from silonet.client import Credentials, DeviceIdentity, SiloClient
from silonet.server import SiloServer

from lib.silo import StoredServer

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession, problem

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')
CAPS = FakeResponse(
    200, {'state': 'available', 'include_items_max': 2, 'row_item_limit_max': 100, 'section_item_limit_max': 100}
)


def movie(cid, title, played=False, position=None):
    return {
        'content_id': cid,
        'type': 'movie',
        'title': title,
        'year': 2024,
        'poster_url': '/api/v2/artwork/tmdb/movies/1/poster/w500.abc',
        'backdrop_url': '/api/v2/artwork/tmdb/movies/1/backdrop/w1920.abc',
        'duration_seconds': 7200,
        'position_seconds': position,
        'release_date': '2024-05-01',
        'user_state': {'played': played, 'is_favorite': False, 'in_watchlist': False, 'not_interested': False},
    }


def series(cid, title, watched, unplayed):
    return {
        'content_id': cid,
        'type': 'series',
        'title': title,
        'year': 2019,
        'poster_url': '/api/v2/artwork/tvdb/series/2/poster/w500.def',
        'user_state': {'played': False, 'watched_count': watched, 'unplayed_count': unplayed},
    }


def episode(cid, show, season, number):
    return {
        'content_id': cid,
        'type': 'episode',
        'title': 'Pilot',
        'series_id': 'sh1',
        'series_title': show,
        'season_number': season,
        'episode_number': number,
        'poster_url': '/api/v2/artwork/still/3.ghi',
        'series_poster_url': '/api/v2/artwork/tvdb/series/2/poster/w500.def',
        'duration_seconds': 3000,
        'position_seconds': 900,
        'user_state': {'played': False},
    }


def section(sid, kind, title, items=None, total=None, more=False):
    row = {
        'id': sid,
        'section_type': kind,
        'title': title,
        'title_key': 'home.section.x',
        'featured': False,
        'item_limit': 100,
    }
    if items is not None:
        row.update(items=items, collections=[], total_count=len(items) if total is None else total, has_more=more)
    return row


SECTIONS = [
    section('s1', 'continue_watching', 'Continue Watching'),
    section('s2', 'recently_added', 'Recently Added Movies'),
    section('s3', 'trending_discover', 'Trending This Week'),
]


def layout(*included):
    return FakeResponse(200, {'sections': SECTIONS, 'included_sections': list(included)})


def rowItems(sid, kind, title, items, more=False):
    return FakeResponse(200, section(sid, kind, title, items, more=more))


def server(session):
    client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(accessToken='A1', profileId='p1'), session=session)
    return SiloServer(StoredServer('http://silo.lan:8090', 'srv-1', 'Silo'), client)


def homeRows(session):
    session.queue(FakeResponse(200, {'items': []}))  # no favorites (silonet.lists.favoritesRow())
    return server(session).hubs(None, count=20, section_ids=['lib-m'], continue_watching_count=40)


class SiloHomeRowsTest(KodiTestCase):
    def standard(self):
        return FakeSession(
            CAPS,
            layout(
                section('s1', 'continue_watching', 'Continue Watching', [episode('e1', 'Severance', 1, 1)]),
                section(
                    's2',
                    'recently_added',
                    'Recently Added Movies',
                    [movie(f'm{i}', 'Film') for i in range(20)],
                    total=57,
                    more=True,
                ),
            ),
            rowItems('s3', 'trending_discover', 'Trending This Week', [series('t1', 'Andor', 3, 9)]),
        )

    def test_rows_in_silo_order_with_silo_titles(self):
        hubs = homeRows(self.standard())

        self.assertEqual([h.title for h in hubs], ['Continue Watching', 'Recently Added Movies', 'Trending This Week'])
        self.assertEqual(hubs[0].hubIdentifier, 'continueWatching')
        self.assertEqual(len({h.hubIdentifier for h in hubs}), 3)

    def test_first_rows_come_with_the_layout_the_rest_one_by_one(self):
        session = self.standard()

        homeRows(session)

        urls = [c['url'].replace('http://silo.lan:8090/api/v2', '') for c in session.calls]
        self.assertEqual(urls, ['/home/capabilities', '/home/layout', '/home/sections/s3/items', '/favorites'])
        self.assertEqual(session.calls[1]['params'], {'include_items': 2, 'item_limit': 20})
        self.assertEqual(session.calls[2]['params'], {'item_limit': 20})
        self.assertEqual(session.calls[1]['headers']['X-Profile-Id'], 'p1')

    def test_continue_watching_asks_for_its_own_count(self):
        session = FakeSession(
            CAPS,
            layout(
                section(
                    's1',
                    'continue_watching',
                    'Continue Watching',
                    [movie(f'm{i}', 'M') for i in range(20)],
                    total=33,
                    more=True,
                ),
                section('s2', 'recently_added', 'Recently Added Movies', [movie('m1', 'Dune')]),
            ),
            rowItems('s1', 'continue_watching', 'Continue Watching', [movie(f'm{i}', 'M') for i in range(33)]),
            rowItems('s3', 'trending_discover', 'Trending This Week', []),
        )

        hubs = homeRows(session)

        self.assertEqual(session.calls[2]['url'], 'http://silo.lan:8090/api/v2/home/sections/s1/items')
        self.assertEqual(session.calls[2]['params'], {'item_limit': 40})
        self.assertEqual(len(hubs[0].items), 33)

    def test_without_the_capabilities_every_row_is_asked_for_plainly(self):
        session = FakeSession(
            FakeResponse(200, {'state': 'available'}),
            layout(),
            rowItems('s1', 'continue_watching', 'Continue Watching', []),
            rowItems('s2', 'recently_added', 'Recently Added Movies', [movie('m1', 'Dune')]),
            rowItems('s3', 'trending_discover', 'Trending This Week', []),
        )

        hubs = homeRows(session)

        self.assertEqual([c['params'] for c in session.calls[1:5]], [None, None, None, None])
        self.assertEqual(session.calls[5]['params'], {'limit': 20})  # the favorites row
        self.assertEqual([len(h.items) for h in hubs], [0, 1, 0])

    def test_a_movie_tile(self):
        session = FakeSession(
            CAPS,
            layout(
                section('s1', 'continue_watching', 'Continue Watching', [movie('m1', 'Dune', position=600)]),
                section('s2', 'recently_added', 'Recently Added Movies', [movie('m2', 'Up', played=True)]),
            ),
            rowItems('s3', 'trending_discover', 'Trending This Week', []),
        )

        hubs = homeRows(session)
        dune, up = hubs[0].items[0], hubs[1].items[0]

        self.assertEqual((dune.type, dune.ratingKey, dune.title, dune.year.asInt()), ('movie', 'm1', 'Dune', 2024))
        self.assertEqual(
            dune.defaultThumb.asTranscodedImageURL(300, 450),
            'http://silo.lan:8090/api/v2/artwork/tmdb/movies/1/poster/w500.abc',
        )
        self.assertEqual((dune.duration.asInt(), dune.viewOffset.asInt()), (7200000, 600000))
        self.assertTrue(dune.isWatched)  # Plex's isWatched: played or in progress
        self.assertFalse(dune.isPlayed)
        self.assertTrue(up.isPlayed)

    def test_a_series_tile(self):
        andor = homeRows(self.standard())[2].items[0]

        self.assertEqual((andor.type, andor.title), ('show', 'Andor'))
        self.assertEqual((andor.leafCount.asInt(), andor.viewedLeafCount.asInt()), (12, 3))

    def test_an_episode_tile(self):
        ep = homeRows(self.standard())[0].items[0]

        self.assertEqual(ep.type, 'episode')
        self.assertEqual((ep.grandparentTitle, ep.parentIndex.asInt(), ep.index.asInt()), ('Severance', 1, 1))
        self.assertEqual(ep.grandparentThumb, '/api/v2/artwork/tvdb/series/2/poster/w500.def')
        self.assertEqual(ep.viewOffset.asInt(), 900000)

    def test_two_rows_of_one_kind_stay_two_rows(self):
        """Home keys its rows by getCleanHubIdentifier(), which strips trailing numbers, and Silo's section ids are
        numbers: on the user's box both Recently Added rows became one."""
        rows = [
            section('152896533422071873', 'recently_added', 'Recently Added Movies', [movie('m1', 'Dune')]),
            section('152896533422137409', 'recently_added', 'Recently Added TV', [series('t1', 'Andor', 0, 1)]),
        ]
        session = FakeSession(CAPS, FakeResponse(200, {'sections': rows, 'included_sections': rows}))

        ids = [h.getCleanHubIdentifier(is_home=True) for h in homeRows(session)]

        self.assertEqual(len(set(ids)), 2, ids)

    def test_audiobooks_ebooks_and_podcasts_are_left_out(self):
        other = [dict(movie('b1', 'Book'), type=kind) for kind in ('audiobook', 'ebook', 'podcast', 'podcast_episode')]
        session = FakeSession(
            CAPS,
            layout(
                section('s1', 'continue_watching', 'Continue Watching', other + [movie('m1', 'Dune')]),
                section('s2', 'recently_added', 'Recently Added Movies', []),
            ),
            rowItems('s3', 'trending_discover', 'Trending This Week', []),
        )

        self.assertEqual([i.title for i in homeRows(session)[0].items], ['Dune'])

    def test_a_row_that_fails_is_left_out(self):
        session = FakeSession(
            CAPS,
            layout(
                section('s1', 'continue_watching', 'Continue Watching', []),
                section('s2', 'recently_added', 'Recently Added Movies', [movie('m1', 'Dune')]),
            ),
            problem(500, 'internal_error'),
        )

        self.assertEqual([h.title for h in homeRows(session)], ['Continue Watching', 'Recently Added Movies'])

    def test_no_layout_means_no_rows(self):
        self.assertEqual(homeRows(FakeSession(CAPS, problem(503, 'dependency_unavailable'))), [])

    def test_a_row_with_more_says_so(self):
        hubs = homeRows(self.standard())

        self.assertEqual([h.more.asBool() for h in hubs], [False, True, False])


class SiloSeeMoreTest(KodiTestCase):
    """A row's "See more" grid (see_more.HubGridWindow): server.query(hub.key, limit=0) for how many, then
    plexobjects.listItems(server, hub.key, offset, limit) for each page. Silo answers a row's items from the start,
    at most section_item_limit_max of them."""

    def row(self, count, total=None):
        items = [movie(f'm{i}', f'Film {i}') for i in range(count)]
        total = count if total is None else total
        return FakeResponse(
            200, section('s2', 'recently_added', 'Recently Added Movies', items, total=total, more=total > count)
        )

    def test_a_rows_key_is_its_own_path(self):
        hubs = homeRows(SiloHomeRowsTest().standard())

        self.assertEqual(hubs[1].key, '/hubs/silo/s2')

    def test_counts_the_rows_items(self):
        session = FakeSession(CAPS, self.row(57))

        answer = server(session).query('/hubs/silo/s2', limit=0)

        self.assertEqual(answer.get('totalSize'), '57')
        self.assertEqual(len(answer), 0)
        self.assertEqual(session.calls[1]['params'], {'item_limit': 100})

    def test_counts_only_what_silo_answers(self):
        answer = server(FakeSession(CAPS, self.row(100, total=300))).query('/hubs/silo/s2', limit=0)

        self.assertEqual(answer.get('totalSize'), '100')

    def test_a_page(self):
        from plexnet import plexobjects, video  # noqa: F401 (video registers the item types)

        items = plexobjects.listItems(server(FakeSession(CAPS, self.row(57))), '/hubs/silo/s2', offset=20, limit=10)

        self.assertEqual([i.title for i in items], [f'Film {i}' for i in range(20, 30)])

    def test_a_row_that_fails_has_no_items(self):
        self.assertIsNone(
            server(FakeSession(CAPS, problem(503, 'dependency_unavailable'))).query('/hubs/silo/s2', limit=0)
        )
