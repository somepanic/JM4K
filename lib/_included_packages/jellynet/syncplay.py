"""
Jellyfin SyncPlay client (Task 9.1): REST helpers for /SyncPlay/* endpoints, tick/timestamp
conversions, and SyncPlaySocket over ws(s)://<base>/socket authenticated with the standard
MediaBrowser Authorization header.
"""

import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from . import ws
from .client import log

TICKS_PER_SECOND = 10_000_000
TERMINAL_STATUSES = (401, 403)
DEFAULT_KEEPALIVE = 30.0
MIN_KEEPALIVE = 5.0
CLOCK_SAMPLES = 8

_ISO_RE = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$')


def seconds_to_ticks(seconds):
    """Convert playback seconds to Jellyfin 100ns ticks; negative or invalid values clamp to 0."""
    if seconds is None:
        return 0
    value = float(seconds)
    if not math.isfinite(value) or value <= 0.0:
        return 0
    return round(value * TICKS_PER_SECOND)


def ticks_to_seconds(ticks):
    """Convert Jellyfin 100ns ticks to seconds (float); missing or non-positive values return 0.0."""
    if not ticks:
        return 0.0
    value = int(ticks)
    return value / TICKS_PER_SECOND if value > 0 else 0.0


def iso_now(seconds=None):
    """Format a UTC RFC-3339 timestamp with millisecond precision (.mmmZ), Kodi Python 3.8+ safe."""
    when = time.time() if seconds is None else float(seconds)
    return datetime.fromtimestamp(when, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'  # noqa: UP017


def parse_iso(text):
    """Parse an ISO-8601/RFC-3339 timestamp into float epoch seconds (preserving sub-second precision)."""
    if not isinstance(text, str):
        return None
    match = _ISO_RE.match(text.strip())
    if not match:
        return None
    base, frac, zone = match.group(1), match.group(2), match.group(3)
    iso_zone = '+00:00' if zone == 'Z' else zone
    if frac:
        # Jellyfin emits up to 7 fractional digits; Python 3.8-3.10 fromisoformat expects 3 or 6.
        micro = (frac + '000000')[:6]
        normalized = f'{base}.{micro}{iso_zone}'
    else:
        normalized = f'{base}{iso_zone}'
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


# --- SyncPlay REST endpoints (Jellyfin 10.11.5 OpenAPI) ---


def list_groups(client):
    """GET /SyncPlay/List -> list[GroupInfoDto]."""
    data = client.get('/SyncPlay/List')
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def create_group(client, group_name):
    """POST /SyncPlay/New with NewGroupRequestDto -> GroupInfoDto."""
    data = client.post('/SyncPlay/New', json={'GroupName': str(group_name)})
    return data if isinstance(data, dict) else {}


def join_group(client, group_id):
    """POST /SyncPlay/Join with JoinGroupRequestDto."""
    client.post('/SyncPlay/Join', json={'GroupId': str(group_id)})


def leave_group(client):
    """POST /SyncPlay/Leave."""
    client.post('/SyncPlay/Leave')


def set_new_queue(client, item_ids, playing_position=0, start_seconds=0.0):
    """POST /SyncPlay/SetNewQueue with PlayRequestDto."""
    client.post(
        '/SyncPlay/SetNewQueue',
        json={
            'PlayingQueue': [str(item_id) for item_id in item_ids],
            'PlayingItemPosition': int(playing_position),
            'StartPositionTicks': seconds_to_ticks(start_seconds),
        },
    )


def queue_items(client, item_ids, mode='Queue'):
    """POST /SyncPlay/Queue with QueueRequestDto."""
    client.post(
        '/SyncPlay/Queue',
        json={
            'ItemIds': [str(item_id) for item_id in item_ids],
            'Mode': str(mode),
        },
    )


def pause(client):
    """POST /SyncPlay/Pause."""
    client.post('/SyncPlay/Pause')


def unpause(client):
    """POST /SyncPlay/Unpause."""
    client.post('/SyncPlay/Unpause')


def stop(client):
    """POST /SyncPlay/Stop."""
    client.post('/SyncPlay/Stop')


def seek(client, position_seconds):
    """POST /SyncPlay/Seek with SeekRequestDto."""
    client.post('/SyncPlay/Seek', json={'PositionTicks': seconds_to_ticks(position_seconds)})


def buffering(client, when_iso, position_seconds, is_playing, playlist_item_id):
    """POST /SyncPlay/Buffering with BufferRequestDto."""
    client.post(
        '/SyncPlay/Buffering',
        json={
            'When': str(when_iso),
            'PositionTicks': seconds_to_ticks(position_seconds),
            'IsPlaying': bool(is_playing),
            'PlaylistItemId': str(playlist_item_id),
        },
    )


def ready(client, when_iso, position_seconds, is_playing, playlist_item_id):
    """POST /SyncPlay/Ready with ReadyRequestDto."""
    client.post(
        '/SyncPlay/Ready',
        json={
            'When': str(when_iso),
            'PositionTicks': seconds_to_ticks(position_seconds),
            'IsPlaying': bool(is_playing),
            'PlaylistItemId': str(playlist_item_id),
        },
    )


def ping(client, ping_ms):
    """POST /SyncPlay/Ping with PingRequestDto."""
    client.post('/SyncPlay/Ping', json={'Ping': max(0, int(ping_ms))})


def next_item(client, playlist_item_id):
    """POST /SyncPlay/NextItem with NextItemRequestDto."""
    client.post('/SyncPlay/NextItem', json={'PlaylistItemId': str(playlist_item_id)})


def previous_item(client, playlist_item_id):
    """POST /SyncPlay/PreviousItem with PreviousItemRequestDto."""
    client.post('/SyncPlay/PreviousItem', json={'PlaylistItemId': str(playlist_item_id)})


def title_of(client, item_id):
    """Fetch GET /Items/{itemId} and format a display title (Movie name or Series · S#E# · Title)."""
    item = client.get(f'/Items/{quote(str(item_id), safe="")}') or {}
    if not isinstance(item, dict):
        return ''
    name = item.get('Name') or ''
    if item.get('Type') == 'Episode' and item.get('SeriesName'):
        season = item.get('ParentIndexNumber') or 0
        episode = item.get('IndexNumber') or 0
        return f'{item["SeriesName"]} · S{season}E{episode} · {name}'
    return str(name)


# --- Jellyfin SyncPlay WebSocket client ---


class SyncPlaySocket:
    """
    Holds open Jellyfin's /socket WebSocket, answers KeepAlive/ForceKeepAlive messages,
    and dispatches SyncPlayCommand and SyncPlayGroupUpdate payloads to callbacks.
    Stops permanently on HTTP 401/403 or stop(); reconnects with exponential backoff on drops.
    """

    def __init__(self, client, on_command=None, on_group_update=None, socket_factory=ws.WebSocket, wait=None):
        self.client = client
        self.on_command = on_command
        self.on_group_update = on_group_update
        self.socket_factory = socket_factory
        self._stopped = threading.Event()
        self.wait = wait or self._stopped.wait
        self.ended = None
        self.keepalive_interval = DEFAULT_KEEPALIVE
        self._sock = None
        self._lock = threading.Lock()
        self._keepalive_wake = threading.Event()
        self._samples = []
        self._thread = None

    @property
    def url(self):
        base = re.sub(r'^http', 'ws', self.client.base_url)
        return f'{base}/socket'

    @property
    def connected(self):
        return self._sock is not None

    @property
    def rtt_ms(self):
        if not self._samples:
            return 0.0
        return max(0.0, min(self._samples)[0] * 1000.0)

    def start(self):
        self._thread = threading.Thread(target=self.run, name='jellynet.syncplay', daemon=True)
        self._thread.start()

    def stop(self):
        self._stopped.set()
        self._keepalive_wake.set()
        with self._lock:
            sock = self._sock
        if sock is not None:
            sock.close()

    def send(self, message):
        """Send a JSON envelope if connected; return False when disconnected or send fails."""
        with self._lock:
            sock = self._sock
        if sock is None:
            return False
        try:
            sock.send(json.dumps(message))
            return True
        except OSError:
            return False

    def run(self):
        failures = 0
        while not self._stopped.is_set() and self.ended is None:
            sock = self._connect()
            if sock is None:
                if self.ended is None and not self._stopped.is_set():
                    failures += 1
                    self.wait(min(30, 2**failures))
                continue
            failures = 0
            self._listen(sock)
            if self.ended is None and not self._stopped.is_set():
                self.wait(1)
        log(f'syncplay socket ended: {self.ended or "stopped"}')

    def _connect(self):
        try:
            sock = self.socket_factory(self.url, headers={'Authorization': self.client.auth_header()})
            sock.open()
        except ws.HandshakeError as e:
            log(f'syncplay socket refused: {e.status}')
            if e.status in TERMINAL_STATUSES:
                self.ended = 'refused'
            return None
        except OSError as e:
            log(f'syncplay socket unreachable: {type(e).__name__}')
            return None
        with self._lock:
            self._sock = sock
        return sock

    def _listen(self, sock):
        conn_done = threading.Event()
        keepalive = threading.Thread(
            target=self._keepalive_loop,
            args=(sock, conn_done),
            name='jellynet.syncplay.keepalive',
            daemon=True,
        )
        keepalive.start()
        try:
            while not self._stopped.is_set():
                text = sock.receive()
                if text is None:
                    break
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._handle(message)
        finally:
            conn_done.set()
            self._keepalive_wake.set()
            with self._lock:
                self._sock = None
            sock.close()

    def _keepalive_loop(self, sock, conn_done):
        while not conn_done.is_set() and not self._stopped.is_set():
            self._keepalive_wake.clear()
            woken = self._keepalive_wake.wait(self.keepalive_interval)
            if conn_done.is_set() or self._stopped.is_set():
                break
            if woken:
                continue  # ForceKeepAlive already sent KeepAlive and updated keepalive_interval
            with self._lock:
                if self._sock is not sock:
                    break
            self.send({'MessageType': 'KeepAlive'})

    def _handle(self, message):
        kind = message.get('MessageType')
        data = message.get('Data')
        if kind == 'ForceKeepAlive':
            try:
                timeout = float(data)
            except (TypeError, ValueError):
                timeout = 0.0
            if timeout > 0:
                self.keepalive_interval = max(MIN_KEEPALIVE, timeout / 2.0)
            self.send({'MessageType': 'KeepAlive'})
            self._keepalive_wake.set()
        elif kind == 'KeepAlive':
            self.send({'MessageType': 'KeepAlive'})
        elif kind == 'SyncPlayCommand' and isinstance(data, dict) and self.on_command is not None:
            self.on_command(data)
        elif kind == 'SyncPlayGroupUpdate' and isinstance(data, dict) and self.on_group_update is not None:
            self.on_group_update(data)

    def clock_sample(self, client_sent, server_received, server_sent, client_received):
        """Record an NTP-style round-trip clock sample (all float epoch seconds)."""
        if None in (client_sent, server_received, server_sent, client_received):
            return
        rtt = max(0.0, (client_received - client_sent) - (server_sent - server_received))
        offset = ((server_received - client_sent) + (server_sent - client_received)) / 2.0
        self._samples = (self._samples + [(rtt, offset)])[-CLOCK_SAMPLES:]

    def server_time(self, now=None):
        """Estimate the server's clock now (epoch seconds) using the lowest-RTT sample."""
        now = time.time() if now is None else float(now)
        return now + (min(self._samples)[1] if self._samples else 0.0)
