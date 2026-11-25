"""
jellynet.syncplay: Jellyfin 10.11.5 SyncPlay REST helpers, tick/timestamp conversions,
and SyncPlaySocket WebSocket client (Task 9.1).
"""

import json
from typing import ClassVar

from jellynet import syncplay, ws
from jellynet.client import ClientIdentity, JellyfinClient

from .base import KodiTestCase
from .jellynet_fakes import FakeResponse, FakeSession

BASE = 'http://jellyfin.lan:8096'
IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Kodi', 'device-uuid-1')


def make_client(session, base_url=BASE, token='tok-secret-1', username='alice'):
    return JellyfinClient(base_url, IDENTITY, token=token, username=username, session=session)


def ok(body=None, status=200):
    return FakeResponse(status, body)


class FakeSocket:
    """Scripted WebSocket stand-in for SyncPlaySocket tests."""

    made: ClassVar[list] = []

    def __init__(self, url, headers=None, protocols=None, frames=(), open_error=None):
        self.url = url
        self.headers = dict(headers or {})
        self.protocols = list(protocols or ())
        self.frames = [json.dumps(f) for f in frames]
        self.open_error = open_error
        self.sent = []
        self.closed = False
        FakeSocket.made.append(self)

    def open(self):
        if self.open_error is not None:
            raise self.open_error
        return self

    def send(self, text):
        self.sent.append(json.loads(text))

    def receive(self):
        return self.frames.pop(0) if self.frames else None

    def close(self, code=1000, send=True):
        self.closed = True


def make_factory(*steps):
    """Build a socket factory; each step is either a list of frames or an Exception raised by open()."""
    FakeSocket.made = []
    queue = list(steps)

    def factory(url, headers=None, protocols=None):
        step = queue.pop(0) if queue else ()
        if isinstance(step, Exception):
            return FakeSocket(url, headers=headers, protocols=protocols, open_error=step)
        return FakeSocket(url, headers=headers, protocols=protocols, frames=step)

    return factory


class SyncPlayRestTest(KodiTestCase):
    def test_rest_group_and_transport_calls(self):
        group_dto = {
            'GroupId': 'grp-1',
            'GroupName': "Alice's group",
            'State': 'Idle',
            'Participants': ['alice'],
            'LastUpdatedAt': '2026-11-25T10:00:00.000Z',
        }
        session = FakeSession(
            ok([group_dto]),  # list_groups
            ok(group_dto),  # create_group
            ok(None, 204),  # join_group
            ok(None, 204),  # leave_group
            ok(None, 204),  # queue_items
            ok(None, 204),  # pause
            ok(None, 204),  # unpause
            ok(None, 204),  # stop
            ok(None, 204),  # buffering
            ok(None, 204),  # ready
            ok(None, 204),  # ping
            ok(None, 204),  # next_item
            ok(None, 204),  # previous_item
            ok({'Id': 'mov-1', 'Type': 'Movie', 'Name': 'Arrival'}),  # title_of movie
            ok(
                {
                    'Id': 'ep-2',
                    'Type': 'Episode',
                    'Name': 'Pilot',
                    'SeriesName': 'Andor',
                    'ParentIndexNumber': 1,
                    'IndexNumber': 2,
                }
            ),  # title_of episode
        )
        client = make_client(session)

        groups = syncplay.list_groups(client)
        created = syncplay.create_group(client, "Alice's group")
        syncplay.join_group(client, 'grp-1')
        syncplay.leave_group(client)
        syncplay.queue_items(client, ['mov-2', 'mov-3'], mode='QueueNext')
        syncplay.pause(client)
        syncplay.unpause(client)
        syncplay.stop(client)
        syncplay.buffering(client, '2026-11-25T10:00:01.000Z', 42.5, True, 'pli-1')
        syncplay.ready(client, '2026-11-25T10:00:02.000Z', 42.5, True, 'pli-1')
        syncplay.ping(client, 37.8)
        syncplay.next_item(client, 'pli-1')
        syncplay.previous_item(client, 'pli-2')
        movie_title = syncplay.title_of(client, 'mov-1')
        ep_title = syncplay.title_of(client, 'ep-2')

        self.assertEqual(groups, [group_dto])
        self.assertEqual(created['GroupId'], 'grp-1')
        self.assertEqual(movie_title, 'Arrival')
        self.assertEqual(ep_title, 'Andor · S1E2 · Pilot')

        calls = [(c['method'], c['url'].replace(BASE, ''), c['json']) for c in session.calls]
        self.assertEqual(
            calls,
            [
                ('GET', '/SyncPlay/List', None),
                ('POST', '/SyncPlay/New', {'GroupName': "Alice's group"}),
                ('POST', '/SyncPlay/Join', {'GroupId': 'grp-1'}),
                ('POST', '/SyncPlay/Leave', None),
                ('POST', '/SyncPlay/Queue', {'ItemIds': ['mov-2', 'mov-3'], 'Mode': 'QueueNext'}),
                ('POST', '/SyncPlay/Pause', None),
                ('POST', '/SyncPlay/Unpause', None),
                ('POST', '/SyncPlay/Stop', None),
                (
                    'POST',
                    '/SyncPlay/Buffering',
                    {
                        'When': '2026-11-25T10:00:01.000Z',
                        'PositionTicks': 425_000_000,
                        'IsPlaying': True,
                        'PlaylistItemId': 'pli-1',
                    },
                ),
                (
                    'POST',
                    '/SyncPlay/Ready',
                    {
                        'When': '2026-11-25T10:00:02.000Z',
                        'PositionTicks': 425_000_000,
                        'IsPlaying': True,
                        'PlaylistItemId': 'pli-1',
                    },
                ),
                ('POST', '/SyncPlay/Ping', {'Ping': 37}),
                ('POST', '/SyncPlay/NextItem', {'PlaylistItemId': 'pli-1'}),
                ('POST', '/SyncPlay/PreviousItem', {'PlaylistItemId': 'pli-2'}),
                ('GET', '/Items/mov-1', None),
                ('GET', '/Items/ep-2', None),
            ],
        )

    def test_ticks_conversion_for_seek_and_queue(self):
        session = FakeSession(ok(None, 204), ok(None, 204))
        client = make_client(session)

        self.assertEqual(syncplay.TICKS_PER_SECOND, 10_000_000)
        self.assertEqual(syncplay.seconds_to_ticks(12.345), 123_450_000)
        self.assertEqual(syncplay.seconds_to_ticks(-5.0), 0)
        self.assertAlmostEqual(syncplay.ticks_to_seconds(123_450_000), 12.345, places=6)
        self.assertEqual(syncplay.ticks_to_seconds(None), 0.0)
        self.assertEqual(syncplay.iso_now(1000.25), '1970-01-01T00:16:40.250Z')
        self.assertAlmostEqual(syncplay.parse_iso('1970-01-01T00:16:40.2500000Z'), 1000.25, places=6)
        self.assertIsNone(syncplay.parse_iso('not-a-timestamp'))

        syncplay.set_new_queue(client, ['mov-1', 'mov-2'], playing_position=1, start_seconds=90.5)
        syncplay.seek(client, 305.25)

        self.assertEqual(
            session.calls[0]['json'],
            {
                'PlayingQueue': ['mov-1', 'mov-2'],
                'PlayingItemPosition': 1,
                'StartPositionTicks': 905_000_000,
            },
        )
        self.assertEqual(session.calls[0]['url'], BASE + '/SyncPlay/SetNewQueue')
        self.assertEqual(session.calls[1]['url'], BASE + '/SyncPlay/Seek')
        self.assertEqual(session.calls[1]['json'], {'PositionTicks': 3_052_500_000})


class SyncPlaySocketTest(KodiTestCase):
    def test_socket_answers_keepalive_and_dispatches_commands(self):
        client = make_client(FakeSession())
        commands = []
        cmd_data = {
            'GroupId': 'grp-1',
            'PlaylistItemId': 'pli-1',
            'When': '2026-11-25T10:00:05.000Z',
            'PositionTicks': 120_000_000,
            'Command': 'Unpause',
            'EmittedAt': '2026-11-25T10:00:04.500Z',
        }
        frames = [
            {'MessageType': 'ForceKeepAlive', 'Data': 60},
            {'MessageType': 'KeepAlive'},
            {'MessageType': 'SyncPlayCommand', 'Data': cmd_data},
        ]

        link = None

        def on_command(cmd):
            commands.append(cmd)
            link.stop()

        link = syncplay.SyncPlaySocket(
            client,
            on_command=on_command,
            socket_factory=make_factory(frames),
            wait=lambda seconds: None,
        )
        link.run()

        self.assertEqual(len(FakeSocket.made), 1)
        sock = FakeSocket.made[0]
        self.assertEqual(sock.url, 'ws://jellyfin.lan:8096/socket')
        self.assertNotIn('?', sock.url)
        self.assertEqual(sock.headers['Authorization'], client.auth_header())
        self.assertEqual(
            sock.sent,
            [{'MessageType': 'KeepAlive'}, {'MessageType': 'KeepAlive'}],
        )
        self.assertEqual(link.keepalive_interval, 30.0)
        self.assertEqual(commands, [cmd_data])

    def test_socket_dispatches_group_updates(self):
        client = make_client(FakeSession())
        updates = []
        joined = {
            'GroupId': 'grp-1',
            'Type': 'GroupJoined',
            'Data': {'GroupId': 'grp-1', 'GroupName': 'Movie Night', 'State': 'Idle', 'Participants': ['alice']},
        }
        queue = {
            'GroupId': 'grp-1',
            'Type': 'PlayQueue',
            'Data': {
                'Reason': 'NewPlaylist',
                'Playlist': [{'ItemId': 'mov-1', 'PlaylistItemId': 'pli-1'}],
                'PlayingItemIndex': 0,
                'StartPositionTicks': 0,
                'IsPlaying': True,
            },
        }
        state = {
            'GroupId': 'grp-1',
            'Type': 'StateUpdate',
            'Data': {'State': 'Playing', 'Reason': 'Ready'},
        }

        link = None

        def on_update(update):
            updates.append(update)
            if len(updates) == 3:
                link.stop()

        link = syncplay.SyncPlaySocket(
            client,
            on_group_update=on_update,
            socket_factory=make_factory(
                [
                    {'MessageType': 'SyncPlayGroupUpdate', 'Data': joined},
                    {'MessageType': 'SyncPlayGroupUpdate', 'Data': queue},
                    {'MessageType': 'SyncPlayGroupUpdate', 'Data': state},
                ]
            ),
            wait=lambda seconds: None,
        )
        link.run()

        self.assertEqual([u['Type'] for u in updates], ['GroupJoined', 'PlayQueue', 'StateUpdate'])
        self.assertEqual(updates[0]['Data']['GroupName'], 'Movie Night')

    def test_socket_stops_on_401_and_reconnects_on_drop(self):
        client = make_client(FakeSession())
        # Case 1: 401 refused stops cleanly without reconnecting
        refused = syncplay.SyncPlaySocket(
            client,
            socket_factory=make_factory(ws.HandshakeError('upgrade refused: 401', status=401)),
            wait=lambda seconds: None,
        )
        refused.run()
        self.assertEqual(refused.ended, 'refused')
        self.assertEqual(len(FakeSocket.made), 1)

        # Case 2: transient network error + drop reconnects with backoff, then stops cleanly
        waits = []
        updates = []
        link = None

        def on_update(update):
            updates.append(update)
            link.stop()

        link = syncplay.SyncPlaySocket(
            client,
            on_group_update=on_update,
            socket_factory=make_factory(
                OSError('connection reset'),
                [{'MessageType': 'KeepAlive'}],  # connects, then drops (receive() -> None)
                [
                    {
                        'MessageType': 'SyncPlayGroupUpdate',
                        'Data': {'GroupId': 'grp-1', 'Type': 'UserJoined', 'Data': 'bob'},
                    }
                ],
            ),
            wait=waits.append,
        )
        link.run()

        self.assertEqual(len(FakeSocket.made), 3)
        self.assertEqual(waits, [2, 1])  # backoff after OSError (2^1), brief wait after drop (1)
        self.assertEqual(len(updates), 1)
        self.assertIsNone(link.ended)

    def test_clock_offset_and_server_time(self):
        client = make_client(FakeSession())
        link = syncplay.SyncPlaySocket(client)

        self.assertAlmostEqual(link.server_time(200.0), 200.0, places=3)
        # Server is 5.0 s ahead with 0.4 s RTT
        link.clock_sample(100.0, 105.2, 105.3, 100.5)
        self.assertAlmostEqual(link.server_time(200.0), 205.0, places=3)
        self.assertAlmostEqual(link.rtt_ms, 400.0, places=1)
