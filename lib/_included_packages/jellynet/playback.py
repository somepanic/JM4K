"""
Jellyfin playback negotiation: POST /Items/{itemId}/PlaybackInfo with a wide Kodi DeviceProfile (D11)
and optional MaxStreamingBitrate cap (D12). Direct-playable files stream from /Videos/{itemId}/stream;
capped or incompatible items use the server's HLS TranscodingUrl. Stream authentication rides on Kodi's
url|Authorization=... pipe header syntax using Jellyfin's MediaBrowser Authorization header.
"""

from urllib.parse import quote, urlencode

from . import detail, streaminfo, trackprefs
from .client import log
from .errors import JellyfinError

TICKS_PER_SECOND = 10_000_000
TICKS_PER_MILLISECOND = 10_000
DEFAULT_MAX_BITRATE = 1_000_000_000

PLAY_METHOD_DECISIONS = {
    'DirectPlay': 'directplay',
    'DirectStream': 'remux',
    'Transcode': 'transcode',
}

DIRECT_PLAY_CONTAINERS = 'mkv,mp4,mov,m4v,avi,ts,m2ts,webm,asf,wmv,flv,mpegts,mpeg,ogg'
VIDEO_CODECS = 'h264,hevc,av1,vp9,vp8,mpeg2video,mpeg1video,mpeg4,vc1,wmv3,h263,msmpeg4v3,theora'
AUDIO_CODECS = (
    'aac,ac3,eac3,dts,dca,truehd,mlp,flac,alac,opus,vorbis,mp3,mp2,pcm,pcm_s16le,pcm_s24le,pcm_bluray,wmav2,wmapro'
)

SUBTITLE_EXTERNAL_FORMATS = ('srt', 'subrip', 'ass', 'ssa', 'vtt', 'webvtt', 'smi', 'sub')
SUBTITLE_EMBED_FORMATS = (
    'srt',
    'subrip',
    'ass',
    'ssa',
    'pgs',
    'pgssub',
    'dvdsub',
    'vobsub',
    'vtt',
    'webvtt',
    'mov_text',
    'tx3g',
    'dvbsub',
    'smi',
)
SUBTITLE_ENCODE_FORMATS = ('pgs', 'pgssub', 'dvdsub', 'vobsub', 'ass', 'ssa', 'srt', 'subrip')

ERROR_MESSAGES = {
    'NotAllowed': 'You are not allowed to play this item.',
    'NoCompatibleStream': 'No compatible stream is available for this item.',
    'RateLimitExceeded': 'Playback rate limit exceeded. Try again later.',
}


def seconds_to_ticks(seconds):
    """Convert seconds to Jellyfin ticks (10,000,000 ticks/second)."""
    return round(max(0.0, float(seconds or 0.0)) * TICKS_PER_SECOND)


def ms_to_ticks(milliseconds):
    """Convert milliseconds to Jellyfin ticks (10,000 ticks/millisecond)."""
    return round(max(0.0, float(milliseconds or 0.0))) * TICKS_PER_MILLISECOND


def device_profile(max_bitrate=None, hdr=True):
    """Build Jellyfin's DeviceProfile describing Kodi's native direct-play and HLS transcode capabilities."""
    effective_bitrate = int(max_bitrate) if max_bitrate and int(max_bitrate) > 0 else DEFAULT_MAX_BITRATE
    subtitle_profiles = (
        [{'Format': fmt, 'Method': 'External'} for fmt in SUBTITLE_EXTERNAL_FORMATS]
        + [{'Format': fmt, 'Method': 'Embed'} for fmt in SUBTITLE_EMBED_FORMATS]
        + [{'Format': fmt, 'Method': 'Encode'} for fmt in SUBTITLE_ENCODE_FORMATS]
    )
    codec_profiles = []
    if not hdr:
        codec_profiles.append(
            {
                'Type': 'Video',
                'Conditions': [
                    {
                        'Condition': 'EqualsAny',
                        'Property': 'VideoRangeType',
                        'Value': 'SDR',
                        'IsRequired': False,
                    }
                ],
            }
        )
    return {
        'Name': 'JellyMod (Kodi)',
        'MaxStreamingBitrate': effective_bitrate,
        'MaxStaticBitrate': effective_bitrate,
        'DirectPlayProfiles': [
            {
                'Type': 'Video',
                'Container': DIRECT_PLAY_CONTAINERS,
                'VideoCodec': VIDEO_CODECS,
                'AudioCodec': AUDIO_CODECS,
            }
        ],
        'TranscodingProfiles': [
            {
                'Container': 'ts',
                'Type': 'Video',
                'VideoCodec': 'h264',
                'AudioCodec': 'aac,ac3,eac3',
                'Protocol': 'hls',
                'Context': 'Streaming',
                'TranscodeSeekInfo': 'Auto',
                'CopyTimestamps': True,
                'EnableSubtitlesInManifest': False,
                'BreakOnNonKeyFrames': True,
            }
        ],
        'ContainerProfiles': [],
        'CodecProfiles': codec_profiles,
        'SubtitleProfiles': subtitle_profiles,
    }


def playback_info_body(
    media_source_id=None,
    start_seconds=0.0,
    audio_index=None,
    subtitle_index=None,
    max_bitrate=None,
    hdr=True,
    user_id=None,
):
    """Build the PlaybackInfoDto JSON body for POST /Items/{itemId}/PlaybackInfo."""
    body = {
        'DeviceProfile': device_profile(max_bitrate=max_bitrate, hdr=hdr),
        'StartTimeTicks': seconds_to_ticks(start_seconds),
        'EnableDirectPlay': True,
        'EnableDirectStream': True,
        'EnableTranscoding': True,
        'AllowVideoStreamCopy': True,
        'AllowAudioStreamCopy': True,
        'AutoOpenLiveStream': True,
    }
    if user_id:
        body['UserId'] = str(user_id)
    if media_source_id:
        body['MediaSourceId'] = str(media_source_id)
    if audio_index is not None:
        body['AudioStreamIndex'] = int(audio_index)
    if subtitle_index is not None:
        body['SubtitleStreamIndex'] = int(subtitle_index)
    if max_bitrate and int(max_bitrate) > 0:
        body['MaxStreamingBitrate'] = int(max_bitrate)
    return body


def _join_url(base_url, path):
    if '://' in path:
        return path
    clean = path if path.startswith('/') else '/' + path
    return base_url.rstrip('/') + clean


def direct_stream_url(base_url, item_id, media_source_id, play_session_id, static=True, etag=None):
    """Build Jellyfin's /Videos/{itemId}/stream URL without embedding credentials in the query string."""
    params = {
        'static': 'true' if static else 'false',
        'mediaSourceId': str(media_source_id),
        'playSessionId': str(play_session_id),
    }
    if etag:
        params['tag'] = str(etag)
    route = f'/Videos/{quote(str(item_id), safe="")}/stream'
    return f'{base_url.rstrip("/")}{route}?{urlencode(params)}'


def attach_auth_header(url, auth_header, extra_headers=None):
    """Attach HTTP headers to a Kodi stream URL using Kodi's url|Header=value syntax (percent-encoded with %20)."""
    if not url:
        return url
    headers = []
    if auth_header:
        headers.append(f'Authorization={quote(str(auth_header), safe="")}')
    if isinstance(extra_headers, dict):
        for key, value in extra_headers.items():
            if str(key).lower() == 'authorization' or value is None:
                continue
            headers.append(f'{quote(str(key), safe="")}={quote(str(value), safe="")}')
    if not headers:
        return url
    separator = '&' if '|' in url else '|'
    return f'{url}{separator}{"&".join(headers)}'


def _select_media_source(sources, media_source_id):
    if media_source_id is not None:
        wanted = str(media_source_id)
        for source in sources:
            if isinstance(source, dict) and str(source.get('Id') or '') == wanted:
                return source
    return sources[0]


class PlaybackSession:
    """One active Jellyfin playback session and its negotiated stream URL and metadata."""

    def __init__(
        self,
        client,
        item_id,
        media_source_id,
        play_session_id,
        play_method,
        url,
        stream_url,
        media_source,
        container='',
        audio_stream_index=None,
        subtitle_stream_index=None,
        start_seconds=0.0,
        video_stream_id=None,
        audio_stream_id=None,
        subtitle_stream_id=None,
    ):
        self.client = client
        self.item_id = str(item_id)
        self.media_source_id = str(media_source_id)
        self.play_session_id = str(play_session_id)
        self.play_method = play_method
        self.url = url
        self.stream_url = stream_url
        self.media_source = media_source
        self.container = container
        self.audio_stream_index = audio_stream_index
        self.subtitle_stream_index = subtitle_stream_index
        self.video_stream_id = str(video_stream_id) if video_stream_id else None
        self.audio_stream_id = str(audio_stream_id) if audio_stream_id else None
        self.subtitle_stream_id = str(subtitle_stream_id) if subtitle_stream_id else None
        self.start_seconds = max(0.0, float(start_seconds or 0.0))
        self.position_ticks = seconds_to_ticks(self.start_seconds)
        self.is_paused = False
        self.started = False
        self.ended = False

    def __repr__(self):
        return f'<PlaybackSession item={self.item_id} method={self.play_method}>'

    def external_subtitle_url(self, stream_index):
        """Return the server-supplied DeliveryUrl for an external subtitle stream on this session, or None."""
        try:
            wanted = int(stream_index)
        except (TypeError, ValueError):
            return None
        source = self.media_source if isinstance(self.media_source, dict) else {}
        for stream in source.get('MediaStreams') or ():
            if not isinstance(stream, dict) or stream.get('Type') != 'Subtitle':
                continue
            if stream.get('Index') == wanted and stream.get('DeliveryUrl'):
                return _join_url(self.client.base_url, detail.cleanDeliveryUrl(stream['DeliveryUrl']))
        return None

    @property
    def is_direct_play(self):
        return self.play_method == 'DirectPlay'

    @property
    def is_transcoded(self):
        return self.play_method != 'DirectPlay'

    def _playing_payload(self):
        payload = {
            'ItemId': self.item_id,
            'MediaSourceId': self.media_source_id,
            'PlaySessionId': self.play_session_id,
            'PositionTicks': self.position_ticks,
            'IsPaused': self.is_paused,
            'PlayMethod': self.play_method,
            'CanSeek': True,
        }
        if self.audio_stream_index is not None:
            payload['AudioStreamIndex'] = self.audio_stream_index
        if self.subtitle_stream_index is not None:
            payload['SubtitleStreamIndex'] = self.subtitle_stream_index
        return payload

    def _post_safe(self, path, *, params=None, json=None):
        try:
            self.client.post(path, params=params, json=json)
        except JellyfinError as e:
            log(f'{path} for {self.item_id} failed: {e.status}')

    def report_start(self, position_ms=None, paused=False):
        """Report playback start via POST /Sessions/Playing; errors are logged and swallowed."""
        if self.ended:
            return
        if position_ms is not None:
            self.position_ticks = ms_to_ticks(position_ms)
        self.is_paused = bool(paused)
        self.started = True
        self._post_safe('/Sessions/Playing', json=self._playing_payload())

    def report_progress(self, position_ms=None, paused=False):
        """Report playback progress via POST /Sessions/Playing/Progress, pinging active transcodes."""
        if self.ended:
            return
        if position_ms is not None:
            self.position_ticks = ms_to_ticks(position_ms)
        self.is_paused = bool(paused)
        self._post_safe('/Sessions/Playing/Progress', json=self._playing_payload())
        if self.play_method == 'Transcode' and self.play_session_id:
            self.ping()

    def ping(self):
        """Keep an active transcoding session alive via POST /Sessions/Playing/Ping."""
        if self.ended or not self.play_session_id:
            return
        self._post_safe('/Sessions/Playing/Ping', params={'playSessionId': self.play_session_id})

    def report_stopped(self, position_ms=None):
        """Report playback stop once via POST /Sessions/Playing/Stopped; errors are logged and swallowed."""
        if self.ended:
            return
        self.ended = True
        if position_ms is not None:
            self.position_ticks = ms_to_ticks(position_ms)
        payload = {
            'ItemId': self.item_id,
            'MediaSourceId': self.media_source_id,
            'PlaySessionId': self.play_session_id,
            'PositionTicks': self.position_ticks,
        }
        self._post_safe('/Sessions/Playing/Stopped', json=payload)


def session_element(session):
    """Build a Plex /status/sessions-shaped <Video> element for an active PlaybackSession."""
    return streaminfo.sessionElement(session)


class Playback:
    """Manages Jellyfin playback negotiation and active playback sessions by item id."""

    def __init__(self, client):
        self.client = client
        self.sessions = {}

    def start(
        self,
        item_id,
        media_source_id=None,
        seconds=0.0,
        audio_index=None,
        subtitle_index=None,
        bandwidth_cap_kbps=None,
        hdr=True,
        user_id=None,
        video_stream_id=None,
        audio_stream_id=None,
        subtitle_stream_id=None,
        fallback_tracks=None,
    ):
        """Negotiate playback via POST /Items/{itemId}/PlaybackInfo and return a PlaybackSession."""
        key = str(item_id)
        max_bitrate = int(bandwidth_cap_kbps) * 1000 if bandwidth_cap_kbps and int(bandwidth_cap_kbps) > 0 else None
        body = playback_info_body(
            media_source_id=media_source_id,
            start_seconds=seconds,
            audio_index=audio_index,
            subtitle_index=subtitle_index,
            max_bitrate=max_bitrate,
            hdr=hdr,
            user_id=user_id,
        )
        route = f'/Items/{quote(key, safe="")}/PlaybackInfo'
        response = self.client.post(route, json=body)
        if not isinstance(response, dict):
            response = {}

        error_code = response.get('ErrorCode')
        if error_code:
            reason = ERROR_MESSAGES.get(str(error_code), f'Playback refused ({error_code}).')
            raise JellyfinError(0, reason)

        sources = [s for s in (response.get('MediaSources') or []) if isinstance(s, dict)]
        if not sources:
            raise JellyfinError(0, 'Server returned no playable media sources.')

        source = _select_media_source(sources, media_source_id)
        source_id = str(source.get('Id') or media_source_id or key)
        play_session_id = str(response.get('PlaySessionId') or '')

        supports_dp = bool(source.get('SupportsDirectPlay'))
        supports_ds = bool(source.get('SupportsDirectStream'))
        supports_tc = bool(source.get('SupportsTranscoding'))
        transcoding_url = source.get('TranscodingUrl') or ''
        source_bitrate = int(source.get('Bitrate') or 0)
        over_cap = bool(max_bitrate and source_bitrate and source_bitrate > max_bitrate)

        if supports_dp and not (over_cap and transcoding_url):
            play_method = 'DirectPlay'
            stream_url = direct_stream_url(
                self.client.base_url,
                key,
                source_id,
                play_session_id,
                static=True,
                etag=source.get('ETag'),
            )
            container = str(source.get('Container') or '')
        elif transcoding_url:
            play_method = 'DirectStream' if (supports_ds and not supports_tc) else 'Transcode'
            stream_url = _join_url(self.client.base_url, detail.cleanDeliveryUrl(transcoding_url))
            container = str(source.get('TranscodingContainer') or source.get('Container') or '')
        elif supports_ds:
            play_method = 'DirectStream'
            stream_url = direct_stream_url(
                self.client.base_url,
                key,
                source_id,
                play_session_id,
                static=False,
                etag=source.get('ETag'),
            )
            container = str(source.get('Container') or '')
        else:
            raise JellyfinError(0, ERROR_MESSAGES['NoCompatibleStream'])

        play_url = attach_auth_header(
            stream_url,
            self.client.auth_header(),
            extra_headers=source.get('RequiredHttpHeaders'),
        )
        fallback_audio, fallback_sub = (None, None)
        if fallback_tracks:
            fallback_audio, fallback_sub = trackprefs.select_media_source_indices(source, *fallback_tracks)

        if audio_index is not None:
            effective_audio = int(audio_index)
        elif source.get('DefaultAudioStreamIndex') is not None:
            effective_audio = int(source['DefaultAudioStreamIndex'])
        else:
            effective_audio = fallback_audio

        raw_default_sub = source.get('DefaultSubtitleStreamIndex')
        if subtitle_index is not None:
            parsed_sub = int(subtitle_index)
            effective_sub = parsed_sub if parsed_sub >= 0 else None
        elif raw_default_sub is not None:
            effective_sub = int(raw_default_sub) if int(raw_default_sub) >= 0 else None
        else:
            effective_sub = fallback_sub

        session = PlaybackSession(
            client=self.client,
            item_id=key,
            media_source_id=source_id,
            play_session_id=play_session_id,
            play_method=play_method,
            url=play_url,
            stream_url=stream_url,
            media_source=source,
            container=container,
            audio_stream_index=effective_audio,
            subtitle_stream_index=effective_sub,
            start_seconds=seconds,
            video_stream_id=video_stream_id,
            audio_stream_id=audio_stream_id,
            subtitle_stream_id=subtitle_stream_id,
        )
        previous = self.sessions.pop(key, None)
        if previous is not None and previous.started and not previous.ended:
            previous.report_stopped()
        self.sessions[key] = session
        log(f'playing {key} via {play_method}')
        return session

    def find_session(self, session_id=None, rating_key=None):
        """Return the active PlaybackSession matching rating_key or play_session_id, else None."""
        if rating_key is not None:
            session = self.sessions.get(str(rating_key))
            if session is not None and not session.ended:
                return session
        if session_id is not None:
            wanted = str(session_id)
            for session in self.sessions.values():
                if not session.ended and session.play_session_id == wanted:
                    return session
        return None

    def session_for(self, media_source_id):
        """Return the latest active PlaybackSession for media_source_id, or None."""
        wanted = str(media_source_id)
        for session in reversed(list(self.sessions.values())):
            if not session.ended and session.media_source_id == wanted:
                return session
        return None

    def timeline(self, rating_key, state, milliseconds):
        """Translate a Plex timeline update into Jellyfin Playing / Progress (+Ping) / Stopped calls."""
        key = str(rating_key)
        session = self.sessions.get(key)
        if session is None:
            return
        if state == 'stopped':
            self.sessions.pop(key, None)
            session.report_stopped(milliseconds)
        elif not session.started:
            session.report_start(milliseconds, paused=state == 'paused')
        else:
            session.report_progress(milliseconds, paused=state == 'paused')
