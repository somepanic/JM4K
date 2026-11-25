"""
Per-show remembered audio and subtitle track choices on Jellyfin (decision D13).
Jellyfin's server uses User.Configuration.RememberAudioSelections / RememberSubtitleSelections to compute
PlaybackInfo defaults; this module mirrors silonet.trackprefs so Uno's per-show rememberStream() choices
(from PlexPart.setSelectedStream()) apply across episodes of a series and remember external vs embedded
subtitles (SM4K #49). A movie has no series, so its choice is not kept per-show.
"""

import threading

from .detail import languageAttrs

AUDIO, SUBTITLE = '2', '3'  # Plex stream types


def _select(streams, chosen):
    for stream in streams:
        stream.set('selected', '1' if stream is chosen else '0')


def _language(code):
    return languageAttrs(code).get('languageCode') if code else None


def _as_bool(value):
    if hasattr(value, 'asBool'):
        return bool(value.asBool())
    return str(value or '').lower() in ('1', 'true', 'yes')


def _as_int(value):
    if hasattr(value, 'asInt'):
        return value.asInt()
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pick_audio(candidates, pref, lang_of, title_of):
    if not pref or not candidates:
        return None
    index = pref.get('audio_track_index')
    language = _language(pref.get('audio_language'))
    sig = pref.get('track_signature') or {}
    title = str(sig.get('title') or '')

    at_index = candidates[index] if isinstance(index, int) and 0 <= index < len(candidates) else None
    if at_index is not None and (not language or lang_of(at_index) == language):
        return at_index
    if language:
        if title:
            by_title = next((s for s in candidates if lang_of(s) == language and title_of(s) == title), None)
            if by_title is not None:
                return by_title
        by_lang = next((s for s in candidates if lang_of(s) == language), None)
        if by_lang is not None:
            return by_lang
    return at_index


def _pick_subtitle(candidates, pref, lang_of, is_ext, is_forced, is_hi):
    if not pref or not candidates:
        return None
    if pref.get('subtitle_mode') == 'off':
        return None
    language = _language(pref.get('subtitle_language'))
    sig = pref.get('track_signature') or {}
    source = sig.get('source')  # 'external' or 'embedded'
    want_forced = bool(sig.get('forced'))
    want_hi = bool(sig.get('hearing_impaired'))

    def source_matches(stream):
        if source == 'external':
            return is_ext(stream)
        if source == 'embedded':
            return not is_ext(stream)
        return True

    if language:
        exact = next(
            (
                s
                for s in candidates
                if lang_of(s) == language and source_matches(s) and is_forced(s) == want_forced and is_hi(s) == want_hi
            ),
            None,
        )
        if exact is not None:
            return exact
        by_source = next((s for s in candidates if lang_of(s) == language and source_matches(s)), None)
        if by_source is not None:
            return by_source
        by_lang = next((s for s in candidates if lang_of(s) == language), None)
        if by_lang is not None:
            return by_lang

    index = pref.get('subtitle_track_index')
    if isinstance(index, int) and 0 <= index < len(candidates) and source_matches(candidates[index]):
        return candidates[index]
    return None


def apply(root, audio, subtitle):
    """Mark remembered audio/subtitle tracks selected across every <Part> of an episode XML element."""
    if not audio and not subtitle:
        return
    for part in root.iter('Part'):
        streams = list(part.iter('Stream'))
        audios = [s for s in streams if s.get('streamType') == AUDIO]
        subtitles = [s for s in streams if s.get('streamType') == SUBTITLE]

        if audio and audios:
            chosen = _pick_audio(
                audios,
                audio,
                lang_of=lambda s: s.get('languageCode'),
                title_of=lambda s: s.get('title') or '',
            )
            if chosen is not None:
                _select(audios, chosen)

        if subtitle and subtitles:
            if subtitle.get('subtitle_mode') == 'off':
                _select(subtitles, None)
            else:
                chosen = _pick_subtitle(
                    subtitles,
                    subtitle,
                    lang_of=lambda s: s.get('languageCode'),
                    is_ext=lambda s: bool(s.get('key')),
                    is_forced=lambda s: _as_bool(s.get('forced')),
                    is_hi=lambda s: _as_bool(s.get('hearingImpaired')),
                )
                if chosen is not None:
                    _select(subtitles, chosen)


def select_media_source_indices(media_source, audio, subtitle):
    """Resolve remembered (audio_index, subtitle_index) against a Jellyfin MediaSourceInfo dict."""
    raw_streams = [s for s in (media_source.get('MediaStreams') or ()) if isinstance(s, dict)]
    audios = [s for s in raw_streams if s.get('Type') == 'Audio']
    embedded_subs = [s for s in raw_streams if s.get('Type') == 'Subtitle' and not s.get('IsExternal')]
    external_subs = [s for s in raw_streams if s.get('Type') == 'Subtitle' and s.get('IsExternal')]
    subtitles = embedded_subs + external_subs

    chosen_audio = _pick_audio(
        audios,
        audio,
        lang_of=lambda s: _language(s.get('Language')),
        title_of=lambda s: str(s.get('Title') or ''),
    )
    chosen_sub = _pick_subtitle(
        subtitles,
        subtitle,
        lang_of=lambda s: _language(s.get('Language')),
        is_ext=lambda s: bool(s.get('IsExternal')),
        is_forced=lambda s: bool(s.get('IsForced')),
        is_hi=lambda s: bool(s.get('IsHearingImpaired')),
    )
    audio_idx = chosen_audio.get('Index') if chosen_audio is not None else None
    sub_idx = chosen_sub.get('Index') if chosen_sub is not None else None
    return audio_idx, sub_idx


def audioUpdate(stream, ordinal):
    """Build an audio track preference record matching SM4K's trackprefs schema."""
    return {
        'audio_track_index': ordinal,
        'audio_language': str(getattr(stream, 'languageCode', '') or ''),
        'track_signature': {
            'codec': str(getattr(stream, 'codec', '') or ''),
            'channels': _as_int(getattr(stream, 'channels', 0)),
            'title': str(getattr(stream, 'title', '') or ''),
            'language': str(getattr(stream, 'languageCode', '') or ''),
            'default': _as_bool(stream.get('default') if hasattr(stream, 'get') else False),
        },
    }


def subtitleUpdate(stream, ordinal):
    """Build a subtitle track preference record (or off record when stream is None / NoneStream)."""
    if stream is None or str(getattr(stream, 'id', '') or '') == '0':
        return {'subtitle_track_index': -1, 'subtitle_mode': 'off'}
    is_external = bool(getattr(stream, 'key', None))
    return {
        'subtitle_track_index': ordinal,
        'subtitle_language': str(getattr(stream, 'languageCode', '') or ''),
        'subtitle_mode': 'always',
        'track_signature': {
            'codec': str(getattr(stream, 'codec', '') or ''),
            'language': str(getattr(stream, 'languageCode', '') or ''),
            'label': str(getattr(stream, 'title', '') or ''),
            'forced': _as_bool(getattr(stream, 'forced', False)),
            'hearing_impaired': _as_bool(stream.get('hearingImpaired') if hasattr(stream, 'get') else False),
            'source': 'external' if is_external else 'embedded',
        },
    }


class TrackPrefsStore:
    """Thread-safe per-series track preference store kept on a JellyfinServer."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_series = {}

    def get(self, series_id):
        """Return (audio_pref, subtitle_pref) for series_id, each None when unset."""
        if not series_id:
            return None, None
        with self._lock:
            entry = self._by_series.get(str(series_id))
            if not entry:
                return None, None
            return entry.get('audio'), entry.get('subtitle')

    def remember(self, series_id, stream_type, stream, streams):
        """Save the chosen audio (2) or subtitle (3; None/NoneStream for off) stream for series_id."""
        if not series_id:
            return
        same = [s for s in (streams or ()) if _as_int(getattr(s, 'streamType', 0)) == stream_type]
        ordinal = next(
            (
                i
                for i, s in enumerate(same)
                if stream is not None and getattr(s, 'id', None) == getattr(stream, 'id', None)
            ),
            -1,
        )
        if stream_type == 2 and stream is not None:
            kind, record = 'audio', audioUpdate(stream, ordinal)
        elif stream_type == 3:
            kind, record = 'subtitle', subtitleUpdate(stream, ordinal)
        else:
            return
        with self._lock:
            entry = self._by_series.setdefault(str(series_id), {})
            entry[kind] = record
