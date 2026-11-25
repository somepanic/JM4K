from __future__ import absolute_import

import hashlib
import os
from urllib.parse import quote

from kodi_six import xbmcgui

from lib import util
from . import kodigui


QR_PREFIX = 'jellymod_qr_'


def qrImage(text):
    """A PNG of text's QR code in the add-on's profile folder, for the card's image; the one before it is deleted. Each
    text gets its own name, as Kodi caches an image by its path. Not Kodi's temp folder: Kodi loads no image from there
    ("Direct texture file loading failed" on the user's box, for any PNG)."""
    import segno

    folder = os.path.join(util.PROFILE, 'qr')
    os.makedirs(folder, exist_ok=True)
    name = QR_PREFIX + hashlib.sha1(text.encode('utf-8')).hexdigest()[:12] + '.png'
    for old in os.listdir(folder):
        if old.startswith(QR_PREFIX) and old != name:
            os.remove(os.path.join(folder, old))
    path = os.path.join(folder, name)
    segno.make_qr(text).save(path, scale=10, border=2)
    return path


class PinLoginWindow(kodigui.BaseWindow):
    xmlFile = 'script-plex-pin_login.xml'
    path = util.ADDON.getAddonInfo('path')
    theme = 'Main'
    res = '1080i'
    width = 1920
    height = 1080

    USERNAME_ID = 201
    PASSWORD_ID = 202
    SIGN_IN_ID = 203
    CHANGE_ID = 204

    # what the user did on the card, read by lib/silo.py's signIn() while it waits
    abort = submitted = change = False
    username = password = ''

    def setOptions(self, options, address, username):
        """The server's name and subtitle (silonet.server_info.LoginOptions), its address and the last username."""
        self._address = address
        self.username = username
        self.setProperty('server.name', options.name or util.T(32460, 'Sign In'))
        self.setProperty('subtitle', options.subtitle)
        self.setProperty('address', address)
        self.setProperty('username', username)
        self.setProperty('password.enabled', '1' if options.password else '')
        self.setProperty('quickconnect.enabled', '1' if getattr(options, 'quick_connect', False) else '')
        if not options.password:
            # the username field the card starts on is hidden: Change is its one button then
            self.setFocusId(self.CHANGE_ID)
        for name, label in (
            ('username', util.T(35301, 'Username')),
            ('password', util.T(35302, 'Password')),
            ('signin', util.T(32460, 'Sign In')),
            ('heading', util.T(35309, 'Sign in to continue')),
            ('phone', util.T(35304, 'Quick Connect')),
            ('server', util.T(35305, 'Server')),
            ('change', util.T(35306, 'Change')),
        ):
            self.setProperty('label.' + name, label)

    def showError(self, message):
        """A failed password sign-in: the message, and the password to type again."""
        self.password = ''
        self.setProperty('password', '')
        self.setProperty('error', message)

    def setCode(self, code, address=None):
        """Jellyfin's Quick Connect code (jellynet.auth.QuickConnectCode): six digits, web link, and QR."""
        base = (address or getattr(self, '_address', '')).rstrip('/')
        page_url = f'{base}/web/#/quickconnect' if base else ''
        qr_url = f'{page_url}?code={quote(str(code.code), safe="")}' if page_url else str(code.code)
        self.setProperty(
            'instructions',
            util.T(35243, 'On a phone or computer, open {link} and enter this code:').format(link=page_url),
        )
        self.setProperty('pin', str(code.code))
        self.setProperty('qr', qrImage(qr_url))
        self.setProperty('quickconnect.enabled', '1')

    def onClick(self, controlID):
        if controlID in (self.USERNAME_ID, self.PASSWORD_ID, self.SIGN_IN_ID):
            self.setProperty('error', '')  # the last sign-in's, gone once the user tries again
        if controlID == self.USERNAME_ID:
            self.username = xbmcgui.Dialog().input(util.T(35301, 'Username'), self.username) or self.username
            self.setProperty('username', self.username)
        elif controlID == self.PASSWORD_ID:
            self.password = xbmcgui.Dialog().input(util.T(35302, 'Password'), option=xbmcgui.ALPHANUM_HIDE_INPUT)
            self.setProperty('password', '\N{BULLET}' * len(self.password))
        elif controlID == self.SIGN_IN_ID:
            self.submitted = True
        elif controlID == self.CHANGE_ID:
            self.change = True

    def onAction(self, action):
        try:
            if action == xbmcgui.ACTION_NAV_BACK or action == xbmcgui.ACTION_PREVIOUS_MENU:
                self.abort = True
        except:
            util.ERROR()

        kodigui.BaseWindow.onAction(self, action)
