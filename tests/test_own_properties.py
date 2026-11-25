"""
SiloMod's window properties are its own: Plex Uno shares script.plex.* among its renamed copies (the same app), but
SiloMod is another app, and with the shared names Plex Uno running - it starts at boot on the user's box - made
SiloMod take itself for running and hand its launch to Plex Uno, which never opened it (seen on the box,
2026-11-24). The add-on's global properties and every skin reference to them are under its add-on ID.

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

import tempfile

from kodi_six import xbmcgui
from kodienv import ENV

ENV.abort_requested = True
from lib.kodi_util import ADDON_ID  # noqa: E402
from lib.properties import getGlobalProperty, setGlobalProperty  # noqa: E402

from .base import KodiTestCase, make_engine  # noqa: E402
from .test_templates import render_theme  # noqa: E402


class OwnPropertiesTest(KodiTestCase):
    def test_its_properties_are_under_its_own_id(self):
        setGlobalProperty('running', ADDON_ID)

        self.assertEqual(xbmcgui.Window(10000).getProperty(ADDON_ID + '.running'), ADDON_ID)
        self.assertEqual(xbmcgui.Window(10000).getProperty('script.plex.running'), '')
        self.assertEqual(getGlobalProperty('running'), ADDON_ID)

    def test_plex_unos_running_flag_isnt_its(self):
        xbmcgui.Window(10000).setProperty('script.plex.running', 'script.plexmod-uno')

        self.assertEqual(getGlobalProperty('running'), '')

    def test_its_window_property_helper(self):
        from lib.windows import kodigui

        with kodigui.GlobalProperty('busy'):
            self.assertEqual(xbmcgui.Window(10000).getProperty(ADDON_ID + '.busy'), '1')
        self.assertEqual(xbmcgui.Window(10000).getProperty('script.plex.busy'), '')

    def test_its_skins_read_its_own_properties(self):
        rendered = render_theme(make_engine(tempfile.mkdtemp()), 'modern-colored')

        stray = [name for name, xml in rendered.items() if 'Property(script.plex.' in xml]
        self.assertEqual(stray, [])
        self.assertIn('Window(10000).Property({}.sort.alpha)'.format(ADDON_ID), rendered['posters'])
