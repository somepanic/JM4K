"""
Seek-bar previews from Silo's trickplay (decision D17): a file's previews (GET /api/v2/watch/{id}/trickplay) are
JPEG sheets of thumbnails, each covering interval_ms; the preview store (lib/windows/previews.py) asks the server for
the file's index instead of downloading a Plex BIF file, and the index cuts the thumbnail for a position out of its
sheet (fetched once). Responses are written inline in the shape of /api/v2/openapi.json's WatchTrickplay (Silo
build-2987).
"""

import copy
import io

from PIL import Image
from plexnet import plexplayer

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession
from .test_silonet_detail import MOVIE, server, tile
from .test_silonet_markers import WATCH
from .test_silonet_playback import CAPABILITIES, decision

BASE = 'http://silo.lan:8090/api/v2'
COLOURS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def sheet():
    """A 2x2 sheet of 16x9 thumbnails, each one colour."""
    image = Image.new('RGB', (32, 18))
    for n, colour in enumerate(COLOURS):
        image.paste(colour, ((n % 2) * 16, (n // 2) * 9, (n % 2) * 16 + 16, (n // 2) * 9 + 9))
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=95)
    return out.getvalue()


TRICKPLAY = {
    'file_id': '241',
    'interval_ms': 10000,
    'thumbnail_width': 16,
    'thumbnail_height': 9,
    'tile_columns': 2,
    'tile_rows': 2,
    'thumbnail_count': 7,
    'sheets': [
        {'index': 0, 'url': '/api/v2/trickplay/241/0.jpg?sig=a'},
        {'index': 1, 'url': '/api/v2/trickplay/241/1.jpg?sig=b'},
    ],
    'expires_at': '2030-01-01T00:00:00Z',
}


def ok(body, status=200):
    return FakeResponse(status, body)


def withPreviews():
    answer = copy.deepcopy(WATCH)
    answer['versions'][0]['trickplay_available'] = True
    return answer


class SheetIndexTest(KodiTestCase):
    def index(self):
        from silonet import trickplay

        self.fetched = []

        def fetch(url):
            self.fetched.append(url)
            return sheet()

        return trickplay.SheetIndex(TRICKPLAY, 'http://silo.lan:8090', fetch)

    def test_which_thumbnail_a_position_shows(self):
        index = self.index()

        self.assertEqual((index.count, index.interval), (7, 10000))
        self.assertEqual([index.index_at(ms) for ms in (0, 9999, 25000, 999999)], [0, 0, 2, 6])

    def test_a_thumbnail_is_cut_from_its_sheet(self):
        index = self.index()

        pictures = [Image.open(io.BytesIO(index.picture(n))) for n in (1, 3, 5)]

        self.assertEqual([p.size for p in pictures], [(16, 9)] * 3)
        centre = [p.convert('RGB').getpixel((8, 4)) for p in pictures]
        self.assertTrue(all(abs(a - b) < 40 for a, b in zip(centre[0], COLOURS[1])))  # sheet 0, top right
        self.assertTrue(all(abs(a - b) < 40 for a, b in zip(centre[1], COLOURS[3])))  # sheet 0, bottom right
        self.assertTrue(all(abs(a - b) < 40 for a, b in zip(centre[2], COLOURS[1])))  # sheet 1, top right
        self.assertEqual(
            self.fetched,
            [
                'http://silo.lan:8090/api/v2/trickplay/241/0.jpg?sig=a',
                'http://silo.lan:8090/api/v2/trickplay/241/1.jpg?sig=b',
            ],
        )


class SheetMemoryTest(KodiTestCase):
    def test_only_the_last_sheets_stay_decoded(self):
        # a sheet is about 3200x1440 decoded (10x8 thumbnails of 320): a scrub through a film kept every one
        from silonet import trickplay

        manifest = dict(TRICKPLAY, thumbnail_count=12)
        manifest['sheets'] = TRICKPLAY['sheets'] + [{'index': 2, 'url': '/api/v2/trickplay/241/2.jpg?sig=c'}]
        fetched = []
        index = trickplay.SheetIndex(manifest, 'http://silo.lan:8090', lambda url: fetched.append(url) or sheet())

        for n in (0, 4, 8, 0):  # sheets 0, 1, 2, then 0 again
            index.picture(n)

        self.assertEqual(
            [url.split('/')[-1] for url in fetched], ['0.jpg?sig=a', '1.jpg?sig=b', '2.jpg?sig=c', '0.jpg?sig=a']
        )


class SiloPreviewsTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def played(self, watch, *more):
        session = FakeSession(ok(MOVIE), ok(CAPABILITIES), ok(decision(), 201), ok(watch), *more)
        srv = server(session)
        movie = tile(srv, MOVIE).reload()
        player = plexplayer.PlexPlayer(movie, 0)
        player.build()
        self.session, self.server = session, srv
        return player

    def test_a_file_with_previews(self):
        player = self.played(withPreviews(), ok(TRICKPLAY))

        part = player.media.parts[0]
        self.assertTrue(player.getBifUrl())
        self.assertTrue(part.getIndexPath('sd'))
        index = self.server.trickplayIndex(part.id)
        call = self.session.calls[4]
        self.assertEqual(
            (call['url'], call['params']), (BASE + '/watch/movie-tmdb-718821/trickplay', {'file_id': '241'})
        )
        self.assertEqual(index.count, 7)

    def test_a_file_without(self):
        player = self.played(WATCH)

        self.assertFalse(player.getBifUrl())
        self.assertIsNone(player.media.parts[0].getIndexPath('sd'))
