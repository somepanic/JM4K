"""
Detail screens from Silo: an item's reload() (PlexObject.reload(): /library/metadata/<ratingKey>) answered from
GET /api/v2/catalog/items/{id} as the full Plex element the pre-play screen reads - metadata, cast and crew, ratings,
and its versions as Media > Part > Stream. Responses are written inline in the shape of /api/v2/openapi.json's
CatalogItemDetail (Silo build-2987); nothing is copied from Silo's sources.
"""

import copy

from plexnet import plexobjects, video  # noqa: F401 (video registers the item types, as the add-on's imports do)
from silonet.client import Credentials, DeviceIdentity, SiloClient
from silonet.items import itemElement
from silonet.server import SiloServer

from lib.silo import StoredServer

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')


def person(name, pid, **extra):
    return dict(
        {'name': name, 'person_id': pid, 'photo_url': f'/api/v2/artwork/tmdb/people/{pid}/profile/w185.x'}, **extra
    )


MOVIE = {
    'content_id': 'movie-tmdb-718821',
    'type': 'movie',
    'title': 'Twisters',
    'year': 2024,
    'runtime': 123,
    'overview': 'Storm season.',
    'tagline': 'Chase. Ride. Survive.',
    'release_date': '2024-07-10',
    'content_rating': 'PG-13',
    'studios': ['Universal Pictures', 'Amblin'],
    'genres': ['Action', 'Adventure'],
    'countries': ['US'],
    'rating_imdb': 6.5,
    'rating_tmdb': 6.742,
    'rating_rt_critic': 75,
    'rating_rt_audience': 52,
    'poster_url': '/api/v2/artwork/tmdb/movies/718821/poster/w500.x',
    'backdrop_url': '/api/v2/artwork/tmdb/movies/718821/backdrop/w1920.x',
    'position_seconds': 600,
    'user_state': {'played': False},
    'cast': [
        person('Daisy Edgar-Jones', 'p1', character='Kate', order=0),
        person('Glen Powell', 'p2', character='Tyler', order=1),
    ],
    'crew': [
        person('Lee Isaac Chung', 'p3', job='Director'),
        person('Mark L. Smith', 'p4', job='Screenplay'),
        person('Frank Marshall', 'p5', job='Producer'),
        person('Someone', 'p6', job='Editor'),
    ],
    'versions': [
        {
            'file_id': '241',
            'resolution': '2160p',
            'codec_video': 'hevc',
            'codec_audio': 'truehd',
            'hdr': True,
            'container': 'mkv',
            'file_size': 26904003318,
            'duration': 7344,
            'bitrate': 29305,
            'video_tracks': [
                {
                    'codec': 'hevc',
                    'width': 3840,
                    'height': 2160,
                    'frame_rate': '23.976',
                    'bit_depth': 10,
                    'dolby_vision': 'Profile 8',
                    'dv_profile': 8,
                    'dv_level': 6,
                    'dv_bl_compat_id': 1,
                    'color_transfer': 'smpte2084',
                    'color_primaries': 'bt2020',
                    'video_range': 'DolbyVision',
                }
            ],
            'audio_tracks': [
                {
                    'title': 'Dolby TrueHD Atmos 7.1',
                    'language': 'en',
                    'codec': 'truehd',
                    'channels': 8,
                    'default': True,
                },
                {'title': 'Commentary', 'language': 'en', 'codec': 'ac3', 'channels': 2, 'default': False},
            ],
            'subtitle_tracks': [
                {
                    'index': 4,
                    'language': 'en',
                    'codec': 'subrip',
                    'title': 'English',
                    'forced': False,
                    'default': False,
                },
                {
                    'index': 6,
                    'language': 'fr',
                    'codec': 'hdmv_pgs_subtitle',
                    'title': 'French',
                    'forced': True,
                    'default': False,
                },
            ],
        }
    ],
}


def server(session):
    client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(accessToken='A1', profileId='p1'), session=session)
    return SiloServer(StoredServer('http://silo.lan:8090', 'srv-1', 'Silo'), client)


def tile(srv, data):
    """A tile as Home or a grid builds it (silonet.items), the object a detail screen reloads."""
    return plexobjects.buildItem(srv, itemElement(data), '/hubs')


class SiloMovieDetailTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def reloaded(self, detail=MOVIE):
        session = FakeSession(FakeResponse(200, detail))
        movie = tile(server(session), detail).reload(includeExtras=1, includeExtrasCount=10)
        self.session = session
        return movie

    def test_reload_asks_silo_for_the_item(self):
        self.reloaded()

        self.assertEqual(self.session.calls[0]['url'], 'http://silo.lan:8090/api/v2/catalog/items/movie-tmdb-718821')

    def test_the_movies_metadata(self):
        movie = self.reloaded()

        self.assertFalse(movie.reloadFailed)
        self.assertEqual(
            (movie.title, movie.year.asInt(), movie.summary, movie.tagline),
            ('Twisters', 2024, 'Storm season.', 'Chase. Ride. Survive.'),
        )
        self.assertEqual(
            (movie.contentRating, movie.originallyAvailableAt, movie.studio),
            ('PG-13', '2024-07-10', 'Universal Pictures'),
        )
        self.assertEqual(movie.duration.asInt(), 7344000)
        self.assertEqual(movie.viewOffset.asInt(), 600000)
        self.assertEqual([g.tag for g in movie.genres()], ['Action', 'Adventure'])

    def test_cast_and_crew(self):
        movie = self.reloaded()

        self.assertEqual(
            [(r.tag, r.role, r.id) for r in movie.roles],
            [('Daisy Edgar-Jones', 'Kate', 'p1'), ('Glen Powell', 'Tyler', 'p2')],
        )
        self.assertEqual(movie.roles[0].thumb, '/api/v2/artwork/tmdb/people/p1/profile/w185.x')
        self.assertEqual([d.tag for d in movie.directors], ['Lee Isaac Chung'])
        self.assertEqual([w.tag for w in movie.writers], ['Mark L. Smith'])
        self.assertEqual([p.tag for p in movie.producers], ['Frank Marshall'])

    def test_ratings_with_icons_uno_has(self):
        movie = self.reloaded()

        self.assertEqual(
            [(str(r.image), str(r.value), str(r.type)) for r in movie.ratings],
            [
                ('imdb://image.rating', '6.5', 'audience'),
                ('rottentomatoes://image.rating.ripe', '7.5', 'critic'),
                ('rottentomatoes://image.rating.spilled', '5.2', 'audience'),
                ('themoviedb://image.rating', '6.7', 'audience'),
            ],
        )

    def test_its_version_as_media(self):
        movie = self.reloaded()

        media = movie.media[0]
        self.assertEqual(
            (
                media.videoResolution,
                media.videoCodec,
                media.audioCodec,
                media.audioChannels.asInt(),
                media.get('container'),
            ),
            ('4k', 'hevc', 'truehd', 8, 'mkv'),
        )
        part = media.parts[0]
        self.assertEqual(part.id, '241')
        audio = [s for s in part.streams if s.streamType.asInt() == 2]
        subtitles = [s for s in part.streams if s.streamType.asInt() == 3]
        self.assertEqual(
            [(a.title, a.codec, a.isSelected()) for a in audio],
            [('Dolby TrueHD Atmos 7.1', 'truehd', True), ('Commentary', 'ac3', False)],
        )
        self.assertEqual([(s.title, s.forced.asBool()) for s in subtitles], [('English', False), ('French', True)])

    def test_an_item_silo_cant_find_fails_to_reload(self):
        session = FakeSession(problem(404, 'not_found'))

        movie = tile(server(session), MOVIE).reload()

        self.assertTrue(movie.reloadFailed)


class SiloStreamDetailTest(KodiTestCase):
    """What the pre-play badges read of a version's tracks: the language name of each track (Plex's ISO 639-2/B
    languageCode and English name, from Silo's 'en', 'zh-Hans' or 'yue') and the video's HDR / Dolby Vision."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def part(self, detail=MOVIE):
        movie = tile(server(FakeSession(FakeResponse(200, detail))), detail).reload()
        return movie.media[0].parts[0]

    def test_track_languages_as_plex_names_them(self):
        streams = self.part().streams
        audio = [s for s in streams if s.streamType.asInt() == 2]
        subtitles = [s for s in streams if s.streamType.asInt() == 3]

        self.assertEqual([(a.languageCode, a.getLanguageName()) for a in audio], [('eng', 'English')] * 2)
        self.assertEqual(
            [(s.languageCode, s.getLanguageName()) for s in subtitles], [('eng', 'English'), ('fre', 'French')]
        )

    def test_regional_and_three_letter_codes(self):
        import copy

        detail = copy.deepcopy(MOVIE)
        detail['versions'][0]['subtitle_tracks'] = [
            {'index': 9, 'language': 'zh-Hans', 'codec': 'subrip', 'title': 'Chinese (Simplified)'},
            {'index': 8, 'language': 'yue', 'codec': 'subrip', 'title': 'Cantonese'},
        ]

        subtitles = [s for s in self.part(detail).streams if s.streamType.asInt() == 3]

        self.assertEqual([s.languageCode for s in subtitles], ['chi', 'yue'])

    def test_file_info_has_what_silo_gives(self):
        # pre-play's File info read addedAt, stream bitrates, chroma, sampling rate and the Dolby Vision layers, which
        # a Silo item lacked: "Added: 12/31/1969", "@ kBit", "Hz"
        detail = copy.deepcopy(MOVIE)
        version = detail['versions'][0]
        version['added_at'] = '2026-11-20T18:54:17.808Z'
        version['video_tracks'][0].update(
            pixel_format='yuv420p10le', dv_bl_present=True, dv_rpu_present=True, dv_enhancement_layer='none'
        )
        version['audio_tracks'][0].update(bitrate=5200, sample_rate=48000)
        version['audio_tracks'][1].update(bitrate=640, sample_rate=48000)

        movie = tile(server(FakeSession(FakeResponse(200, detail))), detail).reload()

        streams = movie.media[0].parts[0].streams
        picture = next(s for s in streams if s.streamType.asInt() == 1)
        audio = next(s for s in streams if s.streamType.asInt() == 2)
        self.assertEqual(movie.addedAt.asInt(), 1795200857)
        self.assertEqual(
            (
                picture.bitrate.asInt(),
                picture.chromaSubsampling,
                picture.DOVIBLPresent,
                picture.DOVIELPresent,
                picture.DOVIRPUPresent,
            ),
            (29305 - 5200 - 640, '4:2:0', '1', '0', '1'),
        )
        self.assertEqual((audio.bitrate.asInt(), audio.samplingRate.asInt()), (5200, 48000))

    def test_dolby_vision_and_hdr_render(self):
        stream = next(s for s in self.part().streams if s.streamType.asInt() == 1)

        self.assertEqual(stream.videoCodecRendering, 'DV P8.1/HDR')
