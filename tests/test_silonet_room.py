"""
silonet.room: a watch-together room (decision D26) from Silo's /api/v2/watch-together: joining by code, creating one
and choosing what it plays as its host, and the room socket that keeps it in step - a fresh ticket for every
connection (each lasts at most five minutes), the frames handed on, the server's clock from ping and pong, and the
end of the room or of this device's membership ending it. Responses are written inline in the shape of
/api/v2/openapi.json and docs/realtime-api.md (Silo build-2987).
"""

import json
from typing import ClassVar

from silonet import room

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import server

BASE = 'http://silo.lan:8090/api/v2'
SNAPSHOT = {'room_id': 'r1', 'phase': 'lobby', 'code': 'ABCD', 'generation': 3, 'self_role': 'guest'}
TICKET = {'ticket': 'T' * 43, 'expires_in': 30, 'max_connection_seconds': 300, 'protocol': 'silo.room.v2'}


def ok(body=None, status=200):
    return FakeResponse(status, body)


class FakeSocket:
    """A room socket that answers with scripted frames, then ends."""

    made: ClassVar[list] = []

    def __init__(self, url, protocols, frames=()):
        self.url, self.protocols = url, protocols
        self.frames = [json.dumps(f) for f in frames]
        self.sent = []
        self.closed = False
        FakeSocket.made.append(self)

    def open(self):
        self.protocol = 'silo.room.v2'
        return self

    def send(self, text):
        self.sent.append(json.loads(text))

    def receive(self):
        return self.frames.pop(0) if self.frames else None

    def close(self, code=1000, send=True):
        self.closed = True


def sockets(*connections):
    """A socket factory whose nth connection answers with connections[n]."""
    FakeSocket.made = []
    queue = list(connections)
    return lambda url, protocols: FakeSocket(url, protocols, queue.pop(0) if queue else ())


class RoomRequestsTest(KodiTestCase):
    def test_joining_by_code(self):
        session = FakeSession(ok({'room': SNAPSHOT, 'room_access_token': 'proof'}))

        joined = room.join(server(session).client, ' abcd ')

        self.assertEqual((session.calls[0]['method'], session.calls[0]['url']), ('POST', BASE + '/watch-together/join'))
        self.assertEqual(session.calls[0]['json'], {'code': 'ABCD'})
        self.assertEqual((joined.id, joined.token, joined.snapshot['code']), ('r1', 'proof', 'ABCD'))

    def test_creating_one_to_host(self):
        session = FakeSession(ok({'room': dict(SNAPSHOT, self_role='host'), 'room_access_token': 'proof'}, 201))

        made = room.create(server(session).client)

        body = session.calls[0]['json']
        self.assertEqual(session.calls[0]['url'], BASE + '/watch-together/rooms')
        self.assertEqual(body['selection_mode'], 'host_pick')
        self.assertEqual(len(body['room_id']), 36)
        self.assertTrue(made.isHost)

    def test_choosing_what_it_plays(self):
        session = FakeSession(ok({'room': dict(SNAPSHOT, phase='playing'), 'room_access_token': 'proof2'}))
        joined = room.Room({'room': SNAPSHOT, 'room_access_token': 'proof'})

        joined.select(server(session).client, 'movie-tmdb-238', 4711)

        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url'], session.calls[0]['json']),
            ('PUT', BASE + '/watch-together/rooms/r1/selection', {'content_id': 'movie-tmdb-238', 'file_id': '4711'}),
        )
        self.assertEqual((joined.snapshot['phase'], joined.token), ('playing', 'proof2'))

    def test_the_host_stages_starts_and_stops(self):
        lobby = {
            'room': dict(SNAPSHOT, self_role='host', selected_content_id='movie-tmdb-238'),
            'room_access_token': 'p2',
        }
        session = FakeSession(
            ok(lobby), ok({'room': dict(lobby['room'], phase='playing')}), ok({'room': lobby['room']})
        )
        joined = room.Room({'room': SNAPSHOT, 'room_access_token': 'proof'})
        client = server(session).client

        joined.stage(client, 'movie-tmdb-238', 4711)
        joined.start(client)
        joined.stop(client)

        self.assertEqual(
            [(c['method'], c['url'].replace(BASE, ''), c['json']) for c in session.calls],
            [
                (
                    'PUT',
                    '/watch-together/rooms/r1/staged-selection',
                    {'content_id': 'movie-tmdb-238', 'file_id': '4711'},
                ),
                ('POST', '/watch-together/rooms/r1/playback/start', None),
                ('POST', '/watch-together/rooms/r1/playback/stop', None),
            ],
        )
        self.assertEqual(joined.snapshot['phase'], 'lobby')

    def test_the_host_lets_guests_play_and_pause(self):
        session = FakeSession(ok({'room': dict(SNAPSHOT, guest_control_policy='guest_play_pause')}))
        joined = room.Room({'room': SNAPSHOT, 'room_access_token': 'proof'})

        joined.setGuestControl(server(session).client, True)

        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url'], session.calls[0]['json']),
            ('PATCH', BASE + '/watch-together/rooms/r1/policy', {'guest_control_policy': 'guest_play_pause'}),
        )
        self.assertEqual(joined.snapshot['guest_control_policy'], 'guest_play_pause')

    def test_the_host_switches_to_voting(self):
        session = FakeSession(ok({'room': dict(SNAPSHOT, selection_mode='vote')}))
        joined = room.Room({'room': SNAPSHOT, 'room_access_token': 'proof'})

        joined.setVoting(server(session).client, True)

        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url'], session.calls[0]['json']),
            ('PATCH', BASE + '/watch-together/rooms/r1/selection-mode', {'selection_mode': 'vote'}),
        )
        self.assertEqual(joined.snapshot['selection_mode'], 'vote')

    def test_suggestions_are_read_page_by_page(self):
        first = {
            'items': [{'id': 's1', 'title': 'Dune', 'vote_count': 2}],
            'page': {'has_more': True, 'next_cursor': 'c2'},
        }
        second = {'items': [{'id': 's2', 'title': 'Heat', 'vote_count': 0}], 'page': {'has_more': False}}
        session = FakeSession(ok(first), ok(second))
        joined = room.Room({'room': SNAPSHOT, 'room_access_token': 'proof'})

        found = joined.suggestions(server(session).client)

        self.assertEqual([s['id'] for s in found], ['s1', 's2'])
        self.assertEqual(session.calls[1]['params'], {'limit': 100, 'cursor': 'c2'})
        self.assertEqual(session.calls[0]['headers']['X-Room-Token'], 'proof')

    def test_suggesting_voting_and_promoting(self):
        session = FakeSession(
            ok({'suggestion_id': 'x'}, 201), ok(None, 204), ok(None, 204), ok({'room': dict(SNAPSHOT, phase='playing')})
        )
        joined = room.Room({'room': SNAPSHOT, 'room_access_token': 'proof'})
        client = server(session).client

        joined.suggest(client, 'episode-tvdb-78857-1-2', 'episode', 'My Name Is Konohamaru!', 'Naruto · S1E2')
        joined.vote(client, 's1', True)
        joined.vote(client, 's1', False)
        joined.promote(client, 's1')

        calls = [(c['method'], c['url'].replace(BASE + '/watch-together/rooms/r1', '')) for c in session.calls]
        self.assertEqual(
            calls,
            [
                ('POST', '/suggestions'),
                ('POST', '/suggestions/s1/vote'),
                ('DELETE', '/suggestions/s1/vote'),
                ('POST', '/suggestions/promote'),
            ],
        )
        body = session.calls[0]['json']
        self.assertEqual(
            {k: body[k] for k in ('content_id', 'content_type', 'title', 'subtitle')},
            {
                'content_id': 'episode-tvdb-78857-1-2',
                'content_type': 'episode',
                'title': 'My Name Is Konohamaru!',
                'subtitle': 'Naruto · S1E2',
            },
        )
        self.assertEqual(len(body['suggestion_id']), 36)
        self.assertEqual(session.calls[3]['json'], {'suggestion_id': 's1'})
        self.assertTrue(all(c['headers']['X-Room-Token'] == 'proof' for c in session.calls))
        self.assertEqual(joined.snapshot['phase'], 'playing')

    def test_what_a_room_plays_by_name(self):
        session = FakeSession(
            ok({'content_id': 'movie-tmdb-238', 'type': 'movie', 'title': 'The Godfather'}),
            ok(
                {
                    'content_id': 'episode-tvdb-78857-1-2',
                    'type': 'episode',
                    'title': 'My Name Is Konohamaru!',
                    'series_title': 'Naruto',
                    'season_number': 1,
                    'episode_number': 2,
                }
            ),
        )
        client = server(session).client

        self.assertEqual(room.titleOf(client, 'movie-tmdb-238'), 'The Godfather')
        self.assertEqual(room.titleOf(client, 'episode-tvdb-78857-1-2'), 'Naruto · S1E2 · My Name Is Konohamaru!')

    def test_a_ticket_needs_the_room_proof(self):
        session = FakeSession(ok(TICKET, 201))
        joined = room.Room({'room': SNAPSHOT, 'room_access_token': 'proof'})

        ticket = joined.ticket(server(session).client)

        self.assertEqual(session.calls[0]['url'], BASE + '/watch-together/rooms/r1/ws-ticket')
        self.assertEqual(session.calls[0]['headers']['X-Room-Token'], 'proof')
        self.assertEqual(ticket, 'T' * 43)


class RoomSocketTest(KodiTestCase):
    def connection(self, *connections, responses=None):
        session = FakeSession(*(responses or [ok(TICKET, 201) for _ in connections]))
        self.got = []
        joined = room.Room({'room': SNAPSHOT, 'room_access_token': 'proof'})
        link = room.RoomSocket(
            server(session).client, joined, self.got.append, socketFactory=sockets(*connections), wait=lambda s: None
        )
        self.session = link.client.session
        return link

    def test_it_connects_with_a_fresh_ticket(self):
        link = self.connection(
            [{'type': 'snapshot', 'room': dict(SNAPSHOT, generation=4)}, {'type': 'room_closed', 'reason': 'host_left'}]
        )

        link.run()

        sock = FakeSocket.made[0]
        self.assertEqual(sock.url, 'ws://silo.lan:8090/api/v2/watch-together/rooms/r1/ws')
        self.assertEqual(sock.protocols, ['silo.room.v2', 'silo.ticket.' + 'T' * 43])
        self.assertEqual(sock.sent[0]['type'], 'ping')
        self.assertEqual([m['type'] for m in self.got], ['snapshot', 'room_closed'])
        self.assertEqual(link.ended, 'host_left')

    def test_a_dropped_connection_reconnects(self):
        link = self.connection(
            [{'type': 'snapshot', 'room': SNAPSHOT}], [{'type': 'connection_replaced', 'reason': 'elsewhere'}]
        )

        link.run()

        self.assertEqual(len(FakeSocket.made), 2)
        self.assertEqual(len(self.session.calls), 2)  # a ticket for each
        self.assertEqual(link.ended, 'connection_replaced')

    def test_a_refused_ticket_ends_it(self):
        link = self.connection([], responses=[problem(409, 'conflict')])

        link.run()

        self.assertEqual(FakeSocket.made, [])
        self.assertEqual(link.ended, 'refused')

    def test_messages_go_out_while_connected(self):
        link = self.connection([{'type': 'room_closed', 'reason': 'ended'}])
        link.run()

        self.assertFalse(link.send({'type': 'state_report'}))  # not connected any more

    def test_the_servers_clock_from_a_pong(self):
        link = self.connection()
        # sent at 100.0, the server took it at 105.2 and answered at 105.3, back at 100.5: the server is 5 s ahead
        link.clockSample(100.0, 105.2, 105.3, 100.5)

        self.assertAlmostEqual(link.serverTime(200.0), 205.0, places=3)
