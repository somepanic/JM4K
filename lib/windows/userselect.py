from __future__ import absolute_import

import threading

from kodi_six import xbmc
from kodi_six import xbmcgui
from plexnet import plexapp
from jellynet import auth
from jellynet.errors import JellyfinAuthError, JellyfinError, JellyfinUnavailable

from lib import util
from lib.util import T
from . import busy
from . import dropdown
from . import kodigui
from .signin import qrImage

USERS, FORM, QUICK_CONNECT = '', 'form', 'quickconnect'  # what the screen shows (Window.Property(mode))


class UserSelectWindow(kodigui.BaseWindow):
    """Sign in to continue: the server's users as tiles (those Jellyfin shows on its login screen, and those signed in
    on this Kodi), then Use Quick Connect, Manual Login and Switch server. A user signed in here switches at once;
    any other, or Manual Login, swaps the tiles for User and Password with a Quick Connect code below them."""

    xmlFile = 'script-plex-user_select.xml'
    path = util.ADDON.getAddonInfo('path')
    theme = 'Main'
    res = '1080i'
    width = 1920
    height = 1080

    USER_LIST_ID = 101
    USERNAME_ID = 201
    PASSWORD_ID = 202
    SIGN_IN_ID = 203
    FORM_CANCEL_ID = 205
    QUICK_CONNECT_CANCEL_ID = 206
    QUICK_CONNECT_ID = 401
    MANUAL_LOGIN_ID = 402
    SWITCH_SERVER_ID = 403
    SHUTDOWN_BUTTON_ID = 500

    def __init__(self, *args, **kwargs):
        self.task = None
        self.selected = False
        self.username = self.password = ''
        self._qc = 0  # Quick Connect's turn: each form or Quick Connect view asks for a code of its own
        kodigui.BaseWindow.__init__(self, *args, **kwargs)

    def onFirstInit(self):
        self.userList = kodigui.ManagedControlList(self, self.USER_LIST_ID, 6)

        self.start()

    def onAction(self, action):
        try:
            # Jellyfin has no PINs: number keys do nothing here (Uno's four-digit entry sent them as a password)
            ID = action.getId()
            if ID in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU,  xbmcgui.ACTION_BACKSPACE):
                if self.getProperty('mode') != USERS:
                    self.showUsers()
                    return
                # return to selected user
                self.selected = 'cancel'
                self.doClose()
                return
        except:
            util.ERROR()

        kodigui.BaseWindow.onAction(self, action)

    def onClick(self, controlID):
        if controlID == self.USER_LIST_ID:
            user = self.userList.getSelectedItem().dataSource
            if plexapp.ACCOUNT.hasSession(user.id) or not user.isProtected:
                self.userSelected(user)
            else:
                self.showForm(user.title)
        elif controlID == self.MANUAL_LOGIN_ID:
            self.showForm('')
        elif controlID == self.QUICK_CONNECT_ID:
            self.showQuickConnect()
        elif controlID == self.SWITCH_SERVER_ID:
            self.switchServer()
        elif controlID == self.USERNAME_ID:
            self.username = (xbmcgui.Dialog().input(T(35301, 'Username'), self.username) or self.username).strip()
            self.setProperty('username', self.username)
        elif controlID == self.PASSWORD_ID:
            self.password = xbmcgui.Dialog().input(T(35302, 'Password'), option=xbmcgui.ALPHANUM_HIDE_INPUT)
            self.setProperty('password', '\N{BULLET}' * len(self.password))
        elif controlID == self.SIGN_IN_ID:
            self.signIn()
        elif controlID in (self.FORM_CANCEL_ID, self.QUICK_CONNECT_CANCEL_ID):
            self.showUsers()
        elif controlID == self.SHUTDOWN_BUTTON_ID:
            self.shutdownClicked()

    def start(self, with_busy=True):
        if with_busy:
            self.setProperty('busy', '1')
        try:
            server = getattr(plexapp.SERVERMANAGER, 'selectedServer', None)
            self.setProperty('server.name', getattr(server, 'name', '') or '')
            for name, label in (
                ('heading', T(35309, 'Sign in to continue')),
                ('quickconnect', T(35363, 'Use Quick Connect')),
                ('manual', T(35321, 'Manual Login')),
                ('switch', T(35311, 'Switch server')),
                ('username', T(35301, 'Username')),
                ('password', T(35302, 'Password')),
                ('signin', T(32460, 'Sign In')),
                ('cancel', T(32337, 'Cancel')),
                ('qc.heading', T(35304, 'Quick Connect')),
            ):
                self.setProperty('label.' + name, label)

            items = []
            selectIndex = None
            for idx, user in enumerate(plexapp.ACCOUNT.homeUsers):
                thumb = plexapp.ACCOUNT.safeUserThumb(user.id, thumb=user.thumb)
                letter = user.title[0].upper() if user.title else '?'
                items.append(kodigui.ManagedListItem(user.title, letter, thumbnailImage=thumb, data_source=user))
                if plexapp.ACCOUNT.ID == user.id:
                    selectIndex = idx
            self.userList.addItems(items)

            self.showUsers()
            if selectIndex is not None:
                self.userList.setSelectedItemByPos(selectIndex)

            self.setProperty('initialized', '1')
        finally:
            self.setProperty('busy', '')

    def showUsers(self):
        self._qc += 1  # any Quick Connect code shown stops being asked about
        self.setProperty('mode', USERS)
        self.setFocusId(self.USER_LIST_ID if len(self.userList) else self.MANUAL_LOGIN_ID)

    def showForm(self, username):
        """User and Password in place of the tiles, the name filled in (Manual Login: none), a Quick Connect code
        below them."""
        self.username, self.password = username, ''
        self.setProperty('username', username)
        self.setProperty('password', '')
        self.setProperty('error', '')
        self.setProperty('mode', FORM)
        self.setFocusId(self.PASSWORD_ID if username else self.USERNAME_ID)
        self.startQuickConnect()

    def showQuickConnect(self):
        self.setProperty('mode', QUICK_CONNECT)
        self.setFocusId(self.QUICK_CONNECT_CANCEL_ID)
        self.startQuickConnect()

    def startQuickConnect(self):
        """Ask Jellyfin for a Quick Connect code and wait for it to be approved, off the GUI thread."""
        self._qc += 1
        for key in ('pin', 'qr', 'instructions', 'qc.ready', 'qc.error'):
            self.setProperty(key, '')
        threading.Thread(target=self._quickConnect, args=(self._qc,), name='jellyfin.quickconnect', daemon=True).start()

    def _quickConnect(self, turn):
        client = plexapp.ACCOUNT.client

        def current():
            return turn == self._qc and not self._closing

        def show(code):
            if current():
                page = f"{client.base_url.rstrip('/')}/web/#/quickconnect"
                self.setProperty('instructions', T(35243, 'On a phone or computer, open {link} and enter this code:').format(link=page))
                self.setProperty('pin', str(code.code))
                self.setProperty('qr', qrImage(f'{page}?code={code.code}'))
                self.setProperty('qc.ready', '1')

        def wait(seconds):
            waited = 0.0
            while waited < seconds:
                if not current() or util.MONITOR.waitForAbort(0.25):
                    return False
                waited += 0.25
            return current()

        try:
            result = auth.QuickConnect(client).poll(show, wait)
        except JellyfinError as e:
            util.LOG('Quick Connect unavailable: {0}', e.status)
            if current():
                self.setProperty('qc.error', T(35364, 'Quick Connect is off on this server.'))
            return
        if result is not None and current():
            self.signedIn(result)

    def signIn(self):
        if not self.username:
            self.setProperty('error', T(35365, 'Enter your username.'))
            return
        self.setProperty('error', '')
        try:
            with self.propertyContext('busy'):
                result = auth.passwordLogin(plexapp.ACCOUNT.client, self.username, self.password)
        except JellyfinAuthError:
            self.password = ''
            self.setProperty('password', '')
            self.setProperty('error', T(35303, 'Wrong username or password.'))
            return
        except JellyfinError:
            self.setProperty('error', T(35319, "Can't reach the Jellyfin server. Check your connection and try again."))
            return
        self.signedIn(result)

    def signedIn(self, result):
        """Someone signed in (password or Quick Connect): their token is kept beside the other users' on this Kodi."""
        self._qc += 1
        plexapp.ACCOUNT.recordAuth(result)
        plexapp.ACCOUNT.isAuthenticated = True
        self.selected = True
        self.doClose()

    def switchServer(self):
        """Connect to Server with this server's address filled in; another server (or this one at a new address)
        restarts start-up on it."""
        from lib import jellyfin

        server = getattr(plexapp.SERVERMANAGER, 'selectedServer', None)
        address = getattr(server, 'url', '') or ''
        stored = jellyfin.connectToServer(jellyfin.deviceIdentity(), address=address)
        if stored is None or (stored.serverId == getattr(server, 'uuid', None) and stored.baseUrl == address):
            return
        self.selected = jellyfin.SWITCH_SERVER
        self.doClose()

    def shutdownClicked(self):
        options = []
        options.append({'key': 'sign_out', 'display': T(32421, 'Sign Out')})
        options.append({'key': 'exit', 'display': T(32422, 'Exit')})
        if util.getSetting('kiosk.mode'):
            if xbmc.getCondVisibility('System.CanPowerDown'):
                options.append({'key': 'shutdown', 'display': T(32423, 'Shutdown')})
            if xbmc.getCondVisibility('System.CanSuspend'):
                options.append({'key': 'suspend', 'display': T(32424, 'Suspend')})
            if xbmc.getCondVisibility('System.CanHibernate'):
                options.append({'key': 'hibernate', 'display': T(32425, 'Hibernate')})
            if xbmc.getCondVisibility('System.CanReboot'):
                options.append({'key': 'reboot', 'display': T(32426, 'Reboot')})

        with self.propertyContext('dropdown'):
            choice = dropdown.showDropdown(options, (60, 101))
            if not choice:
                return

        if choice['key'] == 'sign_out':
            self.selected = 'signout'
            self.doClose()
        elif choice['key'] == 'exit':
            self.doClose()
        elif choice['key'] == 'shutdown':
            xbmc.executebuiltin('Powerdown()')
        elif choice['key'] == 'suspend':
            xbmc.executebuiltin('Suspend()')
        elif choice['key'] == 'hibernate':
            xbmc.executebuiltin('Hibernate()')
        elif choice['key'] == 'reboot':
            xbmc.executebuiltin('Reset()')

    @busy.dialog()
    def userSelected(self, user):
        # xbmc.sleep(500)
        util.DEBUG_LOG('Home user selected: {0}', user)

        try:
            if not plexapp.ACCOUNT.switchHomeUser(user.id):
                util.messageDialog(T(32427, 'Failed'), T(35303, 'Wrong username or password.'))
                return
        except (JellyfinUnavailable, JellyfinError):
            util.messageDialog(T(32427, 'Failed'),
                               T(35319, "Can't reach the Jellyfin server. Check your connection and try again."))
            return

        self.selected = True
        self.doClose()

    def finished(self):
        if self.task:
            self.task.cancel()


def start(base_win_id):
    w = UserSelectWindow.create()
    if w.waitForOpen(base_win_id=base_win_id):
        w.modal()
    selected = w.selected
    del w
    return selected
