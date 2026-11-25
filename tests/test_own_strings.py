"""
SM4K's own name and its server's on the screens and settings it keeps (Phase 9): the strings that said "Plex" for
the add-on (the Exit question, the skin prompts, the startup and exit settings) say SM4K, and those that meant the
media server (the empty library, markers, subtitles, the watchlist) say Silo. Plextuary, the skin, keeps its name.
"""

import os
import re

from kodienv import parse_strings_po

from .base import LANGUAGE_DIR, KodiTestCase

ADDON_NAME = ('32043', '32335', '35008', '35085', '35086', '35101', '35103', '35104', '35113', '35116', '35123')
SERVER_NAME = ('33604', '33630', '33695', '33702', '34008', '35133')
PLEX = re.compile(r'Plex(?!tuary)')


class OwnStringsTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.strings = parse_strings_po(os.path.join(LANGUAGE_DIR, 'resource.language.en_gb', 'strings.po'))

    def test_the_addon_is_called_sm4k(self):
        for sid in ADDON_NAME:
            with self.subTest(sid):
                self.assertIsNone(PLEX.search(self.strings[int(sid)]))
                self.assertIn('SM4K', self.strings[int(sid)])

    def test_the_server_is_called_silo(self):
        for sid in SERVER_NAME:
            with self.subTest(sid):
                self.assertIsNone(PLEX.search(self.strings[int(sid)]))
                self.assertIn('Silo', self.strings[int(sid)])

    def test_the_exit_question(self):
        self.assertEqual(self.strings[32335], 'Are you ready to exit SM4K?')
