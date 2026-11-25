"""
SM4K's Silo session: its server (found on the network or typed in) and signing in on the sign-in card (silonet.auth)
through Uno's PIN window.
"""

import json

from kodi_six import xbmc, xbmcgui
from plexnet import plexapp
from plexnet import util as pnUtil
from silonet import auth, discovery
from silonet.account import SiloAccount
from silonet.client import Credentials, DeviceIdentity, SiloClient
from silonet.errors import SiloAuthError, SiloError, SiloIncompatible, SiloRateLimited, SiloUnavailable
from silonet.server import SiloServer
from silonet.server_info import LoginOptions, loginOptions, probe
from silonet.servermanager import SiloServerManager

from . import util
from .util import T
from .windows import optionsdialog, signin
from .windows.dialog import showOptionsDialog

SERVERS_SETTING = 'silo_servers_json'
CREDENTIALS_SETTING = 'silo_credentials_json'
USERNAME_SETTING = 'silo_username'  # the last username signed in with; never the password
CHANGE = 'change'  # signIn(): the user asked for another server address
ADD_SERVER = 'add'  # pickServer()'s Add server row
ENTER_ADDRESS = 'enter'  # addServerDialog()'s Enter address row

# how often a wait checks for Back while it sleeps
WAIT_STEP = 0.1

FAILURES = {
    'denied': (35244, 'Sign-in was refused.'),
    'expired': (35245, 'The code expired before it was approved. Please try again.'),
}


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


def addServer(address, device, session=None, serverId=None):
    """Probe the address and store the server; the same server at a new address replaces its old one. serverId: the
    server expected there; another one raises SiloIncompatible."""
    baseUrl = normalizeUrl(address)
    info = probe(baseUrl, device, session=session)
    if serverId and info.serverId != serverId:
        raise SiloIncompatible(0, 'other_server', f'{baseUrl} is another server')
    stored = StoredServer(baseUrl, info.serverId, info.name)
    saveServers([s for s in storedServers() if s.serverId != info.serverId] + [stored])
    util.DEBUG_LOG('Silo: stored server {} ({})', stored.name or 'unnamed', info.serverVersion)
    return stored


def relocate(stored, device, session=None):
    """The stored server at a new address, found on the network under its ID (silonet.discovery) and stored there; None
    when it isn't found."""
    for found in discovery.find():
        if found.serverId == stored.serverId and found.baseUrl != stored.baseUrl:
            try:
                return addServer(found.baseUrl, device, session=session, serverId=stored.serverId)
            except SiloError:
                return None
    return None


def useServer(serverId):
    """Make a stored server the one start-up opens: init() opens the one stored last."""
    servers = storedServers()
    saveServers([s for s in servers if s.serverId != serverId] + [s for s in servers if s.serverId == serverId])


def pickServer(device, session=None, dialog_props=None):
    """
    The user menu's Switch server, in SiloMod's picker: the stored servers, the one in use ticked, then Add server. The
    ID of the server in use when the user picked or added another one, which start-up then opens (initAfterSwitch());
    else None.
    """
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
    added = addServerDialog(device, session=session, dialog_props=dialog_props)
    return current if added is not None and added.serverId != current else None


def addServerDialog(device, session=None, address='', dialog_props=None):
    """Ask for a Silo server until one answers or the user gives up: its StoredServer, or None. With no address to
    start from, the servers found on the network come first (silonet.discovery, in SiloMod's picker), then the address
    box."""
    found = [] if address else discovery.find()
    while True:
        picked = None
        if found:
            options = [(f.baseUrl, f'{f.name} ({f.baseUrl})') for f in found]
            choice = showOptionsDialog(
                T(35308, 'Silo servers on your network'),
                options + [(ENTER_ADDRESS, T(35307, 'Enter address'))],
                non_playback=True,
                dialog_props=dialog_props,
            )
            if choice is None:
                return None
            picked = None if choice == ENTER_ADDRESS else choice
            found = []
        address = picked or xbmcgui.Dialog().input(
            T(35247, 'Silo server address, for example http://192.168.1.10:8090'), address
        )
        if not address:
            return None
        try:
            return addServer(address, device, session=session)
        except SiloUnavailable:
            message = T(35248, 'Could not reach a Silo server at {address}.')
        except SiloIncompatible:
            message = T(35249, '{address} is not a Silo server SiloMod can use.')
        retry = optionsdialog.show(
            T(32427, 'Failed'),
            message.format(address=address),
            T(35032, 'Try again'),
            T(32337, 'Cancel'),
            dialog_props=dialog_props,
        )
        if retry != 0:
            return None


def signIn(client, address, reachable=True):
    """The sign-in card until someone signs in: the session (auth.PollResult from an approved code, auth.Tokens from a
    password), CHANGE when the user asked for another server address, or None when they gave up. reachable=False: the
    card only says it can't reach the server, with Change."""
    window = signin.PinLoginWindow.create()
    options = loginOptions(client) if reachable else LoginOptions(None, '', False)
    window.setOptions(options, address, util.getSetting(USERNAME_SETTING, ''))
    session = []

    def signInWithPassword():
        window.submitted = False
        try:
            session.append(auth.passwordLogin(client, window.username, window.password))
        except SiloAuthError:
            window.showError(T(35303, 'Wrong username or password.'))
        except SiloRateLimited:
            window.showError(T(35314, 'Too many sign-in attempts. Wait a moment and try again.'))
        except SiloError:
            window.showError(T(35252, "Can't reach the Silo server. Check your connection and try again."))
        else:
            util.setSetting(USERNAME_SETTING, window.username)
        return bool(session)

    def wait(seconds):
        for _ in range(max(1, int(seconds / WAIT_STEP))):
            if window.submitted and signInWithPassword():
                return False  # the code it no longer needs is withdrawn
            if window.abort or window.change or util.MONITOR.waitForAbort(WAIT_STEP):
                return False
        return not (window.abort or window.change)

    try:
        if reachable:
            result = auth.signInWithCode(auth.DeviceLogin(client), client.device.name, window.setCode, wait)
        else:
            window.showError(T(35248, 'Could not reach a Silo server at {address}.').format(address=address))
            window.setFocusId(window.CHANGE_ID)
            while wait(1):
                pass
            result = None
    finally:
        window.doClose()

    if session:
        return session[0]
    if window.change:
        return CHANGE
    if result is None or result.status == 'approved':
        return result
    util.DEBUG_LOG('Silo sign-in ended: {}', result.status)
    msgId, msg = FAILURES.get(result.status, (35246, "Couldn't sign in. Please try again."))
    util.messageDialog(T(32427, 'Failed'), T(msgId, msg))
    return None


def deviceIdentity():
    """This Kodi as Silo sees it: Uno's client ID (plexnet creates it on first run) and Kodi's device name."""
    name = xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'
    return DeviceIdentity(pnUtil.X_PLEX_IDENTIFIER, name, 'kodi', util.ADDON.getAddonInfo('version'))


def storedSessions():
    """Each server's stored session by its ID. One stored before there could be several is a single object."""
    try:
        data = json.loads(util.getSetting(CREDENTIALS_SETTING, '') or '[]')
    except ValueError:
        data = []
    if isinstance(data, dict):
        data = [data]
    return {d.get('server_id'): d for d in data if isinstance(d, dict)} if isinstance(data, list) else {}


def saveCredentials(serverId, credentials, user):
    sessions = storedSessions()
    sessions[serverId] = {
        'server_id': serverId,
        'access_token': credentials.accessToken,
        'refresh_token': credentials.refreshToken,
        'profile_id': credentials.profileId,
        'profile_token': credentials.profileToken,
        'user': {'role': user.get('role'), 'username': user.get('username')},
    }
    util.setSetting(CREDENTIALS_SETTING, json.dumps(list(sessions.values())))


def loadCredentials(serverId):
    """The stored session for this server, saved again whenever a refresh rotates it; and the account's user."""
    data = storedSessions().get(serverId) or {}
    user = dict(data.get('user') or {})
    credentials = Credentials(
        data.get('access_token'), data.get('refresh_token'), data.get('profile_id'), data.get('profile_token')
    )
    credentials.onChange = lambda changed: saveCredentials(serverId, changed, user)
    return credentials, user


def chooseProfile(account):
    """
    Whether Home opens as the acting profile or the profile picker comes first (account.isAuthenticated, which
    lib/main.py reads), as Plex's account decided it. A code sign-in for the whole account binds no profile: the only
    profile is taken when there is just one (the picker still asks for its PIN), else the picker shows. A stored
    profile is kept with Automatically Sign In, or when it is the only one and has no PIN.
    """
    credentials = account.client.credentials
    if credentials.profileId and account.getHomeUser(credentials.profileId) is None:
        credentials.profileId = credentials.profileToken = None  # removed since: choose again
        account.isAuthenticated = False
    if account.isAuthenticated:
        return
    if not credentials.profileId and len(account.homeUsers) == 1:
        only = account.homeUsers[0]
        if only.isProtected:
            credentials.profileId = only.id
        else:
            account.switchHomeUser(only.id)
        return
    account.isAuthenticated = bool(credentials.profileId) and bool(
        util.getSetting('auto_signin', False) or (len(account.homeUsers) <= 1 and not account.isProtected)
    )


def changeAddress(stored, device, session=None):
    """Change on the card: the address box pre-filled with the stored address, then start-up again. A new address is
    stored last, which the restart picks; none keeps the card on this one."""
    addServerDialog(device, session=session, address=stored.baseUrl)
    return init(session)


def initAfterSwitch(previousId, session=None):
    """Start-up on the server just picked (pickServer()). Given up on (Back on its sign-in card, or out of reach),
    start-up on the one the user came from instead."""
    if init(session):
        return True
    useServer(previousId)
    return init(session)


def init(session=None):
    """
    Get SM4K ready to open Home: a server (asked for on first run), a session (signed in with a code when there is
    none or it ended), and the account and server manager Uno's screens use. False when the user gave up or the
    server can't be reached.
    """
    device = deviceIdentity()
    servers = storedServers()
    stored = servers[-1] if servers else addServerDialog(device, session=session)
    if stored is None:
        return False

    credentials, user = loadCredentials(stored.serverId)
    client = SiloClient(stored.baseUrl, device, credentials, session=session)
    account = SiloAccount(client, user)
    while True:
        try:
            if not credentials.accessToken:
                result = signIn(client, stored.baseUrl)
                if result == CHANGE:
                    return changeAddress(stored, device, session)
                if result is None:
                    return False
                user.clear()
                user.update(result.user)
                credentials.accessToken, credentials.refreshToken = result.accessToken, result.refreshToken
                credentials.profileId, credentials.profileToken = result.profileId, result.profileToken
                saveCredentials(stored.serverId, credentials, user)
                account.isAuthenticated = bool(result.profileId)  # a session bound to one profile goes home as it
            account.updateHomeUsers()
            break
        except SiloAuthError:
            util.DEBUG_LOG('Silo: the session ended, signing in again')
            credentials.accessToken = credentials.refreshToken = credentials.profileToken = None
            saveCredentials(stored.serverId, credentials, user)
        except SiloUnavailable:
            # a server whose address changed answers on the network under its ID; else the card says so, with Change
            if relocate(stored, device, session=session):
                return init(session)
            if signIn(client, stored.baseUrl, reachable=False) == CHANGE:
                return changeAddress(stored, device, session)
            return False
        except SiloError:
            util.messageDialog(
                T(32427, 'Failed'), T(35252, "Can't reach the Silo server. Check your connection and try again.")
            )
            return False

    chooseProfile(account)
    plexapp.ACCOUNT = pnUtil.ACCOUNT = account
    plexapp.SERVERMANAGER = pnUtil.SERVERMANAGER = SiloServerManager([SiloServer(stored, client)])
    return True
