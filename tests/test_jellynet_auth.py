"""
jellynet.auth: signing in to Jellyfin with Quick Connect (initiate, poll /QuickConnect/Connect,
exchange via /Users/AuthenticateWithQuickConnect) or with username and password (/Users/AuthenticateByName).
"""

from unittest import mock

import requests
from jellynet import auth, errors
from jellynet.client import ClientIdentity, JellyfinClient, device_id_for
from kodienv import ENV

from .base import KodiTestCase
from .jellynet_fakes import FakeResponse, FakeSession, problem

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Kodi', 'device-uuid-1')


def qc_result(code='123456', secret='SECRET-1', authenticated=False):
    return FakeResponse(
        200,
        {
            'Authenticated': authenticated,
            'Secret': secret,
            'Code': code,
            'DeviceId': 'device-uuid-1',
            'DeviceName': 'Kodi',
            'AppName': 'JellyMod',
            'AppVersion': '0.1.0',
            'DateAdded': '2026-11-25T07:00:00Z',
        },
    )


def auth_response(token='TOKEN-1', user_id='user-1', user_name='alice', is_admin=True, server_id='server-1'):
    return FakeResponse(
        200,
        {
            'AccessToken': token,
            'ServerId': server_id,
            'User': {
                'Id': user_id,
                'Name': user_name,
                'ServerId': server_id,
                'Policy': {'IsAdministrator': is_admin},
            },
            'SessionInfo': {'UserId': user_id, 'UserName': user_name},
        },
    )


class Waits:
    """wait(seconds): records each wait interval; returns False (cancelled) from cancel_at onward."""

    def __init__(self, cancel_at=None):
        self.waits = []
        self.cancel_at = cancel_at

    def __call__(self, seconds):
        self.waits.append(seconds)
        return self.cancel_at is None or len(self.waits) < self.cancel_at


def make_client(session):
    return JellyfinClient('http://jellyfin.lan:8096', IDENTITY, session=session)


class QuickConnectTest(KodiTestCase):
    def test_quick_connect_code_then_authenticated_then_token(self):
        session = FakeSession(
            qc_result('482910', 'QC-SECRET-1', authenticated=False),
            qc_result('482910', 'QC-SECRET-1', authenticated=False),
            qc_result('482910', 'QC-SECRET-1', authenticated=True),
            auth_response(token='TOK-QC', user_id='u1', user_name='alice', is_admin=True, server_id='srv1'),
        )
        qc = auth.QuickConnect(make_client(session))

        code = qc.initiate()
        self.assertIsInstance(code, auth.QuickConnectCode)
        self.assertEqual((code.code, code.secret), ('482910', 'QC-SECRET-1'))
        self.assertFalse(qc.check(code))
        self.assertTrue(qc.check(code))
        result = qc.finish(code)

        self.assertIsInstance(result, auth.AuthResult)
        self.assertEqual(
            (result.access_token, result.user_id, result.user_name, result.is_admin, result.server_id),
            ('TOK-QC', 'u1', 'alice', True, 'srv1'),
        )
        self.assertEqual(
            [(c['method'], c['url']) for c in session.calls],
            [
                ('POST', 'http://jellyfin.lan:8096/QuickConnect/Initiate'),
                ('GET', 'http://jellyfin.lan:8096/QuickConnect/Connect'),
                ('GET', 'http://jellyfin.lan:8096/QuickConnect/Connect'),
                ('POST', 'http://jellyfin.lan:8096/Users/AuthenticateWithQuickConnect'),
            ],
        )
        self.assertEqual(session.calls[1]['params'], {'secret': 'QC-SECRET-1'})
        self.assertEqual(session.calls[3]['json'], {'Secret': 'QC-SECRET-1'})
        self.assertTrue(all('Token=' not in c['headers']['Authorization'] for c in session.calls))

    def test_polls_until_authenticated_or_cancelled(self):
        # Polls at SM4K's 5s interval, survives brief outage/503, finishes when authenticated
        session = FakeSession(
            qc_result('111222', 'SEC-1'),
            qc_result('111222', 'SEC-1', authenticated=False),
            FakeResponse(503, raw='Starting', headers={'Retry-After': '3'}),
            requests.ConnectionError('brief drop'),
            qc_result('111222', 'SEC-1', authenticated=True),
            auth_response(token='TOK-1', is_admin=False),
        )
        shown = []
        waits = Waits()

        result = auth.QuickConnect(make_client(session)).poll(shown.append, waits)

        self.assertEqual([c.code for c in shown], ['111222'])
        self.assertEqual(waits.waits, [5, 5, 3, 5])
        self.assertEqual(result.access_token, 'TOK-1')
        self.assertFalse(result.is_admin)

        # Cancelled by wait() returning False stops polling and returns None
        cancel_session = FakeSession(
            qc_result('333444', 'SEC-2'),
            qc_result('333444', 'SEC-2', authenticated=False),
        )
        cancel_shown = []
        cancel_waits = Waits(cancel_at=2)

        cancelled = auth.QuickConnect(make_client(cancel_session)).poll(cancel_shown.append, cancel_waits)

        self.assertIsNone(cancelled)
        self.assertEqual([c.code for c in cancel_shown], ['333444'])
        self.assertEqual(cancel_waits.waits, [5, 5])

    def test_expired_code_starts_a_new_one(self):
        session = FakeSession(
            qc_result('111111', 'OLD-SECRET'),
            problem(404, 'Not Found', 'Unknown quick connect secret'),
            qc_result('222222', 'NEW-SECRET'),
            qc_result('222222', 'NEW-SECRET', authenticated=True),
            auth_response(token='TOK-NEW'),
        )
        shown = []
        waits = Waits()

        result = auth.QuickConnect(make_client(session)).poll(shown.append, waits)

        self.assertEqual([c.code for c in shown], ['111111', '222222'])
        self.assertEqual(result.access_token, 'TOK-NEW')
        self.assertEqual(session.calls[-1]['json'], {'Secret': 'NEW-SECRET'})


class PasswordLoginTest(KodiTestCase):
    def test_password_sign_in(self):
        session = FakeSession(
            auth_response(token='TOK-PW', user_id='u2', user_name='bob', is_admin=False, server_id='srv1')
        )
        client = make_client(session)

        result = auth.passwordLogin(client, 'bob', 's3cr3t')

        call = session.calls[0]
        self.assertEqual((call['method'], call['url']), ('POST', 'http://jellyfin.lan:8096/Users/AuthenticateByName'))
        self.assertEqual(call['json'], {'Username': 'bob', 'Pw': 's3cr3t'})
        self.assertIn(f'DeviceId="{device_id_for(IDENTITY, "bob")}"', call['headers']['Authorization'])
        self.assertNotIn('Token=', call['headers']['Authorization'])
        self.assertEqual(
            (result.access_token, result.user_id, result.user_name, result.is_admin, result.server_id),
            ('TOK-PW', 'u2', 'bob', False, 'srv1'),
        )

    def test_user_without_password_signs_in_with_empty_pw(self):
        session = FakeSession(
            auth_response(token='TOK-NOPW-1', user_id='u3', user_name='kids', is_admin=False),
            auth_response(token='TOK-NOPW-2', user_id='u3', user_name='kids', is_admin=False),
        )
        client = make_client(session)

        first = auth.passwordLogin(client, 'kids', '')
        second = auth.passwordLogin(client, 'kids', None)

        self.assertEqual(session.calls[0]['json'], {'Username': 'kids', 'Pw': ''})
        self.assertEqual(session.calls[1]['json'], {'Username': 'kids', 'Pw': ''})
        self.assertEqual(first.access_token, 'TOK-NOPW-1')
        self.assertEqual(second.access_token, 'TOK-NOPW-2')

    def test_wrong_password_is_an_auth_error(self):
        session = FakeSession(
            FakeResponse(401, raw='Invalid username or password entered.'),
            FakeResponse(401, raw='Invalid username or password entered.'),
        )
        client = make_client(session)
        client.onSessionEnded = mock.Mock()

        with self.assertRaises(errors.JellyfinAuthError) as caught:
            auth.passwordLogin(client, 'bob', 'wrong')

        self.assertEqual(caught.exception.status, 401)
        client.onSessionEnded.assert_not_called()

        # Even if called with an already-authenticated client (e.g. switching users), a wrong password
        # for another user must never trigger the active session's onSessionEnded hook.
        authed_client = JellyfinClient(
            'http://jellyfin.lan:8096', IDENTITY, token='ACTIVE', username='alice', session=session
        )
        authed_client.onSessionEnded = mock.Mock()
        with self.assertRaises(errors.JellyfinAuthError):
            auth.passwordLogin(authed_client, 'bob', 'wrong')
        authed_client.onSessionEnded.assert_not_called()

    def test_secret_never_logged(self):
        qc_secret_1 = 'QC-SECRET-EXPIRED-999'
        qc_secret_2 = 'QC-SECRET-ACTIVE-888'
        qc_token = 'ACCESS-TOKEN-QC-777'
        pw_secret = 'USER-PASSWORD-666'
        pw_token = 'ACCESS-TOKEN-PW-555'
        session = FakeSession(
            qc_result('111111', qc_secret_1),
            requests.ConnectionError(f'GET /QuickConnect/Connect?secret={qc_secret_1} failed'),
            problem(404, 'Not Found', f'unknown secret {qc_secret_1}'),
            qc_result('222222', qc_secret_2),
            qc_result('222222', qc_secret_2, authenticated=True),
            auth_response(token=qc_token),
            auth_response(token=pw_token),
        )
        client = make_client(session)
        shown = []

        qc_auth = auth.QuickConnect(client).poll(shown.append, Waits())
        pw_auth = auth.passwordLogin(client, 'alice', pw_secret)

        self.assertTrue(ENV.log_lines, 'auth requests should be logged')
        for sensitive in (qc_secret_1, qc_secret_2, qc_token, pw_secret, pw_token):
            self.assertFalse(ENV.logged(sensitive), sensitive)
        self.assertNotIn(qc_secret_1, repr(shown[0]))
        self.assertNotIn(qc_secret_2, repr(shown[1]))
        self.assertNotIn(qc_token, repr(qc_auth))
        self.assertNotIn(pw_token, repr(pw_auth))
