"""
The settings screen offers nothing that only a Plex server or plex.tv reads (Phase 9's settings pass): the Network
section - local mode, insecure connections, LAN discovery and GDM, plex.direct mapping, manual connections - set how
the add-on reached Plex servers, and a Silo server is reached at the one address it was added by. Nor what only Plex's
transcoder, its cinema trailers and pre-rolls, or its request cache read: those keep their defaults, hidden.

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from lib import playback_utils
from lib.windows import playbacksettings, settings

from .base import KodiTestCase

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

# read by Plex's transcode request (subtitles burned in, SSA styling), its subtitle auto-sync, its cinema trailers and
# pre-rolls, and its request cache: Silo plays without any of them
PLEX_ONLY = {
    'burn_subtitles',
    'burn_ssa',
    'auto_sync',
    'preplay_preroll',
    'preplay_preroll_first',
    'preplay_trailers',
    'cache_requests',
    'persist_requests_cache',
}


class SiloSettingsTest(KodiTestCase):
    def offered(self):
        # a section can hold None for a setting this platform leaves out
        return {s.ID for _, section in settings.Settings.SETTINGS.values() for s in section if s is not None}

    def test_no_network_section(self):
        self.assertNotIn('network', settings.Settings.SECTION_IDS)
        self.assertNotIn('network', settings.Settings.SETTINGS)

    def test_no_plex_connection_settings(self):
        self.assertEqual(set(), PLEX_CONNECTION & self.offered())

    def test_every_section_listed_is_there(self):
        self.assertEqual(set(settings.Settings.SECTION_IDS), set(settings.Settings.SETTINGS))

    def shown(self):
        return {
            s.ID
            for _, section in settings.Settings.SETTINGS.values()
            for s in section
            if s is not None and s.should_show()
        }

    def test_nothing_only_plex_reads_is_shown(self):
        self.assertEqual(set(), PLEX_ONLY & self.shown())

    def test_a_hidden_setting_keeps_its_default(self):
        # the library and item screens test 'items' in cache_requests: a missing default would be None there
        self.assertEqual(settings.util.getSetting('cache_requests'), [])
        self.assertEqual(settings.util.getSetting('burn_subtitles'), 'auto')

    def test_a_shows_playback_settings_have_no_auto_sync(self):
        manager = mock.Mock(return_value=playback_utils.PlaybackSettings(*[False] * 7))
        manager.transMap = playback_utils.TRANS_MAP
        with (
            mock.patch.object(playbacksettings.INTERFACE, 'playbackManager', manager, create=True),
            mock.patch.object(playbacksettings, 'showOptionsDialog') as picker,
        ):
            playbacksettings.PlaybackSettingsMixin().playbackSettings(mock.Mock(defaultTitle='Harbor Lights'))

        self.assertNotIn('auto_sync', [key for key, _label, _on in picker.call_args[0][1]])
