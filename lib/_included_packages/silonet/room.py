"""
A watch-together room (decision D26) from Silo's /api/v2/watch-together: joining one by its code, creating one and
choosing what it plays as its host, and the room socket (protocol silo.room.v2, docs/realtime-api.md) that keeps its
members in step. A socket connection lasts at most five minutes, so RoomSocket reconnects with a fresh single-use
ticket until the room ends, another device takes this profile's membership, or it is stopped.
"""

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from . import ws
from .client import log
from .errors import SiloError
from .items import epoch

PROTOCOL = 'silo.room.v2'
TICKET = re.compile(r'^[A-Za-z0-9_-]{43}$')
TERMINAL = (401, 403, 404, 409, 410)  # a ticket or upgrade refused for good
CLOCK_SAMPLES = 8  # the server's clock from the fastest of the last this many pings
FAST_PINGS, FAST_PING_EVERY, PING_EVERY = 4, 2.0, 15.0  # seconds
SUGGESTION_PAGE, MAX_SUGGESTIONS = 100, 200  # a room holds 200 at most (docs/realtime-api.md)


class Room:
    """A room this profile is in: its snapshot (the server's typed room fields) and the room proof the room's
    requests carry."""

    def __init__(self, answer):
        self.snapshot, self.token = {}, None
        self.update(answer)

    def update(self, answer):
        """A room answer ({room, room_access_token}) or a socket snapshot frame's room; an older generation is kept out."""
        snapshot = answer.get('room') if 'room' in answer else answer
        if snapshot and snapshot.get('generation', 0) >= self.snapshot.get('generation', 0):
            self.snapshot = snapshot
        self.token = answer.get('room_access_token') or self.token

    @property
    def id(self):
        return self.snapshot.get('room_id')

    @property
    def isHost(self):
        return self.snapshot.get('self_role') == 'host'

    def _path(self, tail=''):
        return f'/api/v2/watch-together/rooms/{self.id}{tail}'

    def select(self, client, contentId, fileId=None):
        """Play contentId (its fileId version, when given) for the room, as its host."""
        body = {'content_id': contentId}
        if fileId:
            body['file_id'] = str(fileId)
        self.update(client.request('PUT', self._path('/selection'), json=body) or {})

    def stage(self, client, contentId, fileId=None):
        """Put contentId up next in the lobby, as its host, without starting it: members mark themselves ready, and the
        host starts it for everyone (start())."""
        body = {'content_id': contentId}
        if fileId:
            body['file_id'] = str(fileId)
        self.update(client.request('PUT', self._path('/staged-selection'), json=body) or {})

    def start(self, client):
        """Start what's up next for everyone, as the host."""
        self.update(client.request('POST', self._path('/playback/start')) or {})

    def stop(self, client):
        """Stop playing for everyone, as the host: the room goes back to its lobby, the item up next again."""
        self.update(client.request('POST', self._path('/playback/stop')) or {})

    def setGuestControl(self, client, allowed):
        """Let guests play and pause the room (or only the host), as the host."""
        policy = 'guest_play_pause' if allowed else 'host_only'
        self.update(client.request('PATCH', self._path('/policy'), json={'guest_control_policy': policy}) or {})

    def setVoting(self, client, voting):
        """Have members vote on what to watch (or the host pick), as the host; only in the lobby."""
        mode = 'vote' if voting else 'host_pick'
        self.update(client.request('PATCH', self._path('/selection-mode'), json={'selection_mode': mode}) or {})

    def suggestions(self, client):
        """Every suggestion in the room (each with voted_by_me, this profile's own vote), page by page."""
        found, cursor = [], None
        while True:
            params = {'limit': SUGGESTION_PAGE}
            if cursor:
                params['cursor'] = cursor
            answer = client.request('GET', self._path('/suggestions'), params=params, headers=self._proof()) or {}
            found.extend(answer.get('items') or [])
            page = answer.get('page') or {}
            cursor = page.get('next_cursor')
            if not page.get('has_more') or not cursor or len(found) >= MAX_SUGGESTIONS:
                return found

    def suggest(self, client, contentId, contentType, title, subtitle=''):
        """Suggest something to watch (a movie or an episode) in a voting room."""
        body = {
            'suggestion_id': str(uuid.uuid4()),
            'content_id': contentId,
            'content_type': contentType,
            'title': title,
        }
        if subtitle:
            body['subtitle'] = subtitle
        client.request('POST', self._path('/suggestions'), json=body, headers=self._proof())

    def vote(self, client, suggestionId, on):
        """Vote for a suggestion, or take the vote back."""
        client.request(
            'POST' if on else 'DELETE', self._path(f'/suggestions/{suggestionId}/vote'), headers=self._proof()
        )

    def promote(self, client, suggestionId):
        """Play a suggestion for everyone, as the host (whatever the votes say)."""
        answer = client.request(
            'POST', self._path('/suggestions/promote'), json={'suggestion_id': suggestionId}, headers=self._proof()
        )
        self.update(answer or {})

    def _proof(self):
        return {'X-Room-Token': self.token}

    def read(self, client):
        self.update(client.request('GET', self._path(), headers={'X-Room-Token': self.token}) or {})

    def ticket(self, client):
        """A single-use credential for one socket connection."""
        answer = client.request('POST', self._path('/ws-ticket'), headers={'X-Room-Token': self.token}) or {}
        if answer.get('protocol') != PROTOCOL or not TICKET.match(answer.get('ticket') or ''):
            raise SiloError(0, 'bad_ticket', 'the room socket credential is not one this client speaks')
        return answer['ticket']

    def end(self, client):
        """End the room for everyone, as its host."""
        client.request('DELETE', self._path())


def titleOf(client, contentId):
    """What a room plays, by name: a film's title, an episode's series, numbers and title."""
    item = client.request('GET', f'/api/v2/catalog/items/{contentId}') or {}
    title = item.get('title') or ''
    if item.get('type') == 'episode' and item.get('series_title'):
        numbers = f'S{item.get("season_number") or 0}E{item.get("episode_number") or 0}'
        return f'{item["series_title"]} · {numbers} · {title}'
    return title


def join(client, code):
    """The room a code names (letters and digits, as Silo makes them)."""
    return Room(client.request('POST', '/api/v2/watch-together/join', json={'code': code.strip().upper()}) or {})


def create(client):
    """A new room this profile hosts, choosing what plays itself (host_pick)."""
    body = {'room_id': str(uuid.uuid4()), 'selection_mode': 'host_pick'}
    return Room(client.request('POST', '/api/v2/watch-together/rooms', json=body) or {})


def isoTime(seconds):
    # timezone.utc, not datetime.UTC: the add-on runs on Kodi 19's Python 3.8 too (xbmc.python 3.0.0)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'  # noqa: UP017


class RoomSocket:
    """
    The room socket, held open on its own thread (start()) or the caller's (run()): every frame goes to onMessage,
    pongs set the server's clock. Ends (ended says why) when the room closes, another device takes this membership,
    or a ticket is refused; stop() ends it from here.
    """

    def __init__(self, client, room, onMessage, socketFactory=ws.WebSocket, wait=None):
        self.client, self.room, self.onMessage = client, room, onMessage
        self.socketFactory = socketFactory
        self._stopped = threading.Event()
        self.wait = wait or self._stopped.wait
        self.ended = None
        self._sock = None
        self._lock = threading.Lock()
        self._samples = []
        self._thread = None

    @property
    def url(self):
        base = re.sub(r'^http', 'ws', self.client.baseUrl)
        return f'{base}/api/v2/watch-together/rooms/{self.room.id}/ws'

    def start(self):
        self._thread = threading.Thread(target=self.run, name='silo.room', daemon=True)
        self._thread.start()

    def stop(self):
        self._stopped.set()
        with self._lock:
            sock = self._sock
        if sock is not None:
            sock.close()

    @property
    def connected(self):
        return self._sock is not None

    def send(self, message):
        """Send a frame while connected; False when not (it is dropped: the room's next command or snapshot says what
        to do)."""
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
                if self.ended is None:
                    failures += 1
                    self.wait(min(30, 2**failures))
                continue
            failures = 0
            self._listen(sock)
            if self.ended is None and not self._stopped.is_set():
                self.wait(1)
        log(f'room socket ended: {self.ended or "stopped"}')

    def _connect(self):
        try:
            ticket = self.room.ticket(self.client)
            sock = self.socketFactory(self.url, [PROTOCOL, 'silo.ticket.' + ticket])
            sock.open()
        except SiloError as e:
            log(f'room ticket refused: {e.status} {e.type}')
            if e.status in TERMINAL:
                self.ended = 'refused'
            return None
        except ws.HandshakeError as e:
            log(f'room socket refused: {e.status}')
            if e.status in TERMINAL:
                self.ended = 'refused'
            return None
        except OSError as e:
            log(f'room socket unreachable: {type(e).__name__}')
            return None
        with self._lock:
            self._sock = sock
        threading.Thread(target=self._ping, args=(sock,), name='silo.room.ping', daemon=True).start()
        return sock

    def _listen(self, sock):
        try:
            while not self._stopped.is_set():
                text = sock.receive()
                if text is None:
                    break
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                if self._handle(message):
                    break
        finally:
            with self._lock:
                self._sock = None
            sock.close()

    def _handle(self, message):
        """Hand a frame on; True when it ends the room for this device."""
        kind = message.get('type')
        if kind == 'pong':
            self.clockSample(
                epoch(message.get('client_sent_at')),
                epoch(message.get('server_received_at')),
                epoch(message.get('server_sent_at')),
                time.time(),
            )
            return False
        if kind == 'snapshot' and message.get('room'):
            self.room.update(message['room'])
        self.onMessage(message)
        if kind == 'room_closed':
            self.ended = message.get('reason') or 'room_closed'
        elif kind == 'connection_replaced':
            self.ended = 'connection_replaced'
        elif kind == 'error' and message.get('code') in ('not_found', 'gone'):
            self.ended = message['code']
        return self.ended is not None

    def _ping(self, sock):
        while self._sock is sock and not self._stopped.is_set():
            self.send({'type': 'ping', 'client_sent_at': isoTime(time.time())})
            self._stopped.wait(FAST_PING_EVERY if len(self._samples) < FAST_PINGS else PING_EVERY)

    def clockSample(self, clientSent, serverReceived, serverSent, clientReceived):
        if None in (clientSent, serverReceived, serverSent, clientReceived):
            return
        roundTrip = (clientReceived - clientSent) - (serverSent - serverReceived)
        offset = ((serverReceived - clientSent) + (serverSent - clientReceived)) / 2
        self._samples = (self._samples + [(roundTrip, offset)])[-CLOCK_SAMPLES:]

    def serverTime(self, now=None):
        """The server's clock now (seconds since the epoch), from the fastest recent round trip; ours before any."""
        now = time.time() if now is None else now
        return now + (min(self._samples)[1] if self._samples else 0.0)
