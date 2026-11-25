"""
silonet.ws: the small WebSocket client (RFC 6455) the watch-together room socket runs on (decision D26). Kodi's
Python has none, and the add-on bundles no library for it. Each test plays the server over a socket pair: the upgrade
handshake (subprotocols offered in order, credentials only in a subprotocol, the accept key checked), masked text
frames out, and text, fragmented, ping and close frames in.
"""

import base64
import hashlib
import json
import socket
import struct
import threading

from silonet import ws

from .base import KodiTestCase

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


def accept(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(opcode, payload=b'', fin=True):
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack('!H', n)
    else:
        head += bytes([127]) + struct.pack('!Q', n)
    return head + payload


def readFrame(sock):
    """A client frame: (opcode, payload unmasked, was masked)."""

    def exact(n):
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    b0, b1 = exact(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack('!H', exact(2))[0]
    elif n == 127:
        n = struct.unpack('!Q', exact(8))[0]
    mask = exact(4) if b1 & 0x80 else b'\0\0\0\0'
    payload = bytes(c ^ mask[i % 4] for i, c in enumerate(exact(n)))
    return b0 & 0x0F, payload, bool(b1 & 0x80)


class Server:
    """The far end of a socket pair: reads the upgrade, answers it, then whatever a test scripts."""

    def __init__(self, status=101, protocol='silo.room.v2', badAccept=False):
        self.client, self.sock = socket.socketpair()
        self.status, self.protocol, self.badAccept = status, protocol, badAccept
        self.request = None

    def connect(self, address, timeout=None):
        self.address = address
        threading.Thread(target=self.handshake, daemon=True).start()
        return self.client

    def handshake(self):
        data = b''
        while b'\r\n\r\n' not in data:
            data += self.sock.recv(4096)
        self.request = data.decode()
        headers = dict(line.split(': ', 1) for line in self.request.split('\r\n')[1:] if ': ' in line)
        key = headers['Sec-WebSocket-Key']
        if self.status != 101:
            self.sock.sendall(f'HTTP/1.1 {self.status} Forbidden\r\nContent-Length: 7\r\n\r\nrefused'.encode())
            return
        lines = [
            'HTTP/1.1 101 Switching Protocols',
            'Upgrade: websocket',
            'Connection: Upgrade',
            'Sec-WebSocket-Accept: ' + ('x' if self.badAccept else accept(key)),
            'Sec-WebSocket-Protocol: ' + self.protocol,
        ]
        self.sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())


def opened(server, url='ws://silo.lan:8090/api/v2/watch-together/rooms/r1/ws'):
    sock = ws.WebSocket(url, ['silo.room.v2', 'silo.ticket.T1'], connect=server.connect)
    sock.open()
    return sock


class WebSocketTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.sock.close()
            server.client.close()
        super().tearDown()

    def serve(self, **kwargs):
        server = Server(**kwargs)
        self.servers.append(server)
        return server

    def test_the_upgrade_offers_the_room_protocol_then_the_ticket(self):
        server = self.serve()
        sock = opened(server)

        request = server.request.split('\r\n')
        self.assertEqual(request[0], 'GET /api/v2/watch-together/rooms/r1/ws HTTP/1.1')
        self.assertIn('Sec-WebSocket-Protocol: silo.room.v2, silo.ticket.T1', request)
        self.assertIn('Host: silo.lan:8090', request)
        self.assertEqual(server.address, ('silo.lan', 8090))
        self.assertEqual(sock.protocol, 'silo.room.v2')

    def test_a_refused_upgrade_says_how(self):
        with self.assertRaises(ws.HandshakeError) as caught:
            opened(self.serve(status=403))
        self.assertEqual(caught.exception.status, 403)

    def test_a_wrong_accept_key_is_refused(self):
        with self.assertRaises(ws.HandshakeError):
            opened(self.serve(badAccept=True))

    def test_text_goes_out_masked(self):
        server = self.serve()
        sock = opened(server)

        sock.send(json.dumps({'type': 'ping'}))

        self.assertEqual(readFrame(server.sock), (0x1, b'{"type": "ping"}', True))

    def test_long_text_goes_out_whole(self):
        server = self.serve()
        sock = opened(server)
        text = 'x' * 70000

        sock.send(text)

        self.assertEqual(readFrame(server.sock)[1].decode(), text)

    def test_text_comes_in_whole_even_in_pieces(self):
        server = self.serve()
        sock = opened(server)
        server.sock.sendall(frame(0x1, b'{"type": "sna', fin=False) + frame(0x0, b'pshot"}'))
        server.sock.sendall(frame(0x1, ('y' * 300).encode()))

        self.assertEqual(sock.receive(), '{"type": "snapshot"}')
        self.assertEqual(sock.receive(), 'y' * 300)

    def test_a_ping_is_answered(self):
        server = self.serve()
        sock = opened(server)
        server.sock.sendall(frame(0x9, b'p1') + frame(0x1, b'after'))

        self.assertEqual(sock.receive(), 'after')
        self.assertEqual(readFrame(server.sock), (0xA, b'p1', True))

    def test_a_close_ends_it(self):
        server = self.serve()
        sock = opened(server)
        server.sock.sendall(frame(0x8, struct.pack('!H', 4001) + b'expired'))

        self.assertIsNone(sock.receive())
        self.assertEqual(sock.closeCode, 4001)
        self.assertEqual(readFrame(server.sock)[0], 0x8)

    def test_a_dropped_connection_ends_it(self):
        server = self.serve()
        sock = opened(server)
        server.sock.close()

        self.assertIsNone(sock.receive())
