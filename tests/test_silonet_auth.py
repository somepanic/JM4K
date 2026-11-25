"""
silonet.auth: signing in by approving a code on another device (Silo's device sign-in,
docs/architecture/device-login.md), and the password sign-in behind it.
"""

from unittest import mock

import requests
from silonet import auth, errors
from silonet.client import Credentials, DeviceIdentity, SiloClient

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession, problem

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')


def started(code='dc-1', user='4821-7730', interval=5):
    return FakeResponse(
        201,
        {
            'client_purpose': 'device_login',
            'device_code': code,
            'device_name': 'Living room',
            'device_platform': 'kodi',
            'expires_at': '2026-11-23T21:00:00Z',
            'expires_in': 900,
            'interval': interval,
            'match_code': 'apple pear',
            'temporary': False,
            'user_code': user,
            'verification_uri': 'http://silo.lan:8090/activate',
            'verification_uri_complete': 'http://silo.lan:8090/activate?code=48217730',
        },
    )


def polled(status, opened=False, pollAfter=5, tokens=None):
    body = {
        'status': status,
        'opened': opened,
        'poll_after': pollAfter,
        'temporary': False,
        'profile_id': 'p1' if status == 'approved' else '',
        'profile_token': 'PT' if status == 'approved' else '',
    }
    if tokens:
        body['tokens'] = tokens
    return FakeResponse(200, body)


TOKENS = {'access_token': 'A1', 'refresh_token': 'R1', 'expires_in': 900, 'user': {'id': 'u1', 'username': 'sam'}}


def login(session):
    return auth.DeviceLogin(SiloClient('http://silo.lan:8090', DEVICE, Credentials(), session=session))


class Waits:
    """wait(seconds): records each wait; answers False (the user pressed Back) from the given wait on."""

    def __init__(self, backAt=None):
        self.waits = []
        self.backAt = backAt
        self.now = 1000.0

    def __call__(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return self.backAt is None or len(self.waits) < self.backAt

    def clock(self):
        return self.now


class DeviceLoginTest(KodiTestCase):
    def test_start_returns_code_and_link(self):
        session = FakeSession(started())

        code = login(session).start('Living room')

        call = session.calls[0]
        self.assertEqual(call['url'], 'http://silo.lan:8090/api/v2/auth/device/start')
        self.assertEqual(
            call['json'], {'client_purpose': 'device_login', 'device_name': 'Living room', 'device_platform': 'kodi'}
        )
        self.assertNotIn('Authorization', call['headers'])
        self.assertEqual((code.deviceCode, code.userCode, code.interval), ('dc-1', '4821-7730', 5))
        self.assertEqual(code.verificationUri, 'http://silo.lan:8090/activate')
        self.assertEqual(code.verificationUriComplete, 'http://silo.lan:8090/activate?code=48217730')

    def test_user_code_shown_as_4_plus_4(self):
        self.assertEqual(auth.formatUserCode('4821-7730'), '4821 7730')
        self.assertEqual(auth.formatUserCode('48217730'), '4821 7730')

    def test_waits_poll_after_between_polls_until_approved(self):
        session = FakeSession(started(interval=5), polled('pending', pollAfter=7), polled('approved', tokens=TOKENS))
        waits = Waits()
        devLogin = login(session)

        result = devLogin.waitForApproval(devLogin.start('Living room'), waits)

        self.assertEqual(waits.waits, [5, 7])
        self.assertEqual(session.calls[1]['json'], {'device_code': 'dc-1'})
        self.assertEqual(result.status, 'approved')
        self.assertEqual((result.accessToken, result.refreshToken), ('A1', 'R1'))
        self.assertEqual((result.profileId, result.profileToken), ('p1', 'PT'))

    def test_denied_ends_the_wait(self):
        session = FakeSession(started(), polled('denied'))
        devLogin = login(session)

        result = devLogin.waitForApproval(devLogin.start('Living room'), Waits())

        self.assertEqual(result.status, 'denied')
        self.assertIsNone(result.accessToken)

    def test_back_cancels_the_request(self):
        session = FakeSession(started(), polled('pending'), FakeResponse(200, {'status': 'canceled'}))
        devLogin = login(session)

        result = devLogin.waitForApproval(devLogin.start('Living room'), Waits(backAt=2))

        self.assertIsNone(result)
        self.assertEqual(session.calls[-1]['url'], 'http://silo.lan:8090/api/v2/auth/device/cancel')
        self.assertEqual(session.calls[-1]['json'], {'device_code': 'dc-1'})

    def test_poll_survives_rate_limit_and_brief_outage(self):
        session = FakeSession(
            started(),
            problem(429, 'rate_limit_exceeded', headers={'Retry-After': '3'}),
            requests.ConnectionError('down'),
            polled('approved', tokens=TOKENS),
        )
        waits = Waits()
        devLogin = login(session)

        with mock.patch('silonet.client.time.monotonic', waits.clock):
            result = devLogin.waitForApproval(devLogin.start('Living room'), waits)

        self.assertEqual(result.status, 'approved')
        self.assertEqual(waits.waits, [5, 3, 5])

    def test_unopened_expired_code_is_replaced(self):
        shown = []
        session = FakeSession(
            started('dc-1', '1111-1111'),
            polled('expired'),
            started('dc-2', '2222-2222'),
            polled('approved', tokens=TOKENS),
        )

        result = auth.signInWithCode(login(session), 'Living room', shown.append, Waits())

        self.assertEqual([c.userCode for c in shown], ['1111-1111', '2222-2222'])
        self.assertEqual(result.status, 'approved')

    def test_opened_expired_code_is_not_replaced(self):
        shown = []
        session = FakeSession(started(), polled('expired', opened=True))

        result = auth.signInWithCode(login(session), 'Living room', shown.append, Waits())

        self.assertEqual(len(shown), 1)
        self.assertEqual(result.status, 'expired')


class PasswordLoginTest(KodiTestCase):
    def test_password_login_returns_tokens(self):
        session = FakeSession(FakeResponse(200, TOKENS))
        client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(), session=session)

        tokens = auth.passwordLogin(client, 'sam', 'secret')

        self.assertEqual(session.calls[0]['url'], 'http://silo.lan:8090/api/v2/auth/login')
        self.assertEqual(session.calls[0]['json'], {'username': 'sam', 'password': 'secret'})
        self.assertEqual((tokens.accessToken, tokens.refreshToken), ('A1', 'R1'))

    def test_wrong_password_is_auth_error(self):
        session = FakeSession(problem(401, 'invalid_credentials'))
        client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(), session=session)

        with self.assertRaises(errors.SiloAuthError):
            auth.passwordLogin(client, 'sam', 'wrong')
