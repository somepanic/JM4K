"""
A show's remembered audio and subtitle choice, kept by Silo per profile and series (decision D13):
GET/PUT /api/v2/audio-prefs/{series_id} and /api/v2/subtitle-prefs/{series_id}. An episode's detail marks the
remembered tracks selected (apply()), which Plex Uno's media decision engine then plays; choosing a track for an
episode saves it (SiloServer.rememberStream(), from plexnet's PlexPart.setSelectedStream()). A movie has no show, so
its choice isn't kept.
"""

from .client import log
from .detail import languageAttrs
from .errors import SiloError

AUDIO, SUBTITLE = '2', '3'  # Plex stream types


def load(client, seriesId):
    """(audio preference, subtitle preference) for the series, each None when Silo remembers none."""
    found = []
    for kind in ('audio', 'subtitle'):
        try:
            found.append(client.request('GET', f'/api/v2/{kind}-prefs/{seriesId}'))
        except SiloError as e:
            if e.status != 404:
                log(f'{kind} preference of {seriesId} failed: {e.status} {e.type}')
            found.append(None)
    return tuple(found)


def _select(streams, chosen):
    for stream in streams:
        stream.set('selected', '1' if stream is chosen else '0')


def _language(code):
    return languageAttrs(code).get('languageCode') if code else None


def apply(root, audio, subtitle):
    """Marks the remembered tracks selected in the detail's every version: the audio track by its index, else its
    language; subtitles off, or the first in the remembered language."""
    for part in root.iter('Part'):
        streams = list(part.iter('Stream'))
        audios = [s for s in streams if s.get('streamType') == AUDIO]
        subtitles = [s for s in streams if s.get('streamType') == SUBTITLE]
        if audio and audios:
            index = audio.get('audio_track_index')
            language = _language(audio.get('audio_language'))
            chosen = audios[index] if isinstance(index, int) and 0 <= index < len(audios) else None
            if chosen is None and language:
                chosen = next((s for s in audios if s.get('languageCode') == language), None)
            if chosen is not None:
                _select(audios, chosen)
        if subtitle and subtitles:
            if subtitle.get('subtitle_mode') == 'off':
                _select(subtitles, None)
            else:
                language = _language(subtitle.get('subtitle_language'))
                chosen = next((s for s in subtitles if language and s.get('languageCode') == language), None)
                if chosen is not None:
                    _select(subtitles, chosen)


def audioUpdate(stream, ordinal):
    return {
        'audio_track_index': ordinal,
        'audio_language': str(stream.languageCode or ''),
        'track_signature': {
            'codec': str(stream.codec or ''),
            'channels': stream.channels.asInt(),
            'title': str(stream.title or ''),
            'language': str(stream.languageCode or ''),
            'default': stream.get('default').asBool(),
        },
    }


def subtitleUpdate(stream, ordinal):
    """A chosen subtitle track (its position among the version's subtitles, a hint; Silo also has the language and
    signature), or None for subtitles off."""
    if stream is None:
        return {'subtitle_track_index': -1, 'subtitle_mode': 'off'}
    return {
        'subtitle_track_index': ordinal,
        'subtitle_language': str(stream.languageCode or ''),
        'subtitle_mode': 'always',
        'track_signature': {
            'codec': str(stream.codec or ''),
            'language': str(stream.languageCode or ''),
            'label': str(stream.title or ''),
            'forced': stream.forced.asBool(),
            'hearing_impaired': stream.get('hearingImpaired').asBool(),
            'source': 'external' if stream.key else 'embedded',  # a file beside the video has its own key
        },
    }


def remember(client, seriesId, streamType, stream, streams):
    """Saves the choice for the series: streamType 2 audio, 3 subtitles (stream None: off)."""
    same = [s for s in streams if s.streamType.asInt() == streamType]
    ordinal = next((i for i, s in enumerate(same) if stream is not None and s.id == stream.id), -1)
    if streamType == 2 and stream is not None:
        kind, body = 'audio', audioUpdate(stream, ordinal)
    elif streamType == 3:
        kind, body = 'subtitle', subtitleUpdate(stream, ordinal)
    else:
        return
    try:
        client.request('PUT', f'/api/v2/{kind}-prefs/{seriesId}', json=body)
    except SiloError as e:
        log(f'remembering {kind} of {seriesId} failed: {e.status} {e.type}')
