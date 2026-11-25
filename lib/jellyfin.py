"""
JM4K's Jellyfin session: server storage, UDP discovery, server picker, Quick Connect and password
sign-in on Uno's sign-in card, multi-user session persistence, and start-up into Home.
Stored addresses always keep the normalized URL the user typed or the UDP sender IP from discovery,
never /System/Info/Public's LocalAddress (which can be an unreachable Docker bridge IP).
"""

import json

from jellynet import auth, discovery
from jellynet.account import JellyfinAccount
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.errors import JellyfinAuthError, JellyfinError, JellyfinIncompatible, JellyfinUnavailable
from jellynet.server import JellyfinServer
from jellynet.server_info import LoginOptions, loginOptions, probe
from jellynet.servermanager import JellyfinServerManager
from kodi_six import xbmc, xbmcgui
from plexnet import plexapp
from plexnet import util as pnUtil

from . import util
from .util import T
from .windows import optionsdialog, signin
from .windows.dialog import showOptionsDialog

SERVERS_SETTING = 'jellyfin_servers_json'
SESSIONS_SETTING = 'jellyfin_sessions_json'
USERNAME_SETTING = 'jellyfin_username'  # last username signed in with; never the password
CHANGE = 'change'  # signIn(): user asked for another server address
ADD_SERVER = 'add'  # pickServer()'s Add server row
ENTER_ADDRESS = 'enter'  # addServerDialog()'s Enter address row

WAIT_STEP = 0.1  # how often signIn() checks for Back/submit while polling Quick Connect


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


def signIn(client, address, reachable=True, server_name=None):
    """Run the sign-in card until someone signs in (Quick Connect or password), asks to change the
    server (CHANGE), or cancels (None). When reachable=False, shows the error and focuses Change."""
    window = signin.PinLoginWindow.create()
    options = (
        loginOptions(client, name=server_name)
        if reachable
        else LoginOptions(False, '', name=server_name, password=False)
    )
    window.setOptions(options, address, util.getSetting(USERNAME_SETTING, ''))
    password_result = []

    def signInWithPassword():
        window.submitted = False
        try:
            password_result.append(auth.passwordLogin(client, window.username, window.password))
        except JellyfinAuthError:
            window.showError(T(35303, 'Wrong username or password.'))
        except JellyfinError:
            window.showError(T(35319, "Can't reach the Jellyfin server. Check your connection and try again."))
        else:
            util.setSetting(USERNAME_SETTING, window.username)
        return bool(password_result)

    def wait(seconds):
        steps = max(1, int(seconds / WAIT_STEP))
        for _ in range(steps):
            if window.submitted and signInWithPassword():
                return False
            if window.abort or window.change or util.MONITOR.waitForAbort(WAIT_STEP):
                return False
        return not (window.abort or window.change)

    qc_result = None
    try:
        if not reachable:
            window.showError(T(35317, 'Could not reach a Jellyfin server at {address}.').format(address=address))
            window.setFocusId(window.CHANGE_ID)
            while wait(1):
                pass
        elif options.quick_connect:
            try:
                qc_result = auth.QuickConnect(client, poll_interval=auth.POLL_INTERVAL).poll(
                    lambda code: window.setCode(code, address), wait
                )
            except JellyfinError:
                window.setProperty('quickconnect.enabled', '')
                while wait(1):
                    pass
        else:
            while wait(1):
                pass
    finally:
        window.doClose()

    if password_result:
        return password_result[0]
    if window.change:
        return CHANGE
    return qc_result


def chooseUser(account):
    """Decide whether Home opens immediately or UserSelectWindow asks who is watching."""
    if account.isAuthenticated:
        return
    if not account.isSignedIn:
        account.isAuthenticated = False
        return
    account.isAuthenticated = bool(util.getSetting('auto_signin', False) or len(account.homeUsers) <= 1)


def changeAddress(stored, identity=None, session=None):
    """Change on the sign-in card: pre-fill the stored address, then restart start-up."""
    addServerDialog(identity, session=session, address=stored.baseUrl)
    return init(session)


def initAfterSwitch(previousId, session=None):
    """Start-up on the server just picked; if the user backs out, return to previousId."""
    if init(session):
        return True
    useServer(previousId)
    return init(session)


def init(session=None):
    """Prepare JM4K to open Home: ensure a server and signed-in Jellyfin session, load home users,
    and install JellyfinAccount and JellyfinServerManager on plexapp."""
    identity = deviceIdentity()
    servers = storedServers()
    stored = servers[-1] if servers else addServerDialog(identity, session=session)
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
            if not account.isSignedIn:
                result = signIn(client, stored.baseUrl, server_name=stored.name)
                if result == CHANGE:
                    return changeAddress(stored, identity, session)
                if result is None:
                    return False
                account.recordAuth(result)
                account.isAuthenticated = True
            else:
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
            if signIn(client, stored.baseUrl, reachable=False, server_name=stored.name) == CHANGE:
                return changeAddress(stored, identity, session)
            return False
        except JellyfinError:
            util.messageDialog(
                T(32427, 'Failed'),
                T(35319, "Can't reach the Jellyfin server. Check your connection and try again."),
            )
            return False

    chooseUser(account)
    plexapp.ACCOUNT = pnUtil.ACCOUNT = account
    plexapp.SERVERMANAGER = pnUtil.SERVERMANAGER = JellyfinServerManager([JellyfinServer(stored, client)])
    return True
