"""
Jellyfin settings and shipped-string guards (Phase 10):
- The settings screen offers nothing only a Plex server or plex.tv reads: no Network section, and Plex-only
  transcoder, subtitle auto-sync, watchlist, cinema trailer/pre-roll, and request-cache settings stay hidden
  while keeping their defaults for upstream code that still reads them.
- Nothing shipped to users names Silo, SiloMod, or SM4K (addon.xml, language strings, templates, and non-comment
  Python string literals under lib/).

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

import ast
import os
import re
from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.server import JellyfinServer
from jellynet.servermanager import JellyfinServerManager
from plexnet import plexapp

from lib import jellyfin, playback_utils
from lib.windows import playbacksettings, seekdialog, settings

from . import REPO_ROOT
from .base import LANGUAGE_DIR, KodiTestCase
from .jellynet_fakes import FakeResponse, FakeSession, problem

PLEX_CONNECTION = {
    'local_mode',
    'allow_insecure',
    'smart_discover_local',
    'prefer_local',
    'gdm_discovery',
    'handle_plexdirect',
    'force_pd_mapping',
    'manual_ip_0',
    'manual_port_0',
    'manual_ip_1',
    'manual_port_1',
}

# Settings read only by Plex's transcoder, subtitle auto-sync, Plex watchlist, cinema trailers/pre-rolls, or Plex's
# request cache: Jellyfin plays without any of them, so they stay hidden while keeping their defaults.
PLEX_ONLY = {
    'burn_subtitles',
    'burn_ssa',
    'auto_sync',
    'use_watchlist',
    'watchlist_auto_remove',
    'preplay_preroll',
    'preplay_preroll_first',
    'preplay_trailers',
    'cache_requests',
    'persist_requests_cache',
}

SILO_RE = re.compile(r'\b(?:silo|silomod|sm4k)\b', re.IGNORECASE)
XML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)


def _iter_translated_defaults(path):
    """The English text the add-on shows when a string isn't translated: every string given to T(), as T(id, text)
    or util.T(id, text). Internal literals (platform names, thread names, sentinel values) are not shown to anyone
    and stay as SM4K has them, so merges from SM4K stay small."""
    with open(path, encoding='utf-8') as fp:
        tree = ast.parse(fp.read(), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', None)
        if name != 'T':
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                yield arg.lineno, arg.value


class JellyfinSettingsTest(KodiTestCase):
    def offered(self):
        # a section can hold None for a setting this platform leaves out
        return {s.ID for _, section in settings.Settings.SETTINGS.values() for s in section if s is not None}

    def shown(self):
        return {
            s.ID
            for _, section in settings.Settings.SETTINGS.values()
            for s in section
            if s is not None and s.should_show()
        }

    def test_no_network_section(self):
        self.assertNotIn('network', settings.Settings.SECTION_IDS)
        self.assertNotIn('network', settings.Settings.SETTINGS)

    def test_no_plex_connection_settings(self):
        self.assertEqual(set(), PLEX_CONNECTION & self.offered())

    def test_every_section_listed_is_there(self):
        self.assertEqual(set(settings.Settings.SECTION_IDS), set(settings.Settings.SETTINGS))

    def test_plex_only_settings_hidden(self):
        self.assertEqual(set(), PLEX_ONLY & self.shown())

    def test_a_hidden_setting_keeps_its_default(self):
        # the library and item screens test 'items' in cache_requests: a missing default would be None there
        self.assertEqual(settings.util.getSetting('cache_requests'), [])
        self.assertEqual(settings.util.getSetting('burn_subtitles'), 'auto')
        self.assertIs(settings.util.getSetting('use_watchlist'), True)

    def test_a_shows_playback_settings_have_no_auto_sync(self):
        manager = mock.Mock(return_value=playback_utils.PlaybackSettings(*[False] * 7))
        manager.transMap = playback_utils.TRANS_MAP
        with (
            mock.patch.object(playbacksettings.INTERFACE, 'playbackManager', manager, create=True),
            mock.patch.object(playbacksettings, 'showOptionsDialog') as picker,
        ):
            playbacksettings.PlaybackSettingsMixin().playbackSettings(mock.Mock(defaultTitle='Harbor Lights'))

        self.assertNotIn('auto_sync', [key for key, _label, _on in picker.call_args[0][1]])

    def test_subtitle_download_from_labels_jellyfin(self):
        audio_settings = {s.ID: s for s in settings.Settings.SETTINGS['audio'][1] if s is not None}
        option_map = dict(audio_settings['subtitle_download_from'].options)
        self.assertEqual(option_map['plex'], 'Jellyfin')

        ENV.settings['subtitle_download_from'] = 'ask'
        dlg = object.__new__(seekdialog.SeekDialog)
        dlg.handler = mock.Mock(player=mock.Mock(playState=0, STATE_PLAYING=1, STATE_PAUSED=2, video=mock.Mock()))
        dlg.downloadPlexSubtitles = mock.Mock(return_value=True)
        dlg.setSubtitles = mock.Mock()
        with mock.patch.object(seekdialog.optionsdialog, 'show', return_value=0) as prompt:
            dlg.downloadSubtitles()

        args = prompt.call_args[0]
        self.assertIn('Jellyfin', args)
        self.assertNotIn('Plex', args)
        dlg.downloadPlexSubtitles.assert_called_once_with(dlg.player.video)

    def test_nothing_shipped_names_silo(self):
        hits = []

        # 1. addon.xml (outside XML comments)
        addon_xml = os.path.join(REPO_ROOT, 'addon.xml')
        with open(addon_xml, encoding='utf-8') as fp:
            clean = XML_COMMENT_RE.sub('', fp.read())
        for lineno, line in enumerate(clean.splitlines(), 1):
            if SILO_RE.search(line):
                hits.append(f'addon.xml:{lineno}: {line.strip()}')

        # 2. All language strings.po files (msgid and msgstr lines, ignoring # comments)
        for lang in sorted(os.listdir(LANGUAGE_DIR)):
            po_file = os.path.join(LANGUAGE_DIR, lang, 'strings.po')
            if not os.path.exists(po_file):
                continue
            with open(po_file, encoding='utf-8') as fp:
                for lineno, line in enumerate(fp, 1):
                    stripped = line.lstrip()
                    if stripped.startswith('#'):
                        continue
                    if SILO_RE.search(line):
                        rel = os.path.relpath(po_file, REPO_ROOT)
                        hits.append(f'{rel}:{lineno}: {line.strip()}')

        # 3. Skin templates and XML resources (outside XML comments)
        resources_dir = os.path.join(REPO_ROOT, 'resources')
        for root, _, files in os.walk(resources_dir):
            for fn in sorted(files):
                if not fn.endswith(('.xml', '.tpl')):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding='utf-8') as fp:
                    clean = XML_COMMENT_RE.sub('', fp.read())
                for lineno, line in enumerate(clean.splitlines(), 1):
                    if SILO_RE.search(line):
                        rel = os.path.relpath(path, REPO_ROOT)
                        hits.append(f'{rel}:{lineno}: {line.strip()}')

        # 4. Text shown from Python code under lib/ (T()'s English defaults)
        lib_dir = os.path.join(REPO_ROOT, 'lib')
        for root, dirs, files in os.walk(lib_dir):
            dirs[:] = sorted(d for d in dirs if d != '__pycache__')
            for fn in sorted(files):
                if not fn.endswith('.py'):
                    continue
                path = os.path.join(root, fn)
                for lineno, text in _iter_translated_defaults(path):
                    if SILO_RE.search(text):
                        rel = os.path.relpath(path, REPO_ROOT)
                        hits.append(f'{rel}:{lineno}: {text!r}')

        self.assertEqual([], hits, 'Shipped files still reference Silo/SiloMod/SM4K:\n' + '\n'.join(hits))

    def test_about_server_version_reads_jellyfin_raw_version(self):
        identity = ClientIdentity('JellyMod', '0.1.0', 'Kodi', 'dev-1')
        setting = settings.ServerVersionSetting('server_version', 'Current Server Version', None)

        # Stored version avoids any network call
        s_known = FakeSession()
        stored_known = jellyfin.StoredServer('http://jf.lan:8096', 'srv-1', 'TheRubys', version='10.11.5')
        srv_known = JellyfinServer(stored_known, JellyfinClient('http://jf.lan:8096', identity, session=s_known))
        with (
            mock.patch('lib.windows.sidebar_model.sidebarServers', return_value=[]),
            mock.patch.object(plexapp, 'SERVERMANAGER', JellyfinServerManager([srv_known])),
        ):
            self.assertEqual(setting.valueLabel(), 'TheRubys 10.11.5')
        self.assertEqual(s_known.calls, [])

        # Legacy stored server without version probes /System/Info/Public once and caches it
        s_legacy = FakeSession(FakeResponse(200, {'Id': 'srv-1', 'ServerName': 'TheRubys', 'Version': '10.11.5'}))
        stored_legacy = jellyfin.StoredServer('http://jf.lan:8096', 'srv-1', 'TheRubys')
        srv_legacy = JellyfinServer(stored_legacy, JellyfinClient('http://jf.lan:8096', identity, session=s_legacy))
        with (
            mock.patch('lib.windows.sidebar_model.sidebarServers', return_value=[]),
            mock.patch.object(plexapp, 'SERVERMANAGER', JellyfinServerManager([srv_legacy])),
        ):
            self.assertEqual(setting.valueLabel(), 'TheRubys 10.11.5')
            self.assertEqual(setting.valueLabel(), 'TheRubys 10.11.5')
        self.assertEqual(len(s_legacy.calls), 1)
        self.assertTrue(s_legacy.calls[0]['url'].endswith('/System/Info/Public'))

        # Unreachable server falls back cleanly to '?'
        s_down = FakeSession(problem(503, 'Unavailable'))
        srv_down = JellyfinServer(stored_legacy, JellyfinClient('http://jf.lan:8096', identity, session=s_down))
        with (
            mock.patch('lib.windows.sidebar_model.sidebarServers', return_value=[]),
            mock.patch.object(plexapp, 'SERVERMANAGER', JellyfinServerManager([srv_down])),
        ):
            self.assertEqual(setting.valueLabel(), 'TheRubys ?')
