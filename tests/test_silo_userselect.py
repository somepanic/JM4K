"""
lib/windows/userselect.py with Silo profiles: a PIN-protected profile asks for its PIN with Kodi's keyboard (Silo
PINs are 1 to 72 characters, not Uno's four digits), and a wrong, locked or unreachable check says so.
"""

from collections import deque
from unittest import mock

import requests
from kodienv import ENV
from plexnet import plexapp
from silonet.account import SiloAccount, SiloProfile
from silonet.client import Credentials, DeviceIdentity, SiloClient

from lib.windows import userselect

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession, problem

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')


class FakeItem:
    def __init__(self, profile):
        self.dataSource = profile
        self.props = {}

    def setProperty(self, key, value):
        self.props[key] = value

    def getProperty(self, key):
        return self.props.get(key, '')


class UserSelectSiloTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.kids = SiloProfile({'id': 'p2', 'name': 'Kids', 'has_pin': True})

    def run_click(self, *responses):
        session = FakeSession(*responses)
        client = SiloClient(
            'http://silo.lan:8090', DEVICE, Credentials(accessToken='A1', profileId='p1'), session=session
        )
        account = SiloAccount(client)
        account.homeUsers = [SiloProfile({'id': 'p1', 'name': 'Sam'}), self.kids]
        window = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
        window.selected = None
        window.closed = False
        window.userList = mock.Mock()
        window.userList.getSelectedItem.return_value = FakeItem(self.kids)
        window.doClose = lambda: setattr(window, 'closed', True)
        with (
            mock.patch.object(plexapp, 'ACCOUNT', account),
            mock.patch.object(userselect.busy.BusyWindow, 'create', side_effect=RuntimeError),
            mock.patch.object(userselect.busy.update_restart, 'filesReplaced', return_value=True),
            mock.patch.object(userselect.util, 'messageDialog') as dialog,
        ):
            window.onClick(window.USER_LIST_ID)
        return window, session, dialog, account

    def test_protected_profile_asks_for_pin_with_keyboard(self):
        ENV.dialog_answers = deque(['271828'])

        window, session, dialog, account = self.run_click(FakeResponse(200, {'valid': True, 'profile_token': 'PT2'}))

        self.assertEqual(session.calls[0]['json'], {'pin': '271828'})
        self.assertTrue(window.selected)
        self.assertEqual(account.ID, 'p2')
        dialog.assert_not_called()

    def test_wrong_pin_tells_the_user(self):
        ENV.dialog_answers = deque(['0000'])

        window, _, dialog, account = self.run_click(FakeResponse(200, {'valid': False}))

        self.assertIsNone(window.selected)
        self.assertEqual(account.ID, 'p1')
        self.assertIn('Wrong pin', dialog.call_args[0][1])

    def test_locked_profile_says_when_to_try_again(self):
        ENV.dialog_answers = deque(['0000'])

        window, _, dialog, _ = self.run_click(problem(429, 'rate_limited', headers={'Retry-After': '300'}))

        self.assertIsNone(window.selected)
        self.assertIn('5 minutes', dialog.call_args[0][1])

    def test_unreachable_server_says_so(self):
        ENV.dialog_answers = deque(['0000'])

        window, _, dialog, _ = self.run_click(requests.ConnectionError('down'))

        self.assertIsNone(window.selected)
        self.assertIn('Silo server', dialog.call_args[0][1])

    def test_no_pin_entered_does_nothing(self):
        ENV.dialog_answers = deque([''])

        window, session, dialog, _ = self.run_click()

        self.assertIsNone(window.selected)
        self.assertEqual(session.calls, [])
        dialog.assert_not_called()

    def test_refresh_while_silo_is_down_keeps_the_profiles(self):
        session = FakeSession(requests.ConnectionError('refused'))
        client = SiloClient(
            'http://silo.lan:8090', DEVICE, Credentials(accessToken='A1', profileId='p1'), session=session
        )
        account = SiloAccount(client)
        account.homeUsers = [SiloProfile({'id': 'p1', 'name': 'Sam'}), self.kids]
        window = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
        window.userList = mock.Mock()
        window.userList.getSelectedItem.return_value = FakeItem(userselect.kodigui.EmptyDataSource())  # Refresh
        window.setProperty = mock.Mock()
        window.propertyContext = mock.MagicMock()
        window.start = mock.Mock()

        with (
            mock.patch.object(plexapp, 'ACCOUNT', account),
            mock.patch.object(userselect.util, 'showNotification') as notice,
        ):
            window.onClick(window.USER_LIST_ID)

        self.assertEqual([user.id for user in account.homeUsers], ['p1', 'p2'])
        window.start.assert_called_once_with(with_busy=False)  # the list shown again, as it was
        notice.assert_called_once()
