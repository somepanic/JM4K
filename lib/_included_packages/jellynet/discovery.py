"""
Finding Jellyfin servers on the local network: broadcast "who is JellyfinServer?" to UDP port 7359
and read JSON ServerDiscoveryInfo replies. When a server runs in Docker and advertises an unreachable
bridge IP as its Address, the UDP sender IP is preferred with the reply's scheme, port and path.
"""

import json
import socket
import time
from urllib.parse import urlsplit

BROADCAST_ADDR = ('255.255.255.255', 7359)
WHO_IS_JELLYFIN = b'who is JellyfinServer?'
DEFAULT_PORT = 8096


class Found:
    def __init__(self, url, server_id, name):
        self.url = url
        self.server_id = server_id
        self.name = name

    def __repr__(self):
        return f'<Found {self.server_id!r}>'


def _broadcast_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return sock


def _reply_url(address, sender_ip):
    raw = address.strip().rstrip('/') if isinstance(address, str) else ''
    scheme = 'http'
    port = None
    path = ''
    host = None
    parsed = None
    if raw:
        candidate = raw if '://' in raw else 'http://' + raw
        parsed = urlsplit(candidate)
        scheme = parsed.scheme or 'http'
        host = parsed.hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        path = parsed.path.rstrip('/')
        if host and sender_ip and host == sender_ip:
            return f'{scheme}://{parsed.netloc}{path}'

    if sender_ip and sender_ip != '0.0.0.0':
        effective_port = port if port is not None else (DEFAULT_PORT if scheme == 'http' else None)
        host_part = f'[{sender_ip}]' if ':' in sender_ip else sender_ip
        port_part = f':{effective_port}' if effective_port is not None else ''
        return f'{scheme}://{host_part}{port_part}{path}'

    if parsed is not None and host:
        return f'{scheme}://{parsed.netloc}{path}'
    return None


def _parse_reply(data, sender_ip):
    try:
        payload = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    server_id = payload.get('Id')
    if not isinstance(server_id, str) or not server_id.strip():
        return None
    server_id = server_id.strip()
    url = _reply_url(payload.get('Address'), sender_ip)
    if not url:
        return None
    raw_name = payload.get('Name')
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else url
    return Found(url, server_id, name)


def discover(timeout=2.0, sock=None):
    """Broadcast once on UDP 7359 and return deduplicated Found servers answering within timeout seconds."""
    servers = {}
    try:
        sock = sock or _broadcast_socket()
        sock.sendto(WHO_IS_JELLYFIN, BROADCAST_ADDR)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                data, sender = sock.recvfrom(4096)
            except socket.timeout:  # noqa: UP041
                break
            sender_ip = sender[0] if isinstance(sender, tuple) and sender else ''
            found = _parse_reply(data, sender_ip)
            if found is not None:
                servers.setdefault(found.server_id, found)
    except OSError:
        return list(servers.values())
    finally:
        if sock is not None:
            sock.close()
    return list(servers.values())
