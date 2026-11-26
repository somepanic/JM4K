"""
JM4K's Jellyfin session: server storage, UDP discovery, server picker, Quick Connect and password
sign-in on Uno's sign-in card, multi-user session persistence, and start-up into Home.
Stored addresses always keep the normalized URL the user typed or the UDP sender IP from discovery,
never /System/Info/Public's LocalAddress (which can be an unreachable Docker bridge IP).
"""

import json
import threading

from jellynet import discovery
from jellynet.account import JellyfinAccount
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.errors import JellyfinAuthError, JellyfinError, JellyfinIncompatible, JellyfinUnavailable
from jellynet.server import JellyfinServer
from jellynet.server_info import probe
from jellynet.servermanager import JellyfinServerManager
from kodi_six import xbmc
from plexnet import plexapp
from plexnet import util as pnUtil

from . import util
from .util import T
from .windows import signin, userselect
from .windows.dialog import showOptionsDialog

SERVERS_SETTING = 'jellyfin_servers_json'
SESSIONS_SETTING = 'jellyfin_sessions_json'
ADD_SERVER = 'add'  # pickServer()'s Add server row
SWITCH_SERVER = 'switch_server'  # the sign-in screen's Switch server connected to another server


class StoredServer:
    def __init__(self, baseUrl, serverId, name=None, version=None):
        self.baseUrl = baseUrl
        self.serverId = serverId
        self.name = name
        self.version = str(version or '')

    def toJson(self):
        data = {'base_url': self.baseUrl, 'server_id': self.serverId, 'name': self.name}
        if self.version:
            data['version'] = self.version
        return data


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
        StoredServer(d['base_url'], d['server_id'], d.get('name'), version=d.get('version'))
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
    stored = StoredServer(baseUrl, info.server_id, info.name, version=info.version)
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
    added = connectToServer(identity, session=session)
    return current if added is not None and added.serverId != current else None


def connectToServer(identity=None, session=None, address='', error=''):
    """Connect to Server until a Jellyfin server answers at the Host typed (its StoredServer, stored as the one start-up
    opens) or Cancel (None). Without an address, a server found on the network fills in the Host."""

    def connect(host):
        host = (host or '').strip()
        if not host:
            return None, T(35362, 'Enter the address of your Jellyfin server.')
        try:
            return addServer(host, identity, session=session), ''
        except JellyfinUnavailable:
            return None, T(35317, 'Could not reach a Jellyfin server at {address}.').format(address=host)
        except JellyfinIncompatible:
            return None, T(35318, '{address} is not a Jellyfin server JellyMod can use.').format(address=host)

    window = signin.PinLoginWindow.create(show=False)
    window.connector = connect
    window.setHost(address, error)
    if not address:
        threading.Thread(target=_fillInFoundServer, args=(window,), name='jellyfin.discovery', daemon=True).start()
    window.modal()
    stored = window.stored
    del window
    return stored


def _fillInFoundServer(window):
    window.setFound(next(iter(discovery.discover()), None))


def deviceIdentity():
    """This Kodi as Jellyfin sees it: Uno's client UUID (created on first run) and Kodi's device name."""
    name = xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'
    return ClientIdentity('JellyMod', util.ADDON.getAddonInfo('version'), name, pnUtil.X_PLEX_IDENTIFIER)


def storedSessions():
    """Per-server stored multi-user sessions keyed by server_id."""
    try:
        data = json.loads(util.getSetting(SESSIONS_SETTING, '') or '{}')
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for server_id, entry in data.items():
        if isinstance(entry, dict):
            users = entry.get('users')
            cleaned[str(server_id)] = {
                'active_user_id': entry.get('active_user_id'),
                'users': users if isinstance(users, dict) else {},
            }
    return cleaned


def saveSessions(serverId, users, activeUserId):
    sessions = storedSessions()
    sessions[str(serverId)] = {
        'active_user_id': activeUserId,
        'users': {str(uid): dict(info) for uid, info in (users or {}).items() if isinstance(info, dict)},
    }
    util.setSetting(SESSIONS_SETTING, json.dumps(sessions))


def loadSessions(serverId):
    entry = storedSessions().get(str(serverId)) or {}
    return dict(entry.get('users') or {}), entry.get('active_user_id')


def signInScreen():
    """Sign in to continue: the server's users as tiles, Use Quick Connect, Manual Login and Switch server
    (UserSelectWindow). True once someone signed in, SWITCH_SERVER after Switch server, else None (Back)."""
    result = userselect.start(None)
    return result if result in (True, SWITCH_SERVER) else None


def chooseUser(account):
    """Decide whether Home opens immediately or UserSelectWindow asks who is watching."""
    if account.isAuthenticated:
        return
    if not account.isSignedIn:
        account.isAuthenticated = False
        return
    account.isAuthenticated = bool(util.getSetting('auto_signin', False) or len(account.homeUsers) <= 1)


def initAfterSwitch(previousId, session=None):
    """Start-up on the server just picked; if the user backs out, return to previousId."""
    if init(session):
        return True
    useServer(previousId)
    return init(session)


def init(session=None):
    """Prepare JM4K to open Home: a server (Connect to Server), a signed-in user (Sign in to continue), the server's
    users, and JellyfinAccount and JellyfinServerManager on plexapp."""
    identity = deviceIdentity()
    servers = storedServers()
    stored = servers[-1] if servers else connectToServer(identity, session=session)
    if stored is None:
        return False

    users, active_user_id = loadSessions(stored.serverId)
    client = JellyfinClient(stored.baseUrl, identity, session=session)
    account = JellyfinAccount(
        client,
        sessions=users,
        active_user_id=active_user_id,
        on_change=lambda updated_users, updated_active: saveSessions(stored.serverId, updated_users, updated_active),
    )

    while True:
        try:
            if account.isSignedIn:
                account.verifyCurrentUser()
            account.updateHomeUsers()
            break
        except JellyfinAuthError:
            util.DEBUG_LOG('Jellyfin: stored session ended, signing in again')
            if account.ID:
                account.sessions.pop(account.ID, None)
            account._active_user_id = None
            account._sync_client()
            account.isAuthenticated = False
            saveSessions(stored.serverId, account.sessions, None)
        except JellyfinUnavailable:
            if relocate(stored, identity, session=session):
                return init(session)
            error = T(35317, 'Could not reach a Jellyfin server at {address}.').format(address=stored.baseUrl)
            if connectToServer(identity, session=session, address=stored.baseUrl, error=error) is None:
                return False
            return init(session)
        except JellyfinError:
            util.messageDialog(
                T(32427, 'Failed'),
                T(35319, "Can't reach the Jellyfin server. Check your connection and try again."),
            )
            return False

    plexapp.ACCOUNT = pnUtil.ACCOUNT = account
    plexapp.SERVERMANAGER = pnUtil.SERVERMANAGER = JellyfinServerManager([JellyfinServer(stored, client)])
    if not account.isSignedIn:
        result = signInScreen()
        if result == SWITCH_SERVER:
            return init(session)
        if result is not True:
            return False
    chooseUser(account)
    return True
