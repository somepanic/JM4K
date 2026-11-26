from __future__ import absolute_import

import hashlib
import os

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
    """Connect to Server: the Jellyfin server's address (Host), then Connect, or Cancel. connector(host) answers Connect
    with (StoredServer, '') or (None, why): the screen stays open with the reason until a server answers."""

    xmlFile = 'script-plex-pin_login.xml'
    path = util.ADDON.getAddonInfo('path')
    theme = 'Main'
    res = '1080i'
    width = 1920
    height = 1080

    HOST_ID = 201
    CONNECT_ID = 203
    CANCEL_ID = 204

    host = ''
    stored = None
    connector = None

    def setHost(self, host, error=''):
        """The Host to show (an address to connect to, or none yet) and why the last one failed, if it did."""
        self.host = host or ''
        self.setProperty('host', self.host)
        self.setProperty('error', error or '')
        for name, label in (
            ('title', util.T(35357, 'Connect to Server')),
            ('host', util.T(35358, 'Host')),
            ('hint', util.T(35359, '192.168.1.100:8096 or https://myserver.com')),
            ('connect', util.T(35360, 'Connect')),
            ('cancel', util.T(32337, 'Cancel')),
        ):
            self.setProperty('label.' + name, label)

    def setFound(self, found):
        """A Jellyfin server found on the network fills in the Host, unless one is there already."""
        if found is None or self.host:
            return
        self.setHost(found.url)
        self.setProperty('found', util.T(35361, 'Found {name} on your network').format(name=found.name))
        try:
            self.setFocusId(self.CONNECT_ID)  # one press connects
        except RuntimeError:
            pass  # not on screen yet: the Host keeps the focus

    def onClick(self, controlID):
        if controlID == self.HOST_ID:
            host = xbmcgui.Dialog().input(util.T(35358, 'Host'), self.host)
            if host:
                self.host = host.strip()
                self.setProperty('host', self.host)
                self.setProperty('error', '')
                self.setProperty('found', '')
        elif controlID == self.CONNECT_ID:
            self.setProperty('error', '')
            with self.propertyContext('busy'):
                stored, error = self.connector(self.host)
            if stored is None:
                self.setProperty('error', error)
            else:
                self.stored = stored
                self.doClose()
        elif controlID == self.CANCEL_ID:
            self.doClose()
