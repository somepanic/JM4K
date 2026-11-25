"""
silonet.account.SiloAccount: the signed-in Silo account in the shape Uno's screens expect of plexapp.ACCOUNT, with
Silo's household profiles as its home users (PIN-protected ones verified through /profiles/{id}/verify-pin).
"""

from unittest import mock

import requests
from plexnet import util as pnUtil
from silonet import errors
from silonet.account import SiloAccount
from silonet.client import Credentials, DeviceIdentity, SiloClient

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession, problem

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')


def profile(pid, name, pin=False, primary=False, avatar=''):
    return {
        'id': pid,
        'name': name,
        'has_pin': pin,
        'is_primary': primary,
        'is_child': False,
        'avatar_source': 'upload' if avatar else 'none',
        'avatar_url': avatar,
    }


PROFILES = FakeResponse(
    200,
    {
        'items': [
            profile('p1', 'Sam', primary=True),
            profile('p2', 'Kids', pin=True, avatar='https://silo.lan/avatar/p2.webp'),
        ]
    },
)


def account(session, user=None, profileId='p1'):
    client = SiloClient(
        'http://silo.lan:8090',
        DEVICE,
        Credentials(accessToken='A1', refreshToken='R1', profileId=profileId),
        session=session,
    )
    return SiloAccount(client, user=user or {'role': 'user', 'username': 'sam'})


class SiloAccountTest(KodiTestCase):
    def test_lists_profiles_without_profile_header(self):
        session = FakeSession(PROFILES)
        acct = account(session)

        acct.updateHomeUsers()

        self.assertEqual(
            [(u.id, u.title, u.isProtected) for u in acct.homeUsers], [('p1', 'Sam', False), ('p2', 'Kids', True)]
        )
        self.assertEqual(acct.homeUsers[1].thumb, 'https://silo.lan/avatar/p2.webp')
        self.assertNotIn('X-Profile-Id', session.calls[0]['headers'])
        self.assertEqual(acct.getHomeUser('p2').title, 'Kids')

    def test_signed_in_state(self):
        acct = account(FakeSession(), user={'role': 'admin'})

        self.assertEqual(acct.ID, 'p1')
        self.assertTrue(acct.isSignedIn)
        self.assertTrue(acct.isAdmin)
        self.assertFalse(acct.isOffline)
        self.assertEqual(acct.authToken, 'A1')
        self.assertTrue(acct.hasPlexPass())

    def test_switch_to_pin_profile_verifies_pin(self):
        session = FakeSession(PROFILES, FakeResponse(200, {'valid': True, 'profile_token': 'PT2', 'expires_at': None}))
        acct = account(session)
        acct.updateHomeUsers()

        self.assertTrue(acct.switchHomeUser('p2', '1234'))

        self.assertEqual(session.calls[1]['url'], 'http://silo.lan:8090/api/v2/profiles/p2/verify-pin')
        self.assertEqual(session.calls[1]['json'], {'pin': '1234'})
        self.assertEqual(
            (acct.ID, acct.client.credentials.profileId, acct.client.credentials.profileToken), ('p2', 'p2', 'PT2')
        )
        self.assertTrue(acct.switchUser)

    def test_wrong_pin_returns_false_and_keeps_current(self):
        session = FakeSession(PROFILES, FakeResponse(200, {'valid': False, 'expires_at': None}))
        acct = account(session)
        acct.updateHomeUsers()

        self.assertFalse(acct.switchHomeUser('p2', '0000'))

        self.assertEqual(acct.ID, 'p1')
        self.assertFalse(acct.switchUser)

    def test_switch_to_unprotected_profile_needs_no_pin(self):
        session = FakeSession(PROFILES)
        acct = account(session, profileId='p2')
        acct.client.credentials.profileToken = 'PT2'
        acct.updateHomeUsers()

        self.assertTrue(acct.switchHomeUser('p1'))

        self.assertEqual(len(session.calls), 1)
        self.assertEqual((acct.client.credentials.profileId, acct.client.credentials.profileToken), ('p1', None))

    def test_locked_profile_is_reported_not_retried(self):
        session = FakeSession(PROFILES, problem(429, 'rate_limited', headers={'Retry-After': '300'}), PROFILES)
        acct = account(session)
        acct.updateHomeUsers()

        with self.assertRaises(errors.SiloRateLimited):
            acct.switchHomeUser('p2', '0000')

        acct.updateHomeUsers()
        self.assertEqual(len(session.calls), 3)

    def test_profile_verification_required_reprompts(self):
        acct = account(FakeSession(), profileId='p2')
        acct.client.credentials.profileToken = 'PT2'

        acct.requireVerification()

        self.assertIsNone(acct.client.credentials.profileToken)
        self.assertFalse(acct.isAuthenticated)

    def test_a_locked_profile_asks_for_its_pin_again(self):
        """Any request as a PIN-protected profile whose PIN session ended (403 profile_verification_required, its
        token expired): the token is forgotten, also in the stored session, and Home hears it once."""
        locked = problem(403, 'profile_verification_required', 'PIN needed')
        session = FakeSession(PROFILES, locked, locked)
        acct = account(session, profileId='p2')
        credentials = acct.client.credentials
        credentials.profileToken = 'PT2'
        credentials.onChange = mock.Mock()
        acct.updateHomeUsers()
        acct.isAuthenticated = True
        app = mock.Mock()

        with mock.patch.object(pnUtil, 'APP', app):
            for _ in range(2):
                with self.assertRaises(errors.SiloError):
                    acct.client.request('GET', '/api/v2/user/libraries')

        self.assertIsNone(credentials.profileToken)
        credentials.onChange.assert_called_with(credentials)
        self.assertFalse(acct.isAuthenticated)
        self.assertTrue(acct.isProtected)
        app.trigger.assert_called_once_with('account:verification_required')

    def sidebarProperties(self, acct):
        """Home's sidebar user properties (sidebar_model.serverAndUserProperties()) with this account signed in."""
        from lib.windows import sidebar_model

        with (
            mock.patch.object(sidebar_model.plexapp, 'ACCOUNT', acct),
            mock.patch.object(sidebar_model, 'sidebarServers', return_value=[]),
        ):
            return dict(sidebar_model.serverAndUserProperties())

    def test_sidebar_shows_the_acting_profile(self):
        acct = account(FakeSession(PROFILES), profileId='p2')
        acct.updateHomeUsers()

        props = self.sidebarProperties(acct)

        self.assertEqual(
            (props['user.name'], props['user.avatar'], props['user.avatar.letter']),
            ('Kids', 'https://silo.lan/avatar/p2.webp', 'K'),
        )

    def test_sidebar_without_a_profile_shows_the_account(self):
        acct = account(FakeSession(PROFILES), profileId=None)
        acct.updateHomeUsers()

        props = self.sidebarProperties(acct)

        self.assertEqual((props['user.name'], props['user.avatar']), ('sam', ''))

    def test_sign_out_revokes_session_and_clears_credentials(self):
        session = FakeSession(FakeResponse(204))
        acct = account(session)

        acct.signOut()

        self.assertEqual(session.calls[0]['url'], 'http://silo.lan:8090/api/v2/auth/logout')
        self.assertIsNone(acct.client.credentials.accessToken)
        self.assertIsNone(acct.client.credentials.refreshToken)
        self.assertFalse(acct.isSignedIn)

    def test_sign_out_without_a_server_still_forgets_the_session(self):
        acct = account(FakeSession(requests.ConnectionError('down')))

        acct.signOut()

        self.assertIsNone(acct.client.credentials.accessToken)
        self.assertFalse(acct.isSignedIn)
