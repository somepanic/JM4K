"""
lib/silo.py: the Silo servers SM4K knows, stored in the add-on's settings, and the dialog that adds one by its
address after checking that a Silo server answers there.
"""

import json
from collections import deque
from unittest import mock

import requests
from kodienv import ENV

ENV.abort_requested = True
from silonet import errors
from silonet.client import DeviceIdentity
from silonet.discovery import Found

from lib import silo
from lib.windows import library

from .base import KodiTestCase, fixture
from .silonet_fakes import FakeResponse, FakeSession

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')


def silo_answers(serverId='00000000-0000-4000-8000-000000000001'):
    return [
        FakeResponse(200, json.loads(fixture('silonet', 'system_info.json'))),
        FakeResponse(200, {'server_id': serverId}),
        FakeResponse(200, json.loads(fixture('silonet', 'theme_branding.json'))),
    ]


class StoredServersTest(KodiTestCase):
    def test_add_probes_and_stores(self):
        stored = silo.addServer('http://silo.lan:8090', DEVICE, session=FakeSession(*silo_answers()))

        self.assertEqual(
            (stored.baseUrl, stored.serverId, stored.name),
            ('http://silo.lan:8090', '00000000-0000-4000-8000-000000000001', 'Silo'),
        )
        self.assertEqual([s.baseUrl for s in silo.storedServers()], ['http://silo.lan:8090'])

    def test_same_server_new_address_updates(self):
        silo.addServer('http://silo.lan:8090', DEVICE, session=FakeSession(*silo_answers()))
        silo.addServer('https://media.example.com/silo/', DEVICE, session=FakeSession(*silo_answers()))

        self.assertEqual([s.baseUrl for s in silo.storedServers()], ['https://media.example.com/silo'])

    def test_a_bare_host_gets_http(self):
        session = FakeSession(*silo_answers())

        stored = silo.addServer('silo.lan:8090', DEVICE, session=session)

        self.assertEqual(stored.baseUrl, 'http://silo.lan:8090')
        self.assertEqual(session.calls[0]['url'], 'http://silo.lan:8090/api/v2/system/info')

    def test_rejects_unsupported_server(self):
        with self.assertRaises(errors.SiloIncompatible):
            silo.addServer('http://jellyfin.lan:8096', DEVICE, session=FakeSession(FakeResponse(404)))

        self.assertEqual(silo.storedServers(), [])

    def test_unreadable_setting_means_no_servers(self):
        ENV.settings['silo_servers_json'] = 'not json'

        self.assertEqual(silo.storedServers(), [])


class AddServerDialogTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.found = []
        mock.patch.object(silo.discovery, 'find', side_effect=lambda: self.found).start()
        self.picker = mock.patch.object(silo, 'showOptionsDialog').start()  # SiloMod's own picker
        self.addCleanup(mock.patch.stopall)

    def test_servers_found_on_the_network_come_first(self):
        self.found = [Found('Silo', 'http://silo.lan:8090', '00000000-0000-4000-8000-000000000001')]
        self.picker.return_value = 'http://silo.lan:8090'

        stored = silo.addServerDialog(DEVICE, session=FakeSession(*silo_answers()))

        self.assertEqual(stored.baseUrl, 'http://silo.lan:8090')
        heading, options = self.picker.call_args[0][:2]
        self.assertEqual(heading, 'Silo servers on your network')
        self.assertEqual([label for _key, label in options], ['Silo (http://silo.lan:8090)', 'Enter address'])
        self.assertFalse([c for c in ENV.dialog_calls if c[0] == 'input'])

    def test_enter_address_from_the_list(self):
        self.found = [Found('Silo', 'http://silo.lan:8090', '00000000-0000-4000-8000-000000000001')]
        self.picker.return_value = silo.ENTER_ADDRESS
        ENV.dialog_answers = deque(['silo.lan:8091'])

        stored = silo.addServerDialog(DEVICE, session=FakeSession(*silo_answers()))

        self.assertEqual(stored.baseUrl, 'http://silo.lan:8091')

    def test_dialog_adds_the_server_entered(self):
        ENV.dialog_answers = deque(['silo.lan:8090'])

        stored = silo.addServerDialog(DEVICE, session=FakeSession(*silo_answers()))

        self.assertEqual(stored.baseUrl, 'http://silo.lan:8090')

    def test_unreachable_address_offers_to_try_again(self):
        ENV.dialog_answers = deque(['silo.lan:8090', 'silo.lan:8091'])
        session = FakeSession(requests.ConnectionError('refused'), *silo_answers())

        with mock.patch.object(silo.optionsdialog, 'show', return_value=0) as prompt:  # SiloMod's own: Try again
            stored = silo.addServerDialog(DEVICE, session=session)

        self.assertEqual(stored.baseUrl, 'http://silo.lan:8091')
        self.assertIn('silo.lan:8090', prompt.call_args[0][1])
        self.assertEqual(prompt.call_args[0][2:4], ('Try again', 'Cancel'))

    def test_cancelled_entry_adds_nothing(self):
        ENV.dialog_answers = deque([''])

        self.assertIsNone(silo.addServerDialog(DEVICE))
        self.assertEqual(silo.storedServers(), [])


TWO_SERVERS = [
    {'base_url': 'http://a.lan:8090', 'server_id': 'srv-1', 'name': 'Silo A'},
    {'base_url': 'http://b.lan:8090', 'server_id': 'srv-2', 'name': 'Silo B'},  # the one in use: stored last
]


class PickServerTest(KodiTestCase):
    """Switch server, in SiloMod's own picker (lib/windows/dialog.showOptionsDialog())."""

    def setUp(self):
        super().setUp()
        ENV.settings[silo.SERVERS_SETTING] = json.dumps(TWO_SERVERS)
        self.picker = mock.patch.object(silo, 'showOptionsDialog').start()
        mock.patch.object(silo.discovery, 'find', return_value=[]).start()
        self.addCleanup(mock.patch.stopall)

    def test_the_picker_lists_the_stored_servers_then_add(self):
        self.picker.return_value = None

        self.assertIsNone(silo.pickServer(DEVICE))

        heading, options = self.picker.call_args[0][:2]
        self.assertEqual(heading, 'Switch server')
        self.assertEqual(
            [label for _key, label in options],
            ['Silo A (http://a.lan:8090)', 'Silo B (http://b.lan:8090)', 'Add server'],
        )
        self.assertEqual(self.picker.call_args.kwargs['selected_idx'], 1)  # the one in use, ticked

    def test_picking_another_server_makes_it_the_one_start_up_opens(self):
        self.picker.return_value = 'srv-1'

        previous = silo.pickServer(DEVICE)

        self.assertEqual(previous, 'srv-2')
        self.assertEqual([s.serverId for s in silo.storedServers()], ['srv-2', 'srv-1'])

    def test_picking_the_server_in_use_changes_nothing(self):
        self.picker.return_value = 'srv-2'

        self.assertIsNone(silo.pickServer(DEVICE))
        self.assertEqual([s.serverId for s in silo.storedServers()], ['srv-1', 'srv-2'])

    def test_a_server_added_from_the_picker_is_the_one_start_up_opens(self):
        self.picker.return_value = silo.ADD_SERVER
        ENV.dialog_answers = deque(['c.lan:8090'])

        previous = silo.pickServer(DEVICE, session=FakeSession(*silo_answers('srv-3')))

        self.assertEqual(previous, 'srv-2')
        self.assertEqual(silo.storedServers()[-1].baseUrl, 'http://c.lan:8090')


class SwitchServerMenuTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        account = mock.Mock(isSignedIn=True, homeUsers=[mock.Mock()])
        selected = mock.Mock(spec=['platform'], platform='silo')
        for name, value in (('ACCOUNT', account), ('SERVERMANAGER', mock.Mock(selectedServer=selected))):
            patch = mock.patch.object(library.plexapp, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.window = library.LibraryWindow.__new__(library.LibraryWindow)
        self.window._closeSessionWithOption = mock.Mock()

    def test_the_user_menu_offers_switch_server(self):
        with mock.patch.object(library, 'showOptionsDialog', return_value=None) as dialog:
            self.window.showUserMenu()

        self.assertIn(('server', 'Switch server'), dialog.call_args[0][1])

    def test_another_server_picked_ends_the_session_for_it(self):
        with mock.patch.object(silo, 'pickServer', return_value='srv-2'):
            self.window.doUserOption('server')

        self.window._closeSessionWithOption.assert_called_once_with({'switch_server': 'srv-2'})

    def test_no_server_picked_keeps_the_session(self):
        with mock.patch.object(silo, 'pickServer', return_value=None):
            self.window.doUserOption('server')

        self.window._closeSessionWithOption.assert_not_called()
