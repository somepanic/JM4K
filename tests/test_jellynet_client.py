"""
jellynet's HTTP client: Jellyfin's MediaBrowser Authorization header, per-user DeviceId derivation,
session-end handling on 401, 503/network unavailability, and keeping tokens out of Kodi's log.
"""

import os
from unittest import mock

import requests
from jellynet import errors
from jellynet.client import ClientIdentity, JellyfinClient, device_id_for
from kodienv import ENV

from . import REPO_ROOT
from .base import KodiTestCase
from .jellynet_fakes import FakeResponse, FakeSession, problem

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Kodi', 'device-uuid-1')
JELLYNET_DIR = os.path.join(REPO_ROOT, 'lib', '_included_packages', 'jellynet')
LEGACY_TOKENS = (
    'api_key',
    'x-emby-token',
    'x-mediabrowser-token',
    'x-emby-authorization',
    '/emby/',
    '/mediabrowser/',
)


def make_client(session, base_url='http://jellyfin.lan:8096', token='T1', username='alice', identity=IDENTITY):
    return JellyfinClient(base_url, identity, token=token, username=username, session=session)


class JellyfinClientTest(KodiTestCase):
    def test_authorization_header_format(self):
        session = FakeSession(FakeResponse(200, {'Items': []}))
        expected_device_id = device_id_for(IDENTITY, 'alice')

        make_client(session, token='T1', username='alice').request('GET', '/UserViews')

        headers = session.calls[0]['headers']
        self.assertEqual(
            headers['Authorization'],
            f'MediaBrowser Token="T1", Client="JellyMod", Version="0.1.0", '
            f'DeviceId="{expected_device_id}", Device="Kodi"',
        )
        self.assertEqual(headers['Accept'], 'application/json')

    def test_unauthenticated_header_omits_token(self):
        session = FakeSession(FakeResponse(200, {'Version': '10.11.5'}))
        expected_device_id = device_id_for(IDENTITY, None)

        make_client(session, token=None, username=None).request('GET', '/System/Info/Public')

        headers = session.calls[0]['headers']
        self.assertEqual(
            headers['Authorization'],
            f'MediaBrowser Client="JellyMod", Version="0.1.0", DeviceId="{expected_device_id}", Device="Kodi"',
        )
        self.assertNotIn('Token=', headers['Authorization'])

    def test_no_legacy_auth_anywhere(self):
        session = FakeSession(FakeResponse(200, {'Items': []}), FakeResponse(200, {'Version': '10.11.5'}))

        make_client(session, token='T1', username='alice').get('/UserViews', params={'IncludeHidden': 'false'})
        make_client(session, token=None, username=None).get('/System/Info/Public')

        for call in session.calls:
            lower_headers = {k.lower(): v for k, v in call['headers'].items()}
            lower_url = call['url'].lower()
            lower_params = {k.lower() for k in (call['params'] or {})}
            for forbidden in ('x-emby-token', 'x-mediabrowser-token', 'x-emby-authorization'):
                self.assertNotIn(forbidden, lower_headers)
            self.assertNotIn('api_key', lower_params)
            self.assertNotIn('api_key', lower_url)
            self.assertNotIn('/emby/', lower_url)
            self.assertNotIn('/mediabrowser/', lower_url)

        for root, _, files in os.walk(JELLYNET_DIR):
            for filename in files:
                if not filename.endswith('.py'):
                    continue
                path = os.path.join(root, filename)
                with open(path, encoding='utf-8') as f:
                    source = f.read().lower()
                for forbidden in LEGACY_TOKENS:
                    self.assertNotIn(forbidden, source, f'{forbidden} found in {path}')

    def test_device_id_differs_per_user(self):
        alice_id = device_id_for(IDENTITY, 'alice')
        bob_id = device_id_for(IDENTITY, 'bob')
        anon_id = device_id_for(IDENTITY, None)

        self.assertNotEqual(alice_id, bob_id)
        self.assertNotEqual(alice_id, anon_id)
        self.assertNotEqual(bob_id, anon_id)

    def test_device_id_stable_for_same_user(self):
        first = device_id_for(IDENTITY, 'Alice')
        second = device_id_for(IDENTITY, 'alice')
        third = device_id_for(IDENTITY, 'ALICE')

        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(device_id_for(IDENTITY, None), device_id_for(IDENTITY, ''))

    def test_401_ends_the_session_once(self):
        session = FakeSession(problem(401, 'Unauthorized'), problem(401, 'Unauthorized'))
        client = make_client(session, token='T1', username='alice')
        client.onSessionEnded = mock.Mock()

        with self.assertRaises(errors.JellyfinAuthError) as first:
            client.request('GET', '/UserViews')
        with self.assertRaises(errors.JellyfinAuthError):
            client.request('GET', '/UserViews')

        self.assertEqual(first.exception.status, 401)
        client.onSessionEnded.assert_called_once_with()

    def test_401_without_token_does_not_end_session(self):
        session = FakeSession(FakeResponse(401, raw='Invalid username or password.'))
        client = make_client(session, token=None, username=None)
        client.onSessionEnded = mock.Mock()

        with self.assertRaises(errors.JellyfinAuthError) as caught:
            client.post('/Users/AuthenticateByName', json={'Username': 'alice', 'Pw': 'bad'})

        self.assertIn('Invalid username or password.', caught.exception.message)
        client.onSessionEnded.assert_not_called()

    def test_503_while_server_starts_is_unavailable(self):
        session = FakeSession(
            FakeResponse(
                503,
                raw='<html>Server is starting</html>',
                headers={'Retry-After': '5', 'Message': 'Startup in progress', 'Content-Type': 'text/html'},
            )
        )

        with self.assertRaises(errors.JellyfinUnavailable) as caught:
            make_client(session).request('GET', '/System/Info/Public')

        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(caught.exception.retry_after, 5)
        self.assertIn('Startup in progress', caught.exception.message)

    def test_connection_and_timeout_errors_are_unavailable(self):
        for exc in (requests.ConnectionError('refused'), requests.Timeout('timed out')):
            session = FakeSession(exc)
            with self.assertRaises(errors.JellyfinUnavailable) as caught:
                make_client(session).request('GET', '/System/Info/Public')
            self.assertEqual(caught.exception.status, 0)
            self.assertIsNone(caught.exception.retry_after)

    def test_other_http_errors_become_jellyfin_error(self):
        session = FakeSession(problem(404, 'Not Found', 'Item not found'))

        with self.assertRaises(errors.JellyfinError) as caught:
            make_client(session).request('GET', '/Items/missing')

        self.assertEqual(caught.exception.status, 404)
        self.assertIn('Item not found', caught.exception.message)
        self.assertNotIsInstance(caught.exception, errors.JellyfinAuthError)
        self.assertNotIsInstance(caught.exception, errors.JellyfinUnavailable)

    def test_logs_never_contain_token(self):
        secret_token = 'SECRET-TOKEN-999'
        session = FakeSession(
            FakeResponse(200, {'Items': []}),
            FakeResponse(401, raw='Unauthorized'),
            requests.ConnectionError(f'failed with {secret_token}'),
        )
        client = make_client(session, token=secret_token, username='alice')

        client.request('GET', '/UserViews')
        with self.assertRaises(errors.JellyfinAuthError):
            client.request('GET', '/Users/Me')
        with self.assertRaises(errors.JellyfinUnavailable):
            client.request('GET', '/UserViews')

        self.assertTrue(ENV.log_lines, 'the client should log its requests')
        self.assertFalse(ENV.logged(secret_token))

    def test_url_keeps_a_path_prefix_and_handles_no_content_and_false_json(self):
        session = FakeSession(
            FakeResponse(200, False),
            FakeResponse(204),
            FakeResponse(200, {'ok': True}),
        )
        client = make_client(session, base_url='http://host:8096/jellyfin/')

        self.assertIs(client.get('/QuickConnect/Enabled'), False)
        self.assertIsNone(client.delete('/Sessions/Logout'))
        self.assertEqual(client.post('/Ping', json={'a': 1}), {'ok': True})
        self.assertEqual(session.calls[0]['url'], 'http://host:8096/jellyfin/QuickConnect/Enabled')
        self.assertEqual(session.calls[1]['method'], 'DELETE')
        self.assertEqual(session.calls[2]['json'], {'a': 1})

    def test_close_and_drop_idle_connections(self):
        session = FakeSession()
        client = make_client(session)

        client.dropIdleConnections()
        self.assertFalse(session.closed)
        client.close()
        self.assertTrue(session.closed)

    def test_header_escapes_quotes_commas_and_non_ascii_and_repr_hides_token(self):
        identity = ClientIdentity('JellyMod', '0.1.0', 'Living "TV", Stübchen\r\n', 'device-uuid-1')
        session = FakeSession(FakeResponse(200, {}))
        client = make_client(session, identity=identity, token='SECRET-TOK')

        client.get('/System/Info/Public')

        auth = session.calls[0]['headers']['Authorization']
        self.assertTrue(auth.isascii())
        self.assertNotIn('\r', auth)
        self.assertNotIn('\n', auth)
        self.assertIn('Device="Living %22TV%22%2C St%C3%BCbchen%0D%0A"', auth)
        self.assertNotIn('SECRET-TOK', repr(client))
