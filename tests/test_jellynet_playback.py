"""
Playing movies and episodes from Jellyfin (Task 5.1): POST /Items/{itemId}/PlaybackInfo negotiates direct play
(D11) or HLS transcoding when quality is capped (D12), builds Kodi play URLs carrying Jellyfin's MediaBrowser
Authorization header via Kodi's pipe header syntax, and keeps tokens scrubbed from Kodi's log (Review Focus 1).
"""

from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree

from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.errors import JellyfinError
from jellynet.server import JellyfinServer
from kodienv import ENV
from plexnet import plexobjects, plexplayer, serverdecision, util, video

from lib.jellyfin import StoredServer

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem

BASE = 'http://jellyfin.lan:8096'
IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')
SECRET_TOKEN = 'SUPER-SECRET-TOKEN-XYZ'


def make_server(session, base_url=BASE, token=SECRET_TOKEN):
    client = JellyfinClient(base_url, IDENTITY, token=token, username='alice', session=session)
    return JellyfinServer(StoredServer(base_url, 'srv-1', 'Home Jellyfin'), client)


def make_movie(server, item_id='item-1', versions=None, kind='movie'):
    """Build a real plexnet Movie/Episode with Media/Part/Stream elements attached to JellyfinServer."""
    tag = 'Video'
    elem = ElementTree.Element(
        tag,
        {
            'type': kind,
            'ratingKey': item_id,
            'key': f'/library/metadata/{item_id}',
            'title': 'Sample Title',
            'duration': '7200000',
        },
    )
    if versions is None:
        versions = [
            {
                'id': 'src-1',
                'container': 'mkv',
                'videoResolution': '1080',
                'bitrate': '12000',
                'streams': [
                    {
                        'streamType': '1',
                        'id': 'src-1.v0',
                        'index': '0',
                        'codec': 'hevc',
                        'width': '1920',
                        'height': '1080',
                    },
                    {
                        'streamType': '2',
                        'id': 'src-1.a1',
                        'index': '1',
                        'codec': 'dts',
                        'channels': '6',
                        'languageCode': 'eng',
                        'selected': '1',
                    },
                    {
                        'streamType': '3',
                        'id': 'src-1.s2',
                        'index': '2',
                        'codec': 'srt',
                        'languageCode': 'eng',
                    },
                ],
            }
        ]
    for ver in versions:
        media_attrs = {
            'id': ver['id'],
            'duration': '7200000',
            'container': ver.get('container', 'mkv'),
            'videoResolution': ver.get('videoResolution', '1080'),
            'bitrate': ver.get('bitrate', '12000'),
        }
        if ver.get('selected'):
            media_attrs['selected'] = '1'
        media_el = ElementTree.SubElement(elem, 'Media', media_attrs)
        part_el = ElementTree.SubElement(
            media_el,
            'Part',
            {
                'id': ver['id'],
                'key': f'/library/parts/{ver["id"]}/file',
                'duration': '7200000',
                'size': '8000000000',
                'container': ver.get('container', 'mkv'),
                'file': ver.get('file', '/media/movies/sample.mkv'),
            },
        )
        for stream in ver.get('streams', ()):
            ElementTree.SubElement(part_el, 'Stream', stream)

    container_el = ElementTree.Element('MediaContainer', {'size': '1'})
    container_el.append(elem)
    container = plexobjects.PlexContainer(
        container_el, initpath=f'/library/metadata/{item_id}', server=server, address=f'/library/metadata/{item_id}'
    )
    cls = video.Episode if kind == 'episode' else video.Movie
    return cls(elem, initpath=f'/library/metadata/{item_id}', server=server, container=container)


def playback_info_response(
    sources=None,
    play_session_id='ps-1',
    error_code=None,
):
    if sources is None:
        sources = [
            {
                'Id': 'src-1',
                'Container': 'mkv',
                'Path': '/media/movies/sample.mkv',
                'ETag': 'etag-1',
                'Bitrate': 12_000_000,
                'SupportsDirectPlay': True,
                'SupportsDirectStream': True,
                'SupportsTranscoding': True,
                'DefaultAudioStreamIndex': 1,
                'MediaStreams': [],
            }
        ]
    body = {'MediaSources': sources, 'PlaySessionId': play_session_id}
    if error_code is not None:
        body['ErrorCode'] = error_code
    return FakeResponse(200, body)


class JellyfinPlaybackTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_direct_play_url(self):
        session = FakeSession(playback_info_response())
        srv = make_server(session)
        movie = make_movie(srv)

        player = plexplayer.PlexPlayer(movie, 0)
        player.build()

        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        self.assertEqual(call['method'], 'POST')
        self.assertEqual(call['url'], f'{BASE}/Items/item-1/PlaybackInfo')
        self.assertIn('Authorization', call['headers'])
        self.assertTrue(call['headers']['Authorization'].startswith('MediaBrowser '))

        dto = call['json']
        self.assertTrue(dto['EnableDirectPlay'])
        self.assertTrue(dto['EnableDirectStream'])
        self.assertTrue(dto['EnableTranscoding'])
        self.assertTrue(dto['AutoOpenLiveStream'])
        self.assertEqual(dto['MediaSourceId'], 'src-1')
        self.assertEqual(dto['AudioStreamIndex'], 1)
        self.assertEqual(dto['StartTimeTicks'], 0)
        self.assertNotIn('MaxStreamingBitrate', dto)

        profile = dto['DeviceProfile']
        dp_profiles = profile['DirectPlayProfiles']
        self.assertTrue(dp_profiles)
        containers = set(dp_profiles[0]['Container'].split(','))
        self.assertTrue(
            {'mkv', 'mp4', 'mov', 'avi', 'ts', 'm2ts', 'webm', 'asf', 'wmv', 'flv', 'mpegts', 'mpeg'} <= containers
        )
        self.assertTrue({'h264', 'hevc', 'av1', 'vp9'} <= set(dp_profiles[0]['VideoCodec'].split(',')))
        self.assertTrue({'aac', 'ac3', 'eac3', 'dts', 'truehd', 'flac'} <= set(dp_profiles[0]['AudioCodec'].split(',')))
        tc_profiles = profile['TranscodingProfiles']
        self.assertTrue(
            any(p['Protocol'] == 'hls' and p['Container'] == 'ts' and 'h264' in p['VideoCodec'] for p in tc_profiles)
        )
        sub_methods = {p['Method'] for p in profile['SubtitleProfiles']}
        self.assertTrue({'External', 'Embed', 'Encode'} <= sub_methods)

        meta = player.metadata
        self.assertFalse(meta.isTranscoded)
        self.assertEqual(meta.playStart, 0)
        self.assertIs(player.getServerDecision(), player)
        self.assertEqual(srv.fileId(movie), 'src-1')

        play_url = meta.streamUrls[0]
        base_part, sep, header_part = play_url.partition('|')
        self.assertEqual(sep, '|')
        parsed = urlsplit(base_part)
        self.assertEqual(f'{parsed.scheme}://{parsed.netloc}{parsed.path}', f'{BASE}/Videos/item-1/stream')
        qs = parse_qs(parsed.query)
        self.assertEqual(qs.get('static'), ['true'])
        self.assertEqual(qs.get('mediaSourceId'), ['src-1'])
        self.assertEqual(qs.get('playSessionId'), ['ps-1'])
        self.assertEqual(qs.get('tag'), ['etag-1'])
        self.assertNotIn('api_key', parsed.query.lower())

        self.assertTrue(header_part.startswith('Authorization='))
        encoded_auth = header_part[len('Authorization=') :]
        self.assertNotIn('+', encoded_auth)
        self.assertIn('%20', encoded_auth)
        self.assertEqual(unquote(encoded_auth), srv.client.auth_header())

    def test_transcode_when_quality_capped(self):
        transcode_source = {
            'Id': 'src-1',
            'Container': 'mkv',
            'TranscodingContainer': 'ts',
            'TranscodingSubProtocol': 'hls',
            'TranscodingUrl': (
                '/Videos/item-1/master.m3u8?MediaSourceId=src-1&PlaySessionId=ps-tc'
                '&VideoCodec=h264&AudioCodec=aac&TranscodeReasons=ContainerBitrateExceedsLimit'
            ),
            'Bitrate': 20_000_000,
            'SupportsDirectPlay': False,
            'SupportsDirectStream': False,
            'SupportsTranscoding': True,
            'MediaStreams': [],
        }
        session = FakeSession(playback_info_response(sources=[transcode_source], play_session_id='ps-tc'))
        srv = make_server(session)
        movie = make_movie(srv)
        settings = type(movie.settings)
        orig_pref = settings.getPreference

        def get_pref(self, name, default=None):
            return True if name == 'disable_hdr' else orig_pref(self, name, default)

        with (
            mock.patch.object(settings, 'getQualityIndex', lambda self, kind: 7),  # 3000 kbps
            mock.patch.object(settings, 'getPreference', get_pref),
        ):
            player = plexplayer.PlexPlayer(movie, 0)
            player.build()

        dto = session.calls[0]['json']
        self.assertEqual(dto['MaxStreamingBitrate'], 3_000_000)
        self.assertEqual(dto['DeviceProfile']['MaxStreamingBitrate'], 3_000_000)
        self.assertTrue(dto['DeviceProfile']['CodecProfiles'])

        meta = player.metadata
        self.assertTrue(meta.isTranscoded)
        base_part, sep, header_part = meta.streamUrls[0].partition('|')
        self.assertEqual(sep, '|')
        self.assertTrue(base_part.startswith(f'{BASE}/Videos/item-1/master.m3u8?'))
        self.assertIn('PlaySessionId=ps-tc', base_part)
        self.assertEqual(unquote(header_part[len('Authorization=') :]), srv.client.auth_header())

    def test_resume_start_ticks(self):
        session = FakeSession(playback_info_response())
        srv = make_server(session)
        episode = make_movie(srv, item_id='ep-42', kind='episode')

        player = plexplayer.PlexPlayer(episode, seekValue=600_000)
        player.build()

        # Default resume_offset is -3500 ms -> 596500 ms = 596.5 s = 5_965_000_000 ticks
        self.assertEqual(session.calls[0]['json']['StartTimeTicks'], 5_965_000_000)
        self.assertEqual(player.metadata.playStart, 596)

    def test_chosen_version_and_tracks_sent(self):
        versions = [
            {
                'id': 'src-1080',
                'container': 'mkv',
                'videoResolution': '1080',
                'bitrate': '8000',
                'streams': [
                    {
                        'streamType': '1',
                        'id': 'src-1080.v0',
                        'index': '0',
                        'codec': 'h264',
                        'width': '1920',
                        'height': '1080',
                    },
                    {
                        'streamType': '2',
                        'id': 'src-1080.a1',
                        'index': '1',
                        'codec': 'aac',
                        'channels': '2',
                        'languageCode': 'eng',
                        'selected': '1',
                    },
                ],
            },
            {
                'id': 'src-4k',
                'container': 'mkv',
                'videoResolution': '4k',
                'bitrate': '45000',
                'selected': True,
                'streams': [
                    {
                        'streamType': '1',
                        'id': 'src-4k.v0',
                        'index': '0',
                        'codec': 'hevc',
                        'width': '3840',
                        'height': '2160',
                    },
                    {
                        'streamType': '2',
                        'id': 'src-4k.a1',
                        'index': '1',
                        'codec': 'truehd',
                        'channels': '8',
                        'languageCode': 'eng',
                    },
                    {
                        'streamType': '2',
                        'id': 'src-4k.a2',
                        'index': '2',
                        'codec': 'ac3',
                        'channels': '6',
                        'languageCode': 'jpn',
                        'selected': '1',
                    },
                    {'streamType': '3', 'id': 'src-4k.s3', 'index': '3', 'codec': 'srt', 'languageCode': 'eng'},
                    {
                        'streamType': '3',
                        'id': 'src-4k.s4',
                        'index': '4',
                        'codec': 'ass',
                        'languageCode': 'eng',
                        'selected': '1',
                    },
                ],
            },
        ]
        sources = [
            {
                'Id': 'src-1080',
                'Container': 'mkv',
                'SupportsDirectPlay': True,
                'SupportsDirectStream': True,
                'SupportsTranscoding': True,
                'MediaStreams': [],
            },
            {
                'Id': 'src-4k',
                'Container': 'mkv',
                'SupportsDirectPlay': True,
                'SupportsDirectStream': True,
                'SupportsTranscoding': True,
                'MediaStreams': [],
            },
        ]
        session = FakeSession(playback_info_response(sources=sources, play_session_id='ps-4k'))
        srv = make_server(session)
        movie = make_movie(srv, versions=versions)

        player = plexplayer.PlexPlayer(movie, 0)
        player.build()

        dto = session.calls[0]['json']
        self.assertEqual(dto['MediaSourceId'], 'src-4k')
        self.assertEqual(dto['AudioStreamIndex'], 2)
        self.assertEqual(dto['SubtitleStreamIndex'], 4)
        self.assertEqual(srv.fileId(movie), 'src-4k')
        self.assertIn('mediaSourceId=src-4k', player.metadata.streamUrls[0])

    def test_play_url_token_not_in_log(self):
        session = FakeSession(playback_info_response())
        srv = make_server(session, token=SECRET_TOKEN)
        movie = make_movie(srv)

        player = plexplayer.PlexPlayer(movie, 0)
        player.build()

        play_url = player.metadata.streamUrls[0]
        self.assertIn(SECRET_TOKEN, play_url)

        # Kodi's log from PlexPlayer.build() and jellynet must never contain the token.
        self.assertTrue(ENV.log_lines)
        self.assertFalse(ENV.logged(SECRET_TOKEN))

        # cleanObjTokens / cleanToken strip Authorization=... (alone or with trailing Kodi headers) and Token="...".
        cleaned_meta = util.cleanObjTokens(dict(player.metadata))
        self.assertNotIn(SECRET_TOKEN, str(cleaned_meta))
        self.assertIn('|Authorization=****', cleaned_meta['streamUrls'][0])

        with_trailing = f'{play_url}&X-Plex-Session-Id=sess-1'
        cleaned_trailing = util.cleanToken(with_trailing)
        self.assertNotIn(SECRET_TOKEN, cleaned_trailing)
        self.assertIn('|Authorization=****&X-Plex-Session-Id=sess-1', cleaned_trailing)

        raw_header = f'MediaBrowser Token="{SECRET_TOKEN}", Client="JellyMod"'
        self.assertNotIn(SECRET_TOKEN, util.cleanToken(raw_header))
        self.assertIn('Token="****"', util.cleanToken(raw_header))

        # Active PlaybackSession repr must also never leak the token.
        active = srv.playback.sessions.get('item-1')
        self.assertIsNotNone(active)
        self.assertNotIn(SECRET_TOKEN, repr(active))

    def test_refused_playback_shows_reason(self):
        cases = (
            (playback_info_response(sources=[], error_code='NotAllowed'), 'not allowed'),
            (playback_info_response(sources=[], error_code='NoCompatibleStream'), 'compatible stream'),
            (playback_info_response(sources=[], error_code='RateLimitExceeded'), 'rate limit'),
            (playback_info_response(sources=[]), 'no playable'),
            (problem(403, 'Forbidden', 'Parental rating blocked'), 'Parental rating blocked'),
        )
        for response, expected_fragment in cases:
            with self.subTest(expected=expected_fragment):
                session = FakeSession(response)
                srv = make_server(session)
                movie = make_movie(srv)

                with self.assertRaises(serverdecision.DecisionFailure) as raised:
                    plexplayer.PlexPlayer(movie, 0).build()

                self.assertIn(expected_fragment.lower(), raised.exception.reason.lower())

        # Direct call to startPlayback raises JellyfinError with clear message too.
        srv = make_server(FakeSession(playback_info_response(sources=[], error_code='NotAllowed')))
        movie = make_movie(srv)
        part = movie.media()[0].parts[0]
        with self.assertRaises(JellyfinError) as err:
            srv.startPlayback(movie, part)
        self.assertIn('not allowed', err.exception.message.lower())

    def test_direct_stream_fallback_without_transcoding_url(self):
        remux_source = {
            'Id': 'src-1',
            'Container': 'mkv',
            'SupportsDirectPlay': False,
            'SupportsDirectStream': True,
            'SupportsTranscoding': False,
            'MediaStreams': [],
        }
        session = FakeSession(playback_info_response(sources=[remux_source], play_session_id='ps-ds'))
        srv = make_server(session)
        movie = make_movie(srv)

        player = plexplayer.PlexPlayer(movie, 0)
        player.build()

        self.assertTrue(player.metadata.isTranscoded)
        base_part, sep, _ = player.metadata.streamUrls[0].partition('|')
        self.assertEqual(sep, '|')
        qs = parse_qs(urlsplit(base_part).query)
        self.assertEqual(qs.get('static'), ['false'])
        self.assertEqual(qs.get('playSessionId'), ['ps-ds'])
        self.assertEqual(srv.playback.sessions['item-1'].play_method, 'DirectStream')
