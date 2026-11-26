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
from lib.windows import signin

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


def connect_window(*steps):
    """The Connect to Server screen as Kodi runs it: modal() plays the person's steps, each a function of the window."""
    window = signin.PinLoginWindow.__new__(signin.PinLoginWindow)
    window._closing = False
    window._winID = None
    window.windowId = 13001
    window.isOpen = True
    window.closed = False
    window.doClose = lambda: setattr(window, 'closed', True)
    window.modal = lambda: [step(window) for step in steps]
    return window


def click(control_id):
    return lambda window: window.onClick(control_id)


class RunNow:
    """threading.Thread that runs its target at start(), so discovery fills in the Host before the steps."""

    def __init__(self, target=None, args=(), **_kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class ConnectScreenTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.found = []
        mock.patch.object(jellyfin.discovery, 'discover', side_effect=lambda: self.found).start()
        mock.patch.object(jellyfin.threading, 'Thread', RunNow).start()
        self.addCleanup(mock.patch.stopall)

    def connect(self, window, **kwargs):
        with mock.patch.object(signin.PinLoginWindow, 'create', return_value=window):
            return jellyfin.connectToServer(IDENTITY, **kwargs)

    def test_connect_stores_the_server_at_the_host(self):
        ENV.dialog_answers = deque(['jellyfin.lan:8096'])
        window = connect_window(click(signin.PinLoginWindow.HOST_ID), click(signin.PinLoginWindow.CONNECT_ID))

        stored = self.connect(window, session=FakeSession(public_info('srv-1')))

        self.assertEqual((stored.baseUrl, stored.serverId), ('http://jellyfin.lan:8096', 'srv-1'))
        self.assertEqual([s.serverId for s in jellyfin.storedServers()], ['srv-1'])
        self.assertTrue(window.closed)
        props = ENV.window_props[13001]
        self.assertEqual(props.get('label.title'), 'Connect to Server')
        self.assertEqual(props.get('label.hint'), '192.168.1.100:8096 or https://myserver.com')
        self.assertEqual(props.get('host'), 'jellyfin.lan:8096')

    def test_a_server_on_the_network_fills_in_the_host(self):
        self.found = [Found('http://192.168.1.250:8096', 'srv-1', 'TheRubys')]
        window = connect_window(click(signin.PinLoginWindow.CONNECT_ID))

        stored = self.connect(window, session=FakeSession(public_info('srv-1')))

        self.assertEqual(stored.baseUrl, 'http://192.168.1.250:8096')
        self.assertEqual(ENV.window_props[13001].get('found'), 'Found TheRubys on your network')
        self.assertFalse([c for c in ENV.dialog_calls if c[0] == 'input'])  # one press of Connect

    def test_an_unreachable_host_says_so_and_stays_open(self):
        ENV.dialog_answers = deque(['bad.lan:8096', 'good.lan:8096'])
        seen = []
        window = connect_window(
            click(signin.PinLoginWindow.HOST_ID),
            click(signin.PinLoginWindow.CONNECT_ID),
            lambda w: seen.append((w.closed, ENV.window_props[13001].get('error'))),
            click(signin.PinLoginWindow.HOST_ID),
            click(signin.PinLoginWindow.CONNECT_ID),
        )

        stored = self.connect(window, session=FakeSession(requests.ConnectionError('refused'), public_info('srv-1')))

        self.assertEqual(seen, [(False, 'Could not reach a Jellyfin server at bad.lan:8096.')])
        self.assertEqual(stored.baseUrl, 'http://good.lan:8096')

    def test_a_stored_server_that_stopped_answering_opens_with_its_address_and_why(self):
        window = connect_window(click(signin.PinLoginWindow.CANCEL_ID))

        self.assertIsNone(self.connect(window, address='http://a.lan:8096', error='Could not reach it.'))

        props = ENV.window_props[13001]
        self.assertEqual((props.get('host'), props.get('error')), ('http://a.lan:8096', 'Could not reach it.'))
        jellyfin.discovery.discover.assert_not_called()  # an address given: nothing to look for

    def test_cancel_adds_nothing(self):
        window = connect_window(click(signin.PinLoginWindow.CANCEL_ID))

        self.assertIsNone(self.connect(window))
        self.assertEqual(jellyfin.storedServers(), [])
        self.assertTrue(window.closed)


class PickerTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.picker = mock.patch.object(jellyfin, 'showOptionsDialog').start()
        self.addCleanup(mock.patch.stopall)

    def test_picker_lists_servers_and_add(self):
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

    def test_pick_server_switches_and_adds(self):
        ENV.settings[jellyfin.SERVERS_SETTING] = json.dumps(TWO_SERVERS)
        self.picker.return_value = 'srv-1'

        previous = jellyfin.pickServer(IDENTITY)

        self.assertEqual(previous, 'srv-2')
        self.assertEqual([s.serverId for s in jellyfin.storedServers()], ['srv-2', 'srv-1'])

        self.picker.return_value = 'srv-1'
        self.assertIsNone(jellyfin.pickServer(IDENTITY))

        # Add server opens Connect to Server
        self.picker.return_value = jellyfin.ADD_SERVER
        added = jellyfin.StoredServer('http://d.lan:8096', 'srv-4', 'Jellyfin D')
        with mock.patch.object(jellyfin, 'connectToServer', return_value=added) as connect:
            previous = jellyfin.pickServer(IDENTITY)
        connect.assert_called_once()
        self.assertEqual(previous, 'srv-1')
