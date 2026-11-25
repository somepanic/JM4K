"""
JM4K's own identity: the add-on ID and metadata in addon.xml, its window properties, its QR image files, and the
Plextuary builds it pairs with.
"""

import importlib
import os
from unittest import mock
from xml.etree import ElementTree

from kodi_six import xbmcgui
from kodienv import ENV

from lib import properties_core, skin_check, util
from lib.windows import signin

from .base import KodiTestCase

ADDON_XML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'addon.xml')


def addonXml():
    return ElementTree.parse(ADDON_XML).getroot()


class AddonIdentityTest(KodiTestCase):
    def test_addon_xml_declares_jm4k(self):
        root = addonXml()
        meta = root.find("extension[@point='xbmc.addon.metadata']")

        self.assertEqual(root.get('id'), 'script.jellymod')
        self.assertEqual(root.get('name'), 'JellyMod (JM4K)')
        self.assertEqual(root.get('version'), '0.1.0')
        self.assertEqual(meta.findtext('license'), 'GPL-2.0-only')
        self.assertEqual(meta.findtext('website'), 'https://jellyfin.org')
        # Local-only for now (J-D11): no remote source URL yet
        self.assertIsNone(meta.find('source'))
        self.assertIn(
            'not made or supported by the Jellyfin project or by Plex', meta.findtext("disclaimer[@lang='en']")
        )
        summary = meta.findtext("summary[@lang='en_GB']")
        self.assertIn('Jellyfin', summary)
        self.assertNotIn('Plex', summary)
        self.assertNotIn('Silo', summary)
        description = meta.findtext("description[@lang='en_GB']")
        self.assertIn('Jellyfin', description)
        self.assertNotIn('Silo', description)
        self.assertNotIn('Silo', meta.findtext('news') or '')

    def test_window_properties_are_jellymods_own(self):
        addon_id = addonXml().get('id')
        original_id = ENV.addon_info['id']
        try:
            ENV.addon_info['id'] = addon_id
            importlib.reload(properties_core)
            properties_core._setGlobalProperty('running', addon_id)

            self.assertEqual(properties_core.BASE, 'script.jellymod.{0}')
            self.assertEqual(xbmcgui.Window(10000).getProperty('script.jellymod.running'), addon_id)
            self.assertEqual(xbmcgui.Window(10000).getProperty('script.silomod.running'), '')
            self.assertEqual(xbmcgui.Window(10000).getProperty('script.plexmod-uno.running'), '')
            self.assertEqual(xbmcgui.Window(10000).getProperty('script.plex.running'), '')
            self.assertEqual(properties_core._getGlobalProperty('running'), addon_id)
        finally:
            ENV.addon_info['id'] = original_id
            importlib.reload(properties_core)

    def test_qr_files_are_jellymods_own(self):
        path = signin.qrImage('http://jellyfin.lan:8096/quickconnect')
        name = os.path.basename(path)

        self.assertTrue(name.startswith('jellymod_qr_'), name)
        self.assertFalse(name.startswith('silomod_qr_'), name)
        with open(path, 'rb') as f:
            self.assertEqual(f.read(8), b'\x89PNG\r\n\x1a\n')

    def test_pairs_with_plex_unos_plextuary_builds(self):
        # JM4K keeps Plex Uno's screens, measured for the renamed Plextuary builds' fonts, whatever its own ID
        self.assertEqual(
            [skin_id for skin_id, _ in skin_check.VARIANTS],
            ['skin.plextuary-uno', 'skin.plextuaryce-uno', 'skin.plextuarycpm-uno'],
        )
        with mock.patch.object(util, 'platformFlavor', 'default'):
            self.assertEqual(skin_check.detectedVariant(), 'skin.plextuary-uno')
