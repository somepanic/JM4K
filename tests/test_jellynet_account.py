"""
jellynet.account.JellyfinAccount, jellynet.servermanager.JellyfinServerManager, and lib/windows/userselect.py:
multi-user sessions on one Kodi (each user with their own token and DeviceId), switching between signed-in
and new users (password prompt vs passwordless vs hidden user), Refresh Users, 401 session end, and sign-out.
"""

from collections import deque
from unittest import mock

import requests
from jellynet import errors
from jellynet.account import JellyfinAccount, JellyfinUser
from jellynet.client import ClientIdentity, JellyfinClient, device_id_for
from jellynet.servermanager import JellyfinServerManager
from kodienv import ENV

ENV.abort_requested = True
from plexnet import plexapp
from plexnet import util as pnUtil

from lib.windows import userselect

from .base import KodiTestCase
from .jellynet_fakes import FakeResponse, FakeSession, problem

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')


def user_dto(uid, name, has_password=True, is_admin=False, image_tag=''):
    dto = {
        'Id': uid,
        'Name': name,
        'ServerId': 'srv-1',
        'HasPassword': has_password,
        'Policy': {'IsAdministrator': is_admin},
    }
    if image_tag:
        dto['PrimaryImageTag'] = image_tag
    return dto


def auth_ok(token, uid, name, is_admin=False, server_id='srv-1'):
    return FakeResponse(
        200,
        {
            'AccessToken': token,
            'ServerId': server_id,
            'User': user_dto(uid, name, is_admin=is_admin),
            'SessionInfo': {'UserId': uid, 'UserName': name},
        },
    )


PUBLIC_USERS = FakeResponse(
    200,
    [
        user_dto('u1', 'alice', has_password=True, is_admin=True, image_tag='tag-a'),
        user_dto('u2', 'bob', has_password=True, is_admin=False),
        user_dto('u3', 'kids', has_password=False, is_admin=False),
    ],
)


def make_account(session, sessions=None, active_user_id='u1', on_change=None):
    if sessions is None:
        sessions = {
            'u1': {
                'user_id': 'u1',
                'username': 'alice',
                'access_token': 'TOK-ALICE',
                'is_admin': True,
                'has_password': True,
                'primary_image_tag': 'tag-a',
            }
        }
    client = JellyfinClient('http://jellyfin.lan:8096', IDENTITY, session=session)
    return JellyfinAccount(client, sessions=sessions, active_user_id=active_user_id, on_change=on_change)


class FakeListItem:
    def __init__(self, data_source):
        self.dataSource = data_source
        self.props = {}

    def setProperty(self, key, value):
        self.props[key] = value

    def getProperty(self, key):
        return self.props.get(key, '')


def click_user_tile(account, data_source):
    window = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
    window.selected = None
    window.closed = False
    window.userList = mock.Mock()
    window.userList.getSelectedItem.return_value = FakeListItem(data_source)
    window.doClose = lambda: setattr(window, 'closed', True)
    with (
        mock.patch.object(plexapp, 'ACCOUNT', account),
        mock.patch.object(userselect.busy.BusyWindow, 'create', side_effect=RuntimeError),
        mock.patch.object(userselect.busy.update_restart, 'filesReplaced', return_value=True),
        mock.patch.object(userselect.util, 'messageDialog') as dialog,
    ):
        window.onClick(window.USER_LIST_ID)
    return window, dialog


class JellyfinAccountTest(KodiTestCase):
    def test_second_user_keeps_first_users_token(self):
        # Review Focus 3: signing in a second user uses a distinct DeviceId and preserves the first user's token.
        saved = []
        session = FakeSession(PUBLIC_USERS, auth_ok('TOK-BOB', 'u2', 'bob', is_admin=False))
        acct = make_account(session, on_change=lambda s, active: saved.append((dict(s), active)))
        acct.updateHomeUsers()

        alice_auth_header = acct.client.auth_header()
        self.assertTrue(acct.switchHomeUser('u2', 'bob-pw'))

        bob_call = session.calls[1]
        self.assertEqual(bob_call['url'], 'http://jellyfin.lan:8096/Users/AuthenticateByName')
        self.assertEqual(bob_call['json'], {'Username': 'bob', 'Pw': 'bob-pw'})
        self.assertIn(f'DeviceId="{device_id_for(IDENTITY, "bob")}"', bob_call['headers']['Authorization'])
        self.assertIn(f'DeviceId="{device_id_for(IDENTITY, "alice")}"', alice_auth_header)
        self.assertNotEqual(device_id_for(IDENTITY, 'alice'), device_id_for(IDENTITY, 'bob'))

        self.assertEqual((acct.ID, acct.username, acct.authToken, acct.isAdmin), ('u2', 'bob', 'TOK-BOB', False))
        self.assertEqual(acct.sessions['u1']['access_token'], 'TOK-ALICE')
        self.assertEqual(acct.sessions['u2']['access_token'], 'TOK-BOB')
        self.assertEqual(saved[-1][1], 'u2')
        self.assertIn('u1', saved[-1][0])

    def test_switch_to_signed_in_user_needs_no_sign_in(self):
        both = {
            'u1': {'user_id': 'u1', 'username': 'alice', 'access_token': 'TOK-ALICE', 'is_admin': True},
            'u2': {'user_id': 'u2', 'username': 'bob', 'access_token': 'TOK-BOB', 'is_admin': False},
        }
        session = FakeSession(PUBLIC_USERS, FakeResponse(200, {'Items': []}))
        acct = make_account(session, sessions=both, active_user_id='u2')
        acct.updateHomeUsers()
        calls_before = len(session.calls)

        # Clicking an already signed-in password-protected user switches directly without prompting or calling auth.
        alice = acct.getHomeUser('u1')
        window, dialog = click_user_tile(acct, alice)

        self.assertTrue(window.selected)
        self.assertEqual(len(session.calls), calls_before)
        self.assertFalse([c for c in ENV.dialog_calls if c[0] == 'input'])
        dialog.assert_not_called()
        self.assertEqual((acct.ID, acct.authToken, acct.isAdmin), ('u1', 'TOK-ALICE', True))

        acct.client.get('/UserViews')
        auth_header = session.calls[-1]['headers']['Authorization']
        self.assertIn('Token="TOK-ALICE"', auth_header)
        self.assertIn(f'DeviceId="{device_id_for(IDENTITY, "alice")}"', auth_header)

    def test_switch_to_new_user_asks_password(self):
        session = FakeSession(
            PUBLIC_USERS,
            FakeResponse(401, raw='Invalid username or password.'),
            auth_ok('TOK-BOB', 'u2', 'bob'),
        )
        acct = make_account(session)
        acct.updateHomeUsers()
        bob = acct.getHomeUser('u2')

        # Wrong password keeps current user active, never ends active session, and tells the user.
        ENV.dialog_answers = deque(['wrong-pw'])
        with mock.patch.object(pnUtil, 'APP') as app:
            window, dialog = click_user_tile(acct, bob)
        self.assertIsNone(window.selected)
        self.assertEqual(acct.ID, 'u1')
        dialog.assert_called_once()
        self.assertIn('Wrong username or password', dialog.call_args[0][1])
        app.trigger.assert_not_called()

        # Correct password uses hidden keyboard input and signs Bob in.
        with mock.patch.object(userselect.xbmcgui.Dialog, 'input', return_value='good-pw') as prompt:
            window, dialog = click_user_tile(acct, bob)
        self.assertTrue(window.selected)
        self.assertEqual((acct.ID, acct.authToken), ('u2', 'TOK-BOB'))
        dialog.assert_not_called()
        prompt.assert_called_once_with('Password for bob', option=userselect.xbmcgui.ALPHANUM_HIDE_INPUT)

    def test_user_without_password_switches_directly(self):
        session = FakeSession(PUBLIC_USERS, auth_ok('TOK-KIDS', 'u3', 'kids'))
        acct = make_account(session)
        acct.updateHomeUsers()
        kids = acct.getHomeUser('u3')

        window, dialog = click_user_tile(acct, kids)

        self.assertTrue(window.selected)
        self.assertFalse([c for c in ENV.dialog_calls if c[0] == 'input'])
        dialog.assert_not_called()
        self.assertEqual(session.calls[1]['url'], 'http://jellyfin.lan:8096/Users/AuthenticateByName')
        self.assertEqual(session.calls[1]['json'], {'Username': 'kids', 'Pw': ''})
        self.assertEqual((acct.ID, acct.authToken), ('u3', 'TOK-KIDS'))

    def test_hidden_user_can_type_a_name(self):
        # A user hidden from /Users/Public can sign in via Other user and remains visible in homeUsers afterward.
        session = FakeSession(
            PUBLIC_USERS,
            auth_ok('TOK-HIDDEN', 'u9', 'ghost', is_admin=False),
            PUBLIC_USERS,
        )
        acct = make_account(session)
        acct.updateHomeUsers()
        ENV.dialog_answers = deque(['ghost', 'ghost-pw'])

        window, dialog = click_user_tile(acct, userselect.OTHER_USER)

        self.assertTrue(window.selected)
        dialog.assert_not_called()
        self.assertEqual((acct.ID, acct.username, acct.authToken), ('u9', 'ghost', 'TOK-HIDDEN'))
        self.assertEqual(session.calls[1]['json'], {'Username': 'ghost', 'Pw': 'ghost-pw'})
        self.assertIn('u9', [u.id for u in acct.homeUsers])
        self.assertEqual(acct.sessions['u1']['access_token'], 'TOK-ALICE')

        # Refreshing /Users/Public still keeps the signed-in hidden user in homeUsers.
        acct.updateHomeUsers()
        self.assertIn('u9', [u.id for u in acct.homeUsers])

    def test_refresh_users_reloads_the_list_once(self):
        initial = FakeResponse(200, [user_dto('u1', 'alice', has_password=True, is_admin=True)])
        session = FakeSession(initial, PUBLIC_USERS)
        acct = make_account(session)
        acct.updateHomeUsers()
        self.assertEqual([u.id for u in acct.homeUsers], ['u1'])

        window = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
        window.userList = mock.Mock()
        window.userList.getSelectedItem.return_value = FakeListItem(userselect.kodigui.EmptyDataSource())
        window.setProperty = mock.Mock()
        window.propertyContext = mock.MagicMock()
        window.start = mock.Mock()

        with mock.patch.object(plexapp, 'ACCOUNT', acct):
            window.onClick(window.USER_LIST_ID)

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[1]['url'], 'http://jellyfin.lan:8096/Users/Public')
        self.assertEqual([u.id for u in acct.homeUsers], ['u1', 'u2', 'u3'])
        window.start.assert_called_once_with(with_busy=False)

    def test_401_ends_the_session(self):
        # Review Focus 4: a 401 mid-use drops only the revoked token and triggers account:session_ended ONCE.
        both = {
            'u1': {'user_id': 'u1', 'username': 'alice', 'access_token': 'TOK-ALICE', 'is_admin': True},
            'u2': {'user_id': 'u2', 'username': 'bob', 'access_token': 'TOK-BOB', 'is_admin': False},
        }
        session = FakeSession(problem(401, 'Unauthorized'), problem(401, 'Unauthorized'))
        acct = make_account(session, sessions=both, active_user_id='u1')
        acct.isAuthenticated = True

        with mock.patch.object(pnUtil, 'APP') as app:
            with self.assertRaises(errors.JellyfinAuthError):
                acct.client.get('/UserViews')
            with self.assertRaises(errors.JellyfinAuthError):
                acct.client.get('/UserViews')

        app.trigger.assert_called_once_with('account:session_ended')
        self.assertFalse(acct.isSignedIn)
        self.assertFalse(acct.isAuthenticated)
        self.assertNotIn('u1', acct.sessions)
        self.assertEqual(acct.sessions['u2']['access_token'], 'TOK-BOB')

    def test_sign_out_forgets_only_that_user(self):
        both = {
            'u1': {'user_id': 'u1', 'username': 'alice', 'access_token': 'TOK-ALICE', 'is_admin': True},
            'u2': {'user_id': 'u2', 'username': 'bob', 'access_token': 'TOK-BOB', 'is_admin': False},
        }
        saved = []
        session = FakeSession(FakeResponse(204))
        acct = make_account(
            session, sessions=both, active_user_id='u2', on_change=lambda s, active: saved.append((dict(s), active))
        )
        acct.isAuthenticated = True

        acct.signOut()

        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url']), ('POST', 'http://jellyfin.lan:8096/Sessions/Logout')
        )
        self.assertIn('Token="TOK-BOB"', session.calls[0]['headers']['Authorization'])
        self.assertFalse(acct.isSignedIn)
        self.assertFalse(acct.isAuthenticated)
        self.assertIsNone(acct.ID)
        self.assertNotIn('u2', acct.sessions)
        self.assertEqual(acct.sessions['u1']['access_token'], 'TOK-ALICE')
        self.assertEqual(saved[-1], ({'u1': both['u1']}, None))

    def test_sign_out_without_server_still_forgets_only_active_user(self):
        both = {
            'u1': {'user_id': 'u1', 'username': 'alice', 'access_token': 'TOK-ALICE', 'is_admin': True},
            'u2': {'user_id': 'u2', 'username': 'bob', 'access_token': 'TOK-BOB', 'is_admin': False},
        }
        acct = make_account(FakeSession(requests.ConnectionError('down')), sessions=both, active_user_id='u2')

        acct.signOut()

        self.assertFalse(acct.isSignedIn)
        self.assertNotIn('u2', acct.sessions)
        self.assertIn('u1', acct.sessions)

    def test_account_surface_avatars_and_server_manager(self):
        session = FakeSession(PUBLIC_USERS)
        acct = make_account(session)
        acct.updateHomeUsers()

        self.assertEqual(acct.title, 'alice')
        self.assertEqual(acct.thumb, 'http://jellyfin.lan:8096/Users/u1/Images/Primary?tag=tag-a')
        self.assertNotIn('TOK-ALICE', acct.thumb)
        self.assertTrue(acct.isProtected)
        self.assertFalse(acct.hasPlexPass())
        self.assertFalse(acct.isOffline)
        self.assertEqual(acct.safeUserThumb('u1', acct.thumb), acct.thumb)
        self.assertIsInstance(acct.getHomeUser('u1'), JellyfinUser)

        srv = mock.Mock(uuid='srv-1')
        manager = JellyfinServerManager([srv])
        self.assertIs(manager.selectedServer, srv)
        self.assertEqual(manager.serversByUuid, {'srv-1': srv})
        self.assertEqual(manager.connectedServers, [srv])
        self.assertEqual(manager.getServers(), [srv])
        self.assertIs(manager.getServer('srv-1'), srv)
        self.assertTrue(manager.serversKnown())
        self.assertIsNone(manager.getDiscoverServer())
        self.assertIs(manager.getWatchlistServer(), srv)
        self.assertFalse(manager.retestServerNow())

    def test_user_select_start_adds_other_user_and_handles_refresh_failure(self):
        session = FakeSession(PUBLIC_USERS, requests.ConnectionError('down'))
        acct = make_account(session)
        acct.updateHomeUsers()

        window = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
        window.userList = mock.Mock()
        window.setProperty = mock.Mock()
        window.setFocusId = mock.Mock()
        with mock.patch.object(plexapp, 'ACCOUNT', acct):
            window.start(with_busy=False)

        items = window.userList.addItems.call_args[0][0]
        self.assertEqual([i.dataSource.id for i in items[:3]], ['u1', 'u2', 'u3'])
        self.assertEqual(items[0].getProperty('protected'), '')  # u1 already has a stored session
        self.assertEqual(items[1].getProperty('protected'), '1')  # u2 needs password
        self.assertEqual(items[2].getProperty('protected'), '')  # u3 has no password
        self.assertIs(items[3].dataSource, userselect.OTHER_USER)
        self.assertFalse(items[4].dataSource)  # refresh button

        # Failed refresh keeps existing users and shows notification
        window.userList.getSelectedItem.return_value = FakeListItem(userselect.kodigui.EmptyDataSource())
        window.propertyContext = mock.MagicMock()
        window.start = mock.Mock()
        with (
            mock.patch.object(plexapp, 'ACCOUNT', acct),
            mock.patch.object(userselect.util, 'showNotification') as notice,
        ):
            window.onClick(window.USER_LIST_ID)
        self.assertEqual([u.id for u in acct.homeUsers], ['u1', 'u2', 'u3'])
        notice.assert_called_once()
        window.start.assert_called_once_with(with_busy=False)
