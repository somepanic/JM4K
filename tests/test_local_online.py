# coding=utf-8
"""
Going local and back online in one session (live 2026-10-06, after plan Phase 9.3):
- in local mode a GDM round that misses a server (Animal never answers GDM) no longer takes away the
  LAN connections plex.tv's list had found for it, which left it offline with nothing to retest;
- a library refusing the collections check is logged in a line, not as a traceback.

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

from __future__ import absolute_import

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from .test_server_state import ManagerTestCase, make_server  # noqa: E402 - imports plexnet's server manager safely
from lib.windows import library  # noqa: E402
from plexnet import exceptions, plexresource  # noqa: E402
from plexnet import util as pnUtil  # noqa: E402

from .base import KodiTestCase  # noqa: E402

MYPLEX = plexresource.ResourceConnection.SOURCE_MYPLEX
DISCOVERED = plexresource.ResourceConnection.SOURCE_DISCOVERED
MANUAL = plexresource.ResourceConnection.SOURCE_MANUAL


class LocalModeListsTest(ManagerTestCase):
    def setUp(self):
        super(LocalModeListsTest, self).setUp()
        self.manager.waitingForResources = False
        for patcher in (mock.patch.object(pnUtil, 'LOCAL_MODE', True),
                        mock.patch.object(self.manager, 'saveState'),
                        mock.patch.object(self.manager, 'updateReachability')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_gdm_round_that_misses_a_server_keeps_its_connections(self):
        self.manager.setWatchedServers([self.animal.uuid])
        connections = list(self.animal.connections)
        self.manager.updateFromConnectionType([], DISCOVERED)
        self.assertEqual(connections, self.animal.connections)
        self.assertFalse(self.animal.offline)

    def test_nor_does_plex_tvs_list_going(self):
        with mock.patch.object(self.manager, 'deviceRefreshComplete') as settle:
            self.manager.updateFromConnectionType([], MYPLEX)
        settle.assert_not_called()

    def test_a_manual_entry_removed_still_goes(self):
        with mock.patch.object(self.manager, 'deviceRefreshComplete') as settle:
            self.manager.updateFromConnectionType([], MANUAL)
        settle.assert_called_once_with(MANUAL)

    def test_online_a_gdm_round_still_settles(self):
        with mock.patch.object(pnUtil, 'LOCAL_MODE', False), \
                mock.patch.object(self.manager, 'deviceRefreshComplete') as settle:
            self.manager.updateFromConnectionType([], DISCOVERED)
        settle.assert_called_once_with(DISCOVERED)


class CollectionsCheckTest(KodiTestCase):
    def test_a_refusal_is_a_line_not_a_traceback(self):
        server = make_server('refusing-uuid', 'Oscar')
        section = mock.Mock(key='1', server=server, title='Films')
        section.all.side_effect = exceptions.BadRequest('(401) unauthorized')
        with mock.patch.object(library.util, 'ERROR') as error, mock.patch.object(library.util, 'LOG') as log:
            self.assertIsNone(library._askSection(section, 'collections', library._countCollections))
        error.assert_not_called()
        self.assertIn('(401) unauthorized', str(log.call_args))
