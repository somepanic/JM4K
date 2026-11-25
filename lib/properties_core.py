# coding=utf-8

from kodi_six import xbmc, xbmcaddon, xbmcgui

# The add-on's global properties (Window(10000)) are under its own add-on ID. Plex Uno shares script.plex.* among
# its renamed copies, which are one app; SiloMod is another, and sharing them made it take Plex Uno running for
# itself. The skins' references are rewritten to match when they're rendered (lib/templating/core.py).
BASE = xbmcaddon.Addon().getAddonInfo('id') + '.{0}'


def _getGlobalProperty(key, base=BASE):
    return xbmc.getInfoLabel('Window(10000).Property({0})'.format(base.format(key)))

def _setGlobalProperty(key, val, base=BASE):
    xbmcgui.Window(10000).setProperty(base.format(key), val)

def _setGlobalBoolProperty(key, boolean, base=BASE):
    xbmcgui.Window(10000).setProperty(base.format(key), boolean and '1' or '')
