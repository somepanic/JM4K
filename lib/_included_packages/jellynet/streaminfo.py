"""
Stream Info on Jellyfin: builds the Plex /status/sessions-shaped <Video><Media><Part><Stream/></Part></Media></Video>
element for plexnet.videosession.VideoSessionInfo, parses TranscodeReasons from Jellyfin's TranscodingUrl, formats
play methods, and classifies local vs remote server URLs.
"""

import ipaddress
import re
from urllib.parse import parse_qsl, urlsplit

from . import detail

PLAY_METHOD_DECISIONS = {
    'DirectPlay': 'directplay',
    'DirectStream': 'remux',
    'Transcode': 'transcode',
}

_RFC1918_NETWORKS = (
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
)
_IPV6_ULA_NETWORK = ipaddress.ip_network('fc00::/7')

_CAMEL_BOUNDARY = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')

_AUDIO_REASON_KEYS = frozenset(
    (
        'audiocodecnotsupported',
        'audiochannelsnotsupported',
        'audioprofilenotsupported',
        'audiosampleratenotsupported',
        'audiobitdepthnotsupported',
        'audiobitratenotsupported',
        'audioisexternal',
        'secondaryaudionotsupported',
    )
)


def is_local_url(url):
    """True when url's host is loopback, RFC 1918/ULA private IP, link-local, localhost, or a .local mDNS name."""
    host = (urlsplit(str(url or '')).hostname or '').strip().rstrip('.').lower()
    if not host:
        return False
    if host == 'localhost' or host.endswith('.local'):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local:
        return True
    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in _RFC1918_NETWORKS)
    return addr in _IPV6_ULA_NETWORK


def format_transcode_reason(reason):
    """Turn a Jellyfin PascalCase TranscodeReason token into lower-case plain words."""
    token = str(reason or '').strip()
    if not token:
        return ''
    spaced = _CAMEL_BOUNDARY.sub(' ', token).replace('_', ' ')
    return ' '.join(spaced.split()).lower()


def _raw_transcode_reasons(url):
    reasons = []
    seen = set()
    for key, value in parse_qsl(urlsplit(str(url or '')).query, keep_blank_values=False):
        if key.lower() != 'transcodereasons':
            continue
        for part in value.split(','):
            token = part.strip()
            low = token.lower()
            if token and low not in seen:
                seen.add(low)
                reasons.append(token)
    return reasons


def _query_params(url):
    params = {}
    for key, value in parse_qsl(urlsplit(str(url or '')).query, keep_blank_values=False):
        low = key.lower()
        if low not in params and value:
            params[low] = value
    return params


def _source_stream(media_source, kind, stream_index=None):
    streams = [s for s in (media_source.get('MediaStreams') or ()) if isinstance(s, dict) and s.get('Type') == kind]
    if stream_index is not None:
        for stream in streams:
            if stream.get('Index') == stream_index:
                return stream
    return streams[0] if streams and kind == 'Video' else None


def _stream_id(session, attr, default_index):
    explicit = getattr(session, attr, None)
    if explicit:
        return str(explicit)
    if default_index is None:
        return None
    return f'{session.media_source_id}:{default_index}'


def _first_codec(raw):
    if not raw:
        return None
    first = str(raw).split(',')[0].strip().lower()
    return first or None


def sessionElement(session):
    """Build a Plex /status/sessions-shaped <Video> element for an active Jellyfin PlaybackSession."""
    source = session.media_source if isinstance(session.media_source, dict) else {}
    mode = PLAY_METHOD_DECISIONS.get(session.play_method, 'directplay')
    params = _query_params(session.stream_url)
    raw_reasons = _raw_transcode_reasons(session.stream_url)
    formatted_reasons = [r for token in raw_reasons if (r := format_transcode_reason(token))]
    raw_reason_keys = {token.lower() for token in raw_reasons}

    src_video = _source_stream(source, 'Video')
    video_index = src_video.get('Index', 0) if src_video else 0
    video_id = _stream_id(session, 'video_stream_id', video_index)
    audio_id = _stream_id(session, 'audio_stream_id', session.audio_stream_index)
    subtitle_id = _stream_id(session, 'subtitle_stream_id', session.subtitle_stream_index)

    media = {'id': session.media_source_id}
    part = {'id': session.media_source_id, 'decision': mode}
    video = {'streamType': 1, 'id': video_id, 'decision': 'directplay'}
    audio = {'streamType': 2, 'id': audio_id, 'decision': 'directplay'} if audio_id else None
    subtitle = None

    if mode != 'directplay':
        sub_proto = str(source.get('TranscodingSubProtocol') or '').lower()
        is_hls = sub_proto == 'hls' or urlsplit(str(session.stream_url or '')).path.lower().endswith('.m3u8')
        part['container'] = 'hls' if is_hls else (session.container or None)
        if formatted_reasons:
            part['decisionReason'] = ', '.join(formatted_reasons)

    if mode == 'remux':
        video['decision'] = 'copy'
        if audio is not None:
            audio['decision'] = 'copy'
    elif mode != 'directplay':
        v_codec = _first_codec(params.get('videocodec'))
        a_codec = _first_codec(params.get('audiocodec'))
        v_bitrate = detail.kbps(params.get('videobitrate'))
        a_bitrate = detail.kbps(params.get('audiobitrate'))
        a_channels = params.get('audiochannels') or params.get('transcodingmaxaudiochannels')
        v_width = params.get('width') or params.get('maxwidth')
        v_height = params.get('height') or params.get('maxheight')
        sub_method = (params.get('subtitlemethod') or '').strip().lower()

        audio_only_reasons = bool(raw_reason_keys) and raw_reason_keys <= _AUDIO_REASON_KEYS
        video_is_copy = v_codec == 'copy' or (
            audio_only_reasons and v_bitrate is None and not v_width and not v_height and sub_method != 'encode'
        )
        video['decision'] = 'copy' if video_is_copy else 'transcode'
        if video['decision'] == 'transcode':
            res = detail.videoResolution(v_width, v_height)
            if res:
                media['videoResolution'] = res
            if v_codec and v_codec != 'copy':
                video['codec'] = v_codec
            if v_bitrate is not None:
                video['bitrate'] = v_bitrate

        if audio is not None:
            src_audio = _source_stream(source, 'Audio', session.audio_stream_index)
            src_a_codec = str((src_audio or {}).get('Codec') or '').lower()
            has_audio_reason = bool(raw_reason_keys & _AUDIO_REASON_KEYS)
            audio_changed = bool(
                has_audio_reason
                or a_bitrate is not None
                or a_channels is not None
                or (a_codec and a_codec != 'copy' and src_a_codec and a_codec != src_a_codec)
            )
            if a_codec == 'copy' or not audio_changed:
                audio['decision'] = 'copy'
            else:
                audio['decision'] = 'transcode'
                if a_codec:
                    audio['codec'] = a_codec
                if a_channels:
                    audio['channels'] = a_channels
                audio['bitrate'] = a_bitrate if a_bitrate is not None else '?'

        if sub_method == 'encode' and subtitle_id:
            subtitle = {'streamType': 3, 'id': subtitle_id, 'decision': 'burn', 'burn': '1'}

    streams = [detail.element('Stream', video)]
    if audio is not None:
        streams.append(detail.element('Stream', audio))
    if subtitle is not None:
        streams.append(detail.element('Stream', subtitle))

    part_el = detail.element('Part', part)
    part_el.extend(streams)
    media_el = detail.element('Media', media)
    media_el.append(part_el)
    root = detail.element(
        'Video',
        {
            'type': 'movie',
            'ratingKey': str(session.item_id),
            'sessionKey': str(session.play_session_id),
        },
    )
    root.append(media_el)
    return root
