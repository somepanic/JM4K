from __future__ import absolute_import

from kodi_six import xbmc
from kodi_six import xbmcgui
from plexnet import plexapp
from jellynet.errors import JellyfinError, JellyfinUnavailable

from lib import util
from lib.util import T
from . import busy
from . import dropdown
from . import kodigui


class _OtherUser:
    id = None
    title = ''
    isProtected = False
    isAdmin = False
    isOtherUser = True


OTHER_USER = _OtherUser()


def askPassword(name):
    """The password typed for name ('' when OK is pressed on nothing), or None for Back. Jellyfin 12 says every user
    has a password, so a user without one is asked too and signs in with an empty one."""
    keyboard = xbmc.Keyboard('', T(35320, 'Password for {name}').format(name=name), True)
    keyboard.doModal()
    return keyboard.getText() if keyboard.isConfirmed() else None


class UserSelectWindow(kodigui.BaseWindow):
    xmlFile = 'script-plex-user_select.xml'
    path = util.ADDON.getAddonInfo('path')
    theme = 'Main'
    res = '1080i'
    width = 1920
    height = 1080

    USER_LIST_ID = 101
    SHUTDOWN_BUTTON_ID = 500

    def __init__(self, *args, **kwargs):
        self.task = None
        self.selected = False
        kodigui.BaseWindow.__init__(self, *args, **kwargs)

    def onFirstInit(self):
        self.userList = kodigui.ManagedControlList(self, self.USER_LIST_ID, 6)

        self.start()

    def onAction(self, action):
        try:
            # Jellyfin has no PINs: number keys do nothing here (Uno's four-digit entry sent them as a password)
            ID = action.getId()
            if ID in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU,  xbmcgui.ACTION_BACKSPACE):
                # return to selected user
                self.selected = 'cancel'
                self.doClose()
                return
        except:
            util.ERROR()

        kodigui.BaseWindow.onAction(self, action)

    def onClick(self, controlID):
        if controlID == self.USER_LIST_ID:
            item = self.userList.getSelectedItem()
            if getattr(item.dataSource, 'isOtherUser', False):
                username = xbmcgui.Dialog().input(T(35301, 'Username'))
                if username:
                    password = askPassword(username)
                    if password is not None:
                        self.userSelected(item, password, username=username)

            # refresh clicked
            elif not item.dataSource:
                # refresh user list
                with self.propertyContext('busy'):
                    try:
                        plexapp.ACCOUNT.updateHomeUsers(refreshSubscription=True)
                    except JellyfinError:
                        # the users as they were, shown again below
                        util.showNotification(
                            T(35319, "Can't reach the Jellyfin server. Check your connection and try again."))
                    self.userList.reset()
                    self.setProperty('initialized', '')
                    self.start(with_busy=False)
            elif (
                item.dataSource.isProtected
                and not getattr(plexapp.ACCOUNT, 'hasSession', lambda _uid: False)(item.dataSource.id)
            ):
                password = askPassword(item.dataSource.title)
                if password is not None:
                    self.userSelected(item, password)
            else:
                self.userSelected(item)
        elif controlID == self.SHUTDOWN_BUTTON_ID:
            self.shutdownClicked()

    def start(self, with_busy=True):
        if with_busy:
            self.setProperty('busy', '1')
        try:
            users = plexapp.ACCOUNT.homeUsers

            items = []
            selectIndex = None
            for idx, user in enumerate(users):
                thumb = plexapp.ACCOUNT.safeUserThumb(user.id, thumb=user.thumb)
                letter = user.title[0].upper() if user.title else '?'
                mli = kodigui.ManagedListItem(user.title, letter, thumbnailImage=thumb,
                                              data_source=user)
                mli.setProperty('back.image', user.id)
                mli.setProperty('pin', user.title)
                needs_pw = user.isProtected and not getattr(plexapp.ACCOUNT, 'hasSession', lambda _uid: False)(user.id)
                mli.setProperty('protected', needs_pw and '1' or '')
                mli.setProperty('admin', user.isAdmin and '1' or '')

                if plexapp.ACCOUNT.ID == user.id:
                   selectIndex = idx

                items.append(mli)

            # hidden user sign-in tile
            otherLabel = T(35321, 'Other user')
            other = kodigui.ManagedListItem(otherLabel, '+', data_source=OTHER_USER)
            other.setProperty('back.image', 'other')
            other.setProperty('pin', otherLabel)
            other.setProperty('protected', '')
            other.setProperty('admin', '')
            items.append(other)

            # append refresh button
            mli = kodigui.ManagedListItem("", "", data_source=kodigui.EmptyDataSource(), properties={"empty": '1'})
            items.append(mli)

            self.userList.addItems(items)

            self.setFocusId(self.USER_LIST_ID)

            if selectIndex is not None:
                self.userList.setSelectedItemByPos(selectIndex)

            self.setProperty('initialized', '1')
        finally:
            self.setProperty('busy', '')

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
    def userSelected(self, item, pin=None, username=None):
        user = item.dataSource
        # xbmc.sleep(500)
        util.DEBUG_LOG('Home user selected: {0}', user)

        try:
            switched = (
                plexapp.ACCOUNT.switchHomeUser(user.id, pin, username=username)
                if username is not None
                else plexapp.ACCOUNT.switchHomeUser(user.id, pin)
            )
            if not switched:
                item.setProperty('pin', item.dataSource.title or item.getLabel())
                item.setProperty('editing.pin', '')
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
