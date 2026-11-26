"""
jellynet.server_info: probing a Jellyfin server via /System/Info/Public (accepting 10.11 through 12.x)
and reading sign-in options from /QuickConnect/Enabled and /Branding/Configuration.

system_info_public_10_11.json is recorded from a Jellyfin 10.11.5 server and scrubbed;
system_info_public_12_4.json is a synthetic 12.4.0 fixture.
"""

import json

import requests
from jellynet import errors
from jellynet.client import ClientIdentity
from jellynet.server_info import ServerInfo, probe

from .base import KodiTestCase, fixture
from .jellynet_fakes import FakeResponse, FakeSession

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Kodi', 'device-uuid-1')


def recorded(name):
    return json.loads(fixture('jellynet', name))


class ProbeTest(KodiTestCase):
    def test_accepts_10_11_and_12_4(self):
        session_10_11 = FakeSession(FakeResponse(200, recorded('system_info_public_10_11.json')))
        info_10_11 = probe('http://jellyfin.lan:8096', session=session_10_11)

        self.assertIsInstance(info_10_11, ServerInfo)
        self.assertEqual(info_10_11.server_id, '00000000000000000000000000000001')
        self.assertEqual(info_10_11.name, 'Home Jellyfin')
        self.assertEqual(info_10_11.version, '10.11.5')
        self.assertEqual(session_10_11.calls[0]['url'], 'http://jellyfin.lan:8096/System/Info/Public')
        self.assertNotIn('Token=', session_10_11.calls[0]['headers']['Authorization'])

        session_12_4 = FakeSession(FakeResponse(200, recorded('system_info_public_12_4.json')))
        info_12_4 = probe('http://jellyfin.lan:8096/', session=session_12_4)

        self.assertEqual(info_12_4.server_id, '00000000000000000000000000000002')
        self.assertEqual(info_12_4.name, 'Home Jellyfin')
        self.assertEqual(info_12_4.version, '12.4.0')

    def test_rejects_10_10(self):
        older = dict(recorded('system_info_public_10_11.json'), Version='10.10.7')
        session = FakeSession(FakeResponse(200, older))

        with self.assertRaises(errors.JellyfinIncompatible) as caught:
            probe('http://jellyfin.lan:8096', session=session)

        self.assertIn('10.10.7', caught.exception.message)
        self.assertIn('10.11', caught.exception.message)

    def test_unfinished_setup_is_unavailable(self):
        unfinished = dict(recorded('system_info_public_10_11.json'), StartupWizardCompleted=False)
        session = FakeSession(FakeResponse(200, unfinished))

        with self.assertRaises(errors.JellyfinUnavailable):
            probe('http://jellyfin.lan:8096', session=session)

    def test_unreachable_is_unavailable(self):
        session = FakeSession(requests.ConnectionError('refused'))

        with self.assertRaises(errors.JellyfinUnavailable):
            probe('http://jellyfin.lan:8096', session=session)

    def test_503_during_startup_is_unavailable(self):
        session = FakeSession(
            FakeResponse(503, raw='<html>Starting up</html>', headers={'Retry-After': '3', 'Message': 'Starting'})
        )

        with self.assertRaises(errors.JellyfinUnavailable) as caught:
            probe('http://jellyfin.lan:8096', session=session)

        self.assertEqual(caught.exception.retry_after, 3)

    def test_non_json_or_missing_fields_is_incompatible(self):
        for response in (
            FakeResponse(200, raw='<html>Not Jellyfin</html>'),
            FakeResponse(404),
            FakeResponse(200, {'ServerName': 'NoVersion'}),
            FakeResponse(200, {'Id': 'abc', 'Version': 'not-a-version'}),
        ):
            session = FakeSession(response)
            with self.assertRaises(errors.JellyfinIncompatible):
                probe('http://other.lan:8096', session=session)

    def test_silos_jellyfin_port_does_not_crash(self):
        # Silo's Jellyfin-compatible port answers with ServerName "Silo" and Version "12.1.0"
        silo_answer = {'Id': '00000000000000000000000000000009', 'ServerName': 'Silo', 'Version': '12.1.0'}
        session = FakeSession(FakeResponse(200, silo_answer))

        info = probe('http://silo.lan:8096', session=session)

        self.assertEqual(info.server_id, '00000000000000000000000000000009')
        self.assertEqual(info.name, 'Silo')
        self.assertEqual(info.version, '12.1.0')
