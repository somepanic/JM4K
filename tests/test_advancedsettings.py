# coding=utf-8
"""
lib/advancedsettings.py - PM4K's edits to Kodi's advancedsettings.xml.

PM4K rewrites a file Kodi owns, so the tests care mostly about *not* eating
unrelated sections: a user's <network> block has to survive a <hosts> or
<cache> update.
"""

from __future__ import absolute_import

import os

from kodienv import ENV

from lib.advancedsettings import AdvancedSettings, adv

from .base import KodiTestCase

ADV_PATH = "advancedsettings.xml"


def write_adv(content):
    """Write advancedsettings.xml and refresh the module singleton from it.

    `adv` reads the file once, at addon start, and every consumer
    (lib.cache, lib.plex_hosts) works off that one cached copy - so a test
    that writes the file has to re-load it explicitly, exactly like the addon
    would only see the new content on its next run.
    """
    path = os.path.join(ENV.profile_root, ADV_PATH)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(content)
    reload_adv()
    return path


def reload_adv():
    adv._data = None
    adv.load()


def read_adv():
    with open(os.path.join(ENV.profile_root, ADV_PATH), "r", encoding="utf-8") as fp:
        return fp.read()


class AdvSingletonMixin(object):
    """Keeps the shared `adv` singleton and the file on disk in step."""

    def setUp(self):
        KodiTestCase.setUp(self)
        self._adv_path = os.path.join(ENV.profile_root, ADV_PATH)
        if os.path.exists(self._adv_path):
            os.remove(self._adv_path)
        reload_adv()

    def tearDown(self):
        if os.path.exists(self._adv_path):
            os.remove(self._adv_path)
        reload_adv()
        KodiTestCase.tearDown(self)


class AdvancedSettingsTest(AdvSingletonMixin, KodiTestCase):
    def test_no_file_means_no_data(self):
        adv = AdvancedSettings()
        self.assertIsNone(adv.getData())
        self.assertFalse(adv)

    def test_an_existing_file_is_read(self):
        write_adv("<advancedsettings>\n</advancedsettings>")
        adv = AdvancedSettings()
        self.assertIn("<advancedsettings>", adv.getData())
        self.assertTrue(adv)

    def test_write_persists_and_updates_the_cached_copy(self):
        adv = AdvancedSettings()
        adv.write("<advancedsettings><network/></advancedsettings>")
        self.assertIn("<network/>", read_adv())
        self.assertIn("<network/>", adv.getData())

    def test_write_without_data_is_a_no_op(self):
        adv = AdvancedSettings()
        adv.write()
        self.assertFalse(os.path.exists(os.path.join(ENV.profile_root, ADV_PATH)))
