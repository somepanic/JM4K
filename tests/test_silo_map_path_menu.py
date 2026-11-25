"""
A library's menu (the sidebar's context press, LibraryWindow.sectionMenu()): path mapping is one Map path button,
which opens Kodi's folder selector for the Kodi source the library's folder is reached on; Remove path mapping once
it's mapped. The server's own folder (/data/media/...) isn't shown: it means nothing on the Kodi side. An admin's Scan
Library Files asks Silo to scan the library; Plex's Empty Trash and Analyze have no Silo equivalent.
"""

from collections import deque
from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from plexnet import plexlibrary

from lib.path_mapping import pmm
from lib.windows import library
from lib.windows.dialog import GROUP

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession
from .test_silo_path_mapping import LIBRARY_PATHS, SERVER_ROOT
from .test_silonet_detail import server
from .test_silonet_library import LIBRARIES

SHARE = 'smb://ruby/media/radarr-sqp1/'


class LibraryMenuTestCase(KodiTestCase):
    admin = False  # the account's role: an admin's menu has Scan Library Files

    def setUp(self):
        super().setUp()
        ensure_plex_interface()
        ENV.settings['path_mapping'] = 'true'
        self.session = FakeSession(LIBRARIES, LIBRARY_PATHS)
        self.section = plexlibrary.Library(None, server=server(self.session)).sections()[0]
        self.map = {}
        for patch in (
            mock.patch.object(pmm, 'PATH_MAP', self.map),
            mock.patch.object(pmm, 'save', return_value=True),
            mock.patch.object(library.plexapp, 'ACCOUNT', mock.Mock(isAdmin=self.admin)),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        item = mock.Mock(dataSource=self.section)
        item.getProperty = lambda name: '1' if name == 'item' else ''
        self.window = library.LibraryWindow.__new__(library.LibraryWindow)
        self.window.sectionList = mock.Mock(getSelectedItem=lambda: item)
        self.window._rebuildSidebar = mock.Mock()

    def menu(self, *choices):
        """The menu's rows, (key, label), each time it shows while the user chooses choices in turn, then closes it."""
        shown = []

        def picker(heading, options, on_choice=None, **kwargs):
            for choice in choices:
                shown.append([option[:2] for option in options])
                reopened = on_choice(choice)
                if reopened is None:  # a choice that closes the menu, and runs once it has
                    return choice
                options = reopened[0]
            shown.append([option[:2] for option in options])

        with mock.patch.object(library, 'showOptionsDialog', side_effect=picker):
            self.window.sectionMenu()
        return shown


class MapPathMenuTest(LibraryMenuTestCase):
    def test_map_path_is_a_button_and_shows_no_server_folder(self):
        rows = self.menu()[0]

        self.assertIn(('map_path', 'Map path'), rows)
        self.assertNotIn(GROUP, [key for key, _label in rows])
        self.assertFalse([label for _key, label in rows if '/data/media' in label])

    def test_map_path_opens_the_folder_selector_and_maps_the_library(self):
        ENV.dialog_answers = deque([SHARE])

        _before, after = self.menu('map_path')

        browse = next(call for call in ENV.dialog_calls if call[0] == 'browse')
        self.assertEqual(browse[1][1], 'Select Kodi source for 2160p Movies')
        self.assertEqual(self.map, {'Silo': {SHARE: SERVER_ROOT}})
        self.assertIn(('unmap_path', 'Remove path mapping'), after)
        self.assertNotIn(('map_path', 'Map path'), after)

    def test_a_cancelled_selector_maps_nothing(self):
        ENV.dialog_answers = deque([''])

        _before, after = self.menu('map_path')

        self.assertEqual(self.map, {})
        self.assertIn(('map_path', 'Map path'), after)

    def test_remove_path_mapping_unmaps_the_library(self):
        self.map['Silo'] = {SHARE: SERVER_ROOT}

        before, after = self.menu('unmap_path')

        self.assertIn(('unmap_path', 'Remove path mapping'), before)
        self.assertEqual(self.map, {'Silo': {}})
        self.assertIn(('map_path', 'Map path'), after)


class AdminLibraryMenuTest(LibraryMenuTestCase):
    admin = True

    def test_an_admin_gets_scan_without_plexs_trash_and_analyze(self):
        keys = [key for key, _label in self.menu()[0]]

        self.assertIn('refresh', keys)
        self.assertNotIn('emptyTrash', keys)
        self.assertNotIn('analyze', keys)

    def test_scan_asks_silo_to_scan_the_library(self):
        self.session.queue(LIBRARIES, FakeResponse(202, {'status': 'accepted'}))  # the libraries again, then the scan

        with (
            mock.patch.object(library.busy, 'BusyContext'),  # a Kodi window
            mock.patch.object(library.util, 'showNotification') as notice,
            mock.patch.object(library.threading, 'Thread') as watcher,
        ):
            self.menu('refresh')

        scan = self.session.calls[-1]
        self.assertEqual(
            (scan['method'], scan['url'], scan['json']),
            ('POST', 'http://silo.lan:8090/api/v2/scan', {'library_id': '2'}),
        )
        notice.assert_called_once()
        watcher.assert_not_called()  # Silo's library list doesn't say when a scan is done: nothing to watch for
