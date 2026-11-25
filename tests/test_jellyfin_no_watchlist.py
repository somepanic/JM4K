"""
Watchlist (D6) and 5-star user rating (J-D12) are absent on Jellyfin:
- JellyfinServerManager.getWatchlistServer() is None; no Watchlist row in the sidebar or Libraries picker;
  no Watchlist settings shown; no Watchlist buttons/actions on PrePlay/Show/Episodes/itemmenu; Silo's
  silo:// watchlist seams removed without breaking Plex Uno's own watchlist tests.
- 5-star user rating (rating.stars / userRating) is never set or offered in sort options on Jellyfin,
  while TMDB/RT critic & audience ratings and Plex's own star ratings stay intact.
"""

import copy
from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from jellynet import library as jf_library
from jellynet.servermanager import JellyfinServerManager
from plexnet import plexobjects
from plexnet import util as pnUtil

from lib.windows import home, itemmenu, library, section_ids, settings, sidebar_model
from lib.windows.mixins import ratings, watchlist

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession
from .test_jellynet_detail import MOVIE_DETAIL, make_server, make_tile


class _FakeWindow(watchlist.WatchlistUtilsMixin, ratings.RatingsMixin):
    def __init__(self):
        watchlist.WatchlistUtilsMixin.__init__(self)
        self.props = {}

    def setProperty(self, key, value):
        self.props[key] = value

    def setProperties(self, keys, value):
        for key in keys:
            self.props[key] = value

    def setBoolProperty(self, key, value):
        self.props[key] = '1' if value else ''


class JellyfinNoWatchlistOrStarRatingTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_no_watchlist_row_or_setting(self):
        session = FakeSession()
        srv = make_server(session)
        manager = JellyfinServerManager([srv])
        account = mock.Mock(ID='u1', isOffline=False, title='alice', username='alice', thumb='')

        # 1. Server manager & watchlistServer() return None
        self.assertIsNone(manager.getWatchlistServer())
        self.assertIsNone(manager.getDiscoverServer())
        with mock.patch.object(pnUtil, 'SERVERMANAGER', manager, create=True):
            self.assertIsNone(watchlist.watchlistServer())

        # 2. Sidebar & Libraries picker never show Watchlist on Jellyfin
        stored_settings = {}
        with (
            mock.patch.object(sidebar_model.plexapp, 'SERVERMANAGER', manager),
            mock.patch.object(sidebar_model.plexapp, 'ACCOUNT', account),
            mock.patch.object(library.plexapp, 'SERVERMANAGER', manager),
            mock.patch.object(library.plexapp, 'ACCOUNT', account),
            mock.patch.object(
                sidebar_model.util, 'getSetting', lambda key, default=None: stored_settings.get(key, default)
            ),
            mock.patch.object(
                sidebar_model.util, 'setSetting', lambda key, value: stored_settings.__setitem__(key, value)
            ),
            mock.patch.object(sidebar_model.util, 'getUserSetting', lambda _key, default=None: default),
            mock.patch.object(section_ids, 'migrate', lambda: None),
            mock.patch.object(home, 'watchlist_section', mock.Mock(key=section_ids.WATCHLIST_ID)),
        ):
            sidebar_model.refreshWatchlistSection()
            self.assertIsNone(home.watchlist_section)

            # New Jellyfin account onboarding does not seed WATCHLIST_ID
            nav = sidebar_model.loadNavSettings()
            self.assertNotIn(section_ids.WATCHLIST_ID, nav['entries'])

            # Even if WATCHLIST_ID was stored and watchlist_section somehow populated, sections() omits it
            fake_wl = mock.Mock(key=section_ids.WATCHLIST_ID, title='Watchlist')
            with mock.patch.object(home, 'watchlist_section', fake_wl):
                stale_nav = {
                    'version': sidebar_model.SIDEBAR_VERSION,
                    'entries': [section_ids.WATCHLIST_ID],
                    'order': [section_ids.WATCHLIST_ID],
                    'libraries': {},
                }
                self.assertEqual(sidebar_model.sections(stale_nav), [])
                self.assertNotIn(
                    section_ids.WATCHLIST_ID,
                    sidebar_model.pickerOrder(stale_nav, {srv.uuid: []}, [srv]),
                )

                win = library.LibraryWindow.__new__(library.LibraryWindow)
                win.navSettings = stale_nav
                win._pickerServers = [srv]
                win._pickerListed = {srv.uuid: []}
                picker_sids = [o.get('sid') for o in win._libraryPickerOptions() if o]
                self.assertNotIn(section_ids.WATCHLIST_ID, picker_sids)

        # 3. Settings screen hides use_watchlist and watchlist_auto_remove while keeping defaults
        shown_ids = {
            s.ID
            for _, section in settings.Settings.SETTINGS.values()
            for s in section
            if s is not None and s.should_show()
        }
        self.assertNotIn('use_watchlist', shown_ids)
        self.assertNotIn('watchlist_auto_remove', shown_ids)
        self.assertIn('use_watchlist', settings.util.DEFAULT_SETTINGS)
        self.assertIn('watchlist_auto_remove', settings.util.DEFAULT_SETTINGS)

        # 4. Detail screen WatchlistUtilsMixin & auto-remove & blind removal stay disabled on Jellyfin
        movie = make_tile(srv, MOVIE_DETAIL)
        win = _FakeWindow()
        with (
            mock.patch.object(pnUtil, 'SERVERMANAGER', manager, create=True),
            mock.patch.object(pnUtil, 'ACCOUNT', account, create=True),
            mock.patch.object(watchlist.util, 'getUserSetting', lambda _k, default=None: default),
        ):
            self.assertFalse(win.watchlist_setup(movie))
            self.assertFalse(win.wl_enabled)
            self.assertEqual(win.props.get('watchlist_enabled'), '')

            # shouldAutoRemove and removeFromWatchlistBlind are no-ops
            watched_jf = mock.Mock(server=srv, isFullyWatched=True, TYPE='movie', guid='jellyfin://movie/mov-1')
            self.assertFalse(watchlist.shouldAutoRemove(watched_jf))
            watchlist.removeFromWatchlistBlind('silo://movie/1', watched_jf)
            watchlist.removeFromWatchlistBlind('jellyfin://movie/mov-1', watched_jf)
            watchlist.removeFromWatchlistBlind('plex://movie/1', watched_jf)

        # 5. itemmenu never offers remove_from_watchlist for a Jellyfin item
        movie_mli = mock.Mock(dataSource=movie, getProperty=lambda _name: '')
        with mock.patch.object(srv, 'isFavorite', return_value=False):
            rows = itemmenu.options(movie_mli)  # a Jellyfin library is never the Watchlist's grid (movies_shows)
        self.assertNotIn('remove_from_watchlist', [key for key, _ in rows])
        self.assertEqual(session.calls, [])

    def test_no_star_rating_on_jellyfin(self):
        # 1. Jellyfin DTO with UserData.Rating & UserData.Likes never emits userRating or sets rating.stars,
        #    while critic/audience ratings (CommunityRating / CriticRating) still populate.
        rated_dto = copy.deepcopy(MOVIE_DETAIL)
        rated_dto['UserData']['Rating'] = 9.0
        rated_dto['UserData']['Likes'] = True
        srv = make_server(FakeSession(FakeResponse(200, rated_dto)))
        movie = make_tile(srv, rated_dto).reload()

        self.assertEqual(str(movie.userRating), '')
        win = _FakeWindow()
        win.populateRatings(movie, win)
        self.assertEqual(win.props.get('rating.stars'), '')
        self.assertEqual(win.props.get('rating1'), '75%')
        self.assertEqual(win.props.get('rating2'), '6.7')

        # 2. Plex server video with userRating still gets rating.stars (Plex Uno compatibility kept)
        plex_video = mock.Mock(
            server=mock.Mock(platform='Linux'),
            TYPE='movie',
            userRating=plexobjects.PlexValue('8.0'),
            ratings=[],
            rating='',
            audienceRating='',
        )
        win3 = _FakeWindow()
        win3.populateRatings(plex_video, win3)
        self.assertEqual(win3.props.get('rating.stars'), '4')

        # 3. Neither Jellyfin's /sorts nor GridMixin's fallback sort picker offers userRating ("My Rating")
        sorts_xml = jf_library.sortsContainer()
        self.assertNotIn('userRating', [elem.get('key') for elem in sorts_xml])

        for section_type, item_type in (('movie', 'movie'), ('show', 'show'), ('show', 'episode')):
            grid_win = library.LibraryWindow.__new__(library.LibraryWindow)
            grid_win.section = mock.Mock(TYPE=section_type, server=srv)
            grid_win.librarySettings = mock.Mock(itemType=item_type)
            grid_win.sort = 'titleSort'
            grid_win.sortDesc = False
            grid_win.usesServerSorts = lambda: False
            grid_win.serverSortOptions = list
            grid_win.getBoolProperty = lambda _name: False
            with mock.patch('lib.windows.library_grid.showOptionsDialog', return_value=None) as picker:
                grid_win.sortButtonClicked()
            sort_keys = [key for key, _ in picker.call_args[0][1]]
            self.assertNotIn('userRating', sort_keys, (section_type, item_type))

        # Plex server fallback sort picker still offers userRating
        plex_grid = library.LibraryWindow.__new__(library.LibraryWindow)
        plex_grid.section = mock.Mock(TYPE='movie', server=mock.Mock(platform='Linux'))
        plex_grid.librarySettings = mock.Mock(itemType='movie')
        plex_grid.sort = 'titleSort'
        plex_grid.sortDesc = False
        plex_grid.usesServerSorts = lambda: False
        plex_grid.serverSortOptions = list
        plex_grid.getBoolProperty = lambda _name: False
        with mock.patch('lib.windows.library_grid.showOptionsDialog', return_value=None) as plex_picker:
            plex_grid.sortButtonClicked()
        self.assertIn('userRating', [key for key, _ in plex_picker.call_args[0][1]])
