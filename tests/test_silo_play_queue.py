"""
Play and Shuffle All on a Silo server, which has no play queues of its own: SiloMod builds the queue
(playqueue.createLocalPlayQueue()) from the library grid's listing, a collection's titles, and a series library's
episodes, which Silo lists in one catalog listing (type=episode). Responses are written inline in the shape of
/api/v2/openapi.json (Silo build-2987).
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from plexnet import playlist, playqueue

from lib.windows import library, library_grid, opener, preplay

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeSession
from .test_silonet_library import COLLECTIONS, LIBRARIES, movie, page, section, series


def episode(show, season, number, played=False):
    return {
        'content_id': f'{show}-{season}-{number}',
        'type': 'episode',
        'title': f'{show} {season}x{number}',
        'series_id': show,
        'series_title': f'Show {show}',
        'season_number': season,
        'episode_number': number,
        'user_state': {'played': played},
    }


def keys(queue):
    return [str(item.ratingKey) for item in queue]


class SiloPlayAllTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_play_all_queues_a_movie_library_in_grid_order(self):
        session = FakeSession(LIBRARIES, page([movie(0), movie(1), movie(2)], 3))

        queue = playqueue.createPlayQueueForItem(
            section(session), options={'shuffle': False}, args={'sourceType': '1', 'sort': 'titleSort:desc'}
        )

        self.assertIsInstance(queue, playlist.LocalPlaylist)
        self.assertEqual(keys(queue), ['m0', 'm1', 'm2'])
        self.assertEqual(session.calls[1]['params']['sort'], '-title')  # in the grid's sort

    def test_shuffle_all_keeps_every_movie(self):
        session = FakeSession(LIBRARIES, page([movie(n) for n in range(6)], 6))

        queue = playqueue.createPlayQueueForItem(section(session), options={'shuffle': True}, args={'sourceType': '1'})

        self.assertEqual(sorted(keys(queue)), [f'm{n}' for n in range(6)])

    def test_unwatched_filter_plays_only_unwatched(self):
        watched = dict(movie(1), user_state={'played': True})
        session = FakeSession(LIBRARIES, page([movie(0), watched, movie(2)], 3))

        queue = playqueue.createPlayQueueForItem(
            section(session), options={'shuffle': False}, args={'sourceType': '1', 'unwatched': '1'}
        )

        self.assertEqual(keys(queue), ['m0', 'm2'])

    def test_a_series_library_queues_each_shows_episodes_in_grid_order(self):
        # the grid lists s1 before s0; Silo lists the library's episodes in its own order, s9's too, a show the grid
        # left out (filtered)
        episodes = [
            episode('s0', 1, 2),
            episode('s1', 2, 1),
            episode('s9', 1, 1),
            episode('s0', 1, 1),
            episode('s1', 1, 1),
        ]
        session = FakeSession(LIBRARIES, page([series(1), series(0)], 2), page(episodes, 5))

        queue = playqueue.createPlayQueueForItem(
            section(session, 1), options={'shuffle': False}, args={'sourceType': '2'}
        )

        self.assertEqual(keys(queue), ['s1-1-1', 's1-2-1', 's0-1-1', 's0-1-2'])
        params = session.calls[2]['params']
        self.assertEqual((params['library_id'], params['type']), ('5', 'episode'))

    def test_nothing_left_to_play_is_no_queue(self):
        watched = dict(movie(0), user_state={'played': True})
        session = FakeSession(LIBRARIES, page([watched], 1))

        queue = playqueue.createPlayQueueForItem(
            section(session), options={'shuffle': False}, args={'sourceType': '1', 'unwatched': '1'}
        )

        self.assertIsNone(queue)

    def test_a_collection_plays_its_titles(self):
        session = FakeSession(LIBRARIES, COLLECTIONS, page([movie(0), movie(1)], 2))
        alien = section(session).all(0, 50, type_=18)[0]

        queue = playqueue.createPlayQueueForItem(alien, options={'shuffle': False})

        self.assertEqual(keys(queue), ['m0', 'm1'])


class LocalQueuePlaysTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_opener_plays_a_local_playlist(self):
        queue = playlist.LocalPlaylist([], None)
        context = object()

        with mock.patch('lib.windows.videoplayer.play') as play:
            opener.open(queue, context=context, auto_play=True)

        play.assert_called_once_with(play_queue=queue, context=context, auto_play=True)

    def test_preplay_trailer_setting_asks_silo_for_no_queue(self):
        session = FakeSession(LIBRARIES, page([movie(0)], 1))
        film = section(session).all(collections=False)[0]
        ENV.settings['preplay_trailers.p1'] = 4  # one trailer before the film, the profile's setting
        window = preplay.PrePlayWindow.__new__(preplay.PrePlayWindow)
        window.video, window.playBtnClicked, window.startOver, window.useBGM = film, False, False, False
        window.processCommand = lambda command: None
        film.available = lambda: True  # its detail's versions, which pre-play has loaded by then

        with mock.patch('plexnet.util.ACCOUNT', mock.Mock(ID='p1')), mock.patch('lib.windows.videoplayer.play') as play:
            window.playVideo()

        self.assertEqual(len(session.calls), 2)  # the libraries and the grid: no queue asked of Silo
        self.assertIs(play.call_args.kwargs['video'], film)


class GridPlayButtonTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_nothing_left_to_play_says_so_and_keeps_the_button(self):
        watched = dict(movie(0), user_state={'played': True})
        win = library.LibraryWindow.__new__(library.LibraryWindow)
        win.section = section(FakeSession(LIBRARIES, page([watched], 1)))
        win.boolFilters, win.playBtnClicked = {'unwatched': True}, False
        win.getFilterOpts = win.getSortOpts = lambda: None
        win._watchGridPlayback = mock.Mock()

        dialog = mock.patch.object(library_grid.util, 'messageDialog').start()
        open_ = mock.patch.object(library_grid.opener, 'open').start()
        self.addCleanup(mock.patch.stopall)

        win.playButtonClicked()

        dialog.assert_called_once()
        open_.assert_not_called()
        self.assertFalse(win.playBtnClicked)  # Play works again once the filter changes
