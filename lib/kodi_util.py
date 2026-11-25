# coding=utf-8

import sys
import platform

# noinspection PyUnresolvedReferences
from kodi_six import xbmc, xbmcgui, xbmcvfs, xbmcaddon

ADDON = xbmcaddon.Addon()
# Read from addon.xml rather than hard-coded, so a renamed copy (script.plexmod-uno) can sit beside a stock
# install. UPSTREAM_ADDON_ID is the stock add-on's ID.
ADDON_ID = ADDON.getAddonInfo('id')
UPSTREAM_ADDON_ID = 'script.plexmod'

_build = None
# buildversion looks like: XX.X[-TAG] (a+.b+.c+) (.+); there are kodi builds that don't set the build version
sys_ver = xbmc.getInfoLabel('System.BuildVersion')
_ver = sys_ver

try:
    if ' ' in sys_ver and '(' in sys_ver:
        _ver, _build = sys_ver.split()[:2]

    _splitver = _ver.split(".")
    KODI_VERSION_MAJOR, KODI_VERSION_MINOR = int(_splitver[0].split("-")[0].strip()), \
                                             int(_splitver[1].split(" ")[0].split("-")[0].strip())
except:
    xbmc.log(ADDON_ID + ': Couldn\'t determine Kodi version, assuming 19.4. Got: {}'.format(sys_ver), xbmc.LOGINFO)
    # assume something "old"
    KODI_VERSION_MAJOR = 19
    KODI_VERSION_MINOR = 4

_bmajor, _bminor, _bpatch = (KODI_VERSION_MAJOR, KODI_VERSION_MINOR, 0)
parsedBuild = False
if _build:
    try:
        _bmajor, _bminor, _bpatch = _build[1:-1].split(".")
        parsedBuild = True
    except:
        pass
if not parsedBuild:
    xbmc.log(ADDON_ID + ': Couldn\'t determine build version, falling back to Kodi version', xbmc.LOGINFO)

# calculate a comparable build number
KODI_BUILD_NUMBER = int("{0}{1:02d}{2:03d}".format(_bmajor, int(_bminor), int(_bpatch)))

FROM_KODI_REPOSITORY = ADDON.getAddonInfo('name') == "PM4K for Plex"

# The add-on's own updater (update_checker.py, run by the service) installs upstream's script.plexmod zips into
# addons/script.plexmod, so only the stock add-on installed from outside a Kodi repository runs it. A renamed copy
# (script.plexmod-uno) is updated by its own repository. Gates the update settings and About rows too.
UPDATER_ENABLED = not FROM_KODI_REPOSITORY and ADDON_ID == UPSTREAM_ADDON_ID

# The service's global properties. script.plex.* is shared by every installed copy, so a renamed copy keeps its own:
# with a shared service.started, whichever copy's service ran second at boot took itself for a restart after an
# update and skipped its kiosk start. The stock ID keeps the plain names its earlier versions set.
_SERVICE_PROP_SUFFIX = '' if ADDON_ID == UPSTREAM_ADDON_ID else '.' + ADDON_ID
SERVICE_STARTED_PROP = 'service.started' + _SERVICE_PROP_SUFFIX
SERVICE_VERSION_PROP = 'service.version' + _SERVICE_PROP_SUFFIX

try:
    PYTHON_VERSION = platform.python_version()
    PYTHON_VERSION_TUPLE = tuple(map(int, platform.python_version_tuple()))
except:
    # assume something old
    PYTHON_VERSION = "3.8.15"
    PYTHON_VERSION_TUPLE = (3, 8, 15)


# no GIL anymore?
ENABLE_HIGH_CONCURRENCY = False
try:
    ENABLE_HIGH_CONCURRENCY = not sys._is_gil_enabled()
except:
    pass


if KODI_VERSION_MAJOR > 18:
    translatePath = xbmcvfs.translatePath
else:
    translatePath = xbmc.translatePath


ICON_PATH = translatePath(ADDON.getAddonInfo('icon'))


def ensureHome():
    if xbmcgui.getCurrentWindowId() != 10000:
        xbmc.log("Switching to home screen before starting addon: {}".format(xbmcgui.getCurrentWindowId()),
                 xbmc.LOGINFO)
        xbmc.executebuiltin('Action(back)')
        xbmc.executebuiltin('Dialog.Close(all,1)')
        xbmc.executebuiltin('ActivateWindow(home)')
        ct = 0
        while xbmcgui.getCurrentWindowId() != 10000 and ct <= 50:
            xbmc.Monitor().waitForAbort(0.1)
            ct += 1
        if ct > 50:
            xbmc.log("Still active window: {}", xbmc.LOGINFO)
