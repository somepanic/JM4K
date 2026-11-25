"""
Playing from Silo, playback protocol v3 (silo-server's docs/architecture/playback-protocol-v3.md and
docs/playback-api.md): POST /api/v2/playback/start says what Kodi plays and gets back the server's plan. Kodi plays
nearly every codec and container itself, so it reports `declared` evidence with its own wide lists and Silo sends the
original file, byte for byte, for Kodi to play and seek (original_http), as Plex Uno direct-plays from Plex (decision
D11); a transcode is left for what Kodi can't take. The attempt's progress and stop (POST
/api/v2/playback/{session}/progress, DELETE /api/v2/playback/{session}) keep Silo's resume position and watch history
(progress_persistence "server").
"""

import uuid

from .client import log
from .errors import SiloError

PROTOCOL = 3
# What Kodi's player (FFmpeg, with the platform's hardware decoders) plays itself, as Silo's codec and container names
VIDEO_CODECS = [
    'h264',
    'hevc',
    'av1',
    'vp9',
    'vp8',
    'mpeg2video',
    'mpeg4',
    'vc1',
    'wmv3',
    'h263',
    'msmpeg4v3',
    'theora',
]
AUDIO_CODECS = [
    'aac',
    'ac3',
    'eac3',
    'dts',
    'truehd',
    'mlp',
    'flac',
    'alac',
    'opus',
    'vorbis',
    'mp3',
    'mp2',
    'pcm_s16le',
    'pcm_s24le',
    'pcm_bluray',
    'wmav2',
]
CONTAINERS = [
    'mkv',
    'matroska',
    'mp4',
    'mov',
    'm4v',
    'webm',
    'avi',
    'ts',
    'mpegts',
    'm2ts',
    'wmv',
    'asf',
    'flv',
    'ogg',
    'mpeg',
]
HLS_CONTAINERS = ['mp4', 'ts', 'mpegts']
# Kodi renders text and picture subtitles inside the file, styled ASS with its fonts, and text sidecars
SUBTITLES = {
    'embedded_text': True,
    'sidecar_text': True,
    'ass_styling': True,
    'embedded_bitmap': True,
    'sidecar_bitmap': False,
    'font_attachments': True,
}
HDR = {'hdr10': True, 'hdr10_plus': True, 'hlg': True, 'dolby_vision_profiles': [5, 7, 8]}


class PlaybackRefused(Exception):
    """Silo decided the item can't play (a terminal decision): its reason and the message to show."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason
        self.message = message


def delivery(containers=CONTAINERS, claims=()):
    return {
        'enabled': True,
        'supported_on_device': True,
        'containers': list(containers),
        'video_codecs': list(VIDEO_CODECS),
        'audio_decode_codecs': list(AUDIO_CODECS),
        'audio_passthrough_codecs': [],
        'subtitles': dict(SUBTITLES),
        'features': [],
        'auth_header_refresh': False,
        'validated_claims': list(claims),
        'transformations': [],
    }


def startBody(
    installationId,
    profileId,
    fileId,
    position=0.0,
    audioIndex=None,
    subtitleIndex=None,
    quality='auto',
    appVersion='',
    bandwidthCap=None,
    hdr=True,
):
    """The start request (start-request.schema.json): what Kodi plays and which tracks the user chose. A bitrate cap
    (the Quality setting below Original, decision D12) has Silo transcode only a file above it; HDR off (the Disable
    HDR setting) reports an SDR device, so Silo converts HDR for it."""
    body = {
        'installation_id': installationId,
        'protocol_version': PROTOCOL,
        'client_features': ['playback_plan_v3'],
        'file_id': str(fileId),
        'profile_id': profileId,
        'playback_attempt_id': 'sm4k-' + uuid.uuid4().hex,
        'quality_preference': quality or 'auto',
        'subtitle_fidelity_preference': 'preserve',
        'progress_persistence': 'server',
        'start_position': round(max(0.0, position), 3),
        'client_capabilities': {
            'video_evidence': 'declared',
            'audio_evidence': 'declared',
            'codecs_video': list(VIDEO_CODECS),
            'codecs_video_hardware': list(VIDEO_CODECS),
            'codecs_audio': list(AUDIO_CODECS),
            'containers': list(CONTAINERS),
            'hdr': hdr,
            'hdr_details': dict(HDR)
            if hdr
            else {'hdr10': False, 'hdr10_plus': False, 'hlg': False, 'dolby_vision_profiles': []},
        },
        'client_playback_context': {
            'protocol_version': PROTOCOL,
            'form_factor': 'tv',
            'app_version': appVersion or '0',
            'device': {'platform': 'kodi'},
            'output': {},
            'deliveries': {
                # the original's every audio track is Kodi's to pick, so a chosen one needs no remux
                'original_http': delivery(claims=('client_selected_audio_track_v1',)),
                'progressive': delivery(),
                'hls': delivery(HLS_CONTAINERS),
            },
        },
    }
    if bandwidthCap:
        body['bandwidth_cap_kbps'] = int(bandwidthCap)
    if audioIndex is not None:
        body['audio_track_index'] = audioIndex
    if subtitleIndex is not None:
        body['subtitle_track_index'] = subtitleIndex
    return body


class Attempt:
    """One playable attempt: its stream URL and timeline, and its progress and stop."""

    def __init__(self, client, installationId, decision, request=None):
        plan = decision.get('playback_plan') or {}
        self.client = client
        self.installationId = installationId
        self.request = request or {}  # the start request: its attempt id, capabilities and context, for a replan
        self.position = float(self.request.get('start_position') or 0)
        self.sessionId = decision['session_id']
        self.plan = plan
        self.delivery = plan.get('delivery') or ''
        url = (plan.get('stream') or {}).get('url') or ''
        self.url = client.baseUrl + url if url.startswith('/') else url
        self.timeline = plan.get('timeline') or {}
        self.sequence = 0
        self.ended = False

    @property
    def transcoded(self):
        return self.delivery != 'original_http'

    def sidecarUrl(self, source, ref):
        """A subtitle file's URL from the plan's inventory: the ref-th external file, or the downloaded subtitle
        whose id is ref. None when the plan lists none."""
        inventory = (self.plan.get('subtitle') or {}).get('inventory') or []
        if source == 'external':
            files = [entry for entry in inventory if entry.get('source') == 'external']
            entry = files[int(ref)] if str(ref).isdigit() and int(ref) < len(files) else None
        else:
            entry = next((e for e in inventory if f'downloaded_subtitle_id={ref}' in (e.get('url') or '')), None)
        url = (entry or {}).get('url') or ''
        return (self.client.baseUrl + url if url.startswith('/') else url) or None

    def refresh(self):
        """The plan again (a track_change replan keeping the audio track): its inventory then lists what Silo stored
        since, such as a downloaded subtitle. The stream Kodi plays stays as it is."""
        plan = self.plan
        body = {
            'installation_id': self.installationId,
            'protocol_version': PROTOCOL,
            'client_features': list(self.request.get('client_features') or ['playback_plan_v3']),
            'operation': 'track_change',
            'playback_attempt_id': self.request.get('playback_attempt_id'),
            'replan_request_id': 'sm4k-' + uuid.uuid4().hex,
            'failed_plan_id': plan.get('plan_id'),
            'plan_attempt_id': 'sm4k-' + uuid.uuid4().hex,
            'plan_attempt_key': plan.get('plan_attempt_key'),
            'attempted_plan_keys': [plan.get('plan_attempt_key')] if plan.get('plan_attempt_key') else [],
            'attempt_count': 1,
            'quality_preference': self.request.get('quality_preference') or 'auto',
            'position_seconds': round(self.position, 3),
            'selected_tracks': {k: v for k, v in (plan.get('selected_tracks') or {}).items() if k == 'audio'},
            'client_capabilities': self.request.get('client_capabilities'),
            'client_playback_context': self.request.get('client_playback_context'),
        }
        try:
            decision = self.client.request('POST', f'/api/v2/playback/{self.sessionId}/replan', json=body) or {}
        except SiloError as e:
            log(f'replan of {self.sessionId} failed: {e.status} {e.type}')
            return False
        if decision.get('outcome') != 'playable':
            return False
        self.plan = decision.get('playback_plan') or self.plan
        return True

    def _sample(self, seconds, paused):
        self.position = max(0.0, seconds)
        self.sequence += 1
        return {'sequence': self.sequence, 'position': round(max(0.0, seconds), 3), 'is_paused': bool(paused)}

    def progress(self, seconds, paused=False):
        """A progress sample. A session Silo has ended (404, 410) is let go: nothing more is sent for it."""
        if self.ended:
            return
        body = dict(self._sample(seconds, paused), installation_id=self.installationId)
        try:
            self.client.request('POST', f'/api/v2/playback/{self.sessionId}/progress', json=body)
        except SiloError as e:
            log(f'progress of {self.sessionId} failed: {e.status} {e.type}')
            if e.status in (404, 410):
                self.ended = True

    def stop(self, seconds=None):
        """Stops the attempt, once, with its final position: Silo writes the resume position and watch history."""
        if self.ended:
            return
        self.ended = True
        body = {'installation_id': self.installationId, 'stop_id': str(uuid.uuid4())}
        if seconds is not None:
            body.update(self._sample(seconds, False))
        try:
            self.client.request('DELETE', f'/api/v2/playback/{self.sessionId}', json=body)
        except SiloError as e:
            log(f'stop of {self.sessionId} failed: {e.status} {e.type}')


class Playback:
    """A server's playback: its installation id (GET /api/v2/playback/capabilities, asked once and again when Silo
    says it changed) and its attempts, by item."""

    def __init__(self, client, appVersion=''):
        self.client = client
        self.appVersion = appVersion
        self._installationId = None
        self.attempts = {}

    def installationId(self, fresh=False):
        if fresh or not self._installationId:
            answer = self.client.request('GET', '/api/v2/playback/capabilities') or {}
            self._installationId = answer.get('installation_id')
        return self._installationId

    def start(
        self,
        key,
        fileId,
        position=0.0,
        audioIndex=None,
        subtitleIndex=None,
        quality='auto',
        bandwidthCap=None,
        hdr=True,
        pinned=False,
    ):
        """Starts an attempt for the item (key: its ratingKey) and returns it; PlaybackRefused for a terminal. pinned:
        a watch party's file, which Silo keeps to (allow_alternate_versions false), as every member plays it."""
        previous = self.attempts.pop(key, None)
        if previous is not None:
            previous.stop()
        for fresh in (False, True):
            body = startBody(
                self.installationId(fresh),
                self.client.credentials.profileId,
                fileId,
                position,
                audioIndex,
                subtitleIndex,
                quality,
                self.appVersion,
                bandwidthCap,
                hdr,
            )
            if pinned:
                body['allow_alternate_versions'] = False
            try:
                decision = self.client.request('POST', '/api/v2/playback/start', json=body) or {}
                break
            except SiloError as e:
                if e.type != 'installation_changed' or fresh:
                    raise
        if decision.get('outcome') != 'playable' or not decision.get('session_id'):
            terminal = decision.get('terminal') or {}
            raise PlaybackRefused(
                terminal.get('reason') or decision.get('outcome') or '',
                terminal.get('message') or "Silo can't play this",
            )
        attempt = Attempt(self.client, body['installation_id'], decision, body)
        self.attempts[key] = attempt
        log(f'playing {key} as {attempt.delivery} ({attempt.plan.get("decision_reason")})')
        return attempt

    def attemptFor(self, fileId):
        """The attempt playing the file (the latest), or None."""
        for attempt in reversed(list(self.attempts.values())):
            if str(attempt.request.get('file_id')) == str(fileId):
                return attempt
        return None

    def timeline(self, key, state, milliseconds):
        """A player timeline (nowplayingmanager): playing, paused or buffering are progress, stopped stops."""
        attempt = self.attempts.get(key)
        if attempt is None:
            return
        seconds = int(milliseconds or 0) / 1000.0
        if state == 'stopped':
            self.attempts.pop(key, None)
            attempt.stop(seconds)
        else:
            attempt.progress(seconds, paused=state == 'paused')
