"""
Kodi quitting (System.OnQuit) while SiloMod has no Home yet - on the sign-in card, or as it starts - crashed the monitor
with "'NoneType' object has no attribute 'closeOption'" (seen in the box's log). With Home open, Home closes as before.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from lib import monitor
from lib.windows import windowutils

from .base import KodiTestCase


class MonitorQuitTest(KodiTestCase):
    def quit(self):
        with mock.patch.object(monitor.MONITOR, 'actionStop') as stop, mock.patch.object(monitor.MONITOR, 'trigger'):
            monitor.MONITOR.onNotification('xbmc', 'System.OnQuit', '{}')
        stop.assert_called_once_with()

    def test_quitting_with_no_home_yet_stops_playback_only(self):
        with mock.patch.object(windowutils, 'HOME', None):
            self.quit()

    def test_quitting_with_home_open_closes_it(self):
        home = mock.Mock()
        with mock.patch.object(windowutils, 'HOME', home):
            self.quit()

        self.assertEqual(home.closeOption, 'kodi_exit')
        home.doClose.assert_called_once_with()
