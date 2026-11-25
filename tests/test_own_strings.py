"""
JM4K's own name and its server's on the screens and settings it keeps (Phase 10): the strings that said "Plex" or
"SM4K" for the add-on (the Exit question, the skin prompts, the startup and exit settings) say JM4K, and those that
meant the media server (the empty library, markers, subtitles, the watchlist) say Jellyfin. Plextuary, the skin, keeps
its name.
"""

import os
import re

from kodienv import parse_strings_po

from .base import LANGUAGE_DIR, KodiTestCase

ADDON_NAME = ('32043', '32335', '35008', '35085', '35086', '35101', '35103', '35104', '35113', '35116', '35123')
SERVER_NAME = ('33604', '33630', '33695', '33702', '34008', '35133')
PLEX = re.compile(r'Plex(?!tuary)')
SILO = re.compile(r'\b(?:Silo|SiloMod|SM4K)\b', re.IGNORECASE)


class OwnStringsTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.strings = parse_strings_po(os.path.join(LANGUAGE_DIR, 'resource.language.en_gb', 'strings.po'))

    def test_the_addon_is_called_jm4k(self):
        for sid in ADDON_NAME:
            with self.subTest(sid):
                self.assertIsNone(PLEX.search(self.strings[int(sid)]))
                self.assertIsNone(SILO.search(self.strings[int(sid)]))
                self.assertIn('JM4K', self.strings[int(sid)])

    def test_the_server_is_called_jellyfin(self):
        for sid in SERVER_NAME:
            with self.subTest(sid):
                self.assertIsNone(PLEX.search(self.strings[int(sid)]))
                self.assertIsNone(SILO.search(self.strings[int(sid)]))
                self.assertIn('Jellyfin', self.strings[int(sid)])

    def test_the_exit_question(self):
        self.assertEqual(self.strings[32335], 'Are you ready to exit JM4K?')
