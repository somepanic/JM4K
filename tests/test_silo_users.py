"""
Refresh Users on Silo: with one profile known, the sidebar user menu offers Refresh Users, which reads Silo's profiles
again (SiloAccount.updateHomeUsers(): GET /api/v2/profiles). Once there are several, the menu offers Switch User.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
import requests

from lib.windows import library

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession
from .test_silonet_account import PROFILES, account, profile

ONE_PROFILE = FakeResponse(200, {'items': [profile('p1', 'Sam', primary=True)]})


class SiloRefreshUsersTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        # Silo has one profile when SiloMod starts, and a second one (PROFILES) by the time Refresh Users asks again
        self.session = FakeSession(ONE_PROFILE, PROFILES)
        self.account = account(self.session)
        self.account.updateHomeUsers()
        for name, value in (('ACCOUNT', self.account), ('SERVERMANAGER', mock.Mock(selectedServer=None))):
            patch = mock.patch.object(library.plexapp, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.window = library.LibraryWindow.__new__(library.LibraryWindow)

    def menu(self):
        """The user menu's option keys, as the sidebar's user button shows them."""
        with mock.patch.object(library, 'showOptionsDialog', return_value=None) as dialog:
            self.window.showUserMenu()
        return [key for key, _label in dialog.call_args[0][1]]

    def test_one_profile_offers_refresh_users(self):
        self.assertIn('refresh_users', self.menu())

    def test_refresh_users_reads_silos_profiles_again(self):
        self.window.doUserOption('refresh_users')

        self.assertEqual([user.id for user in self.account.homeUsers], ['p1', 'p2'])
        self.assertTrue(self.session.calls[-1]['url'].endswith('/api/v2/profiles'))

    def test_a_new_profile_brings_switch_user(self):
        self.window.doUserOption('refresh_users')

        options = self.menu()

        self.assertIn('switch', options)
        self.assertNotIn('refresh_users', options)

    def test_a_gone_previous_profile_opens_the_menu_instead_of_restarting(self):
        # switched from, then removed on Silo: Refresh Users has the list without it
        ENV.settings['previous_user'] = 'p-gone'
        self.window.doUserOption = mock.Mock()

        with mock.patch.object(library, 'showOptionsDialog', return_value=None) as dialog:
            self.window.fastSwitchUser()

        self.window.doUserOption.assert_not_called()  # 'switch' with nothing to pick only reopened Home
        dialog.assert_called_once()  # the user menu
        self.assertEqual(ENV.settings.get('previous_user'), '')

    def test_a_pin_protected_previous_profile_asks_for_its_pin(self):
        self.window.doUserOption('refresh_users')  # p2 (Kids, with a PIN) is listed now
        ENV.settings['previous_user'] = 'p2'
        self.window.doUserOption = mock.Mock()

        self.window.fastSwitchUser()

        self.window.doUserOption.assert_called_once_with('switch')

    def test_refresh_users_while_silo_is_down_keeps_the_profiles(self):
        self.session.responses = [requests.ConnectionError('refused')]  # Silo is down by the time it's asked

        with mock.patch.object(library.util, 'showNotification') as notice:
            self.window.doUserOption('refresh_users')

        self.assertEqual([user.id for user in self.account.homeUsers], ['p1'])
        notice.assert_called_once()
