"""
Finding Silo servers on the local network (silo-server's docs/architecture/server-discovery.md): every Silo API process
advertises the DNS-SD service _silo._tcp over multicast DNS, its server ID in the TXT key "id". find() asks once and
lists what answers. A found server is only a suggestion: it is confirmed through /api/v2/system/identity before use.
"""

import socket
import struct
import time

SERVICE = '_silo._tcp.local'
GROUP = ('224.0.0.251', 5353)
A, PTR, TXT, SRV = 1, 12, 16, 33


class Found:
    def __init__(self, name, baseUrl, serverId):
        self.name = name
        self.baseUrl = baseUrl
        self.serverId = serverId


def question():
    name = b''.join(bytes([len(label)]) + label.encode() for label in SERVICE.split('.')) + b'\0'
    return struct.pack('>6H', 0, 0, 1, 0, 0, 0) + name + struct.pack('>HH', PTR, 1)


def mdnsSocket():
    """A socket on the mDNS port, shared with the system's own responder, in the mDNS group: answers come to the group.
    A question from any other port goes unanswered by some responders (avahi on the user's Unraid server)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(('', GROUP[1]))
    sock.setsockopt(
        socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(GROUP[0]) + socket.inet_aton('0.0.0.0')
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    return sock


def readName(data, offset):
    """The name at offset, following compression pointers, and the offset after it."""
    labels, end = [], None
    for _ in range(64):
        length = data[offset]
        if length >= 0xC0:
            end = offset + 2 if end is None else end
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            continue
        offset += 1
        if not length:
            return '.'.join(labels), offset if end is None else end
        labels.append(data[offset : offset + length].decode('utf-8', 'replace'))
        offset += length
    raise ValueError('name pointer loop')


def records(data):
    """Each resource record of a DNS message: (name, type, offset of its data, its data)."""
    counts = struct.unpack_from('>6H', data)
    offset = 12
    for _ in range(counts[2]):
        offset = readName(data, offset)[1] + 4
    for _ in range(sum(counts[3:])):
        name, offset = readName(data, offset)
        rtype, _, _, length = struct.unpack_from('>HHIH', data, offset)
        offset += 10
        yield name, rtype, offset, data[offset : offset + length]
        offset += length


def txtValues(rdata):
    values, offset = {}, 0
    while offset < len(rdata):
        entry = rdata[offset + 1 : offset + 1 + rdata[offset]].decode('utf-8', 'replace')
        offset += 1 + rdata[offset]
        key, _, value = entry.partition('=')
        values[key] = value
    return values


def find(timeout=1.5, sock=None):
    """The Silo servers answering on the local network within timeout seconds, one Found per server."""
    instances, services, texts, addresses = [], {}, {}, {}
    try:
        sock = sock or mdnsSocket()
        sock.sendto(question(), GROUP)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                data = sock.recvfrom(9000)[0]
            except socket.timeout:  # noqa: UP041 (not TimeoutError on Kodi 19's Python 3.8)
                break
            try:
                for name, rtype, start, rdata in records(data):
                    if rtype == PTR and name == SERVICE:
                        instances.append(readName(data, start)[0])
                    elif rtype == SRV:
                        services[name] = readName(data, start + 6)[0], struct.unpack_from('>H', rdata, 4)[0]
                    elif rtype == TXT:
                        texts[name] = txtValues(rdata)
                    elif rtype == A and len(rdata) == 4:
                        addresses[name] = socket.inet_ntoa(rdata)
            except (IndexError, ValueError, struct.error):
                continue  # not an answer we can read
    except OSError:
        return []  # no network, no route for multicast, or the mDNS port not shareable
    finally:
        if sock is not None:
            sock.close()

    servers = {}
    for instance in instances:
        host, port = services.get(instance, (None, None))
        if host not in addresses:
            continue
        baseUrl = f'http://{addresses[host]}:{port}'
        serverId = texts.get(instance, {}).get('id', '')
        servers.setdefault(serverId or baseUrl, Found(instance[: -len(SERVICE) - 1], baseUrl, serverId))
    return list(servers.values())
