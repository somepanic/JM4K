"""
Skip markers, seek-bar previews, and chapters from Jellyfin 10.11.5 (Task 6.1):
- Media segments from GET /MediaSegments/{itemId} (D16): Intro, Outro (-> credits), Recap, Preview,
  and Commercial mapped to skippable Plex <Marker> elements with 100ns tick -> ms conversion.
- Seek-bar previews from Jellyfin trickplay sheets (D17): widest width <= 320 chosen (smallest if all
  exceed 320), Interval in ms (no tick conversion), JPEG tile cropping, LRU cache of KEPT_SHEETS = 2
  decoded sheets, authenticated via MediaBrowser Authorization header (never query param or logs).
- Chapters from cached item metadata (or /Items/{itemId}?fields=Chapters fallback) with 100ns tick -> ms
  conversion and /Items/{itemId}/Images/Chapter/{index} thumbs.
- SeekDialog enables marker skip on Jellyfin (JellyfinAccount.hasPlexPass() is True, as on SiloMod) and knows all
  five types.

Responses are written inline in the shape of Jellyfin 10.11.5 OpenAPI (MediaSegmentDtoQueryResult,
TrickplayInfoDto, ChapterInfo, PlaybackInfoResponse).
"""

import copy
import io
import os
import shutil
import tempfile
from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from jellynet import trickplay, watch
from jellynet.account import JellyfinAccount
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.server import JellyfinServer
from PIL import Image
from plexnet import plexapp, plexplayer

from lib.jellyfin import StoredServer
from lib.windows import previews, seekdialog

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem
from .test_jellynet_detail import MOVIE_DETAIL, make_tile
from .test_jellynet_playback import SECRET_TOKEN, make_movie, playback_info_response

BASE = 'http://jellyfin.lan:8096'
IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')
COLOURS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]

ALL_SEGMENTS = {
    'Items': [
        {
            'Id': 'seg-recap',
            'ItemId': 'mov-1',
            'Type': 'Recap',
            'StartTicks': 0,
            'EndTicks': 290_000_000,  # 29,000 ms
        },
        {
            'Id': 'seg-intro',
            'ItemId': 'mov-1',
            'Type': 'Intro',
            'StartTicks': 300_000_000,  # 30,000 ms
            'EndTicks': 955_000_000,  # 95,500 ms
        },
        {
            'Id': 'seg-commercial',
            'ItemId': 'mov-1',
            'Type': 'Commercial',
            'StartTicks': 12_000_000_000,  # 1,200,000 ms
            'EndTicks': 12_600_000_000,  # 1,260,000 ms
        },
        {
            'Id': 'seg-preview',
            'ItemId': 'mov-1',
            'Type': 'Preview',
            'StartTicks': 68_000_000_000,  # 6,800,000 ms
            'EndTicks': 69_000_000_000,  # 6,900,000 ms
        },
        {
            'Id': 'seg-outro',
            'ItemId': 'mov-1',
            'Type': 'Outro',
            'StartTicks': 70_000_000_000,  # 7,000,000 ms
            'EndTicks': 73_420_000_000,  # 7,342,000 ms (within 5s of 7,344,000 ms -> final)
        },
        {
            'Id': 'seg-unknown',
            'ItemId': 'mov-1',
            'Type': 'Unknown',
            'StartTicks': 1_000_000_000,
            'EndTicks': 2_000_000_000,
        },
        {
            'Id': 'seg-invalid',
            'ItemId': 'mov-1',
            'Type': 'Intro',
            'StartTicks': 5_000_000_000,
            'EndTicks': 5_000_000_000,  # end <= start -> ignored
        },
    ],
    'TotalRecordCount': 7,
    'StartIndex': 0,
}

SMALL_TRICKPLAY = {
    'Width': 16,
    'Height': 9,
    'TileWidth': 2,
    'TileHeight': 2,
    'ThumbnailCount': 7,
    'Interval': 10000,
    'Bandwidth': 50000,
}


def make_sheet(colours=COLOURS, width=16, height=9, cols=2, rows=2):
    """Build a cols x rows JPEG sheet where each tile is a solid RGB colour."""
    image = Image.new('RGB', (width * cols, height * rows))
    for n in range(cols * rows):
        colour = colours[n % len(colours)]
        col, row = n % cols, n // cols
        image.paste(colour, (col * width, row * height, (col + 1) * width, (row + 1) * height))
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=95)
    return out.getvalue()


def make_server(session, token=SECRET_TOKEN):
    client = JellyfinClient(BASE, IDENTITY, token=token, username='alice', session=session)
    return JellyfinServer(StoredServer(BASE, 'srv-1', 'Home Jellyfin'), client)


def playback_info_with_segments(source_id='src-4k', has_segments=True):
    return playback_info_response(
        sources=[
            {
                'Id': source_id,
                'Container': 'mkv',
                'Path': '/data/media/movies/Twisters (2024)/Twisters (2024).mkv',
                'ETag': 'etag-4k',
                'Bitrate': 29_305_000,
                'SupportsDirectPlay': True,
                'SupportsDirectStream': True,
                'SupportsTranscoding': True,
                'DefaultAudioStreamIndex': 1,
                'HasSegments': has_segments,
                'MediaStreams': [],
            }
        ],
        play_session_id='ps-4k',
    )


class MediaSegmentsTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def _play_movie(self, segments_response, detail_dto=None, has_segments=True):
        dto = copy.deepcopy(detail_dto or MOVIE_DETAIL)
        responses = [FakeResponse(200, dto), playback_info_with_segments(has_segments=has_segments)]
        if segments_response is not None:
            responses.append(segments_response)
        session = FakeSession(*responses)
        srv = make_server(session)
        movie = make_tile(srv, dto).reload(checkFiles=1, includeChapters=1)
        player = plexplayer.PlexPlayer(movie, 0)
        player.build()
        return session, srv, movie, player

    def test_each_segment_type_is_a_marker(self):
        session, srv, movie, _ = self._play_movie(FakeResponse(200, ALL_SEGMENTS))

        self.assertEqual(session.calls[2]['method'], 'GET')
        self.assertEqual(session.calls[2]['url'], f'{BASE}/MediaSegments/mov-1')
        self.assertTrue(session.calls[2]['headers']['Authorization'].startswith('MediaBrowser '))

        got = [
            (str(m.type), m.startTimeOffset.asInt(), m.endTimeOffset.asInt(), m.final.asBool()) for m in movie.markers
        ]
        self.assertEqual(
            got,
            [
                ('recap', 0, 29_000, False),
                ('intro', 30_000, 95_500, False),
                ('commercial', 1_200_000, 1_260_000, False),
                ('preview', 6_800_000, 6_900_000, False),
                ('credits', 7_000_000, 7_342_000, True),
            ],
        )
        # Every marker id parses via asInt() (seekdialog.py reads m.id.asInt()).
        self.assertEqual([m.id.asInt() for m in movie.markers], [1, 2, 3, 4, 5])

        # SeekDialog enables marker skipping on Jellyfin: Uno gates it on Plex Pass, which JellyfinAccount says it has.
        account = JellyfinAccount(srv.client)
        handler = mock.Mock()
        handler.player.video = movie
        # Exercise real __init__ marker-skip gating without needing Kodi window creation.
        with (
            mock.patch.object(plexapp, 'ACCOUNT', account),
            mock.patch.object(seekdialog.kodigui.BaseDialog, '__init__', lambda self, *a, **kw: None),
        ):
            real_dialog = seekdialog.SeekDialog('script-plex-seek_dialog.xml', '', handler=handler)

        self.assertTrue(real_dialog._enableMarkerSkip)
        with mock.patch.object(type(real_dialog), 'duration', 7_344_000, create=True):
            defs = real_dialog.markers

        self.assertEqual(
            [(d['marker_type'], str(d['name'])) for d in defs],
            [
                ('recap', 'Skip recap'),
                ('intro', 'Skip intro'),
                ('commercial', 'Skip commercial'),
                ('preview', 'Skip preview'),
                ('credits', 'Skip credits'),
            ],
        )

    def test_segment_ticks_to_ms(self):
        # Review Focus 5: MediaSegments StartTicks/EndTicks are 100ns ticks (10,000 ticks/ms).
        segments = [
            {'Id': 'a', 'Type': 'Intro', 'StartTicks': 10_000, 'EndTicks': 10_000_000},  # 1 ms .. 1000 ms
            {'Id': 'b', 'Type': 'Outro', 'StartTicks': 615_000_000, 'EndTicks': 902_500_000},  # 61,500 .. 90,250 ms
            {'Id': 'c', 'Type': 'Commercial', 'StartTicks': 0, 'EndTicks': 150_000_000},  # 0 .. 15,000 ms
            {'Id': 'bad-order', 'Type': 'Recap', 'StartTicks': 500_000_000, 'EndTicks': 400_000_000},
            {'Id': 'bad-none', 'Type': 'Preview', 'StartTicks': None, 'EndTicks': 100_000_000},
        ]
        elems = watch.markerElements(segments, duration_ms=95_000)

        self.assertEqual(
            [(e.get('type'), e.get('startTimeOffset'), e.get('endTimeOffset'), e.get('final')) for e in elems],
            [
                ('commercial', '0', '15000', '0'),
                ('intro', '1', '1000', '0'),
                ('credits', '61500', '90250', '1'),
            ],
        )

    def test_segments_error_returns_empty_markers_without_breaking_playback(self):
        _, _, movie, player = self._play_movie(problem(503, 'Service Unavailable'))

        self.assertEqual(list(movie.markers), [])
        self.assertTrue(player.metadata.streamUrls)
        self.assertFalse(ENV.logged(SECRET_TOKEN))


class TrickplayTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def _index(self, info=SMALL_TRICKPLAY, item_id='mov-1', media_source_id='src-4k'):
        fetched = []

        def fetch(url):
            fetched.append(url)
            return make_sheet()

        idx = trickplay.SheetIndex(info, BASE, item_id, media_source_id, fetch)
        return idx, fetched

    def test_which_thumbnail_a_position_shows(self):
        # Review Focus 5: TrickplayInfoDto.Interval is in MILLISECONDS (no tick conversion!).
        idx, _ = self._index()

        self.assertEqual((idx.count, idx.interval), (7, 10000))
        self.assertEqual(
            [idx.index_at(ms) for ms in (-500, 0, 9999, 10000, 25000, 60000, 999999)], [0, 0, 0, 1, 2, 6, 6]
        )

    def test_a_thumbnail_is_cut_from_its_sheet(self):
        idx, fetched = self._index()

        pictures = [Image.open(io.BytesIO(idx.picture(n))) for n in (1, 3, 5)]

        self.assertEqual([p.size for p in pictures], [(16, 9)] * 3)
        centres = [p.convert('RGB').getpixel((8, 4)) for p in pictures]
        self.assertTrue(all(abs(a - b) < 40 for a, b in zip(centres[0], COLOURS[1])))  # sheet 0, top right
        self.assertTrue(all(abs(a - b) < 40 for a, b in zip(centres[1], COLOURS[3])))  # sheet 0, bottom right
        self.assertTrue(all(abs(a - b) < 40 for a, b in zip(centres[2], COLOURS[1])))  # sheet 1, top right
        self.assertEqual(
            fetched,
            [
                f'{BASE}/Videos/mov-1/Trickplay/16/0.jpg?mediaSourceId=src-4k',
                f'{BASE}/Videos/mov-1/Trickplay/16/1.jpg?mediaSourceId=src-4k',
            ],
        )

    def test_only_the_last_sheets_stay_decoded(self):
        self.assertEqual(trickplay.KEPT_SHEETS, 2)
        info = dict(SMALL_TRICKPLAY, ThumbnailCount=12)
        idx, fetched = self._index(info=info)

        # Sheets 0, 1, 2, then 0 again -> sheet 0 was evicted when sheet 2 was loaded and must be re-fetched.
        for n in (0, 4, 8, 0):
            idx.picture(n)

        self.assertEqual(
            [url.rsplit('/', 1)[-1] for url in fetched],
            [
                '0.jpg?mediaSourceId=src-4k',
                '1.jpg?mediaSourceId=src-4k',
                '2.jpg?mediaSourceId=src-4k',
                '0.jpg?mediaSourceId=src-4k',
            ],
        )
        self.assertEqual(len(idx._sheets), 2)

    def test_widest_trickplay_under_320_chosen(self):
        # Widest <= 320 beats smaller (160) and larger (640).
        dto = copy.deepcopy(MOVIE_DETAIL)
        dto['Trickplay'] = {
            'src-4k': {
                '160': {
                    'Width': 160,
                    'Height': 90,
                    'TileWidth': 10,
                    'TileHeight': 10,
                    'ThumbnailCount': 50,
                    'Interval': 10000,
                },
                '320': {
                    'Width': 320,
                    'Height': 180,
                    'TileWidth': 10,
                    'TileHeight': 10,
                    'ThumbnailCount': 50,
                    'Interval': 10000,
                },
                '640': {
                    'Width': 640,
                    'Height': 360,
                    'TileWidth': 5,
                    'TileHeight': 5,
                    'ThumbnailCount': 50,
                    'Interval': 10000,
                },
            }
        }
        sheet_bytes = make_sheet(width=320, height=180, cols=2, rows=1)
        session = FakeSession(
            FakeResponse(200, dto),
            playback_info_with_segments(has_segments=False),
            FakeResponse(200, raw=sheet_bytes),
        )
        srv = make_server(session)
        movie = make_tile(srv, dto).reload(checkFiles=1, includeChapters=1)
        player = plexplayer.PlexPlayer(movie, 0)
        player.build()

        self.assertTrue(srv.hasPreviews('src-4k'))
        self.assertEqual(player.getBifUrl(), plexplayer.SILO_PREVIEWS)
        self.assertTrue(player.media.parts[0].getIndexPath('sd'))

        idx = srv.trickplayIndex('src-4k')
        self.assertIsNotNone(idx)
        self.assertEqual((idx.width, idx.height, idx.interval, idx.count), (320, 180, 10000, 50))
        pic = idx.picture(0)
        self.assertTrue(pic)

        sheet_call = session.calls[2]
        self.assertEqual(sheet_call['url'], f'{BASE}/Videos/mov-1/Trickplay/320/0.jpg?mediaSourceId=src-4k')
        self.assertEqual(sheet_call['headers']['Authorization'], srv.client.auth_header())
        self.assertNotIn('api_key', sheet_call['url'].lower())
        self.assertNotIn(SECRET_TOKEN, sheet_call['url'])
        self.assertFalse(ENV.logged(SECRET_TOKEN))

        # When all widths exceed 320, choose the smallest available (480 over 640).
        chosen_over = trickplay.select_resolution(
            {
                '640': {
                    'Width': 640,
                    'Height': 360,
                    'TileWidth': 5,
                    'TileHeight': 5,
                    'ThumbnailCount': 10,
                    'Interval': 10000,
                },
                '480': {
                    'Width': 480,
                    'Height': 270,
                    'TileWidth': 5,
                    'TileHeight': 5,
                    'ThumbnailCount': 10,
                    'Interval': 10000,
                },
            }
        )
        self.assertIsNotNone(chosen_over)
        self.assertEqual(chosen_over[0], 480)

        # PreviewStore integration writes cut JPEG and forgets the index on clear().
        tmpdir = tempfile.mkdtemp()
        try:
            store = previews.PreviewStore()
            store.folder = os.path.join(tmpdir, previews.FOLDER)
            store._indexes['src-4k'] = idx
            special = store.picture('src-4k', 0)
            self.assertEqual(special, f'special://temp/{previews.FOLDER}/src-4k/0.jpg')
            store.clear()
            self.assertEqual(store._indexes, {})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_trickplay_no_previews(self):
        dto = copy.deepcopy(MOVIE_DETAIL)
        dto['Trickplay'] = {}
        session = FakeSession(FakeResponse(200, dto), playback_info_with_segments(has_segments=False))
        srv = make_server(session)
        movie = make_tile(srv, dto).reload(checkFiles=1, includeChapters=1)
        player = plexplayer.PlexPlayer(movie, 0)
        player.build()

        self.assertFalse(srv.hasPreviews('src-4k'))
        self.assertIsNone(srv.trickplayIndex('src-4k'))
        self.assertIsNone(player.getBifUrl())
        self.assertIsNone(player.media.parts[0].getIndexPath('sd'))

        # Invalid trickplay (ThumbnailCount=0 or Interval=0) also reports no previews.
        dto_zero = copy.deepcopy(MOVIE_DETAIL)
        dto_zero['Trickplay'] = {
            'src-4k': {
                '320': {
                    'Width': 320,
                    'Height': 180,
                    'TileWidth': 10,
                    'TileHeight': 10,
                    'ThumbnailCount': 0,
                    'Interval': 10000,
                }
            }
        }
        srv_zero = make_server(FakeSession(FakeResponse(200, dto_zero)))
        make_tile(srv_zero, dto_zero).reload(checkFiles=1, includeChapters=1)
        self.assertFalse(srv_zero.hasPreviews('src-4k'))
        self.assertIsNone(srv_zero.trickplayIndex('src-4k'))


class ChaptersTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_chapters_map(self):
        # Cached chapters from item detail reload -> watchInfo returns PlexItemList without extra /Items fetch.
        session = FakeSession(
            FakeResponse(200, MOVIE_DETAIL),
            playback_info_with_segments(has_segments=False),
        )
        srv = make_server(session)
        movie = make_tile(srv, MOVIE_DETAIL).reload(checkFiles=1, includeChapters=1)
        plexplayer.PlexPlayer(movie, 0).build()

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(
            [(c.index.asInt(), str(c.tag), c.startTime(), c.endTimeOffset.asInt()) for c in movie.chapters],
            [
                (0, 'Opening', 0, 600_000),
                (1, 'Oklahoma', 600_000, 1_800_000),
                (2, 'The Chase', 1_800_000, 7_344_000),
            ],
        )
        self.assertEqual(str(movie.chapters[0].thumb), '/Items/mov-1/Images/Chapter/0?tag=ch-img-0')
        self.assertEqual(str(movie.chapters[1].thumb), '')
        self.assertEqual(str(movie.chapters[2].thumb), '/Items/mov-1/Images/Chapter/2?tag=ch-img-2')

        # Uncached direct watchInfo() falls back to GET /Items/{itemId}?fields=Chapters.
        cold_session = FakeSession(
            FakeResponse(200, {'Items': []}),
            FakeResponse(
                200,
                {
                    'Id': 'item-1',
                    'RunTimeTicks': 30_000_000_000,
                    'Chapters': [
                        {'StartPositionTicks': 0, 'Name': 'Cold Open', 'ImageTag': 'ctag-0'},
                        {'StartPositionTicks': 1_500_000_000, 'Name': 'Act One'},
                    ],
                },
            ),
        )
        cold_srv = make_server(cold_session)
        cold_movie = make_movie(cold_srv, item_id='item-1')
        markers, chapters = cold_srv.watchInfo(cold_movie, 'src-1')

        self.assertEqual(list(markers), [])
        self.assertEqual(
            [(c.tag, c.startTime(), c.endTimeOffset.asInt(), str(c.thumb)) for c in chapters],
            [
                ('Cold Open', 0, 150_000, '/Items/item-1/Images/Chapter/0?tag=ctag-0'),
                ('Act One', 150_000, 3_000_000, ''),
            ],
        )
        self.assertEqual(cold_session.calls[1]['url'], f'{BASE}/Items/item-1')
        self.assertEqual(cold_session.calls[1]['params'], {'fields': 'Chapters'})
