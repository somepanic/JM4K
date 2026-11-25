"""
Theme music from Silo (decision D15): a title's theme song (the detail's themes, ThemeSongSet) plays from a
short-lived grant, POST /api/v2/catalog/items/{id}/themes/{theme_id}/playback, asked for only when theme music is
on - and it is off until the user turns it on. Responses are written inline in the shape of /api/v2/openapi.json's
ThemeSongSet and ThemePlayback (Silo build-2987).
"""

import copy

from plexnet import util as pnUtil

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile

BASE = 'http://silo.lan:8090/api/v2'


def withTheme():
    detail = copy.deepcopy(MOVIE)
    detail['themes'] = {
        'owner_id': MOVIE['content_id'],
        'items': [{'id': 'th1', 'title': 'Main theme', 'duration_seconds': 30, 'container': 'mp3'}],
    }
    return detail


class SiloThemeTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_off_until_turned_on(self):
        self.assertEqual(pnUtil.INTERFACE.getThemeMusicValue(), 0)

    def test_a_titles_theme_plays_from_a_grant(self):
        grant = {
            'url': '/api/v2/stream/theme/g1?st=t',
            'expires_at': '2030-01-01T00:00:00Z',
            'delivery': 'original',
            'content_type': 'audio/mpeg',
        }
        session = FakeSession(FakeResponse(200, withTheme()), FakeResponse(200, grant))
        srv = server(session)
        movie = tile(srv, withTheme()).reload()

        url = srv.themeUrl(movie)

        call = session.calls[1]
        self.assertEqual(
            (call['method'], call['url']), ('POST', BASE + '/catalog/items/movie-tmdb-718821/themes/th1/playback')
        )
        self.assertIn({'container': 'mp3'}, call['json']['accepted_formats'])
        self.assertEqual(url, 'http://silo.lan:8090/api/v2/stream/theme/g1?st=t')

    def test_none_without_a_theme(self):
        session = FakeSession(FakeResponse(200, MOVIE))
        srv = server(session)
        movie = tile(srv, MOVIE).reload()

        self.assertIsNone(srv.themeUrl(movie))
        self.assertEqual(len(session.calls), 1)

    def test_none_when_silo_refuses(self):
        session = FakeSession(FakeResponse(200, withTheme()), problem(403, 'forbidden'))
        srv = server(session)
        movie = tile(srv, withTheme()).reload()

        self.assertIsNone(srv.themeUrl(movie))
