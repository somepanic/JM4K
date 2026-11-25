"""
JM4K's Jellyfin server storage, discovery, server picker, and address dialogs.
Stored addresses always keep the normalized URL the user typed or the UDP sender IP from discovery,
never /System/Info/Public's LocalAddress (which can be an unreachable Docker bridge IP).
"""

import json

from jellynet import discovery
from jellynet.errors import JellyfinError, JellyfinIncompatible, JellyfinUnavailable
from jellynet.server_info import probe
from kodi_six import xbmcgui

from . import util
from .util import T
from .windows import optionsdialog
from .windows.dialog import showOptionsDialog

SERVERS_SETTING = 'jellyfin_servers_json'
ADD_SERVER = 'add'  # pickServer()'s Add server row
ENTER_ADDRESS = 'enter'  # addServerDialog()'s Enter address row


class StoredServer:
    def __init__(self, baseUrl, serverId, name=None):
        self.baseUrl = baseUrl
        self.serverId = serverId
        self.name = name

    def toJson(self):
        return {'base_url': self.baseUrl, 'server_id': self.serverId, 'name': self.name}


def normalizeUrl(address):
    """What people type, as a base URL: http:// unless they said otherwise, no trailing slash."""
    address = address.strip().rstrip('/')
    return address if '://' in address else 'http://' + address


def storedServers():
    try:
        data = json.loads(util.getSetting(SERVERS_SETTING, '') or '[]')
    except ValueError:
        data = []
    return [
        StoredServer(d['base_url'], d['server_id'], d.get('name'))
        for d in data
        if isinstance(d, dict) and d.get('base_url') and d.get('server_id')
    ]


def saveServers(servers):
    util.setSetting(SERVERS_SETTING, json.dumps([s.toJson() for s in servers]))


def addServer(address, identity=None, session=None, serverId=None):
    """Probe the address and store the server; the same server at a new address replaces its old one.
    serverId: the server expected there; another one raises JellyfinIncompatible."""
    baseUrl = normalizeUrl(address)
    info = probe(baseUrl, session=session, identity=identity)
    if serverId and info.server_id != serverId:
        raise JellyfinIncompatible(0, f'{baseUrl} is another server')
    stored = StoredServer(baseUrl, info.server_id, info.name)
    saveServers([s for s in storedServers() if s.serverId != info.server_id] + [stored])
    util.DEBUG_LOG('Jellyfin: stored server ({})', info.version)
    return stored


def relocate(stored, identity=None, session=None):
    """Find a stored server at a new address on the local network under its ID and store it there;
    None when it is not found or fails identity verification."""
    for found in discovery.discover():
        if found.server_id == stored.serverId and found.url != stored.baseUrl:
            try:
                return addServer(found.url, identity=identity, session=session, serverId=stored.serverId)
            except JellyfinError:
                return None
    return None


def useServer(serverId):
    """Make a stored server the one start-up opens: init() opens the one stored last."""
    servers = storedServers()
    saveServers([s for s in servers if s.serverId != serverId] + [s for s in servers if s.serverId == serverId])


def pickServer(identity=None, session=None, dialog_props=None):
    """The user menu's Switch server picker: stored servers (the active one selected), then Add server.
    Returns the previous server's ID when the user switched to or added another server, else None."""
    servers = storedServers()
    current = servers[-1].serverId if servers else None
    options = [(s.serverId, f'{s.name or s.baseUrl} ({s.baseUrl})') for s in servers]
    choice = showOptionsDialog(
        T(35311, 'Switch server'),
        options + [(ADD_SERVER, T(35312, 'Add server'))],
        non_playback=True,
        selected_idx=len(servers) - 1 if servers else None,
        dialog_props=dialog_props,
    )
    if choice is None or choice == current:
        return None
    if choice != ADD_SERVER:
        useServer(choice)
        return current
    added = addServerDialog(identity, session=session, dialog_props=dialog_props)
    return current if added is not None and added.serverId != current else None


def addServerDialog(identity=None, session=None, address='', dialog_props=None):
    """Ask for a Jellyfin server until one answers or the user gives up: returns StoredServer or None.
    Without an initial address, servers found via UDP discovery are listed first with an Enter address row."""
    found = [] if address else discovery.discover()
    while True:
        picked = None
        if found:
            options = [(f.url, f'{f.name} ({f.url})') for f in found]
            choice = showOptionsDialog(
                T(35315, 'Jellyfin servers on your network'),
                options + [(ENTER_ADDRESS, T(35307, 'Enter address'))],
                non_playback=True,
                dialog_props=dialog_props,
            )
            if choice is None:
                return None
            picked = None if choice == ENTER_ADDRESS else choice
            found = []
        address = picked or xbmcgui.Dialog().input(
            T(35316, 'Jellyfin server address, for example http://192.168.1.10:8096'), address
        )
        if not address:
            return None
        try:
            return addServer(address, identity, session=session)
        except JellyfinUnavailable:
            message = T(35317, 'Could not reach a Jellyfin server at {address}.')
        except JellyfinIncompatible:
            message = T(35318, '{address} is not a Jellyfin server JellyMod can use.')
        retry = optionsdialog.show(
            T(32427, 'Failed'),
            message.format(address=address),
            T(35032, 'Try again'),
            T(32337, 'Cancel'),
            dialog_props=dialog_props,
        )
        if retry != 0:
            return None


def changeAddress(stored, identity=None, session=None):
    """Change on the sign-in card: pre-fill the stored address, then restart start-up."""
    addServerDialog(identity, session=session, address=stored.baseUrl)
    return init(session)


def init(session=None):
    return False
