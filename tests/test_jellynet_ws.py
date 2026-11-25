"""
jellynet.ws: pure-stdlib RFC 6455 WebSocket client for Jellyfin's /socket endpoint (Task 9.1).
Each test plays the server over a local socket pair: HTTP/1.1 Upgrade with Authorization header
(never a query-string token), optional Sec-WebSocket-Protocol, Accept validation, masked text
frames out, fragmented text receive, PING -> PONG, CLOSE, dropped socket, and handshake refusal.
"""

import base64
import hashlib
import json
import socket
import struct
import threading

from jellynet import ws

from .base import KodiTestCase

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def build_frame(opcode, payload=b'', fin=True):
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack('!H', n)
    else:
        head += bytes([127]) + struct.pack('!Q', n)
    return head + payload


def read_frame(sock):
    """Read one client frame: (opcode, unmasked payload, was_masked)."""

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


class FakeServer:
    """Far end of a socket pair: reads the HTTP Upgrade request and writes a scripted reply."""

    def __init__(self, status=101, protocol=None, bad_accept=False):
        self.client, self.sock = socket.socketpair()
        self.status = status
        self.protocol = protocol
        self.bad_accept = bad_accept
        self.request = None
        self.address = None

    def connect(self, address, timeout=None):
        self.address = address
        threading.Thread(target=self.handshake, daemon=True).start()
        return self.client

    def handshake(self):
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                return
            data += chunk
        self.request = data.decode('latin-1')
        headers = {}
        for line in self.request.split('\r\n')[1:]:
            if ': ' in line:
                k, v = line.split(': ', 1)
                headers[k.lower()] = v
        key = headers['sec-websocket-key']
        if self.status != 101:
            self.sock.sendall(f'HTTP/1.1 {self.status} Unauthorized\r\nContent-Length: 7\r\n\r\nrefused'.encode())
            return
        lines = [
            'HTTP/1.1 101 Switching Protocols',
            'Upgrade: websocket',
            'Connection: Upgrade',
            'Sec-WebSocket-Accept: ' + ('bad' if self.bad_accept else accept_key(key)),
        ]
        if self.protocol:
            lines.append('Sec-WebSocket-Protocol: ' + self.protocol)
        self.sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())


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
        server = FakeServer(**kwargs)
        self.servers.append(server)
        return server

    def open_socket(self, server, url='ws://jellyfin.lan:8096/socket', headers=None, protocols=None):
        sock = ws.WebSocket(url, headers=headers, protocols=protocols, connect=server.connect)
        sock.open()
        return sock

    def test_upgrade_sends_authorization_header_not_query_token(self):
        server = self.serve()
        auth = 'MediaBrowser Token="tok-123", Client="JellyMod", Version="0.1.0", DeviceId="dev-1", Device="Kodi"'

        sock = self.open_socket(server, headers={'Authorization': auth})

        lines = server.request.split('\r\n')
        self.assertEqual(lines[0], 'GET /socket HTTP/1.1')
        self.assertNotIn('?', lines[0])
        self.assertIn('Host: jellyfin.lan:8096', lines)
        self.assertIn('Authorization: ' + auth, lines)
        self.assertFalse(any(line.lower().startswith('sec-websocket-protocol:') for line in lines))
        self.assertEqual(server.address, ('jellyfin.lan', 8096))
        self.assertIsNone(sock.protocol)

    def test_upgrade_sends_protocols_only_when_given(self):
        server = self.serve(protocol='syncplay.v1')

        sock = self.open_socket(server, protocols=['syncplay.v1'])

        lines = server.request.split('\r\n')
        self.assertIn('Sec-WebSocket-Protocol: syncplay.v1', lines)
        self.assertEqual(sock.protocol, 'syncplay.v1')

    def test_masked_send_and_text_receive(self):
        server = self.serve()
        sock = self.open_socket(server)

        sock.send(json.dumps({'MessageType': 'KeepAlive'}))
        self.assertEqual(read_frame(server.sock), (0x1, b'{"MessageType": "KeepAlive"}', True))

        # Large outbound text frame (> 65535 bytes -> 64-bit length encoding)
        large = 'a' * 70000
        sock.send(large)
        self.assertEqual(read_frame(server.sock)[1].decode(), large)

        # Fragmented inbound text frame + medium (> 125 bytes) frame
        server.sock.sendall(build_frame(0x1, b'{"MessageType":', fin=False) + build_frame(0x0, b' "KeepAlive"}'))
        server.sock.sendall(build_frame(0x1, ('z' * 300).encode()))
        self.assertEqual(sock.receive(), '{"MessageType": "KeepAlive"}')
        self.assertEqual(sock.receive(), 'z' * 300)

    def test_ping_answered_with_pong(self):
        server = self.serve()
        sock = self.open_socket(server)
        server.sock.sendall(build_frame(0x9, b'hb-1') + build_frame(0x1, b'ready'))

        self.assertEqual(sock.receive(), 'ready')
        self.assertEqual(read_frame(server.sock), (0xA, b'hb-1', True))

    def test_close_ends_receive(self):
        server = self.serve()
        sock = self.open_socket(server)
        server.sock.sendall(build_frame(0x8, struct.pack('!H', 1000) + b'bye'))

        self.assertIsNone(sock.receive())
        self.assertEqual(sock.closeCode, 1000)
        self.assertEqual(read_frame(server.sock)[0], 0x8)

    def test_dropped_socket_ends_receive(self):
        server = self.serve()
        sock = self.open_socket(server)
        server.sock.close()

        self.assertIsNone(sock.receive())

    def test_handshake_refusal_raises(self):
        with self.assertRaises(ws.HandshakeError) as caught:
            self.open_socket(self.serve(status=401))
        self.assertEqual(caught.exception.status, 401)

        with self.assertRaises(ws.HandshakeError) as bad:
            self.open_socket(self.serve(bad_accept=True))
        self.assertIsNone(bad.exception.status)
