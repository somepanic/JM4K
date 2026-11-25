"""
silonet's HTTP client: the headers Silo expects, its Problem Details errors, the token refresh it asks for, and
backing off when it is busy or unreachable. Silo's rules: docs/auth-api.md and docs/architecture/api-contract.md
in Silo-Server/silo-server.
"""

from unittest import mock

import requests
from kodienv import ENV
from silonet import errors
from silonet.client import Credentials, DeviceIdentity, SiloClient

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession, problem

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')


def makeClient(session, baseUrl='http://silo.lan:8090', **creds):
    values = {'accessToken': 'A1', 'refreshToken': 'R1', 'profileId': 'p1', 'profileToken': 't1'}
    values.update(creds)
    return SiloClient(baseUrl, DEVICE, Credentials(**values), session=session)


class SiloClientTest(KodiTestCase):
    def test_sends_bearer_profile_and_device_headers(self):
        session = FakeSession(FakeResponse(200, {'items': []}))

        makeClient(session).request('GET', '/api/v2/home/layout')

        headers = session.calls[0]['headers']
        self.assertEqual(headers['Authorization'], 'Bearer A1')
        self.assertEqual(headers['X-Profile-Id'], 'p1')
        self.assertEqual(headers['X-Profile-Token'], 't1')
        self.assertEqual(headers['X-Silo-Device-Id'], 'dev1')
        self.assertEqual(headers['X-Silo-Device-Name'], 'Living room')
        self.assertEqual(headers['X-Silo-Device-Platform'], 'kodi')
        self.assertEqual(headers['X-Silo-Client-Version'], '0.4.4')

    def test_profile_false_omits_profile_headers(self):
        session = FakeSession(FakeResponse(200, {'items': []}))

        makeClient(session).request('GET', '/api/v2/profiles', profile=False)

        headers = session.calls[0]['headers']
        self.assertNotIn('X-Profile-Id', headers)
        self.assertNotIn('X-Profile-Token', headers)
        self.assertEqual(headers['Authorization'], 'Bearer A1')

    def test_url_keeps_a_path_prefix(self):
        session = FakeSession(FakeResponse(200, {}))

        makeClient(session, baseUrl='http://h:8090/silo/').request('GET', '/api/v2/system/info')

        self.assertEqual(session.calls[0]['url'], 'http://h:8090/silo/api/v2/system/info')

    def test_returns_decoded_json_and_none_for_no_content(self):
        session = FakeSession(FakeResponse(200, {'server_id': 's1'}), FakeResponse(204))
        client = makeClient(session)

        self.assertEqual(client.request('GET', '/api/v2/system/identity'), {'server_id': 's1'})
        self.assertIsNone(client.request('POST', '/api/v2/auth/logout'))

    def test_problem_json_becomes_typed_error(self):
        session = FakeSession(problem(403, 'profile_verification_required', 'PIN needed'))

        with self.assertRaises(errors.SiloError) as caught:
            makeClient(session).request('GET', '/api/v2/home/layout')

        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(caught.exception.type, 'profile_verification_required')

    def test_refreshes_once_then_retries(self):
        changed = []
        session = FakeSession(
            problem(401, 'token_refresh_required'),
            FakeResponse(200, {'access_token': 'A2', 'refresh_token': 'R2', 'expires_in': 900}),
            FakeResponse(200, {'items': []}),
        )
        client = makeClient(session, onChange=changed.append)

        self.assertEqual(client.request('GET', '/api/v2/home/layout'), {'items': []})

        refresh, retry = session.calls[1], session.calls[2]
        self.assertEqual(refresh['url'], 'http://silo.lan:8090/api/v2/auth/refresh')
        self.assertEqual(refresh['json'], {'refresh_token': 'R1'})
        self.assertNotIn('Authorization', refresh['headers'])
        self.assertEqual(retry['headers']['Authorization'], 'Bearer A2')
        self.assertEqual(len(changed), 1)
        self.assertEqual((changed[0].accessToken, changed[0].refreshToken), ('A2', 'R2'))

    def test_a_second_401_after_refreshing_signs_out(self):
        session = FakeSession(
            problem(401, 'invalid_token'),
            FakeResponse(200, {'access_token': 'A2', 'refresh_token': 'R2', 'expires_in': 900}),
            problem(401, 'invalid_token'),
        )

        with self.assertRaises(errors.SiloAuthError):
            makeClient(session).request('GET', '/api/v2/home/layout')

        self.assertEqual(len(session.calls), 3)

    def test_refused_refresh_signs_out(self):
        session = FakeSession(problem(401, 'token_refresh_required'), problem(401, 'session_expired'))

        with self.assertRaises(errors.SiloAuthError) as caught:
            makeClient(session).request('GET', '/api/v2/home/layout')

        self.assertEqual(caught.exception.type, 'session_expired')

    def test_refresh_while_server_cannot_check_keeps_tokens(self):
        changed = []
        session = FakeSession(
            problem(401, 'token_refresh_required'), problem(503, 'dependency_unavailable', headers={'Retry-After': '4'})
        )
        client = makeClient(session, onChange=changed.append)

        with self.assertRaises(errors.SiloUnavailable) as caught:
            client.request('GET', '/api/v2/home/layout')

        self.assertEqual(caught.exception.retryAfter, 4)
        self.assertEqual((client.credentials.accessToken, client.credentials.refreshToken), ('A1', 'R1'))
        self.assertEqual(changed, [])

    def test_no_refresh_token_means_signed_out(self):
        session = FakeSession(problem(401, 'invalid_token'))

        with self.assertRaises(errors.SiloAuthError):
            makeClient(session, refreshToken=None).request('GET', '/api/v2/home/layout')

        self.assertEqual(len(session.calls), 1)

    def test_429_cooldown(self):
        session = FakeSession(problem(429, 'rate_limit_exceeded', headers={'Retry-After': '7'}))
        client = makeClient(session)

        with (
            mock.patch('silonet.client.time.monotonic', return_value=100.0),
            self.assertRaises(errors.SiloRateLimited) as caught,
        ):
            client.request('GET', '/api/v2/catalog')
        self.assertEqual(caught.exception.retryAfter, 7)
        with mock.patch('silonet.client.time.monotonic', return_value=106.0), self.assertRaises(errors.SiloRateLimited):
            client.request('GET', '/api/v2/catalog')

        self.assertEqual(len(session.calls), 1)

    def test_429_pauses_only_the_endpoint_that_was_limited(self):
        session = FakeSession(
            problem(429, 'rate_limited', headers={'Retry-After': '300'}), FakeResponse(200, {'items': []})
        )
        client = makeClient(session)

        with self.assertRaises(errors.SiloRateLimited):
            client.request('POST', '/api/v2/profiles/p2/verify-pin', json={'pin': '0000'})

        self.assertEqual(client.request('GET', '/api/v2/profiles', profile=False), {'items': []})

    def test_503_dependency_unavailable_is_unavailable(self):
        session = FakeSession(problem(503, 'dependency_unavailable', headers={'Retry-After': '2'}))

        with self.assertRaises(errors.SiloUnavailable) as caught:
            makeClient(session).request('GET', '/api/v2/home/layout')

        self.assertEqual(caught.exception.retryAfter, 2)

    def test_unreachable_server_is_unavailable(self):
        session = FakeSession(requests.ConnectionError('refused'))

        with self.assertRaises(errors.SiloUnavailable) as caught:
            makeClient(session).request('GET', '/api/v2/system/info')

        self.assertIsNone(caught.exception.retryAfter)

    def test_logs_never_contain_secrets(self):
        session = FakeSession(
            problem(401, 'token_refresh_required'),
            FakeResponse(200, {'access_token': 'ACCESS-2', 'refresh_token': 'REFRESH-2'}),
            FakeResponse(200, {'items': []}),
        )

        makeClient(session, accessToken='ACCESS-1', refreshToken='REFRESH-1', profileToken='PROFILE-1').request(
            'GET', '/api/v2/home/layout'
        )

        self.assertTrue(ENV.log_lines, 'the client should log its requests')
        for secret in ('ACCESS-1', 'ACCESS-2', 'REFRESH-1', 'REFRESH-2', 'PROFILE-1'):
            self.assertFalse(ENV.logged(secret), secret)
