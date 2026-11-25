"""
Path mapping on Silo, as Plex Uno does it: a library's folders on the server (GET /api/v2/libraries) are its locations,
which the library menu's "Map path" rows map to a Kodi source (path_mapping.json). A mapped file that Silo sends as the
original plays straight from that source, while the attempt stays Silo's: started, its progress and end reported.
"""

import copy
import os
import shutil
import tempfile
from unittest import mock

from plexnet import plexlibrary, plexpart, plexplayer

from lib import path_mapping
from lib.path_mapping import pmm

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile
from .test_silonet_library import LIBRARIES
from .test_silonet_playback import CAPABILITIES, NO_WATCH, decision, ok

SERVER_ROOT = '/data/media/radarr-sqp1/'
FILM: dict = copy.deepcopy(MOVIE)
FILM['versions'][0]['file_path'] = SERVER_ROOT + 'Heat (1995)/Heat.mkv'
LIBRARY_PATHS = ok(
    {
        'items': [
            {'id': '2', 'name': '2160p Movies', 'type': 'movies', 'paths': ['/data/media/radarr-sqp1']},
            {'id': '5', 'name': '2160p Shows', 'type': 'series', 'paths': ['/data/media/tv', '/data/media/anime/']},
        ]
    }
)


class SiloLibraryLocationsTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_a_librarys_folders_are_its_locations(self):
        session = FakeSession(LIBRARIES, LIBRARY_PATHS)
        movies, shows = plexlibrary.Library(None, server=server(session)).sections()

        self.assertEqual(shows.locations, ['/data/media/tv/', '/data/media/anime/'])
        self.assertEqual(movies.locations, [SERVER_ROOT])
        self.assertTrue(session.calls[1]['url'].endswith('/api/v2/libraries'))
        self.assertEqual(len(session.calls), 2)  # asked once

    def test_folders_silo_couldnt_list_are_asked_again(self):
        session = FakeSession(LIBRARIES, problem(503, 'dependency_unavailable'), LIBRARY_PATHS)
        movies = plexlibrary.Library(None, server=server(session)).sections()[0]

        self.assertEqual(movies.locations, [])  # Silo restarting when the sidebar was built
        self.assertEqual(movies.locations, [SERVER_ROOT])

    def test_folders_silo_wont_list_are_none(self):
        # GET /api/v2/libraries may be refused to an account that isn't an admin
        movies = plexlibrary.Library(None, server=server(FakeSession(LIBRARIES, problem(403, 'forbidden')))).sections()[
            0
        ]

        self.assertEqual(movies.locations, [])


class SiloMappedPlaybackTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()
        self.share = tempfile.mkdtemp()  # the Kodi source the library's folder maps to
        self.addCleanup(shutil.rmtree, self.share)
        self.root = self.share + os.sep
        for patch in (
            mock.patch.object(pmm, 'PATH_MAP', {'Silo': {self.root: SERVER_ROOT}}),
            mock.patch.object(pmm, 'BROKEN_MAP', {}),
            mock.patch.object(pmm, 'NOTIFIED', set()),
            mock.patch.object(plexpart.addonSettings, 'verifyMappedFiles', True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def play(self, delivery='original_http', film=FILM):
        """What Kodi is handed to play the film as Silo plans it, and the requests SiloMod made."""
        session = FakeSession(ok(film), ok(CAPABILITIES), ok(decision(delivery), 201), NO_WATCH)
        movie = tile(server(session), film).reload()
        meta = plexplayer.PlexPlayer(movie, 0).build()
        return meta, session.calls

    def onShare(self):
        os.makedirs(os.path.join(self.share, 'Heat (1995)'))
        local = os.path.join(self.share, 'Heat (1995)', 'Heat.mkv')
        open(local, 'w').close()
        return local

    def test_a_mapped_original_plays_the_local_file(self):
        local = self.onShare()

        meta, calls = self.play()

        self.assertEqual((meta.streamUrls, meta.isMapped, meta.isRequestToServer), ([local], True, False))
        self.assertTrue(any(c['url'].endswith('/api/v2/playback/start') for c in calls))  # still Silo's attempt

    def test_a_transcode_never_plays_the_local_file(self):
        self.onShare()

        meta, _calls = self.play('server_transcode_hls')

        self.assertFalse(meta.isMapped)
        self.assertTrue(meta.streamUrls[0].startswith('http://silo.lan:8090/'))

    def test_a_missing_mapped_file_plays_silos_stream(self):
        with mock.patch.object(path_mapping, 'showNotification') as notice:
            meta, _calls = self.play()

        self.assertFalse(meta.isMapped)
        self.assertTrue(meta.streamUrls[0].startswith('http://silo.lan:8090/'))
        notice.assert_called_once()

    def test_a_file_outside_every_mapped_folder_plays_silos_stream(self):
        film = copy.deepcopy(FILM)
        film['versions'][0]['file_path'] = '/data/media/other/Heat.mkv'

        meta, _calls = self.play(film=film)

        self.assertFalse(meta.isMapped)
