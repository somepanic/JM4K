"""
jellynet.discovery: finding Jellyfin servers on the local network via UDP broadcast on port 7359
("who is JellyfinServer?"). When a reply's Address points at a Docker bridge or other host that
differs from the UDP sender IP, the sender IP is preferred with the reply's scheme and port.
"""

import json
import socket

from jellynet import discovery

from .base import KodiTestCase


def reply(address='http://192.168.1.250:8096', server_id='srv-1', name='Home Jellyfin'):
    return json.dumps({'Address': address, 'Id': server_id, 'Name': name}).encode('utf-8')


class FakeSocket:
    """Records broadcasts sent, returns canned (payload, sender) datagrams, then times out."""

    def __init__(self, *packets):
        self.packets = list(packets)
        self.sent = []
        self.closed = False

    def sendto(self, data, address):
        self.sent.append((data, address))

    def settimeout(self, seconds):
        pass

    def recvfrom(self, size):
        if not self.packets:
            raise socket.timeout()  # noqa: UP041
        return self.packets.pop(0)

    def close(self):
        self.closed = True


class DiscoveryTest(KodiTestCase):
    def test_discovery_reads_replies(self):
        sock = FakeSocket(
            (reply('http://192.168.1.250:8096', 'srv-1', 'Living Room'), ('192.168.1.250', 7359)),
            (reply('http://192.168.1.250:8096', 'srv-1', 'Living Room'), ('192.168.1.250', 7359)),
            (reply('https://192.168.1.20:8920/jf/', 'srv-2', 'Office'), ('192.168.1.20', 7359)),
        )

        found = discovery.discover(timeout=1.0, sock=sock)

        self.assertEqual(sock.sent, [(b'who is JellyfinServer?', ('255.255.255.255', 7359))])
        self.assertTrue(sock.closed)
        self.assertEqual(
            [(f.url, f.server_id, f.name) for f in found],
            [
                ('http://192.168.1.250:8096', 'srv-1', 'Living Room'),
                ('https://192.168.1.20:8920/jf', 'srv-2', 'Office'),
            ],
        )

    def test_discovery_unreachable_reply_url_uses_sender_ip(self):
        # Docker bridge LocalAddress (172.19.0.37) cannot be reached by Kodi; sender IP (192.168.1.250) can.
        sock = FakeSocket(
            (reply('http://172.19.0.37:8096', 'srv-1', 'Docker Jellyfin'), ('192.168.1.250', 7359)),
            (reply('https://172.19.0.38:8920/media/', 'srv-2', 'TLS Docker'), ('192.168.1.251', 7359)),
            (reply('', 'srv-3', 'No Address'), ('192.168.1.252', 7359)),
        )

        found = discovery.discover(timeout=1.0, sock=sock)

        self.assertEqual(
            [(f.url, f.server_id, f.name) for f in found],
            [
                ('http://192.168.1.250:8096', 'srv-1', 'Docker Jellyfin'),
                ('https://192.168.1.251:8920/media', 'srv-2', 'TLS Docker'),
                ('http://192.168.1.252:8096', 'srv-3', 'No Address'),
            ],
        )

    def test_discovery_times_out_empty(self):
        sock = FakeSocket()

        self.assertEqual(discovery.discover(timeout=0.05, sock=sock), [])
        self.assertEqual(sock.sent, [(b'who is JellyfinServer?', ('255.255.255.255', 7359))])
        self.assertTrue(sock.closed)

    def test_garbage_and_network_error_return_empty(self):
        bad_sock = FakeSocket(
            (b'not json', ('192.168.1.250', 7359)),
            (b'[]', ('192.168.1.250', 7359)),
            (b'{}', ('192.168.1.250', 7359)),
        )
        self.assertEqual(discovery.discover(timeout=0.05, sock=bad_sock), [])
        self.assertTrue(bad_sock.closed)

        down_sock = FakeSocket()

        def fail_send(data, address):
            raise OSError(101, 'Network is unreachable')

        down_sock.sendto = fail_send
        self.assertEqual(discovery.discover(timeout=0.05, sock=down_sock), [])
        self.assertTrue(down_sock.closed)
