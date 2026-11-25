"""
lib/jellyfin.py: storing Jellyfin servers in add-on settings, probing by address without overwriting
with Docker LocalAddress, relocating a moved server by ID, and picking or adding servers.
"""

import json
from collections import deque
from unittest import mock

import requests
from jellynet import errors
from jellynet.client import ClientIdentity
from jellynet.discovery import Found
from kodienv import ENV

from lib import jellyfin

from .base import KodiTestCase, fixture
from .jellynet_fakes import FakeResponse, FakeSession

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')


def public_info(server_id='00000000000000000000000000000001', name='Home Jellyfin'):
    data = json.loads(fixture('jellynet', 'system_info_public_10_11.json'))
    data['Id'] = server_id
    data['ServerName'] = name
    return FakeResponse(200, data)


class StoredServersTest(KodiTestCase):
    def test_add_server_probes_and_stores(self):
        # Fixture reports LocalAddress http://172.19.0.10:8096 (Docker bridge); stored URL must stay what was typed.
        session = FakeSession(public_info())

        stored = jellyfin.addServer('jellyfin.lan:8096/', IDENTITY, session=session)

        self.assertEqual(
            (stored.baseUrl, stored.serverId, stored.name),
            ('http://jellyfin.lan:8096', '00000000000000000000000000000001', 'Home Jellyfin'),
        )
        self.assertEqual(session.calls[0]['url'], 'http://jellyfin.lan:8096/System/Info/Public')
        self.assertEqual([s.baseUrl for s in jellyfin.storedServers()], ['http://jellyfin.lan:8096'])

    def test_same_server_new_address_updates(self):
        jellyfin.addServer('http://jellyfin.lan:8096', IDENTITY, session=FakeSession(public_info()))
        jellyfin.addServer('https://media.example.com/jf/', IDENTITY, session=FakeSession(public_info()))

        self.assertEqual([s.baseUrl for s in jellyfin.storedServers()], ['https://media.example.com/jf'])

    def test_rejects_unsupported_server(self):
        older = dict(json.loads(fixture('jellynet', 'system_info_public_10_11.json')), Version='10.10.7')
        with self.assertRaises(errors.JellyfinIncompatible):
            jellyfin.addServer('http://old.lan:8096', IDENTITY, session=FakeSession(FakeResponse(200, older)))

        self.assertEqual(jellyfin.storedServers(), [])

    def test_unreadable_setting_means_no_servers(self):
        ENV.settings[jellyfin.SERVERS_SETTING] = 'not json'

        self.assertEqual(jellyfin.storedServers(), [])

    def test_moved_server_relocated_by_id(self):
        old = jellyfin.addServer('http://192.168.1.10:8096', IDENTITY, session=FakeSession(public_info('srv-1')))
        found_list = [Found('http://192.168.1.250:8096', 'srv-1', 'Home Jellyfin')]

        with mock.patch.object(jellyfin.discovery, 'discover', return_value=found_list):
            moved = jellyfin.relocate(old, IDENTITY, session=FakeSession(public_info('srv-1')))

        self.assertIsNotNone(moved)
        self.assertEqual(moved.baseUrl, 'http://192.168.1.250:8096')
        self.assertEqual([s.baseUrl for s in jellyfin.storedServers()], ['http://192.168.1.250:8096'])

        # Another server claiming the same discovery ID fails identity verification and leaves stored address untouched.
        impostor = [Found('http://192.168.1.99:8096', 'srv-1', 'Impostor')]
        with mock.patch.object(jellyfin.discovery, 'discover', return_value=impostor):
            self.assertIsNone(jellyfin.relocate(moved, IDENTITY, session=FakeSession(public_info('srv-other'))))
        self.assertEqual([s.baseUrl for s in jellyfin.storedServers()], ['http://192.168.1.250:8096'])


TWO_SERVERS = [
    {'base_url': 'http://a.lan:8096', 'server_id': 'srv-1', 'name': 'Jellyfin A'},
    {'base_url': 'http://b.lan:8096', 'server_id': 'srv-2', 'name': 'Jellyfin B'},
]


class PickerAndDialogTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.found = []
        mock.patch.object(jellyfin.discovery, 'discover', side_effect=lambda: self.found).start()
        self.picker = mock.patch.object(jellyfin, 'showOptionsDialog').start()
        self.addCleanup(mock.patch.stopall)

    def test_picker_lists_servers_add_and_enter_rows(self):
        # Switch server picker lists stored servers with the active one selected, plus ADD_SERVER.
        ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps(TWO_SERVERS)
        self.picker.return_value = None

        self.assertIsNone(jellyfin.pickServer(IDENTITY))

        heading, options = self.picker.call_args[0][:2]
        self.assertEqual(heading, 'Switch server')
        self.assertEqual(
            options,
            [
                ('srv-1', 'Jellyfin A (http://a.lan:8096)'),
                ('srv-2', 'Jellyfin B (http://b.lan:8096)'),
                (jellyfin.ADD_SERVER, 'Add server'),
            ],
        )
        self.assertEqual(self.picker.call_args.kwargs['selected_idx'], 1)

        # Network discovery picker lists discovered servers plus ENTER_ADDRESS.
        self.found = [Found('http://192.168.1.250:8096', 'srv-3', 'New Server')]
        self.picker.reset_mock()
        self.picker.return_value = jellyfin.ENTER_ADDRESS
        ENV.dialog_answers = deque(['c.lan:8096'])

        stored = jellyfin.addServerDialog(IDENTITY, session=FakeSession(public_info('srv-3', 'New Server')))

        disc_heading, disc_options = self.picker.call_args[0][:2]
        self.assertEqual(disc_heading, 'Jellyfin servers on your network')
        self.assertEqual(
            disc_options,
            [
                ('http://192.168.1.250:8096', 'New Server (http://192.168.1.250:8096)'),
                (jellyfin.ENTER_ADDRESS, 'Enter address'),
            ],
        )
        self.assertEqual(stored.baseUrl, 'http://c.lan:8096')

    def test_pick_server_switches_and_adds(self):
        ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps(TWO_SERVERS)
        self.picker.return_value = 'srv-1'

        previous = jellyfin.pickServer(IDENTITY)

        self.assertEqual(previous, 'srv-2')
        self.assertEqual([s.serverId for s in jellyfin.storedServers()], ['srv-2', 'srv-1'])

        self.picker.return_value = 'srv-1'
        self.assertIsNone(jellyfin.pickServer(IDENTITY))

        self.picker.return_value = jellyfin.ADD_SERVER
        ENV.dialog_answers = deque(['d.lan:8096'])
        previous = jellyfin.pickServer(IDENTITY, session=FakeSession(public_info('srv-4', 'Jellyfin D')))
        self.assertEqual(previous, 'srv-1')
        self.assertEqual(jellyfin.storedServers()[-1].baseUrl, 'http://d.lan:8096')

    def test_discovered_server_picked_directly_skips_keyboard(self):
        self.found = [Found('http://192.168.1.250:8096', 'srv-1', 'Home Jellyfin')]
        self.picker.return_value = 'http://192.168.1.250:8096'

        stored = jellyfin.addServerDialog(IDENTITY, session=FakeSession(public_info('srv-1')))

        self.assertEqual(stored.baseUrl, 'http://192.168.1.250:8096')
        self.assertFalse([c for c in ENV.dialog_calls if c[0] == 'input'])

    def test_unreachable_address_offers_try_again(self):
        ENV.dialog_answers = deque(['bad.lan:8096', 'good.lan:8096'])
        session = FakeSession(requests.ConnectionError('refused'), public_info('srv-1'))

        with mock.patch.object(jellyfin.optionsdialog, 'show', return_value=0) as prompt:
            stored = jellyfin.addServerDialog(IDENTITY, session=session)

        self.assertEqual(stored.baseUrl, 'http://good.lan:8096')
        self.assertIn('bad.lan:8096', prompt.call_args[0][1])
        self.assertEqual(prompt.call_args[0][2:4], ('Try again', 'Cancel'))

    def test_cancelled_entry_adds_nothing(self):
        ENV.dialog_answers = deque([''])

        self.assertIsNone(jellyfin.addServerDialog(IDENTITY))
        self.assertEqual(jellyfin.storedServers(), [])
