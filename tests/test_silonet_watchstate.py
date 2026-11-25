"""
Watched state from Silo: Plex Uno's Mark as played and unplayed (plexnet Video.markWatched(), markUnwatched(): Plex's
/:/scrobble and /:/unscrobble) and Remove from Continue Watching (/actions/removeFromContinueWatching) become
POST and DELETE /api/v2/watched/{id} and DELETE /api/v2/progress/{id}/position; a season or a show marks every
episode, as Silo's endpoint does. The item is then fetched again, as Plex Uno does.
"""

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession
from .test_silonet_detail import MOVIE, server, tile
from .test_silonet_shows import SERIES

BASE = 'http://silo.lan:8090/api/v2'


def ok(body=None, status=200):
    return FakeResponse(status, body)


class SiloWatchedTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_mark_played(self):
        played = dict(MOVIE, user_state={'played': True}, position_seconds=None)
        session = FakeSession(ok({'outcome': 'watched'}), ok(played))
        movie = tile(server(session), MOVIE)

        movie.markWatched()

        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url']), ('POST', BASE + '/watched/movie-tmdb-718821')
        )
        self.assertEqual(session.calls[1]['url'], BASE + '/catalog/items/movie-tmdb-718821')
        self.assertTrue(movie.isPlayed)

    def test_mark_unplayed(self):
        session = FakeSession(ok(None, 204), ok(MOVIE))

        tile(server(session), MOVIE).markUnwatched()

        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url']), ('DELETE', BASE + '/watched/movie-tmdb-718821')
        )

    def test_a_whole_show(self):
        session = FakeSession(ok({'outcome': 'watched'}), ok(SERIES))

        tile(server(session), SERIES).markWatched()

        self.assertEqual(session.calls[0]['url'], BASE + '/watched/series-tvdb-1')

    def test_remove_from_continue_watching(self):
        session = FakeSession(ok(None, 204), ok(MOVIE))

        tile(server(session), MOVIE).removeFromContinueWatching()

        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url']),
            ('DELETE', BASE + '/progress/movie-tmdb-718821/position'),
        )
