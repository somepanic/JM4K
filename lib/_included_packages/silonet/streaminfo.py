"""
Show Stream Info on Silo. The player's info panel (seekdialog.showPPIDialog(), plexnet.videosession) compares the
version an item plays with the item's session, as Plex's /status/sessions lists it. Silo has no such list: the
item's attempt says what Silo sends (playback protocol v3: the plan's delivery, source, effective_recipe and
transformations), and sessionElement() writes that as a session's <Video><Media><Part><Stream/></Part></Media></Video>,
with the ids silonet.detail gives the version's. What Silo sends unchanged is left out, so the panel shows the
version's own value.
"""

from .detail import RESOLUTIONS, element

# a delivery as the panel's Mode (Plex's part decision): the file itself, repackaged with its codecs untouched, or
# re-encoded (playback-protocol-v3.md section 4)
MODES = {
    'original_http': 'directplay',
    'server_remux_progressive': 'remux',
    'server_remux_hls': 'remux',
    'server_transcode_hls': 'transcode',
}
# what the recipe (the bytes Silo sends) changes of the source when Silo re-encodes the video or the audio
VIDEO = ('video_codec', 'width', 'height', 'dynamic_range')
AUDIO = ('audio_codec', 'audio_channels')


def _decision(plan, keys, transformed):
    """Plex's decision for a stream Silo sends: transcode when a server transformation works on it or the recipe
    changes it, else copy."""
    source = plan.get('source') or {}
    recipe = plan.get('effective_recipe') or {}
    changed = any(key in recipe and key in source and recipe[key] != source[key] for key in keys)
    return 'transcode' if transformed or changed else 'copy'


def sessionElement(attempt):
    """The attempt's session as Plex's /status/sessions lists an item: the version's Media and Part with the video and
    audio streams Silo sends, each with Plex's decision (directplay, copy or transcode). plexnet builds it as a movie:
    the panel reads only its Media."""
    plan = attempt.plan
    delivery = plan.get('delivery') or ''
    recipe = plan.get('effective_recipe') or {}
    fileId = attempt.request.get('file_id')
    audioIndex = ((plan.get('selected_tracks') or {}).get('audio') or {}).get('index')
    if audioIndex is None:
        audioIndex = attempt.request.get('audio_track_index')
    names = [t.get('name') or '' for t in plan.get('transformations') or [] if t.get('executor') == 'server']

    mode = MODES.get(delivery, delivery)
    media = {'id': fileId}
    part = {'id': fileId, 'decision': mode}
    video = {'streamType': 1, 'id': f'{fileId}.v0', 'decision': 'directplay'}
    audio = {'streamType': 2, 'id': f'{fileId}.a{audioIndex}', 'decision': 'directplay'}
    if mode != 'directplay':
        part['container'] = recipe.get('segment_container') or (plan.get('stream') or {}).get('container')
        # why Silo changed the file, in words: the panel's Mode (plexnet.videosession ModePPI)
        part['decisionReason'] = (plan.get('decision_reason') or '').replace('_', ' ') or None
    if mode == 'remux':
        # copied, also when the recipe's dynamic range differs (a Dolby Vision profile Silo converts on the way)
        video['decision'] = audio['decision'] = 'copy'
    elif mode != 'directplay':
        video['decision'] = _decision(plan, VIDEO, any(not name.startswith('audio_') for name in names))
        audio['decision'] = _decision(plan, AUDIO, any(name.startswith('audio_') for name in names))
    if video['decision'] == 'transcode':
        height = recipe.get('height')
        media['videoResolution'] = RESOLUTIONS.get(f'{height}p', str(height)) if height else None
        video.update(codec=recipe.get('video_codec'), bitrate=recipe.get('bitrate_kbps'))
    if audio['decision'] == 'transcode':
        # Silo doesn't say the new audio's bitrate: "?", as plexnet's panel shows one it doesn't know
        audio.update(codec=recipe.get('audio_codec'), channels=recipe.get('audio_channels'), bitrate='?')

    streams = [element('Stream', video)] + ([element('Stream', audio)] if audioIndex is not None else [])
    partElement = element('Part', part)
    partElement.extend(streams)
    mediaElement = element('Media', media)
    mediaElement.append(partElement)
    session = element('Video', {'type': 'movie'})
    session.append(mediaElement)
    return session
