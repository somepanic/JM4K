"""
silonet.discovery.find(): the Silo servers on the local network, from one multicast DNS question for the DNS-SD service
_silo._tcp (silo-server's docs/architecture/server-discovery.md). Answers are built here byte by byte, with the name
compression real responders use.
"""

import socket
import struct

from silonet import discovery

from .base import KodiTestCase


class Packet:
    """An mDNS answer, written the way a responder writes it: a name already written is a pointer the next time."""

    def __init__(self):
        self.data = bytearray(12)
        self.count = 0
        self.offsets = {}

    def name(self, name, into):
        labels = name.split('.')
        for i in range(len(labels)):
            suffix = '.'.join(labels[i:])
            if suffix in self.offsets:
                into += struct.pack('>H', 0xC000 | self.offsets[suffix])
                return
            self.offsets[suffix] = len(self.data) + len(into)
            into += bytes([len(labels[i])]) + labels[i].encode()
        into += b'\0'

    def record(self, name, rtype, rdata):
        """rdata: bytes, or a function writing it (one holding a name) at its place in the packet."""
        head = bytearray()
        self.name(name, head)
        self.data += head
        start = len(self.data) + 10
        body = bytearray()
        if callable(rdata):
            self.data += bytes(10)  # type, class, ttl, length: filled in below
            rdata(body, start)
            self.data[start - 10 : start] = struct.pack('>HHIH', rtype, 1, 120, len(body))
            self.data += body
        else:
            self.data += struct.pack('>HHIH', rtype, 1, 120, len(rdata)) + rdata
        self.count += 1
        return self

    def bytes(self):
        self.data[:12] = struct.pack('>6H', 0, 0x8400, 0, self.count, 0, 0)
        return bytes(self.data)


def answer(instance='Silo', host='silo-1234abcd-a1b2c3.local', ip='192.168.1.250', port=8090, serverId='srv-1'):
    packet = Packet()
    full = instance + '._silo._tcp.local'
    packet.record('_silo._tcp.local', discovery.PTR, lambda body, start: packet.name(full, body))
    srvData = lambda body, start: (body.extend(struct.pack('>HHH', 0, 0, port)), packet.name(host, body))
    packet.record(full, discovery.SRV, srvData)
    txt = b''.join(bytes([len(s)]) + s.encode() for s in ('v=1', 'id=' + serverId))
    packet.record(full, discovery.TXT, txt)
    packet.record(host, discovery.A, socket.inet_aton(ip))
    return packet.bytes()


class FakeSocket:
    """One question sent, the given datagrams answered, then nothing more."""

    def __init__(self, *datagrams):
        self.datagrams = list(datagrams)
        self.sent = []
        self.closed = False

    def sendto(self, data, address):
        self.sent.append((data, address))

    def settimeout(self, seconds):
        pass

    def recvfrom(self, size):
        if not self.datagrams:
            raise socket.timeout()  # noqa: UP041 (what discovery.find() catches on Python 3.8 too)
        return self.datagrams.pop(0), ('192.168.1.250', 5353)

    def close(self):
        self.closed = True


def found(sock):
    return [(f.name, f.baseUrl, f.serverId) for f in discovery.find(timeout=1, sock=sock)]


class DiscoveryTest(KodiTestCase):
    def test_asks_once_for_silo_servers(self):
        sock = FakeSocket()

        discovery.find(timeout=1, sock=sock)

        ((data, address),) = sock.sent
        self.assertEqual(address, ('224.0.0.251', 5353))
        # one question: PTR _silo._tcp.local, class IN, answered to the mDNS group
        self.assertEqual(struct.unpack_from('>6H', data), (0, 0, 1, 0, 0, 0))
        self.assertEqual(data[12:], b'\x05_silo\x04_tcp\x05local\x00' + struct.pack('>HH', 12, 1))
        self.assertTrue(sock.closed)

    def test_a_server_answering(self):
        self.assertEqual(found(FakeSocket(answer())), [('Silo', 'http://192.168.1.250:8090', 'srv-1')])

    def test_the_same_server_answering_twice_is_listed_once(self):
        self.assertEqual(len(found(FakeSocket(answer(), answer()))), 1)

    def test_two_servers(self):
        other = answer('Basement', 'silo-9999-ffffff.local', '192.168.1.20', 8091, 'srv-2')

        self.assertEqual(
            sorted(found(FakeSocket(answer(), other))),
            [('Basement', 'http://192.168.1.20:8091', 'srv-2'), ('Silo', 'http://192.168.1.250:8090', 'srv-1')],
        )

    def test_garbage_and_silence_find_nothing(self):
        self.assertEqual(found(FakeSocket(b'\x00\x01garbage', b'')), [])
        self.assertEqual(found(FakeSocket()), [])

    def test_no_network_finds_nothing(self):
        sock = FakeSocket()

        def sendto(data, address):
            raise OSError(101, 'Network is unreachable')

        sock.sendto = sendto

        self.assertEqual(found(sock), [])
        self.assertTrue(sock.closed)
