"""
Detail screens for movies, shows, seasons, and episodes against Jellyfin 10.11.5 (Task 4.1):
- GET /Items/{itemId} with full detail fields -> Plex <Video>/<Directory> with Media/Part/Stream,
  cast & crew, studios, taglines, chapters, provider IDs, ratings, and cached Trickplay/Chapters.
- GET /Shows/{seriesId}/Seasons and /Shows/{seriesId}/Episodes -> seasons, season episodes, and allLeaves.
- File info popup formatting (lib/windows/info.formatMediaDetails()).

Responses are written inline in the shape of Jellyfin 10.11.5 OpenAPI (BaseItemDto, MediaSourceInfo,
MediaStream, ChapterInfo, TrickplayInfoDto).
"""

import copy

from kodienv import ENV

ENV.abort_requested = True
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.items import itemElement
from jellynet.server import JellyfinServer
from plexnet import plexobjects, video

from lib.jellyfin import StoredServer
from lib.windows import info

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem

BASE = 'http://jellyfin.lan:8096'
IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')
EXPECTED_DETAIL_FIELDS = (
    'Overview,Genres,People,Studios,MediaSources,MediaStreams,Chapters,Trickplay,ProviderIds,Taglines,DateCreated,Path'
)

MOVIE_DETAIL = {
    'Id': 'mov-1',
    'Name': 'Twisters',
    'OriginalTitle': 'Twisters',
    'Type': 'Movie',
    'ProductionYear': 2024,
    'PremiereDate': '2024-07-10T00:00:00.0000000Z',
    'DateCreated': '2026-11-20T18:54:17.8080000Z',
    'Overview': 'Storm season.',
    'Taglines': ['Chase. Ride. Survive.'],
    'OfficialRating': 'PG-13',
    'CommunityRating': 6.7,
    'CriticRating': 75,
    'RunTimeTicks': 73_440_000_000,  # 7344s = 7,344,000 ms
    'Genres': ['Action', 'Adventure'],
    'ProductionLocations': ['United States of America'],
    'Studios': [
        {'Name': 'Universal Pictures', 'Id': 'st-1'},
        {'Name': 'Amblin Entertainment', 'Id': 'st-2'},
    ],
    'ProviderIds': {'Imdb': 'tt12584954', 'Tmdb': '718821'},
    'Path': '/data/media/movies/Twisters (2024)/Twisters (2024).mkv',
    'ImageTags': {'Primary': 'ptag-mov1', 'Logo': 'ltag-mov1'},
    'BackdropImageTags': ['btag-mov1'],
    'People': [
        {'Name': 'Daisy Edgar-Jones', 'Id': 'p1', 'Role': 'Kate', 'Type': 'Actor', 'PrimaryImageTag': 'img-p1'},
        {'Name': 'Glen Powell', 'Id': 'p2', 'Role': 'Tyler', 'Type': 'Actor', 'PrimaryImageTag': 'img-p2'},
        {'Name': 'Lee Isaac Chung', 'Id': 'p3', 'Type': 'Director', 'PrimaryImageTag': 'img-p3'},
        {'Name': 'Mark L. Smith', 'Id': 'p4', 'Type': 'Writer'},
        {'Name': 'Frank Marshall', 'Id': 'p5', 'Type': 'Producer'},
        {'Name': 'Terilyn A. Shropshire', 'Id': 'p6', 'Type': 'Editor'},
    ],
    'Chapters': [
        {'StartPositionTicks': 0, 'Name': 'Opening', 'ImageTag': 'ch-img-0'},
        {'StartPositionTicks': 6_000_000_000, 'Name': 'Oklahoma'},
        {'StartPositionTicks': 18_000_000_000, 'Name': 'The Chase', 'ImageTag': 'ch-img-2'},
    ],
    'Trickplay': {
        'src-4k': {
            '320': {
                'Width': 320,
                'Height': 180,
                'TileWidth': 10,
                'TileHeight': 10,
                'ThumbnailCount': 735,
                'Interval': 10000,
                'Bandwidth': 50000,
            }
        }
    },
    'UserData': {
        'PlaybackPositionTicks': 6_000_000_000,  # 600s = 600,000 ms
        'PlayCount': 0,
        'Played': False,
    },
    'MediaSources': [
        {
            'Id': 'src-4k',
            'Name': 'Twisters (2024) 2160p',
            'Path': '/data/media/movies/Twisters (2024)/Twisters (2024).mkv',
            'Container': 'mkv',
            'Size': 26_904_003_318,
            'Bitrate': 29_305_000,
            'RunTimeTicks': 73_440_000_000,
            'DefaultAudioStreamIndex': 1,
            'DefaultSubtitleStreamIndex': -1,
            'MediaStreams': [
                {
                    'Type': 'Video',
                    'Index': 0,
                    'Codec': 'hevc',
                    'Width': 3840,
                    'Height': 2160,
                    'BitRate': 23_465_000,
                    'AverageFrameRate': 23.976025,
                    'BitDepth': 10,
                    'PixelFormat': 'yuv420p10le',
                    'Profile': 'Main 10',
                    'Level': 153,
                    'ColorRange': 'tv',
                    'ColorSpace': 'bt2020nc',
                    'ColorTransfer': 'smpte2084',
                    'ColorPrimaries': 'bt2020',
                    'VideoRange': 'HDR',
                    'VideoRangeType': 'DOVIWithHDR10',
                    'DvProfile': 8,
                    'DvLevel': 6,
                    'BlPresentFlag': 1,
                    'ElPresentFlag': 0,
                    'RpuPresentFlag': 1,
                    'DvBlSignalCompatibilityId': 1,
                    'DisplayTitle': '4K HEVC Dolby Vision',
                    'IsDefault': True,
                },
                {
                    'Type': 'Audio',
                    'Index': 1,
                    'Codec': 'truehd',
                    'Channels': 8,
                    'ChannelLayout': '7.1',
                    'BitRate': 5_200_000,
                    'SampleRate': 48000,
                    'Language': 'eng',
                    'Title': 'Dolby TrueHD Atmos 7.1',
                    'DisplayTitle': 'English - Dolby TrueHD Atmos 7.1 - Default',
                    'IsDefault': True,
                },
                {
                    'Type': 'Audio',
                    'Index': 2,
                    'Codec': 'ac3',
                    'Channels': 2,
                    'ChannelLayout': 'stereo',
                    'BitRate': 640_000,
                    'SampleRate': 48000,
                    'Language': 'eng',
                    'Title': 'Commentary',
                    'DisplayTitle': 'English - Commentary - AC3 Stereo',
                    'IsDefault': False,
                },
                {
                    'Type': 'Subtitle',
                    'Index': 3,
                    'Codec': 'subrip',
                    'Language': 'eng',
                    'Title': 'English SDH',
                    'DisplayTitle': 'English (SDH)',
                    'IsDefault': False,
                    'IsForced': False,
                    'IsHearingImpaired': True,
                    'IsExternal': False,
                },
                {
                    'Type': 'Subtitle',
                    'Index': 4,
                    'Codec': 'hdmv_pgs_subtitle',
                    'Language': 'fre',
                    'Title': 'French Forced',
                    'DisplayTitle': 'French (Forced)',
                    'IsDefault': False,
                    'IsForced': True,
                    'IsHearingImpaired': False,
                    'IsExternal': False,
                },
            ],
        }
    ],
}

SERIES_DETAIL = {
    'Id': 'ser-1',
    'Name': 'Harbor Lights',
    'Type': 'Series',
    'ProductionYear': 2019,
    'PremiereDate': '2019-03-01T00:00:00.0000000Z',
    'Overview': 'A lighthouse keeper finds a ship that left a century ago.',
    'OfficialRating': 'TV-14',
    'CommunityRating': 8.1,
    'Genres': ['Drama', 'Mystery'],
    'Studios': [{'Name': 'Coast TV', 'Id': 'st-tv'}],
    'ChildCount': 2,
    'RecursiveItemCount': 4,
    'ImageTags': {'Primary': 'stag-1'},
    'BackdropImageTags': ['sbtag-1'],
    'People': [{'Name': 'Ana Reyes', 'Id': 'p-ana', 'Role': 'Mara', 'Type': 'Actor', 'PrimaryImageTag': 'img-ana'}],
    'UserData': {'UnplayedItemCount': 3, 'Played': False},
}

SEASON_1 = {
    'Id': 'sea-1',
    'Name': 'Season 1',
    'Type': 'Season',
    'IndexNumber': 1,
    'SeriesId': 'ser-1',
    'SeriesName': 'Harbor Lights',
    'SeriesPrimaryImageTag': 'stag-1',
    'Overview': 'The ship returns.',
    'ChildCount': 2,
    'ImageTags': {'Primary': 'sea1-tag'},
    'ParentBackdropItemId': 'ser-1',
    'ParentBackdropImageTags': ['sbtag-1'],
    'UserData': {'UnplayedItemCount': 1, 'Played': False},
}

SEASON_2 = {
    'Id': 'sea-2',
    'Name': 'Season 2',
    'Type': 'Season',
    'IndexNumber': 2,
    'SeriesId': 'ser-1',
    'SeriesName': 'Harbor Lights',
    'SeriesPrimaryImageTag': 'stag-1',
    'ChildCount': 2,
    'ImageTags': {'Primary': 'sea2-tag'},
    'UserData': {'UnplayedItemCount': 2, 'Played': False},
}


def episode_detail(ep_id, season_id, season_num, ep_num, title, played=False, position_ticks=0):
    return {
        'Id': ep_id,
        'Name': title,
        'Type': 'Episode',
        'IndexNumber': ep_num,
        'ParentIndexNumber': season_num,
        'SeriesId': 'ser-1',
        'SeriesName': 'Harbor Lights',
        'SeriesPrimaryImageTag': 'stag-1',
        'SeasonId': season_id,
        'SeasonName': f'Season {season_num}',
        'ParentPrimaryImageItemId': season_id,
        'ParentPrimaryImageTag': f'sea{season_num}-tag',
        'Overview': f'{title} plot.',
        'PremiereDate': f'{2018 + season_num}-03-0{ep_num}T00:00:00.0000000Z',
        'DateCreated': '2026-11-20T18:54:17.8080000Z',
        'RunTimeTicks': 30_000_000_000,  # 3000s = 3,000,000 ms
        'ImageTags': {'Primary': f'img-{ep_id}'},
        'ParentBackdropItemId': 'ser-1',
        'ParentBackdropImageTags': ['sbtag-1'],
        'People': [{'Name': 'Jo Park', 'Id': 'p-jo', 'Type': 'Director'}],
        'UserData': {
            'Played': played,
            'PlayCount': 1 if played else 0,
            'PlaybackPositionTicks': position_ticks,
        },
        'MediaSources': [
            {
                'Id': f'src-{ep_id}',
                'Path': f'/media/shows/Harbor Lights/S0{season_num}E0{ep_num}.mkv',
                'Container': 'mkv',
                'Size': 2_500_000_000,
                'Bitrate': 8_000_000,
                'RunTimeTicks': 30_000_000_000,
                'MediaStreams': [
                    {
                        'Type': 'Video',
                        'Index': 0,
                        'Codec': 'h264',
                        'Width': 1920,
                        'Height': 1080,
                        'AverageFrameRate': 23.976,
                    },
                    {
                        'Type': 'Audio',
                        'Index': 1,
                        'Codec': 'eac3',
                        'Channels': 6,
                        'Language': 'eng',
                        'IsDefault': True,
                    },
                ],
            }
        ],
    }


def make_server(session):
    client = JellyfinClient(BASE, IDENTITY, token='TOK-1', username='alice', session=session)
    return JellyfinServer(StoredServer(BASE, 'srv-1', 'Home Jellyfin'), client)


def make_tile(srv, dto):
    return plexobjects.buildItem(srv, itemElement(dto), '/hubs')


class JellyfinDetailTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_movie_detail_maps(self):
        session = FakeSession(FakeResponse(200, MOVIE_DETAIL))
        srv = make_server(session)

        movie = make_tile(srv, MOVIE_DETAIL).reload(checkFiles=1, includeChapters=1)

        self.assertFalse(movie.reloadFailed)
        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        self.assertEqual(call['method'], 'GET')
        self.assertEqual(call['url'], f'{BASE}/Items/mov-1')
        self.assertEqual(call['params']['fields'], EXPECTED_DETAIL_FIELDS)

        # Core movie metadata
        self.assertEqual(
            (movie.title, movie.year.asInt(), movie.summary, movie.tagline),
            ('Twisters', 2024, 'Storm season.', 'Chase. Ride. Survive.'),
        )
        self.assertEqual(
            (movie.contentRating, movie.originallyAvailableAt, movie.studio),
            ('PG-13', '2024-07-10', 'Universal Pictures'),
        )
        self.assertEqual(movie.addedAt.asInt(), 1795200857)
        self.assertEqual(movie.duration.asInt(), 7_344_000)
        self.assertEqual(movie.viewOffset.asInt(), 600_000)
        self.assertEqual([g.tag for g in movie.genres()], ['Action', 'Adventure'])
        self.assertEqual([s.tag for s in movie.studios], ['Universal Pictures', 'Amblin Entertainment'])
        self.assertEqual([c.tag for c in movie.countries], ['United States of America'])
        self.assertEqual([g.id for g in movie.guids], ['imdb://tt12584954', 'tmdb://718821'])
        self.assertEqual(movie.clearLogo, '/Items/mov-1/Images/Logo?tag=ltag-mov1')

        # Ratings from CommunityRating and CriticRating
        self.assertEqual(
            [(str(r.image), str(r.value), str(r.type)) for r in movie.ratings],
            [
                ('themoviedb://image.rating', '6.7', 'audience'),
                ('rottentomatoes://image.rating.ripe', '7.5', 'critic'),
            ],
        )

        # Cast & crew
        self.assertEqual(
            [(r.tag, r.role, r.id, r.tagKey) for r in movie.roles],
            [('Daisy Edgar-Jones', 'Kate', 'p1', 'p1'), ('Glen Powell', 'Tyler', 'p2', 'p2')],
        )
        self.assertEqual(movie.roles[0].thumb, '/Items/p1/Images/Primary?tag=img-p1')
        self.assertEqual([(d.tag, d.id) for d in movie.directors], [('Lee Isaac Chung', 'p3')])
        self.assertEqual([(w.tag, w.id) for w in movie.writers], [('Mark L. Smith', 'p4')])
        self.assertEqual([(p.tag, p.id) for p in movie.producers], [('Frank Marshall', 'p5')])

        # Chapters (startTimeOffset, endTimeOffset, thumb when ImageTag present)
        self.assertEqual(
            [(c.index.asInt(), c.tag, c.startTimeOffset.asInt(), c.endTimeOffset.asInt()) for c in movie.chapters],
            [
                (0, 'Opening', 0, 600_000),
                (1, 'Oklahoma', 600_000, 1_800_000),
                (2, 'The Chase', 1_800_000, 7_344_000),
            ],
        )
        self.assertTrue(str(movie.chapters[0].thumb).startswith('/Items/mov-1/Images/Chapter/0'))
        self.assertEqual(str(movie.chapters[1].thumb), '')
        self.assertTrue(str(movie.chapters[2].thumb).startswith('/Items/mov-1/Images/Chapter/2'))

        # Trickplay and Chapters cached on server for Phase 6
        self.assertIn('mov-1', srv._itemMeta)
        self.assertEqual(len(srv._itemMeta['mov-1']['chapters']), 3)
        self.assertIn('src-4k', srv._trickplayMeta)
        self.assertEqual(srv._trickplayMeta['src-4k']['item_id'], 'mov-1')
        self.assertIn('320', srv._trickplayMeta['src-4k']['trickplay'])

        # 404 marks reloadFailed
        err_srv = make_server(FakeSession(problem(404, 'Not Found')))
        failed = make_tile(err_srv, MOVIE_DETAIL).reload()
        self.assertTrue(failed.reloadFailed)

    def test_two_sources_are_two_versions(self):
        detail = copy.deepcopy(MOVIE_DETAIL)
        detail['MediaSources'].append(
            {
                'Id': 'src-1080',
                'Name': 'Twisters (2024) 1080p',
                'Path': '/data/media/movies/Twisters (2024)/Twisters (2024) 1080p.mp4',
                'Container': 'mp4',
                'Size': 8_500_000_000,
                'Bitrate': 9_500_000,
                'RunTimeTicks': 73_440_000_000,
                'DefaultAudioStreamIndex': 1,
                'MediaStreams': [
                    {
                        'Type': 'Video',
                        'Index': 0,
                        'Codec': 'h264',
                        'Width': 1920,
                        'Height': 1080,
                        'AverageFrameRate': 24.0,
                    },
                    {
                        'Type': 'Audio',
                        'Index': 1,
                        'Codec': 'aac',
                        'Channels': 6,
                        'Language': 'eng',
                        'IsDefault': True,
                    },
                ],
            }
        )
        detail['Trickplay']['src-1080'] = {'320': {'Width': 320, 'Height': 180, 'Interval': 10000}}
        srv = make_server(FakeSession(FakeResponse(200, detail)))
        movie = make_tile(srv, detail).reload()

        self.assertEqual(len(movie.media), 2)
        m4k, m1080 = movie.media[0], movie.media[1]
        self.assertEqual(
            (
                m4k.id,
                m4k.videoResolution,
                m4k.videoCodec,
                m4k.audioCodec,
                m4k.audioChannels.asInt(),
                m4k.bitrate.asInt(),
                m4k.get('container'),
                m4k.aspectRatio,
            ),
            ('src-4k', '4k', 'hevc', 'truehd', 8, 29305, 'mkv', '1.78'),
        )
        self.assertEqual(
            (
                m1080.id,
                m1080.videoResolution,
                m1080.videoCodec,
                m1080.audioCodec,
                m1080.audioChannels.asInt(),
                m1080.bitrate.asInt(),
                m1080.get('container'),
            ),
            ('src-1080', '1080', 'h264', 'aac', 6, 9500, 'mp4'),
        )
        self.assertEqual(
            (m4k.parts[0].id, m4k.parts[0].key, m4k.parts[0].size.asInt()),
            ('src-4k', 'src-4k', 26_904_003_318),
        )
        self.assertEqual(
            (m1080.parts[0].id, m1080.parts[0].key, m1080.parts[0].size.asInt()),
            ('src-1080', 'src-1080', 8_500_000_000),
        )
        self.assertIn('src-1080', srv._trickplayMeta)

        # Lower resolution buckets (720, 480, sd)
        for width, height, expected_res in ((1280, 720, '720'), (720, 480, '480'), (640, 360, 'sd')):
            d = copy.deepcopy(MOVIE_DETAIL)
            d['MediaSources'][0]['MediaStreams'][0]['Width'] = width
            d['MediaSources'][0]['MediaStreams'][0]['Height'] = height
            res_movie = make_tile(make_server(FakeSession(FakeResponse(200, d))), d).reload()
            self.assertEqual(res_movie.media[0].videoResolution, expected_res)

    def test_streams_map(self):
        detail = copy.deepcopy(MOVIE_DETAIL)
        detail['MediaSources'][0]['MediaStreams'].extend(
            [
                {
                    'Type': 'Subtitle',
                    'Index': 5,
                    'Codec': 'subrip',
                    'Language': 'zh-Hans',
                    'Title': 'Chinese External',
                    'DisplayTitle': 'Chinese (External SRT)',
                    'IsExternal': True,
                    'DeliveryUrl': '/Videos/mov-1/src-4k/Subtitles/5/Stream.srt',
                },
                {
                    'Type': 'Subtitle',
                    'Index': 6,
                    'Codec': 'ass',
                    'Language': 'yue',
                    'Title': 'Cantonese External',
                    'DisplayTitle': 'Cantonese (External ASS)',
                    'IsExternal': True,
                },
                {
                    # External subtitle without Index (SM4K #49: must still get distinct id)
                    'Type': 'Subtitle',
                    'Codec': 'srt',
                    'Language': 'spa',
                    'Title': 'Spanish External',
                    'IsExternal': True,
                },
                {
                    # Second external subtitle without Index (must not collide with previous)
                    'Type': 'Subtitle',
                    'Codec': 'srt',
                    'Language': 'deu',
                    'Title': 'German External',
                    'IsExternal': True,
                },
            ]
        )
        srv = make_server(FakeSession(FakeResponse(200, detail)))
        movie = make_tile(srv, detail).reload()
        streams = movie.media[0].parts[0].streams

        # Every stream across video/audio/subtitles has a distinct id
        stream_ids = [str(s.id) for s in streams]
        self.assertEqual(len(stream_ids), len(set(stream_ids)))

        # Video stream
        video_streams = [s for s in streams if s.streamType.asInt() == 1]
        self.assertEqual(len(video_streams), 1)
        v = video_streams[0]
        self.assertEqual((v.index.asInt(), v.codec, v.width.asInt(), v.height.asInt()), (0, 'hevc', 3840, 2160))
        self.assertEqual((v.bitrate.asInt(), v.bitDepth.asInt(), v.chromaSubsampling), (23465, 10, '4:2:0'))
        self.assertEqual((v.frameRate, v.profile, v.level), ('23.976', 'Main 10', '153'))
        self.assertEqual(
            (v.colorRange, v.colorSpace, v.colorTrc, v.colorPrimaries), ('tv', 'bt2020nc', 'smpte2084', 'bt2020')
        )
        self.assertEqual(
            (
                v.DOVIPresent,
                v.DOVIProfile,
                v.DOVILevel,
                v.DOVIBLPresent,
                v.DOVIELPresent,
                v.DOVIRPUPresent,
                v.DOVIBLCompatID,
            ),
            ('1', '8', '6', '1', '0', '1', '1'),
        )
        self.assertEqual(str(v.DOVIVersion), '')  # omitted when DvVersionMajor is absent
        self.assertEqual(v.videoCodecRendering, 'DV P8.1/HDR')

        # Audio streams
        audio = [s for s in streams if s.streamType.asInt() == 2]
        self.assertEqual(len(audio), 2)
        self.assertEqual(
            (
                audio[0].index.asInt(),
                audio[0].codec,
                audio[0].channels.asInt(),
                audio[0].audioChannelLayout,
                audio[0].bitrate.asInt(),
                audio[0].samplingRate.asInt(),
                audio[0].languageCode,
                audio[0].language,
                audio[0].isSelected(),
                bool(audio[0].default),
            ),
            (1, 'truehd', 8, '7.1', 5200, 48000, 'eng', 'English', True, True),
        )
        self.assertFalse(audio[1].isSelected())
        self.assertFalse(bool(audio[1].default))

        # Subtitle streams: embedded first, external after
        subs = [s for s in streams if s.streamType.asInt() == 3]
        self.assertEqual(len(subs), 6)
        # Embedded subtitles have no key and external unset
        self.assertEqual(
            (
                subs[0].languageCode,
                subs[0].language,
                subs[0].codec,
                bool(subs[0].hearingImpaired),
                bool(subs[0].forced),
            ),
            ('eng', 'English', 'subrip', True, False),
        )
        self.assertIsNone(subs[0].key)
        self.assertFalse(bool(subs[0].external))
        self.assertTrue(subs[0].embedded)

        self.assertEqual(
            (subs[1].languageCode, subs[1].language, bool(subs[1].forced), bool(subs[1].default)),
            ('fre', 'French', True, False),
        )
        self.assertIsNone(subs[1].key)

        # External subtitles carry external='1' and key (DeliveryUrl or fallback /Videos/.../Subtitles/.../Stream.{codec})
        self.assertEqual(subs[2].languageCode, 'chi')
        self.assertEqual(str(subs[2].external), '1')
        self.assertEqual(str(subs[2].key), '/Videos/mov-1/src-4k/Subtitles/5/Stream.srt')
        self.assertFalse(subs[2].embedded)

        self.assertEqual(subs[3].languageCode, 'yue')
        self.assertEqual(str(subs[3].external), '1')
        self.assertEqual(str(subs[3].key), '/Videos/mov-1/src-4k/Subtitles/6/Stream.ass')

        self.assertEqual(subs[4].languageCode, 'spa')
        self.assertEqual(subs[5].languageCode, 'ger')
        self.assertNotEqual(str(subs[4].id), str(subs[5].id))

    def test_show_seasons_and_episodes(self):
        ep1 = episode_detail('ep-1-1', 'sea-1', 1, 1, 'Arrival', played=True)
        ep2 = episode_detail('ep-1-2', 'sea-1', 1, 2, 'Undertow', played=False, position_ticks=9_000_000_000)
        ep3 = episode_detail('ep-2-1', 'sea-2', 2, 1, 'Low Tide', played=False)

        # Show reload (with includeOnDeck=1) -> seasons -> season 1 episodes -> allLeaves
        session = FakeSession(
            FakeResponse(200, SERIES_DETAIL),
            FakeResponse(200, {'Items': [ep2], 'TotalRecordCount': 1}),
            FakeResponse(200, {'Items': [SEASON_1, SEASON_2], 'TotalRecordCount': 2}),
            FakeResponse(200, {'Items': [ep1, ep2], 'TotalRecordCount': 2}),
            FakeResponse(200, {'Items': [ep1, ep2, ep3], 'TotalRecordCount': 3}),
        )
        srv = make_server(session)

        show = make_tile(srv, SERIES_DETAIL).reload(includeOnDeck=1)
        self.assertIsInstance(show, video.Show)
        self.assertFalse(show.reloadFailed)
        self.assertEqual(
            (show.title, show.year.asInt(), show.contentRating, show.studio, show.childCount.asInt()),
            ('Harbor Lights', 2019, 'TV-14', 'Coast TV', 2),
        )
        self.assertEqual((show.leafCount.asInt(), show.viewedLeafCount.asInt()), (4, 1))
        self.assertEqual(len(show.onDeck), 1)
        self.assertEqual(show.onDeck[0].ratingKey, 'ep-1-2')
        self.assertEqual(session.calls[1]['url'], f'{BASE}/Shows/NextUp')
        self.assertEqual(session.calls[1]['params']['seriesId'], 'ser-1')

        # Seasons (cached Series type means direct /Shows/ser-1/Seasons without redundant /Items/ser-1)
        seasons = show.seasons()
        self.assertEqual(session.calls[2]['url'], f'{BASE}/Shows/ser-1/Seasons')
        self.assertTrue(all(isinstance(s, video.Season) for s in seasons))
        self.assertEqual(
            [(s.ratingKey, s.index.asInt(), s.title, s.leafCount.asInt(), s.viewedLeafCount.asInt()) for s in seasons],
            [('sea-1', 1, 'Season 1', 2, 1), ('sea-2', 2, 'Season 2', 2, 0)],
        )
        self.assertEqual(
            (seasons[0].parentRatingKey, seasons[0].parentTitle, seasons[0].summary),
            ('ser-1', 'Harbor Lights', 'The ship returns.'),
        )

        # Season episodes (cached Season -> Series means direct /Shows/ser-1/Episodes?seasonId=sea-1)
        episodes = seasons[0].episodes()
        self.assertEqual(session.calls[3]['url'], f'{BASE}/Shows/ser-1/Episodes')
        self.assertEqual(session.calls[3]['params']['seasonId'], 'sea-1')
        self.assertEqual(
            [(e.ratingKey, e.title, e.isPlayed, e.viewOffset.asInt()) for e in episodes],
            [('ep-1-1', 'Arrival', True, 0), ('ep-1-2', 'Undertow', False, 900_000)],
        )
        self.assertEqual(episodes[0].media[0].videoResolution, '1080')
        self.assertEqual(episodes[0].media[0].audioCodec, 'eac3')

        # Show allLeaves -> GET /Shows/ser-1/Episodes (no seasonId)
        all_eps = show.episodes()
        self.assertEqual(session.calls[4]['url'], f'{BASE}/Shows/ser-1/Episodes')
        self.assertNotIn('seasonId', session.calls[4]['params'])
        self.assertEqual([e.ratingKey for e in all_eps], ['ep-1-1', 'ep-1-2', 'ep-2-1'])

        # Failed NextUp when includeOnDeck=1 still loads show cleanly with empty onDeck
        err_deck_srv = make_server(FakeSession(FakeResponse(200, SERIES_DETAIL), problem(404, 'Not Found')))
        ok_show = make_tile(err_deck_srv, SERIES_DETAIL).reload(includeOnDeck=1)
        self.assertFalse(ok_show.reloadFailed)
        self.assertEqual(list(ok_show.onDeck), [])

        # Paginated allLeaves sends startIndex and limit
        page_session = FakeSession(FakeResponse(200, {'Items': [ep2], 'TotalRecordCount': 3}))
        page_srv = make_server(page_session)
        paged_eps = make_tile(page_srv, SERIES_DETAIL).episodes(offset=1, limit=1)
        self.assertEqual([e.ratingKey for e in paged_eps], ['ep-1-2'])
        self.assertEqual(page_session.calls[0]['params']['startIndex'], 1)
        self.assertEqual(page_session.calls[0]['params']['limit'], 1)

        # Cold /library/metadata/{id}/children works for uncached Series, Season, BoxSet, and Movie
        cold_session = FakeSession(
            FakeResponse(200, SEASON_1),
            FakeResponse(200, {'Items': [ep1], 'TotalRecordCount': 1}),
            FakeResponse(200, {'Id': 'box-1', 'Name': 'Trilogy', 'Type': 'BoxSet'}),
            FakeResponse(200, {'Items': [MOVIE_DETAIL], 'TotalRecordCount': 1}),
            FakeResponse(200, MOVIE_DETAIL),
        )
        cold_srv = make_server(cold_session)
        cold_eps = cold_srv.query('/library/metadata/sea-1/children')
        self.assertEqual([e.get('ratingKey') for e in cold_eps], ['ep-1-1'])
        cold_box = cold_srv.query('/library/metadata/box-1/children')
        self.assertEqual([e.get('ratingKey') for e in cold_box], ['mov-1'])
        cold_mov = cold_srv.query('/library/metadata/mov-1/children')
        self.assertEqual(len(list(cold_mov)), 0)

    def test_episode_numbers_and_parent_ids(self):
        ep1 = episode_detail('ep-1-1', 'sea-1', 1, 1, 'Arrival', played=True)
        ep2 = episode_detail('ep-1-2', 'sea-1', 1, 2, 'Undertow', played=False, position_ticks=9_000_000_000)

        session = FakeSession(
            FakeResponse(200, ep2),
            FakeResponse(200, SEASON_1),
            FakeResponse(200, SERIES_DETAIL),
        )
        srv = make_server(session)

        ep = make_tile(srv, ep2).reload(checkFiles=1, includeChapters=1)
        self.assertFalse(ep.reloadFailed)
        self.assertEqual((ep.index.asInt(), ep.parentIndex.asInt()), (2, 1))
        self.assertEqual(
            (ep.parentRatingKey, ep.parentKey, ep.parentTitle),
            ('sea-1', '/library/metadata/sea-1', 'Season 1'),
        )
        self.assertEqual(
            (ep.grandparentRatingKey, ep.grandparentKey, ep.grandparentTitle),
            ('ser-1', '/library/metadata/ser-1', 'Harbor Lights'),
        )
        self.assertEqual((ep.duration.asInt(), ep.viewOffset.asInt()), (3_000_000, 900_000))
        self.assertEqual(ep.originallyAvailableAt, '2019-03-02')
        self.assertEqual([d.tag for d in ep.directors], ['Jo Park'])

        # episode.season() and episode.show() follow parentKey/grandparentKey
        season = ep.season()
        show = ep.show()
        self.assertEqual((season.ratingKey, season.index.asInt(), season.parentRatingKey), ('sea-1', 1, 'ser-1'))
        self.assertEqual((show.ratingKey, show.title), ('ser-1', 'Harbor Lights'))

        # Batch reload (/library/metadata/ep-1-1,ep-missing,ep-1-2) used by EpisodesReloadTask
        batch_session = FakeSession(FakeResponse(200, {'Items': [ep2, ep1], 'TotalRecordCount': 2}))
        batch_srv = make_server(batch_session)
        batch = plexobjects.listItems(
            batch_srv,
            '/library/metadata/ep-1-1,ep-missing,ep-1-2',
            return_data=True,
            checkFiles=1,
            includeChapters=1,
        )
        self.assertEqual(batch_session.calls[0]['url'], f'{BASE}/Items')
        self.assertEqual(batch_session.calls[0]['params']['ids'], 'ep-1-1,ep-missing,ep-1-2')
        self.assertEqual(
            [(d.get('ratingKey'), d.get('parentIndex'), d.get('index')) for d in batch],
            [('ep-1-1', '1', '1'), ('ep-1-2', '1', '2')],
        )

    def test_file_info_lines(self):
        srv = make_server(FakeSession(FakeResponse(200, MOVIE_DETAIL)))
        movie = make_tile(srv, MOVIE_DETAIL).reload(checkFiles=1, includeChapters=1)

        text = info.formatMediaDetails(movie)

        self.assertNotIn('1969', text)
        self.assertIn('2026', text)
        self.assertIn('File: Twisters (2024).mkv', text)
        self.assertIn('3840x2160, DV P8.1/HDR HEVC/10bit/4:2:0/bt2020@23465 kBit, 23.976 fps', text)
        self.assertIn('Level: 6, Profile: 8, BL: 1 (compat ID: 1), EL: 0, RPU: 1', text)
        self.assertNotIn('Version: ,', text)
        self.assertIn('Audio: English (default), TRUEHD/8ch@5200 kBit, 48000 Hz', text)
        self.assertIn('Audio: English, AC3/2ch@640 kBit, 48000 Hz', text)
        self.assertIn('Subtitles: English (SUBRIP), French (HDMV_PGS_SUBTITLE)', text)
        self.assertIn('Chapters: Opening, Oklahoma, The Chase', text)

        # When DvVersionMajor is present, Version is formatted (not empty)
        with_ver = copy.deepcopy(MOVIE_DETAIL)
        with_ver['MediaSources'][0]['MediaStreams'][0]['DvVersionMajor'] = 1
        with_ver['MediaSources'][0]['MediaStreams'][0]['DvVersionMinor'] = 0
        ver_movie = make_tile(make_server(FakeSession(FakeResponse(200, with_ver))), with_ver).reload()
        self.assertIn('Version: 1.0, ', info.formatMediaDetails(ver_movie))

        # Movie without Dolby Vision has no DoVi line
        sdr = copy.deepcopy(MOVIE_DETAIL)
        v_stream = sdr['MediaSources'][0]['MediaStreams'][0]
        for key in (
            'DvProfile',
            'DvLevel',
            'BlPresentFlag',
            'ElPresentFlag',
            'RpuPresentFlag',
            'DvBlSignalCompatibilityId',
        ):
            v_stream.pop(key, None)
        v_stream['VideoRangeType'] = 'SDR'
        v_stream['ColorTransfer'] = 'bt709'
        sdr_movie = make_tile(make_server(FakeSession(FakeResponse(200, sdr))), sdr).reload()
        self.assertNotIn('DoVi', info.formatMediaDetails(sdr_movie))
