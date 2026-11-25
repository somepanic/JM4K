"""
SM4K's own identity: the add-on ID and metadata in addon.xml, and the Plextuary builds it pairs with.
"""

import os
from unittest import mock
from xml.etree import ElementTree

from lib import skin_check, util

from .base import KodiTestCase

ADDON_XML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'addon.xml')


def addonXml():
    return ElementTree.parse(ADDON_XML).getroot()


class AddonIdentityTest(KodiTestCase):
    def test_addon_xml_declares_sm4k(self):
        root = addonXml()

        self.assertEqual(root.get('id'), 'script.silomod')
        self.assertEqual(root.get('name'), 'SiloMod (SM4K)')

    def test_metadata_points_at_sm4k_and_silo(self):
        meta = addonXml().find("extension[@point='xbmc.addon.metadata']")

        self.assertEqual(meta.findtext('source'), 'https://github.com/samtheruby/SM4K')
        self.assertEqual(meta.findtext('website'), 'https://siloserver.org')
        self.assertIn('Silo', meta.findtext("summary[@lang='en_GB']"))
        self.assertNotIn('Plex', meta.findtext("summary[@lang='en_GB']"))

    def test_pairs_with_plex_unos_plextuary_builds(self):
        # SM4K keeps Plex Uno's screens, measured for the renamed Plextuary builds' fonts, whatever its own ID
        self.assertEqual(
            [skin_id for skin_id, _ in skin_check.VARIANTS],
            ['skin.plextuary-uno', 'skin.plextuaryce-uno', 'skin.plextuarycpm-uno'],
        )
        with mock.patch.object(util, 'platformFlavor', 'default'):
            self.assertEqual(skin_check.detectedVariant(), 'skin.plextuary-uno')
