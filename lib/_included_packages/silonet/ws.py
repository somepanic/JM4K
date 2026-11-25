"""
A small WebSocket client (RFC 6455) for Silo's watch-together room socket (decision D26): Kodi's Python has none, and
the add-on bundles no library for it. Text frames only, which is all the room protocol uses; frames out are masked,
pings are answered, a close ends the connection. Credentials travel in a subprotocol, never the URL.
"""

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
TEXT, BINARY, CLOSE, PING, PONG, CONTINUATION = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0
MAX_HEADER = 65536  # bytes of upgrade response read at most
MAX_MESSAGE = 16 * 1024 * 1024  # a message this long ends the connection


class HandshakeError(Exception):
    """The server refused the upgrade (status: its HTTP status) or answered it wrongly (status None)."""

    def __init__(self, message, status=None, body=''):
        super().__init__(message)
        self.status, self.body = status, body


class WebSocket:
    def __init__(self, url, protocols, timeout=10, connect=socket.create_connection):
        self.url, self.protocols, self.timeout, self._connect = url, list(protocols), timeout, connect
        self.protocol = None
        self.closeCode = None
        self._sock = None
        self._buffer = b''
        self._send = threading.Lock()
        self._closed = False

    def open(self):
        parts = urlsplit(self.url)
        secure = parts.scheme == 'wss'
        host = parts.hostname
        port = parts.port or (443 if secure else 80)
        sock = self._connect((host, port), timeout=self.timeout)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        self._sock = sock
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        default = (secure and port == 443) or (not secure and port == 80)
        lines = [
            f'GET {path} HTTP/1.1',
            'Host: ' + (host if default else f'{host}:{port}'),
            'Upgrade: websocket',
            'Connection: Upgrade',
            'Sec-WebSocket-Key: ' + key,
            'Sec-WebSocket-Version: 13',
            'Sec-WebSocket-Protocol: ' + ', '.join(self.protocols),
        ]
        sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())
        status, headers, body = self._response()
        if status != 101:
            self.close(send=False)
            raise HandshakeError(f'upgrade refused: {status}', status, body)
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        chosen = headers.get('sec-websocket-protocol')
        if headers.get('sec-websocket-accept') != expected or chosen not in self.protocols[:1]:
            self.close(send=False)
            raise HandshakeError('upgrade answered wrongly')
        self.protocol = chosen
        sock.settimeout(None)
        return self

    def _response(self):
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = self._sock.recv(4096)
            if not chunk or len(data) > MAX_HEADER:
                raise HandshakeError('upgrade answer cut short')
            data += chunk
        head, _, rest = data.partition(b'\r\n\r\n')
        lines = head.decode('latin-1').split('\r\n')
        try:
            status = int(lines[0].split(' ', 2)[1])
        except (IndexError, ValueError):
            raise HandshakeError('upgrade answer unreadable')
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(':')
            headers[name.strip().lower()] = value.strip()
        self._buffer = rest
        return status, headers, rest.decode('utf-8', 'replace')

    def send(self, text):
        self._frame(TEXT, text.encode('utf-8'))

    def _frame(self, opcode, payload):
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('!H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('!Q', n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send:
            self._sock.sendall(head + mask + masked)

    def _exact(self, n):
        while len(self._buffer) < n:
            chunk = self._sock.recv(max(4096, n - len(self._buffer)))
            if not chunk:
                raise EOFError
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def _readFrame(self):
        b0, b1 = self._exact(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack('!H', self._exact(2))[0]
        elif n == 127:
            n = struct.unpack('!Q', self._exact(8))[0]
        if n > MAX_MESSAGE:
            raise EOFError
        mask = self._exact(4) if b1 & 0x80 else None
        payload = self._exact(n)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return bool(b0 & 0x80), b0 & 0x0F, payload

    def receive(self):
        """The next text message; None once the connection has ended (a close, or the socket dropped)."""
        message, kind = b'', None
        try:
            while True:
                fin, opcode, payload = self._readFrame()
                if opcode == PING:
                    self._frame(PONG, payload)
                    continue
                if opcode == PONG:
                    continue
                if opcode == CLOSE:
                    self.closeCode = struct.unpack('!H', payload[:2])[0] if len(payload) >= 2 else 1005
                    self.close()
                    return None
                if opcode in (TEXT, BINARY):
                    message, kind = payload, opcode
                elif opcode == CONTINUATION:
                    message += payload
                if len(message) > MAX_MESSAGE:
                    raise EOFError
                if fin and kind == TEXT:
                    return message.decode('utf-8', 'replace')
                if fin:
                    message, kind = b'', None  # binary: not part of the room protocol
        except (EOFError, OSError, ValueError):
            self.close(send=False)
            return None

    def close(self, code=1000, send=True):
        if self._closed or self._sock is None:
            return
        self._closed = True
        if send:
            try:
                self._frame(CLOSE, struct.pack('!H', code))
            except OSError:
                pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
