"""
The watchlist from Silo (decision D18: Silo's /api/v2/watchlist, library titles): Plex Uno's watchlist asked plex.tv's
Discover - the Watchlist screen's items (/library/sections/watchlist/all), an item's state (/library/metadata/<key>/
userState) and adding or removing (/actions/addToWatchlist, /actions/removeFromWatchlist, the key as ratingKey). A
Silo server answers them from GET /api/v2/watchlist (every page), GET /api/v2/watchlist/{id}/membership and PUT and
DELETE /api/v2/watchlist/{id}; its items' guid (silo://<type>/<id>) gives Plex Uno's watchlist code their id.
Responses are written inline in the shape of /api/v2/openapi.json (Silo build-2987).

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

from kodienv import ENV

ENV.abort_requested = True
from lib.windows.mixins import watchlist

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession
from .test_silonet_detail import MOVIE, server, tile

BASE = 'http://silo.lan:8090/api/v2'
CARDS = {
    'items': [
        {'content_id': 'movie-tmdb-718821', 'type': 'movie', 'title': 'Twisters', 'year': 2024},
        {'content_id': 'series-tvdb-1', 'type': 'series', 'title': 'Harbor Lights', 'year': 2019},
    ],
    'page': {'has_more': False},
}


def ok(body=None, status=200):
    return FakeResponse(status, body)


class SiloWatchlistTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_the_watchlist_screens_items(self):
        session = FakeSession(ok(CARDS))

        answer = server(session).query('/library/sections/watchlist/all?sort=watchlistedAt:desc', offset=0, limit=50)

        self.assertEqual((session.calls[0]['url'], session.calls[0]['params']), (BASE + '/watchlist', {'limit': 200}))
        self.assertEqual([e.get('title') for e in answer], ['Twisters', 'Harbor Lights'])
        self.assertEqual(answer.get('totalSize'), '2')

    def test_the_watchlist_screens_rows(self):
        session = FakeSession(ok(CARDS))

        hubs = server(session).hubs('/library/sections/watchlist', count=20)

        self.assertEqual(
            [(h.title, [i.title for i in h.items]) for h in hubs],
            [('Movies', ['Twisters']), ('Shows', ['Harbor Lights'])],
        )

    def test_a_long_row_lists_the_rest_under_see_more(self):
        movies = [{'content_id': f'movie-{n}', 'type': 'movie', 'title': f'M{n}'} for n in range(3)]
        session = FakeSession(
            ok({'items': movies, 'page': {'has_more': False}}), ok({'items': movies, 'page': {'has_more': False}})
        )
        srv = server(session)

        hub = srv.hubs('/library/sections/watchlist', count=2)[0]
        more = srv.query(hub.key, offset=0, limit=50)

        self.assertEqual(([i.title for i in hub.items], hub.more.asBool()), (['M0', 'M1'], True))
        self.assertEqual([e.get('title') for e in more], ['M0', 'M1', 'M2'])

    def test_an_items_guid_names_it(self):
        movie = tile(server(FakeSession()), MOVIE)

        self.assertEqual(watchlist.GUIDToRatingKey(movie.guid), 'movie-tmdb-718821')

    def test_whether_an_item_is_on_it(self):
        member = {'item_id': 'movie-tmdb-718821', 'is_member': True, 'added_at': '2026-11-24T00:00:00Z'}
        session = FakeSession(ok(member), ok(dict(member, is_member=False)))
        srv = server(session)

        self.assertEqual(
            [watchlist.is_watchlisted('movie-tmdb-718821', srv), watchlist.is_watchlisted('movie-tmdb-718821', srv)],
            [True, False],
        )
        self.assertEqual(session.calls[0]['url'], BASE + '/watchlist/movie-tmdb-718821/membership')

    def test_adding_and_removing(self):
        session = FakeSession(ok(None, 204), ok(None, 204))
        srv = server(session)

        srv.query('/actions/addToWatchlist', ratingKey='movie-tmdb-718821', method='put')
        srv.query('/actions/removeFromWatchlist', ratingKey='movie-tmdb-718821', method='put')

        self.assertEqual(
            [(c['method'], c['url']) for c in session.calls],
            [('PUT', BASE + '/watchlist/movie-tmdb-718821'), ('DELETE', BASE + '/watchlist/movie-tmdb-718821')],
        )

    def test_silo_drops_whats_been_watched_itself(self):
        movie = tile(server(FakeSession()), MOVIE)

        self.assertFalse(watchlist.shouldAutoRemove(movie))
