"""
Track choices, external subtitles, subtitle search/download (D14), Stream Info, local/remote detection,
and path mapping against Jellyfin 10.11.5 (Task 5.3).

Responses are written inline in the shape of Jellyfin 10.11.5 OpenAPI:
- MediaStream / DeliveryUrl and /Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/Stream.{format}
- RemoteSubtitleInfo via GET/POST /Items/{itemId}/RemoteSearch/Subtitles/...
- VirtualFolderInfo via GET /Library/VirtualFolders
- PlaybackInfoResponse / TranscodingUrl with TranscodeReasons
"""

import copy
import os
import shutil
import tempfile
from unittest import mock

from kodienv import ENV

ENV.abort_requested = True

from jellynet import detail, trackprefs
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.items import itemElement
from jellynet.server import JellyfinServer
from plexnet import plexlibrary, plexobjects, plexpart, plexplayer, plexstream
from plexnet import video as _plex_video  # noqa: F401 (registers plexnet item types)
from plexnet.videosession import VideoSessionInfo

from lib import path_mapping
from lib.jellyfin import StoredServer
from lib.path_mapping import pmm
from lib.windows import playersettings
from lib.windows.mixins.subtitledl import PlexSubtitleDownloadMixin

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem
from .test_jellynet_detail import MOVIE_DETAIL, episode_detail

LAN_BASE = 'http://192.168.10.20:8096'
REMOTE_BASE = 'https://jellyfin.example.com'
IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')
SECRET_TOKEN = 'STREAM-TEST-TOKEN-999'

USER_VIEWS = FakeResponse(
    200,
    {
        'Items': [
            {'Id': 'lib-movies', 'Name': 'Movies', 'CollectionType': 'movies', 'ImageTags': {}},
            {'Id': 'lib-shows', 'Name': 'Shows', 'CollectionType': 'tvshows', 'ImageTags': {}},
        ],
        'TotalRecordCount': 2,
    },
)

VIRTUAL_FOLDERS_WITH_PROVIDER = FakeResponse(
    200,
    [
        {
            'Name': 'Movies',
            'ItemId': 'lib-movies',
            'CollectionType': 'movies',
            'Locations': ['/data/media/movies'],
            'LibraryOptions': {
                'SubtitleFetcherOrder': ['Open Subtitles'],
                'DisabledSubtitleFetchers': [],
                'SubtitleDownloadLanguages': ['eng'],
            },
        },
        {
            'Name': 'Shows',
            'ItemId': 'lib-shows',
            'CollectionType': 'tvshows',
            'Locations': ['/data/media/tv', '/data/media/anime/'],
            'LibraryOptions': {
                'SubtitleFetcherOrder': ['Open Subtitles'],
                'DisabledSubtitleFetchers': [],
            },
        },
    ],
)

VIRTUAL_FOLDERS_NO_PROVIDER = FakeResponse(
    200,
    [
        {
            'Name': 'Movies',
            'ItemId': 'lib-movies',
            'CollectionType': 'movies',
            'Locations': ['/data/media/movies'],
            'LibraryOptions': {
                'SubtitleFetcherOrder': [],
                'DisabledSubtitleFetchers': [],
            },
        }
    ],
)

VIRTUAL_FOLDERS_DISABLED_PROVIDER = FakeResponse(
    200,
    [
        {
            'Name': 'Movies',
            'ItemId': 'lib-movies',
            'CollectionType': 'movies',
            'Locations': ['/data/media/movies'],
            'LibraryOptions': {
                'SubtitleFetcherOrder': ['Open Subtitles'],
                'DisabledSubtitleFetchers': ['Open Subtitles'],
            },
        }
    ],
)

REMOTE_SUB_HASH = {
    'Id': 'sub-hash-1',
    'ProviderName': 'Open Subtitles',
    'Name': 'Twisters.2024.2160p.WEB-DL.srt',
    'Format': 'srt',
    'ThreeLetterISOLanguageName': 'eng',
    'CommunityRating': 8.5,
    'DownloadCount': 420,
    'IsHashMatch': True,
    'Forced': False,
    'HearingImpaired': False,
}

REMOTE_SUB_HI = {
    'Id': 'sub-hi-2',
    'ProviderName': 'Open Subtitles',
    'Name': 'Twisters.2024.SDH.srt',
    'Format': 'srt',
    'ThreeLetterISOLanguageName': 'eng',
    'CommunityRating': 7.2,
    'DownloadCount': 95,
    'IsHashMatch': False,
    'Forced': False,
    'HearingImpaired': True,
}

REMOTE_SUB_FORCED = {
    'Id': 'sub-forced-3',
    'ProviderName': 'SubDL',
    'Name': 'Twisters.2024.Forced.ass',
    'Format': 'ass',
    'ThreeLetterISOLanguageName': 'eng',
    'CommunityRating': 9.0,
    'DownloadCount': 50,
    'IsHashMatch': False,
    'Forced': True,
    'HearingImpaired': False,
}


def make_server(session, base_url=LAN_BASE, name='Home Jellyfin', token=SECRET_TOKEN):
    client = JellyfinClient(base_url, IDENTITY, token=token, username='alice', session=session)
    return JellyfinServer(StoredServer(base_url, 'srv-1', name), client)


def make_tile(srv, dto):
    return plexobjects.buildItem(srv, itemElement(dto), '/hubs')


def playback_info(source_overrides=None, play_session_id='ps-1'):
    source = copy.deepcopy(MOVIE_DETAIL['MediaSources'][0])
    source.update(
        {
            'ETag': 'etag-4k',
            'SupportsDirectPlay': True,
            'SupportsDirectStream': True,
            'SupportsTranscoding': True,
        }
    )
    if source_overrides:
        source.update(source_overrides)
    return FakeResponse(200, {'MediaSources': [source], 'PlaySessionId': play_session_id})


def multi_track_episode(ep_id, ep_num, title, with_external_en=False):
    ep = episode_detail(ep_id, 'sea-1', 1, ep_num, title)
    streams = [
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
            'Codec': 'truehd',
            'Channels': 8,
            'Language': 'eng',
            'Title': 'Main English',
            'IsDefault': True,
        },
        {
            'Type': 'Audio',
            'Index': 2,
            'Codec': 'ac3',
            'Channels': 2,
            'Language': 'eng',
            'Title': 'Commentary',
            'IsDefault': False,
        },
        {
            'Type': 'Audio',
            'Index': 3,
            'Codec': 'aac',
            'Channels': 2,
            'Language': 'jpn',
            'Title': 'Japanese',
            'IsDefault': False,
        },
        {
            'Type': 'Subtitle',
            'Index': 4,
            'Codec': 'subrip',
            'Language': 'eng',
            'Title': 'English Embedded',
            'IsDefault': False,
            'IsForced': False,
            'IsHearingImpaired': False,
            'IsExternal': False,
        },
        {
            'Type': 'Subtitle',
            'Index': 5,
            'Codec': 'subrip',
            'Language': 'fre',
            'Title': 'French Forced',
            'IsDefault': False,
            'IsForced': True,
            'IsHearingImpaired': False,
            'IsExternal': False,
        },
    ]
    if with_external_en:
        streams.append(
            {
                'Type': 'Subtitle',
                'Index': 6,
                'Codec': 'subrip',
                'Language': 'eng',
                'Title': 'English External',
                'IsDefault': False,
                'IsForced': False,
                'IsHearingImpaired': False,
                'IsExternal': True,
                'DeliveryUrl': f'/Videos/{ep_id}/src-{ep_id}/Subtitles/6/Stream.srt',
            }
        )
    ep['MediaSources'][0]['MediaStreams'] = streams
    return ep


class JellyfinExternalSubtitlesTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_external_subtitle_urls(self):
        dto = copy.deepcopy(MOVIE_DETAIL)
        dto['MediaSources'][0]['MediaStreams'].extend(
            [
                {
                    'Type': 'Subtitle',
                    'Index': 5,
                    'Codec': 'subrip',
                    'Language': 'deu',
                    'Title': 'German Relative DeliveryUrl',
                    'IsExternal': True,
                    'DeliveryUrl': '/Videos/mov-1/src-4k/Subtitles/5/etag1/Stream.srt?api_key=LEAKME&startPositionTicks=0',
                },
                {
                    'Type': 'Subtitle',
                    'Index': 6,
                    'Codec': 'webvtt',
                    'Language': 'spa',
                    'Title': 'Spanish Fallback Route',
                    'IsExternal': True,
                },
                {
                    'Type': 'Subtitle',
                    'Index': 7,
                    'Codec': 'ass',
                    'Language': 'jpn',
                    'Title': 'Japanese Absolute DeliveryUrl',
                    'IsExternal': True,
                    'DeliveryUrl': 'https://subs.example.net/mov-1/7.ass',
                },
            ]
        )
        session = FakeSession(FakeResponse(200, dto))
        srv = make_server(session)
        movie = make_tile(srv, dto).reload()

        subs = movie.subtitleStreams
        # Embedded subtitles come before external so embedded typeIndex matches Kodi's order.
        self.assertEqual([s.embedded for s in subs], [True, True, False, False, False])
        self.assertEqual([s.typeIndex for s in subs[:2]], [0, 1])
        self.assertIsNone(subs[0].getSubtitleServerPath())

        rel_url = subs[2].getSubtitleServerPath()
        self.assertEqual(rel_url, f'{LAN_BASE}/Videos/mov-1/src-4k/Subtitles/5/etag1/Stream.srt?startPositionTicks=0')
        self.assertNotIn('api_key', rel_url)
        self.assertNotIn('encoding=utf-8', rel_url)
        self.assertNotIn(SECRET_TOKEN, rel_url)

        fallback_url = subs[3].getSubtitleServerPath()
        self.assertEqual(fallback_url, f'{LAN_BASE}/Videos/mov-1/src-4k/Subtitles/6/Stream.vtt')
        self.assertNotIn('encoding=utf-8', fallback_url)

        abs_url = subs[4].getSubtitleServerPath()
        self.assertEqual(abs_url, 'https://subs.example.net/mov-1/7.ass')
        self.assertNotIn('encoding=utf-8', abs_url)

        # When PlaybackInfo supplies a DeliveryUrl for a stream that lacked one on item detail, buildUrl prefers it.
        pb_source = copy.deepcopy(dto['MediaSources'][0])
        pb_source['MediaStreams'][-2]['DeliveryUrl'] = '/Videos/mov-1/src-4k/Subtitles/6/pb-etag/Stream.vtt'
        session.queue(playback_info({'MediaStreams': pb_source['MediaStreams']}))
        plexplayer.PlexPlayer(movie, 0, forceUpdate=True).build()
        self.assertEqual(
            subs[3].getSubtitleServerPath(),
            f'{LAN_BASE}/Videos/mov-1/src-4k/Subtitles/6/pb-etag/Stream.vtt',
        )

    def test_two_external_files_two_ids(self):
        # Case 1: two external subtitle files without Index (SM4K #49)
        source_no_index = {
            'Id': 'src-ext',
            'Container': 'mkv',
            'MediaStreams': [
                {'Type': 'Video', 'Index': 0, 'Codec': 'hevc', 'Width': 1920, 'Height': 1080},
                {'Type': 'Subtitle', 'Index': 2, 'Codec': 'subrip', 'Language': 'eng', 'IsExternal': False},
                {'Type': 'Subtitle', 'Codec': 'subrip', 'Language': 'deu', 'IsExternal': True},
                {'Type': 'Subtitle', 'Codec': 'ass', 'Language': 'fre', 'IsExternal': True},
            ],
        }
        part = detail.mediaElement('mov-ext', source_no_index).find('Part')
        subs = [s for s in part.iter('Stream') if s.get('streamType') == '3']
        self.assertEqual(len(subs), 3)
        self.assertEqual(len({s.get('id') for s in subs}), 3)
        self.assertIsNone(subs[0].get('key'))
        self.assertEqual(len({subs[1].get('key'), subs[2].get('key')}), 2)
        self.assertTrue(subs[1].get('key').startswith('/Videos/mov-ext/src-ext/Subtitles/'))
        self.assertTrue(subs[2].get('key').startswith('/Videos/mov-ext/src-ext/Subtitles/'))

        # Case 2: two external subtitle files with colliding Index and no DeliveryUrl
        source_dup_index = {
            'Id': 'src-dup',
            'Container': 'mkv',
            'MediaStreams': [
                {'Type': 'Video', 'Index': 0, 'Codec': 'hevc', 'Width': 1920, 'Height': 1080},
                {'Type': 'Subtitle', 'Index': 5, 'Codec': 'subrip', 'Language': 'eng', 'IsExternal': True},
                {'Type': 'Subtitle', 'Index': 5, 'Codec': 'subrip', 'Language': 'spa', 'IsExternal': True},
            ],
        }
        dup_part = detail.mediaElement('mov-dup', source_dup_index).find('Part')
        dup_subs = [s for s in dup_part.iter('Stream') if s.get('streamType') == '3']
        self.assertEqual(len({s.get('id') for s in dup_subs}), 2)
        self.assertEqual(len({s.get('key') for s in dup_subs}), 2)


class JellyfinSubtitleSearchTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_subtitle_search_hidden_without_provider(self):
        for label, vf_response in (
            ('empty_fetchers', VIRTUAL_FOLDERS_NO_PROVIDER),
            ('all_disabled', VIRTUAL_FOLDERS_DISABLED_PROVIDER),
            ('not_admin_without_subtitle_management', problem(403, 'Forbidden')),
        ):
            with self.subTest(case=label):
                me = FakeResponse(200, {'Id': 'u2', 'Policy': {'EnableSubtitleManagement': False}})
                session = FakeSession(FakeResponse(200, MOVIE_DETAIL), vf_response, me)
                srv = make_server(session)
                movie = make_tile(srv, MOVIE_DETAIL).reload()

                self.assertFalse(srv.supportsFeature('subtitleSearch'))
                options, _ = playersettings.subtitleOptions(movie, with_download=True, player=None)
                keys = [opt[0] for opt in options]
                self.assertNotIn(playersettings.DOWNLOAD_SUBTITLES, keys)

                # Calling downloadPlexSubtitles skips /RemoteSearch/Subtitles cleanly.
                mixin = PlexSubtitleDownloadMixin()
                self.assertIsNone(mixin.downloadPlexSubtitles(movie, non_playback=True))
                self.assertFalse(any('/RemoteSearch/Subtitles' in c['url'] for c in session.calls))

        # A user who isn't an administrator can't read /Library/VirtualFolders (which names the providers), but
        # searching needs only Jellyfin's SubtitleManagement policy: Download subtitles follows the user's policy.
        me = FakeResponse(200, {'Id': 'u2', 'Policy': {'EnableSubtitleManagement': True}})
        user_session = FakeSession(FakeResponse(200, MOVIE_DETAIL), problem(403, 'Forbidden'), me)
        user_srv = make_server(user_session)
        user_movie = make_tile(user_srv, MOVIE_DETAIL).reload()
        self.assertTrue(user_srv.supportsFeature('subtitleSearch'))
        options, _ = playersettings.subtitleOptions(user_movie, with_download=True, player=None)
        self.assertIn(playersettings.DOWNLOAD_SUBTITLES, [opt[0] for opt in options])
        self.assertEqual(
            [c['url'] for c in user_session.calls[1:]], [f'{LAN_BASE}/Library/VirtualFolders', f'{LAN_BASE}/Users/Me']
        )

        # When a subtitle fetcher IS enabled, Download subtitles is offered.
        ok_session = FakeSession(FakeResponse(200, MOVIE_DETAIL), VIRTUAL_FOLDERS_WITH_PROVIDER)
        ok_srv = make_server(ok_session)
        ok_movie = make_tile(ok_srv, MOVIE_DETAIL).reload()
        self.assertTrue(ok_srv.supportsFeature('subtitleSearch'))
        options, _ = playersettings.subtitleOptions(ok_movie, with_download=True, player=None)
        self.assertIn(playersettings.DOWNLOAD_SUBTITLES, [opt[0] for opt in options])

    def test_subtitle_search_and_download(self):
        after_download = copy.deepcopy(MOVIE_DETAIL)
        after_download['MediaSources'][0]['MediaStreams'].append(
            {
                'Type': 'Subtitle',
                'Index': 8,
                'Codec': 'subrip',
                'Language': 'eng',
                'Title': 'Twisters.2024.2160p.WEB-DL.srt',
                'DisplayTitle': 'English (External SRT)',
                'IsExternal': True,
                'DeliveryUrl': '/Videos/mov-1/src-4k/Subtitles/8/Stream.srt',
            }
        )
        session = FakeSession(
            FakeResponse(200, MOVIE_DETAIL),
            FakeResponse(200, [REMOTE_SUB_HASH, REMOTE_SUB_HI, REMOTE_SUB_FORCED]),
            FakeResponse(200, [REMOTE_SUB_HASH, REMOTE_SUB_HI, REMOTE_SUB_FORCED]),
            FakeResponse(200, [REMOTE_SUB_HASH, REMOTE_SUB_HI, REMOTE_SUB_FORCED]),
            FakeResponse(204),
            FakeResponse(200, after_download),
        )
        srv = make_server(session)
        movie = make_tile(srv, MOVIE_DETAIL).reload()

        # 2-letter 'en' -> 3-letter 'eng' on Jellyfin
        found = movie.findSubtitles(language='en')
        search_call = session.calls[1]
        self.assertEqual(
            (search_call['method'], search_call['url']), ('GET', f'{LAN_BASE}/Items/mov-1/RemoteSearch/Subtitles/eng')
        )
        self.assertEqual(len(found), 3)
        self.assertEqual(
            (
                found[0].key,
                found[0].providerTitle,
                found[0].title,
                found[0].score.asInt(),
                found[0].downloaded.asBool(),
            ),
            ('sub-hash-1', 'Open Subtitles', 'Twisters.2024.2160p.WEB-DL.srt', 100, False),
        )
        self.assertEqual(found[1].hearingImpaired.asInt(), 1)
        self.assertEqual(found[2].forced.asInt(), 1)

        # Filtering by hearingImpaired=1 and forced=1
        hi_only = movie.findSubtitles(language='eng', hearing_impaired=1)
        self.assertEqual([s.key for s in hi_only], ['sub-hi-2'])
        forced_only = movie.findSubtitles(language='en', forced=1)
        self.assertEqual([s.key for s in forced_only], ['sub-forced-3'])

        # Download POSTs /Items/{id}/RemoteSearch/Subtitles/{subtitleId} and selects the new external stream.
        downloaded = movie.downloadSubtitles(found[0].key)
        dl_call = session.calls[4]
        self.assertEqual(
            (dl_call['method'], dl_call['url']),
            ('POST', f'{LAN_BASE}/Items/mov-1/RemoteSearch/Subtitles/sub-hash-1'),
        )
        self.assertIsNotNone(downloaded)
        self.assertTrue(downloaded.isSelected())
        self.assertEqual(downloaded.getSubtitleServerPath(), f'{LAN_BASE}/Videos/mov-1/src-4k/Subtitles/8/Stream.srt')
        self.assertIn(downloaded, movie.subtitleStreams)

        # Failed search returns [] and failed download returns None without crashing.
        err_session = FakeSession(FakeResponse(200, MOVIE_DETAIL), problem(500, 'Error'), problem(500, 'Error'))
        err_srv = make_server(err_session)
        err_movie = make_tile(err_srv, MOVIE_DETAIL).reload()
        self.assertEqual(err_movie.findSubtitles(language='en'), [])
        self.assertIsNone(err_movie.downloadSubtitles('sub-missing'))


class JellyfinStreamInfoTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def _panel(self, source_overrides=None, base_url=LAN_BASE, movie_dto=None):
        dto = copy.deepcopy(movie_dto or MOVIE_DETAIL)
        session = FakeSession(FakeResponse(200, dto), playback_info(source_overrides))
        srv = make_server(session, base_url=base_url)
        movie = make_tile(srv, dto).reload()
        plexplayer.PlexPlayer(movie, 0).build()
        found = srv.findVideoSession('player-session-1', movie.ratingKey)
        info = VideoSessionInfo(found, movie, srv.anyLANConnection)
        return {name: line.value for name, line in info.attributes.items()}

    def test_transcode_reasons_line(self):
        transcode_url = (
            '/Videos/mov-1/master.m3u8?MediaSourceId=src-4k&PlaySessionId=ps-tc'
            '&VideoCodec=h264&AudioCodec=aac&VideoBitrate=3608000&AudioBitrate=384000'
            '&AudioChannels=2&MaxWidth=1280&MaxHeight=720'
            '&TranscodeReasons=VideoBitrateNotSupported,AudioCodecNotSupported'
        )

        # Direct Play panel
        dp_lines = self._panel()
        self.assertEqual(dp_lines['Mode'], 'directplay, lan (verified)')
        self.assertEqual(dp_lines['Container'], 'mkv')
        self.assertEqual(dp_lines['Video'], 'hevc, 4k, 23465kbit, directplay')
        self.assertEqual(dp_lines['Audio'], 'truehd, 5200kbit, 8ch, directplay')

        # Direct Stream (remux) panel
        remux_lines = self._panel(
            {
                'SupportsDirectPlay': False,
                'SupportsDirectStream': True,
                'SupportsTranscoding': False,
                'TranscodingContainer': 'ts',
                'TranscodingSubProtocol': 'hls',
                'TranscodingUrl': (
                    '/Videos/mov-1/master.m3u8?MediaSourceId=src-4k&PlaySessionId=ps-rmx'
                    '&TranscodeReasons=ContainerNotSupported'
                ),
            }
        )
        self.assertEqual(remux_lines['Mode'], 'remux, container not supported, lan (verified)')
        self.assertEqual(remux_lines['Container'], 'mkv->hls')
        self.assertEqual(remux_lines['Video'], 'hevc, 4k, 23465kbit, copy')
        self.assertEqual(remux_lines['Audio'], 'truehd, 5200kbit, 8ch, copy')

        # Transcode panel with TranscodeReasons parsed into Mode line
        tc_lines = self._panel(
            {
                'SupportsDirectPlay': False,
                'SupportsDirectStream': False,
                'SupportsTranscoding': True,
                'TranscodingContainer': 'ts',
                'TranscodingSubProtocol': 'hls',
                'TranscodingUrl': transcode_url,
            }
        )
        self.assertEqual(
            tc_lines['Mode'],
            'transcode, video bitrate not supported, audio codec not supported, lan (verified)',
        )
        self.assertEqual(tc_lines['Container'], 'mkv->hls')
        self.assertEqual(tc_lines['Video'], 'hevc->h264, 4k->720p, 23465->3608kbit, transcode')
        self.assertEqual(tc_lines['Audio'], 'truehd->aac, 5200->384kbit, 8->2ch, transcode')

        # Audio-only transcode keeps video copied
        audio_only_lines = self._panel(
            {
                'SupportsDirectPlay': False,
                'SupportsDirectStream': False,
                'SupportsTranscoding': True,
                'TranscodingContainer': 'ts',
                'TranscodingSubProtocol': 'hls',
                'TranscodingUrl': (
                    '/Videos/mov-1/master.m3u8?MediaSourceId=src-4k&VideoCodec=copy'
                    '&AudioCodec=eac3&AudioChannels=6&TranscodeReasons=AudioCodecNotSupported'
                ),
            }
        )
        self.assertEqual(audio_only_lines['Mode'], 'transcode, audio codec not supported, lan (verified)')
        self.assertEqual(audio_only_lines['Video'], 'hevc, 4k, 23465kbit, copy')
        self.assertEqual(audio_only_lines['Audio'], 'truehd->eac3, 5200->?kbit, 8->6ch, transcode')

        # Burn-in subtitle transcode reports burn on Subtitles line
        burn_dto = copy.deepcopy(MOVIE_DETAIL)
        burn_dto['MediaSources'][0]['DefaultSubtitleStreamIndex'] = 3
        burn_lines = self._panel(
            {
                'SupportsDirectPlay': False,
                'SupportsDirectStream': False,
                'SupportsTranscoding': True,
                'TranscodingContainer': 'ts',
                'TranscodingSubProtocol': 'hls',
                'TranscodingUrl': (
                    '/Videos/mov-1/master.m3u8?MediaSourceId=src-4k&VideoCodec=h264'
                    '&SubtitleMethod=Encode&TranscodeReasons=SubtitleCodecNotSupported'
                ),
            },
            movie_dto=burn_dto,
        )
        self.assertEqual(burn_lines['Mode'], 'transcode, subtitle codec not supported, lan (verified)')
        self.assertIn('burn', burn_lines['Subtitles'])

    def test_connection_local_or_remote(self):
        local_urls = (
            'http://127.0.0.1:8096',
            'http://[::1]:8096',
            'http://localhost:8096',
            'http://10.0.0.5:8096',
            'http://172.16.4.2:8096',
            'http://172.31.255.1:8096',
            'http://192.168.10.20:8096',
            'http://169.254.1.10:8096',
            'http://[fd12:3456::1]:8096',
            'http://[fe80::1]:8096',
            'http://jellyfin.local:8096',
        )
        for url in local_urls:
            with self.subTest(local=url):
                srv = make_server(FakeSession(), base_url=url)
                self.assertTrue(srv.isLocalConnection())
                self.assertTrue(srv.anyLANConnection)

        remote_urls = (
            'https://jellyfin.example.com',
            'http://8.8.8.8:8096',
            'http://203.0.113.5:8096',
            'http://100.64.0.5:8096',
            'http://172.32.0.1:8096',
            'https://[2606:4700::1]:8920',
            '',
        )
        for url in remote_urls:
            with self.subTest(remote=url):
                srv = make_server(FakeSession(), base_url=url)
                self.assertFalse(srv.isLocalConnection())
                self.assertFalse(srv.anyLANConnection)

        # Panel Mode reflects local vs remote end-to-end
        self.assertEqual(self._panel(base_url=LAN_BASE)['Mode'], 'directplay, lan (verified)')
        self.assertEqual(self._panel(base_url=REMOTE_BASE)['Mode'], 'directplay, remote')


class JellyfinPathMappingTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()
        self.share = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.share)
        self.root = self.share + os.sep
        self.server_root = '/data/media/movies/'
        for patch in (
            mock.patch.object(pmm, 'PATH_MAP', {'Home Jellyfin': {self.root: self.server_root}}),
            mock.patch.object(pmm, 'BROKEN_MAP', {}),
            mock.patch.object(pmm, 'NOTIFIED', set()),
            mock.patch.object(plexpart.addonSettings, 'verifyMappedFiles', True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _create_mapped_file(self):
        folder = os.path.join(self.share, 'Twisters (2024)')
        os.makedirs(folder, exist_ok=True)
        local = os.path.join(folder, 'Twisters (2024).mkv')
        with open(local, 'w', encoding='utf-8'):
            pass
        return local

    def test_path_mapping_rewrites_direct_play(self):
        # Library locations come from GET /Library/VirtualFolders (fetched once)
        loc_session = FakeSession(USER_VIEWS, VIRTUAL_FOLDERS_WITH_PROVIDER)
        movies_sec, shows_sec = plexlibrary.Library(None, server=make_server(loc_session)).sections()
        self.assertEqual(movies_sec.locations, ['/data/media/movies/'])
        self.assertEqual(shows_sec.locations, ['/data/media/tv/', '/data/media/anime/'])
        self.assertEqual(loc_session.calls[1]['url'], f'{LAN_BASE}/Library/VirtualFolders')
        self.assertEqual(len(loc_session.calls), 2)

        # Direct play rewrites MediaSource.Path through path_mapping.json
        local = self._create_mapped_file()
        dp_session = FakeSession(FakeResponse(200, MOVIE_DETAIL), playback_info())
        dp_movie = make_tile(make_server(dp_session), MOVIE_DETAIL).reload()
        self.assertEqual(dp_movie.media[0].parts[0].getPathMappedUrl(), local)

        meta = plexplayer.PlexPlayer(dp_movie, 0).build()
        self.assertEqual((meta.streamUrls, meta.isMapped, meta.isRequestToServer), ([local], True, False))
        self.assertTrue(any(c['url'].endswith('/Items/mov-1/PlaybackInfo') for c in dp_session.calls))

        # Transcode NEVER rewrites to local path
        tc_session = FakeSession(
            FakeResponse(200, MOVIE_DETAIL),
            playback_info(
                {
                    'SupportsDirectPlay': False,
                    'SupportsDirectStream': False,
                    'SupportsTranscoding': True,
                    'TranscodingUrl': '/Videos/mov-1/master.m3u8?MediaSourceId=src-4k&PlaySessionId=ps-tc',
                }
            ),
        )
        tc_movie = make_tile(make_server(tc_session), MOVIE_DETAIL).reload()
        tc_meta = plexplayer.PlexPlayer(tc_movie, 0).build()
        self.assertFalse(tc_meta.isMapped)
        self.assertTrue(tc_meta.streamUrls[0].startswith(f'{LAN_BASE}/Videos/mov-1/master.m3u8'))

        # Missing mapped file falls back to Jellyfin stream and notifies
        os.remove(local)
        fb_session = FakeSession(FakeResponse(200, MOVIE_DETAIL), playback_info())
        fb_movie = make_tile(make_server(fb_session), MOVIE_DETAIL).reload()
        with mock.patch.object(path_mapping, 'showNotification') as notice:
            fb_meta = plexplayer.PlexPlayer(fb_movie, 0).build()
        self.assertFalse(fb_meta.isMapped)
        self.assertTrue(fb_meta.streamUrls[0].startswith(f'{LAN_BASE}/Videos/mov-1/stream'))
        notice.assert_called_once()

    def test_library_locations_forbidden_is_empty(self):
        # Non-admin 403 returns [] cleanly and caches so it doesn't keep spamming 403
        forbidden_session = FakeSession(USER_VIEWS, problem(403, 'Forbidden'))
        srv = make_server(forbidden_session)
        movies_sec = plexlibrary.Library(None, server=srv).sections()[0]

        self.assertEqual(movies_sec.locations, [])
        self.assertEqual(srv.libraryLocations('lib-movies'), [])
        self.assertEqual(len(forbidden_session.calls), 2)

        # Transient error (503) returns [] cleanly and retries next time
        retry_session = FakeSession(problem(503, 'Service Unavailable'), VIRTUAL_FOLDERS_WITH_PROVIDER)
        retry_srv = make_server(retry_session)
        self.assertEqual(retry_srv.libraryLocations('lib-movies'), [])
        self.assertEqual(retry_srv.libraryLocations('lib-movies'), ['/data/media/movies'])


class JellyfinTrackPrefsTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def _selected(self, item, kind):
        return [s.title for s in item.media[0].parts[0].streams if s.streamType.asInt() == kind and s.isSelected()]

    def test_remembered_audio_and_subtitle_across_episodes(self):
        ep1_dto = multi_track_episode('ep-1', 1, 'Pilot')
        ep2_dto = multi_track_episode('ep-2', 2, 'Second')
        ep3_dto = multi_track_episode('ep-3', 3, 'Third')
        # ep3 puts Japanese at index 1 instead of 3 to verify language matching across reordered tracks
        ep3_streams = ep3_dto['MediaSources'][0]['MediaStreams']
        ep3_streams[1], ep3_streams[3] = ep3_streams[3], ep3_streams[1]

        session = FakeSession(
            FakeResponse(200, ep1_dto),
            FakeResponse(200, ep2_dto),
            FakeResponse(200, ep3_dto),
            FakeResponse(200, ep2_dto),
        )
        srv = make_server(session)

        # Load episode 1 -> default audio is Main English, no subtitles selected
        ep1 = make_tile(srv, ep1_dto).reload()
        self.assertEqual(self._selected(ep1, 2), ['Main English'])
        self.assertEqual(self._selected(ep1, 3), [])

        # Choose Commentary audio and French Forced subtitle on ep1 -> remembered for ser-1
        ep1_audios = [s for s in ep1.media[0].parts[0].streams if s.streamType.asInt() == 2]
        ep1_subs = [s for s in ep1.media[0].parts[0].streams if s.streamType.asInt() == 3]
        ep1.selectStream(ep1_audios[1], _async=False)
        ep1.selectStream(ep1_subs[1], _async=False)

        # Loading ep2 applies both remembered preferences
        ep2 = make_tile(srv, ep2_dto).reload()
        self.assertEqual(self._selected(ep2, 2), ['Commentary'])
        self.assertEqual(self._selected(ep2, 3), ['French Forced'])

        # Switching to Japanese audio on ep2 matches Japanese on ep3 even when track order differs
        ep2_audios = [s for s in ep2.media[0].parts[0].streams if s.streamType.asInt() == 2]
        ep2.selectStream(ep2_audios[2], _async=False)
        ep3 = make_tile(srv, ep3_dto).reload()
        self.assertEqual(self._selected(ep3, 2), ['Japanese'])

        # Turning subtitles off on ep3 remembers 'off' on the next load even if server marks a default subtitle
        ep2_with_default_sub = copy.deepcopy(ep2_dto)
        ep2_with_default_sub['MediaSources'][0]['DefaultSubtitleStreamIndex'] = 4
        session.responses[-1] = FakeResponse(200, ep2_with_default_sub)
        ep3.selectStream(plexstream.NoneStream(), _async=False)
        ep2_again = make_tile(srv, ep2_with_default_sub).reload()
        self.assertEqual(self._selected(ep2_again, 3), [])

        # Movie choices are never kept as show preferences
        mov_session = FakeSession(FakeResponse(200, MOVIE_DETAIL))
        mov_srv = make_server(mov_session)
        movie = make_tile(mov_srv, MOVIE_DETAIL).reload()
        mov_audios = [s for s in movie.media[0].parts[0].streams if s.streamType.asInt() == 2]
        movie.selectStream(mov_audios[1], _async=False)
        self.assertEqual(mov_srv.trackPrefs._by_series, {})

    def test_external_subtitle_remembered_as_external(self):
        ext_stream = plexstream.PlexStream(
            detail.element(
                'Stream', {'streamType': 3, 'id': 'src-1:6', 'key': '/Videos/ep-1/src-1/Subtitles/6/Stream.srt'}
            )
        )
        emb_stream = plexstream.PlexStream(detail.element('Stream', {'streamType': 3, 'id': 'src-1:4', 'index': 4}))

        self.assertEqual(
            (
                trackprefs.subtitleUpdate(ext_stream, 1)['track_signature']['source'],
                trackprefs.subtitleUpdate(emb_stream, 0)['track_signature']['source'],
            ),
            ('external', 'embedded'),
        )

        # Remembering external English on ep1 picks external English (not embedded English) on ep2
        ep1_dto = multi_track_episode('ep-1', 1, 'Pilot', with_external_en=True)
        ep2_dto = multi_track_episode('ep-2', 2, 'Second', with_external_en=True)
        session = FakeSession(FakeResponse(200, ep1_dto), FakeResponse(200, ep2_dto))
        srv = make_server(session)

        ep1 = make_tile(srv, ep1_dto).reload()
        ext_en = next(s for s in ep1.subtitleStreams if not s.embedded and s.languageCode == 'eng')
        ep1.selectStream(ext_en, _async=False)

        ep2 = make_tile(srv, ep2_dto).reload()
        selected_subs = [s for s in ep2.subtitleStreams if s.isSelected()]
        self.assertEqual(len(selected_subs), 1)
        self.assertFalse(selected_subs[0].embedded)
        self.assertEqual(selected_subs[0].title, 'English External')

    def test_playback_info_defaults_used_when_no_override(self):
        # When PlaybackInfo returns DefaultAudioStreamIndex / DefaultSubtitleStreamIndex, session uses them;
        # when PlaybackInfo omits them, series remembered preference fills in.
        ep1_dto = multi_track_episode('ep-1', 1, 'Pilot')
        ep2_dto = multi_track_episode('ep-2', 2, 'Second')
        pb_with_defaults = FakeResponse(
            200,
            {
                'PlaySessionId': 'ps-def',
                'MediaSources': [
                    {
                        'Id': 'src-ep-1',
                        'Container': 'mkv',
                        'SupportsDirectPlay': True,
                        'DefaultAudioStreamIndex': 2,
                        'DefaultSubtitleStreamIndex': 5,
                        'MediaStreams': ep1_dto['MediaSources'][0]['MediaStreams'],
                    }
                ],
            },
        )
        pb_without_defaults = FakeResponse(
            200,
            {
                'PlaySessionId': 'ps-rem',
                'MediaSources': [
                    {
                        'Id': 'src-ep-2',
                        'Container': 'mkv',
                        'SupportsDirectPlay': True,
                        'MediaStreams': ep2_dto['MediaSources'][0]['MediaStreams'],
                    }
                ],
            },
        )
        session = FakeSession(
            FakeResponse(200, ep1_dto), pb_with_defaults, FakeResponse(200, ep2_dto), pb_without_defaults
        )
        srv = make_server(session)

        ep1 = make_tile(srv, ep1_dto).reload()
        pb1 = srv.startPlayback(ep1, ep1.media[0].parts[0])
        self.assertEqual((pb1.audio_stream_index, pb1.subtitle_stream_index), (2, 5))

        # Remember Japanese (index 3) and English embedded (index 4) on ser-1
        ep1_audios = [s for s in ep1.media[0].parts[0].streams if s.streamType.asInt() == 2]
        ep1_subs = [s for s in ep1.media[0].parts[0].streams if s.streamType.asInt() == 3]
        ep1.selectStream(ep1_audios[2], _async=False)
        ep1.selectStream(ep1_subs[0], _async=False)

        ep2 = make_tile(srv, ep2_dto).reload()
        pb2 = srv.startPlayback(ep2, ep2.media[0].parts[0])
        self.assertEqual((pb2.audio_stream_index, pb2.subtitle_stream_index), (3, 4))
