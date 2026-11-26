"""
jellynet.account.JellyfinAccount, jellynet.servermanager.JellyfinServerManager, and lib/windows/userselect.py:
multi-user sessions on one Kodi (each user with their own token and DeviceId), switching between signed-in
and new users (password prompt vs passwordless vs hidden user), Refresh Users, 401 session end, and sign-out.
"""

import contextlib
from collections import deque
from unittest import mock

import requests
from jellynet import auth, errors
from jellynet.account import JellyfinAccount, JellyfinUser
from jellynet.client import ClientIdentity, JellyfinClient, device_id_for
from jellynet.servermanager import JellyfinServerManager
from kodienv import ENV

ENV.abort_requested = True
from plexnet import plexapp
from plexnet import util as pnUtil

from lib import jellyfin
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


class RunNow:
    """threading.Thread that runs its target at start(): Quick Connect's poll runs inside the click."""

    def __init__(self, target=None, args=(), **_kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class FakeQuickConnect:
    """Quick Connect as the server answers it: a code to show, then the person approves it (result) or not (None);
    error: the server has Quick Connect off."""

    result = None
    error = None

    def __init__(self, client, poll_interval=None):
        self.client = client

    def poll(self, show_code, wait):
        if FakeQuickConnect.error is not None:
            raise FakeQuickConnect.error
        show_code(auth.QuickConnectCode('482910', 'QC-SECRET'))
        return FakeQuickConnect.result


def picker_window(data_source=None):
    """UserSelectWindow as Kodi runs it: its properties in window.props, its focus in window.focused."""
    window = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
    window.selected = None
    window.closed = False
    window._closing = False
    window._qc = 0
    window.username = window.password = ''
    window.props = {}
    window.setProperty = window.props.__setitem__
    window.getProperty = lambda key: window.props.get(key, '')
    window.focused = None
    window.setFocusId = lambda control_id: setattr(window, 'focused', control_id)
    window.userList = mock.Mock()
    window.userList.getSelectedItem.return_value = FakeListItem(data_source)
    window.userList.__len__ = lambda self: 1
    window.doClose = lambda: setattr(window, 'closed', True)
    return window


@contextlib.contextmanager
def running(account, server=None):
    FakeQuickConnect.result = FakeQuickConnect.error = None
    manager = mock.Mock()
    manager.selectedServer = server or mock.Mock(url='http://jellyfin.lan:8096', uuid='srv-1')
    manager.selectedServer.name = 'TheRubys'
    with (
        mock.patch.object(plexapp, 'ACCOUNT', account),
        mock.patch.object(plexapp, 'SERVERMANAGER', manager),
        mock.patch.object(userselect.busy.BusyWindow, 'create', side_effect=RuntimeError),
        mock.patch.object(userselect.busy.update_restart, 'filesReplaced', return_value=True),
        mock.patch.object(userselect.threading, 'Thread', RunNow),
        mock.patch.object(userselect.auth, 'QuickConnect', FakeQuickConnect),
        mock.patch.object(userselect.util, 'messageDialog') as dialog,
    ):
        yield dialog


def click_user_tile(account, data_source):
    window = picker_window(data_source)
    with running(account) as dialog:
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

    def test_a_user_without_a_session_gets_the_sign_in_form(self):
        # Jellyfin 12 says every user has a password: a tile without a stored session swaps the tiles for the form,
        # their name in User and the password next, a Quick Connect code and QR below it. No keyboard pop-ups yet.
        acct = make_account(FakeSession(PUBLIC_USERS))
        acct.updateHomeUsers()

        window, dialog = click_user_tile(acct, acct.getHomeUser('u2'))

        self.assertEqual(window.props.get('mode'), 'form')
        self.assertEqual(window.props.get('username'), 'bob')
        self.assertEqual(window.focused, window.PASSWORD_ID)
        self.assertEqual(window.props.get('pin'), '482910')
        self.assertTrue(window.props.get('qr'))
        self.assertTrue(window.props.get('qc.ready'))
        self.assertFalse(window.closed)
        self.assertFalse([c for c in ENV.dialog_calls if c[0] == 'input'])
        dialog.assert_not_called()

    def test_sign_in_on_the_form(self):
        session = FakeSession(
            PUBLIC_USERS, FakeResponse(401, raw='Invalid username or password.'), auth_ok('TOK-BOB', 'u2', 'bob')
        )
        acct = make_account(session)
        acct.updateHomeUsers()
        window = picker_window(acct.getHomeUser('u2'))
        ENV.dialog_answers = deque(['wrong-pw', 'bob-pw'])

        with running(acct):
            window.onClick(window.USER_LIST_ID)
            window.onClick(window.PASSWORD_ID)
            window.onClick(window.SIGN_IN_ID)
            # wrong password: says so, keeps the name, clears the password, stays on the form
            self.assertEqual(window.props.get('error'), 'Wrong username or password.')
            self.assertEqual((window.props.get('username'), window.props.get('password')), ('bob', ''))
            self.assertFalse(window.closed)
            window.onClick(window.PASSWORD_ID)
            window.onClick(window.SIGN_IN_ID)

        self.assertTrue(window.selected)
        self.assertTrue(window.closed)
        self.assertEqual(session.calls[2]['json'], {'Username': 'bob', 'Pw': 'bob-pw'})
        self.assertEqual((acct.ID, acct.authToken), ('u2', 'TOK-BOB'))
        self.assertEqual(acct.sessions['u1']['access_token'], 'TOK-ALICE')

    def test_number_keys_on_a_tile_never_sign_in(self):
        # Jellyfin has no PINs: Uno's four-digit PIN entry sent the digits as a password, and a failed sign-in counts
        # towards Jellyfin's lockout, so number keys on the user picker do nothing.
        session = FakeSession(PUBLIC_USERS)
        acct = make_account(session)
        acct.updateHomeUsers()
        window = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
        window.selected = None
        window.userList = mock.Mock()
        window.userList.getSelectedItem.return_value = FakeListItem(acct.getHomeUser('u2'))
        window.setFocusId = mock.Mock()
        window.doClose = mock.Mock()
        with (
            mock.patch.object(plexapp, 'ACCOUNT', acct),
            mock.patch.object(userselect.busy.BusyWindow, 'create', side_effect=RuntimeError),
            mock.patch.object(userselect.busy.update_restart, 'filesReplaced', return_value=True),
            mock.patch.object(userselect.util, 'messageDialog'),
            mock.patch.object(userselect.kodigui.BaseWindow, 'onAction'),
        ):
            for action_id in (59, 60, 61, 62, 142, 143, 144, 145):  # REMOTE_1..4, then the JumpSMS keys
                window.onAction(mock.Mock(getId=mock.Mock(return_value=action_id)))

        self.assertEqual(len(session.calls), 1)  # /Users/Public only: no sign-in attempt
        self.assertIsNone(window.selected)
        window.setFocusId.assert_not_called()

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

    def test_manual_login_opens_an_empty_form(self):
        acct = make_account(FakeSession(PUBLIC_USERS))
        acct.updateHomeUsers()
        window = picker_window()

        with running(acct):
            window.onClick(window.MANUAL_LOGIN_ID)

        self.assertEqual((window.props.get('mode'), window.props.get('username')), ('form', ''))
        self.assertEqual(window.focused, window.USERNAME_ID)
        self.assertEqual(window.props.get('pin'), '482910')

    def test_use_quick_connect_signs_in_whoever_approves(self):
        acct = make_account(FakeSession(PUBLIC_USERS))
        acct.updateHomeUsers()
        window = picker_window()

        with running(acct):
            FakeQuickConnect.result = auth.AuthResult('TOK-KIDS', 'u3', 'kids', False, 'srv-1')
            window.onClick(window.QUICK_CONNECT_ID)

        self.assertEqual(window.props.get('mode'), 'quickconnect')
        self.assertEqual(window.props.get('pin'), '482910')
        self.assertTrue(window.selected)
        self.assertTrue(window.closed)
        self.assertEqual((acct.ID, acct.authToken), ('u3', 'TOK-KIDS'))
        self.assertTrue(acct.isAuthenticated)

    def test_quick_connect_off_on_the_server(self):
        acct = make_account(FakeSession(PUBLIC_USERS))
        acct.updateHomeUsers()
        window = picker_window()

        with running(acct):
            FakeQuickConnect.error = errors.JellyfinError(401, 'Quick connect is not enabled on this server')
            window.onClick(window.QUICK_CONNECT_ID)

        self.assertFalse(window.props.get('qc.ready'))
        self.assertEqual(window.props.get('qc.error'), 'Quick Connect is off on this server.')
        self.assertFalse(window.closed)

    def test_cancel_and_back_return_to_the_users(self):
        acct = make_account(FakeSession(PUBLIC_USERS))
        acct.updateHomeUsers()
        window = picker_window()
        back = mock.Mock(getId=mock.Mock(return_value=userselect.xbmcgui.ACTION_NAV_BACK))

        with running(acct), mock.patch.object(userselect.kodigui.BaseWindow, 'onAction'):
            window.onClick(window.MANUAL_LOGIN_ID)
            window.onClick(window.FORM_CANCEL_ID)
            self.assertEqual((window.props.get('mode'), window.focused), ('', window.USER_LIST_ID))
            window.onClick(window.QUICK_CONNECT_ID)
            window.onAction(back)
            self.assertEqual(window.props.get('mode'), '')
            self.assertFalse(window.closed)
            window.onAction(back)  # Back on the users: leave the screen

        self.assertEqual(window.selected, 'cancel')
        self.assertTrue(window.closed)

    def test_switch_server_opens_connect_to_server(self):
        acct = make_account(FakeSession(PUBLIC_USERS))
        acct.updateHomeUsers()
        window = picker_window()
        other = jellyfin.StoredServer('http://b.lan:8096', 'srv-2', 'Jellyfin B')

        with running(acct), mock.patch.object(jellyfin, 'connectToServer', return_value=None) as connect:
            window.onClick(window.SWITCH_SERVER_ID)
        self.assertEqual(connect.call_args.kwargs['address'], 'http://jellyfin.lan:8096')
        self.assertFalse(window.closed)  # Cancel on Connect to Server: still here

        with running(acct), mock.patch.object(jellyfin, 'connectToServer', return_value=other):
            window.onClick(window.SWITCH_SERVER_ID)
        self.assertEqual(window.selected, jellyfin.SWITCH_SERVER)
        self.assertTrue(window.closed)

    def test_a_user_signed_in_on_the_card_stays_listed(self):
        # A user hidden from /Users/Public who signed in on the card keeps their tile, and Alice keeps her token.
        session = FakeSession(PUBLIC_USERS, PUBLIC_USERS)
        acct = make_account(session)
        acct.updateHomeUsers()

        acct.recordAuth(auth.AuthResult('TOK-HIDDEN', 'u9', 'ghost', False, 'srv-1'))

        self.assertEqual((acct.ID, acct.username, acct.authToken), ('u9', 'ghost', 'TOK-HIDDEN'))
        self.assertEqual(acct.sessions['u1']['access_token'], 'TOK-ALICE')
        acct.updateHomeUsers()
        self.assertIn('u9', [u.id for u in acct.homeUsers])

    def test_refresh_users_reloads_the_list_once(self):
        initial = FakeResponse(200, [user_dto('u1', 'alice', has_password=True, is_admin=True)])
        session = FakeSession(initial, PUBLIC_USERS)
        acct = make_account(session)
        acct.updateHomeUsers()
        self.assertEqual([u.id for u in acct.homeUsers], ['u1'])

        acct.updateHomeUsers(refreshSubscription=True)  # the user menu's Refresh Users

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[1]['url'], 'http://jellyfin.lan:8096/Users/Public')
        self.assertEqual([u.id for u in acct.homeUsers], ['u1', 'u2', 'u3'])

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
        self.assertTrue(acct.hasPlexPass())
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
        self.assertIsNone(manager.getWatchlistServer())
        self.assertFalse(manager.retestServerNow())

    def test_sign_in_screen_lists_the_users_and_its_buttons(self):
        acct = make_account(FakeSession(PUBLIC_USERS))
        acct.updateHomeUsers()
        window = picker_window()

        with running(acct):
            window.start(with_busy=False)

        items = window.userList.addItems.call_args[0][0]
        self.assertEqual([i.dataSource.id for i in items], ['u1', 'u2', 'u3'])  # the users, nothing else
        labels = {
            k: window.props.get(k) for k in ('label.heading', 'label.quickconnect', 'label.manual', 'label.switch')
        }
        self.assertEqual(
            labels,
            {
                'label.heading': 'Sign in to continue',
                'label.quickconnect': 'Use Quick Connect',
                'label.manual': 'Manual Login',
                'label.switch': 'Switch server',
            },
        )
        self.assertEqual(window.props.get('server.name'), 'TheRubys')
        self.assertEqual(window.props.get('mode'), '')
