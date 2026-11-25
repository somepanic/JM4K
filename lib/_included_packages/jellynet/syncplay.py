"""
Jellyfin SyncPlay client (Task 9.1): REST helpers for /SyncPlay/* endpoints, tick/timestamp
conversions, and SyncPlaySocket over ws(s)://<base>/socket authenticated with the standard
MediaBrowser Authorization header.
"""

import json
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from . import ws
from .client import log
from .errors import JellyfinError
from .items import seconds_to_ticks

TERMINAL_STATUSES = (401, 403)
DEFAULT_KEEPALIVE = 30.0
MIN_KEEPALIVE = 5.0
CLOCK_SAMPLES = 8

_ISO_RE = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$')


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


def get_utc_time(client):
    """GET /GetUtcTime -> UtcTimeResponse dict with RequestReceptionTime and ResponseTransmissionTime."""
    data = client.get('/GetUtcTime')
    return data if isinstance(data, dict) else {}


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

    def __init__(
        self, client, on_command=None, on_group_update=None, socket_factory=ws.WebSocket, wait=None, sync_time=False
    ):
        self.client = client
        self.on_command = on_command
        self.on_group_update = on_group_update
        self.socket_factory = socket_factory
        self._sync_time = bool(sync_time)
        self._stopped = threading.Event()
        self._connected_event = threading.Event()
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
        with self._lock:
            return self._sock is not None

    @property
    def rtt_ms(self):
        with self._lock:
            if not self._samples:
                return 0.0
            return max(0.0, min(self._samples)[0] * 1000.0)

    def wait_connected(self, timeout=2.0):
        """Wait up to timeout seconds for the socket to open (or stop/refuse); return whether connected."""
        self._connected_event.wait(timeout)
        return self.connected

    def start(self):
        self._thread = threading.Thread(target=self.run, name='jellynet.syncplay', daemon=True)
        self._thread.start()

    def stop(self):
        self._stopped.set()
        self._keepalive_wake.set()
        self._connected_event.set()
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
                self._connected_event.set()
            return None
        except OSError as e:
            log(f'syncplay socket unreachable: {type(e).__name__}')
            return None
        with self._lock:
            self._sock = sock
        self._connected_event.set()
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
            if not self._stopped.is_set() and self.ended is None:
                self._connected_event.clear()
            sock.close()

    def _keepalive_loop(self, sock, conn_done):
        if self._sync_time and not conn_done.is_set() and not self._stopped.is_set():
            self.sync_clock()
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
            if self._sync_time:
                self.sync_clock()

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

    def sync_clock(self, clock=time.time):
        """Measure clock offset and RTT via GET /GetUtcTime and report RTT via POST /SyncPlay/Ping."""
        try:
            client_sent = float(clock())
            answer = get_utc_time(self.client)
            client_received = float(clock())
        except JellyfinError:
            return False
        server_received = parse_iso(answer.get('RequestReceptionTime'))
        server_sent = parse_iso(answer.get('ResponseTransmissionTime'))
        if server_received is None or server_sent is None:
            return False
        self.clock_sample(client_sent, server_received, server_sent, client_received)
        try:
            ping(self.client, round(self.rtt_ms))
        except JellyfinError:
            pass
        return True

    def clock_sample(self, client_sent, server_received, server_sent, client_received):
        """Record an NTP-style round-trip clock sample (all float epoch seconds)."""
        if None in (client_sent, server_received, server_sent, client_received):
            return
        rtt = max(0.0, (client_received - client_sent) - (server_sent - server_received))
        offset = ((server_received - client_sent) + (server_sent - client_received)) / 2.0
        with self._lock:
            self._samples = (self._samples + [(rtt, offset)])[-CLOCK_SAMPLES:]

    def server_time(self, now=None):
        """Estimate the server's clock now (epoch seconds) using the lowest-RTT sample."""
        now = time.time() if now is None else float(now)
        with self._lock:
            offset = min(self._samples)[1] if self._samples else 0.0
        return now + offset
