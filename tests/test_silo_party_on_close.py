"""
A watch party in progress ends for this Kodi when Home closes for another session - sign out, another profile or
server, a session Silo ended, exit - not when Home only restarts with its new templates (recompile). Left running,
its room socket kept going on the old server, and a room this Kodi hosted was never ended.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from lib import watch_together
from lib.windows import library, windowutils

from .base import KodiTestCase


class PartyOnCloseTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.party = mock.Mock()
        for patch in (
            mock.patch.object(watch_together, 'PARTY', self.party),
            mock.patch.object(windowutils, 'HOME', mock.Mock()),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.window = library.LibraryWindow.__new__(library.LibraryWindow)
        self.window.navigate = mock.Mock()

    def test_closing_for_another_session_leaves_the_party(self):
        for option in ('signout', 'switch', {'switch_server': 'srv-1'}, 'session_ended', 'exit'):
            with self.subTest(option=option):
                self.party.reset_mock()

                self.window._closeSessionWithOption(option)

                self.party.leave.assert_called_once_with()

    def test_restarting_home_with_new_templates_keeps_it(self):
        self.window._closeSessionWithOption('recompile')

        self.party.leave.assert_not_called()
