"""
Watched state, resume point reset, and favorites against Jellyfin 10.11.5 (Task 7.1):
- Mark Played / Mark Unplayed (/:/scrobble, /:/unscrobble) -> POST / DELETE /UserPlayedItems/{itemId}
  (works for movies, episodes, seasons, and shows).
- Resume point reset (/actions/removeFromContinueWatching and /:/progress?key=...&time=0&state=stopped)
  -> POST /UserItems/{itemId}/UserData with {"PlaybackPositionTicks": 0}.
- Favorites (D19): isFavorite() from cached UserData.IsFavorite or GET /UserItems/{itemId}/UserData;
  setFavorite() via POST / DELETE /UserFavoriteItems/{itemId} updating the cache; itemmenu Add/Remove
  actions; Home Favorites row and /jellyfin/favorites (/jellyfin/home/favorites) See more listing.
"""

import copy
from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from plexnet import plexobjects, video  # noqa: F401 (registers plexnet item types)

from lib.windows import itemmenu

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem
from .test_jellynet_detail import (
    BASE,
    MOVIE_DETAIL,
    SEASON_1,
    SERIES_DETAIL,
    episode_detail,
    make_server,
    make_tile,
)
from .test_jellynet_home import DEFAULT_VIEWS, display_prefs, movie_dto, query_result, user_me, user_views


def user_data_dto(item_id, played=False, position_ticks=0, is_favorite=False, unplayed=None):
    data = {
        'ItemId': item_id,
        'Played': played,
        'PlayCount': 1 if played else 0,
        'PlaybackPositionTicks': position_ticks,
        'IsFavorite': is_favorite,
    }
    if unplayed is not None:
        data['UnplayedItemCount'] = unplayed
    return data


def _assert_mediabrowser_only(testcase, calls):
    for call in calls:
        auth = call['headers'].get('Authorization', '')
        testcase.assertTrue(auth.startswith('MediaBrowser '), f'bad auth header: {auth!r}')
        testcase.assertNotIn('X-Emby-Token', call['headers'])
        testcase.assertNotIn('X-MediaBrowser-Token', call['headers'])
        testcase.assertNotIn('api_key', call.get('params') or {})


class JellyfinWatchStateTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_mark_watched_and_unwatched(self):
        # Movie markWatched -> POST /UserPlayedItems/mov-1 + reload
        played_movie = copy.deepcopy(MOVIE_DETAIL)
        played_movie['UserData'] = {
            'Played': True,
            'PlayCount': 1,
            'PlaybackPositionTicks': 0,
            'IsFavorite': False,
        }
        unplayed_movie = copy.deepcopy(MOVIE_DETAIL)
        unplayed_movie['UserData'] = {
            'Played': False,
            'PlayCount': 0,
            'PlaybackPositionTicks': 0,
            'IsFavorite': False,
        }

        session = FakeSession(
            FakeResponse(200, user_data_dto('mov-1', played=True)),
            FakeResponse(200, played_movie),
            FakeResponse(200, user_data_dto('mov-1', played=False)),
            FakeResponse(200, unplayed_movie),
        )
        srv = make_server(session)
        movie = make_tile(srv, MOVIE_DETAIL)
        self.assertFalse(movie.isPlayed)

        movie.markWatched()
        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url']), ('POST', f'{BASE}/UserPlayedItems/mov-1')
        )
        self.assertEqual((session.calls[1]['method'], session.calls[1]['url']), ('GET', f'{BASE}/Items/mov-1'))
        self.assertTrue(movie.isPlayed)
        self.assertEqual(movie.viewOffset.asInt(), 0)

        movie.markUnwatched()
        self.assertEqual(
            (session.calls[2]['method'], session.calls[2]['url']), ('DELETE', f'{BASE}/UserPlayedItems/mov-1')
        )
        self.assertEqual((session.calls[3]['method'], session.calls[3]['url']), ('GET', f'{BASE}/Items/mov-1'))
        self.assertFalse(movie.isPlayed)
        _assert_mediabrowser_only(self, session.calls)

        # Episode markWatched / markUnwatched and whole show markWatched
        ep = episode_detail('ep-1-2', 'sea-1', 1, 2, 'Undertow', played=False, position_ticks=9_000_000_000)
        ep_played = episode_detail('ep-1-2', 'sea-1', 1, 2, 'Undertow', played=True, position_ticks=0)
        ep_unplayed = episode_detail('ep-1-2', 'sea-1', 1, 2, 'Undertow', played=False, position_ticks=0)
        show_played = copy.deepcopy(SERIES_DETAIL)
        show_played['UserData'] = {'UnplayedItemCount': 0, 'Played': True}

        ep_session = FakeSession(
            FakeResponse(200, user_data_dto('ep-1-2', played=True)),
            FakeResponse(200, ep_played),
            FakeResponse(200, user_data_dto('ep-1-2', played=False)),
            FakeResponse(200, ep_unplayed),
            FakeResponse(200, user_data_dto('ser-1', played=True, unplayed=0)),
            FakeResponse(200, show_played),
        )
        ep_srv = make_server(ep_session)
        ep_obj = make_tile(ep_srv, ep)
        ep_obj.markWatched()
        self.assertTrue(ep_obj.isPlayed)
        ep_obj.markUnwatched()
        self.assertFalse(ep_obj.isPlayed)

        show_obj = make_tile(ep_srv, SERIES_DETAIL)
        show_obj.markWatched()
        self.assertTrue(show_obj.isPlayed)
        self.assertEqual(
            [(c['method'], c['url']) for c in ep_session.calls if 'UserPlayedItems' in c['url']],
            [
                ('POST', f'{BASE}/UserPlayedItems/ep-1-2'),
                ('DELETE', f'{BASE}/UserPlayedItems/ep-1-2'),
                ('POST', f'{BASE}/UserPlayedItems/ser-1'),
            ],
        )

        # Error on scrobble returns None from query() cleanly
        err_srv = make_server(FakeSession(problem(500, 'Server Error')))
        self.assertIsNone(err_srv.query('/:/scrobble?key=mov-1&identifier=com.plexapp.plugins.library'))

    def test_season_marks_its_episodes(self):
        watched_season = copy.deepcopy(SEASON_1)
        watched_season['UserData'] = {'UnplayedItemCount': 0, 'Played': True}
        unwatched_season = copy.deepcopy(SEASON_1)
        unwatched_season['UserData'] = {'UnplayedItemCount': 2, 'Played': False}

        session = FakeSession(
            FakeResponse(200, user_data_dto('sea-1', played=True, unplayed=0)),
            FakeResponse(200, watched_season),
            FakeResponse(200, user_data_dto('sea-1', played=False, unplayed=2)),
            FakeResponse(200, unwatched_season),
        )
        srv = make_server(session)
        season = make_tile(srv, SEASON_1)
        self.assertIsInstance(season, video.Season)
        self.assertFalse(season.isPlayed)
        self.assertEqual(season.unViewedLeafCount, 1)

        season.markWatched()
        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url']), ('POST', f'{BASE}/UserPlayedItems/sea-1')
        )
        self.assertEqual((session.calls[1]['method'], session.calls[1]['url']), ('GET', f'{BASE}/Items/sea-1'))
        self.assertTrue(season.isPlayed)
        self.assertTrue(season.isWatched)
        self.assertEqual(season.unViewedLeafCount, 0)
        self.assertEqual(season.viewedLeafCount.asInt(), 2)

        season.markUnwatched()
        self.assertEqual(
            (session.calls[2]['method'], session.calls[2]['url']), ('DELETE', f'{BASE}/UserPlayedItems/sea-1')
        )
        self.assertFalse(season.isPlayed)
        self.assertEqual(season.unViewedLeafCount, 2)
        _assert_mediabrowser_only(self, session.calls)

    def test_reset_resume_point(self):
        # Video.removeFromContinueWatching() -> POST /UserItems/{id}/UserData {"PlaybackPositionTicks": 0} + reload
        reset_movie = copy.deepcopy(MOVIE_DETAIL)
        reset_movie['UserData'] = {'Played': False, 'PlayCount': 0, 'PlaybackPositionTicks': 0, 'IsFavorite': False}

        session = FakeSession(
            FakeResponse(200, user_data_dto('mov-1', played=False, position_ticks=0)),
            FakeResponse(200, reset_movie),
            FakeResponse(200, user_data_dto('mov-1', played=False, position_ticks=0)),
            FakeResponse(200, user_data_dto('mov-1', played=False, position_ticks=600_000_000)),
        )
        srv = make_server(session)
        movie = make_tile(srv, MOVIE_DETAIL)
        self.assertTrue(movie.in_progress)
        self.assertEqual(movie.viewOffset.asInt(), 600_000)

        movie.removeFromContinueWatching()
        self.assertEqual(
            (session.calls[0]['method'], session.calls[0]['url'], session.calls[0]['json']),
            ('POST', f'{BASE}/UserItems/mov-1/UserData', {'PlaybackPositionTicks': 0}),
        )
        self.assertEqual((session.calls[1]['method'], session.calls[1]['url']), ('GET', f'{BASE}/Items/mov-1'))
        self.assertFalse(movie.in_progress)
        self.assertEqual(movie.viewOffset.asInt(), 0)

        # Direct /:/progress?key=mov-1&time=0&state=stopped also resets PlaybackPositionTicks to 0
        srv.query('/:/progress?key=mov-1&time=0&state=stopped')
        self.assertEqual(
            (session.calls[2]['method'], session.calls[2]['url'], session.calls[2]['json']),
            ('POST', f'{BASE}/UserItems/mov-1/UserData', {'PlaybackPositionTicks': 0}),
        )

        # Direct /:/progress with positive time converts ms to ticks (10,000 ticks/ms)
        srv.query('/:/progress?key=mov-1&time=60000&state=paused')
        self.assertEqual(
            (session.calls[3]['method'], session.calls[3]['url'], session.calls[3]['json']),
            ('POST', f'{BASE}/UserItems/mov-1/UserData', {'PlaybackPositionTicks': 600_000_000}),
        )
        _assert_mediabrowser_only(self, session.calls)

        # Missing key makes no server request
        empty_session = FakeSession()
        res = make_server(empty_session).query('/:/progress?time=0&state=stopped')
        self.assertEqual(res.get('size'), '0')
        self.assertEqual(empty_session.calls, [])

    def test_favorite_toggle_and_list(self):
        # 1. Cold isFavorite -> GET /UserItems/{id}/UserData; cached afterwards; itemmenu shows Add/Remove
        cold_session = FakeSession(
            FakeResponse(200, user_data_dto('mov-1', is_favorite=False)),
            FakeResponse(200, user_data_dto('mov-1', is_favorite=True)),
            FakeResponse(200, user_data_dto('mov-1', is_favorite=False)),
        )
        srv = make_server(cold_session)
        movie = make_tile(srv, MOVIE_DETAIL)
        mli = mock.Mock(dataSource=movie, getProperty=lambda _name: '')

        rows = itemmenu.options(mli)
        self.assertIn(('add_favorite', 'Add to Favorites'), rows)
        self.assertEqual(
            (cold_session.calls[0]['method'], cold_session.calls[0]['url']),
            ('GET', f'{BASE}/UserItems/mov-1/UserData'),
        )
        # Cached hit makes no second GET
        self.assertFalse(srv.isFavorite(movie))
        self.assertEqual(len(cold_session.calls), 1)

        # Toggle on via itemmenu.run -> POST /UserFavoriteItems/mov-1, updates cache to True
        window = mock.Mock()
        self.assertTrue(itemmenu.run(window, mli, 'add_favorite'))
        self.assertEqual(
            (cold_session.calls[1]['method'], cold_session.calls[1]['url']),
            ('POST', f'{BASE}/UserFavoriteItems/mov-1'),
        )
        self.assertTrue(srv.isFavorite(movie))
        self.assertIn(('remove_favorite', 'Remove from Favorites'), itemmenu.options(mli))
        self.assertEqual(len(cold_session.calls), 2)

        # Toggle off via itemmenu.run -> DELETE /UserFavoriteItems/mov-1, updates cache to False
        self.assertTrue(itemmenu.run(window, mli, 'remove_favorite'))
        self.assertEqual(
            (cold_session.calls[2]['method'], cold_session.calls[2]['url']),
            ('DELETE', f'{BASE}/UserFavoriteItems/mov-1'),
        )
        self.assertFalse(srv.isFavorite(movie))
        self.assertEqual(len(cold_session.calls), 3)
        _assert_mediabrowser_only(self, cold_session.calls)

        # 2. Reloading a DTO carrying UserData.IsFavorite warms cache without extra /UserData GET
        fav_detail = copy.deepcopy(MOVIE_DETAIL)
        fav_detail['UserData']['IsFavorite'] = True
        warm_session = FakeSession(FakeResponse(200, fav_detail))
        warm_srv = make_server(warm_session)
        reloaded = make_tile(warm_srv, fav_detail).reload()
        self.assertTrue(warm_srv.isFavorite(reloaded))
        self.assertEqual(len(warm_session.calls), 1)  # only GET /Items/mov-1

        # 3. Direct setFavorite return values & error handling
        err_session = FakeSession(problem(503, 'Unavailable'), problem(500, 'Error'))
        err_srv = make_server(err_session)
        err_movie = make_tile(err_srv, MOVIE_DETAIL)
        self.assertIsNone(err_srv.isFavorite(err_movie))
        self.assertIsNone(err_srv.setFavorite(err_movie, True))
        self.assertIsNone(err_srv.isFavorite(mock.Mock(ratingKey='')))
        self.assertIsNone(err_srv.setFavorite(mock.Mock(ratingKey=''), True))

        # 4. Home Favorites row + See more paging via hub.key and /jellyfin/favorites
        fav_movie = movie_dto('mov-1', 'Twisters')
        fav_movie['UserData']['IsFavorite'] = True
        fav_movie_2 = movie_dto('mov-2', 'Arrival')
        fav_movie_2['UserData']['IsFavorite'] = True
        home_session = FakeSession(
            display_prefs({'homesection0': 'latestmedia'}),
            user_me(),
            user_views(DEFAULT_VIEWS[0]),
            FakeResponse(200, [movie_dto('m9', 'Dune')]),
            query_result([fav_movie], total=2),
            # See more via hub.key
            query_result([fav_movie, fav_movie_2], total=2),
            # Direct /jellyfin/favorites route with offset/limit
            query_result([fav_movie_2], total=2, start=1),
        )
        home_srv = make_server(home_session)

        hubs = home_srv.hubs(None, count=1)
        self.assertEqual([h.title for h in hubs], ['Latest Movies', 'Favorites'])
        fav_hub = hubs[1]
        self.assertTrue(fav_hub.more.asBool())
        self.assertEqual([i.title for i in fav_hub.items], ['Twisters'])
        fav_call = home_session.calls[4]
        self.assertEqual(fav_call['url'], f'{BASE}/Items')
        self.assertEqual(fav_call['params']['isFavorite'], 'true')
        self.assertEqual(fav_call['params']['recursive'], 'true')
        self.assertEqual(fav_call['params']['limit'], 1)
        # Cached from favoritesRow DTO
        self.assertTrue(home_srv.isFavorite(fav_hub.items[0]))

        # See more via hub.key lists all favorites
        see_more = home_srv.query(fav_hub.key, offset=0, limit=50)
        self.assertEqual([e.get('title') for e in see_more], ['Twisters', 'Arrival'])
        self.assertEqual(see_more.get('totalSize'), '2')

        # Direct /jellyfin/favorites route pages properly
        direct_favs = home_srv.query('/jellyfin/favorites', offset=1, limit=1)
        self.assertEqual([e.get('title') for e in direct_favs], ['Arrival'])
        self.assertEqual(direct_favs.get('totalSize'), '2')
        self.assertEqual(home_session.calls[6]['params']['isFavorite'], 'true')
        self.assertEqual(home_session.calls[6]['params']['startIndex'], 1)
        self.assertEqual(home_session.calls[6]['params']['limit'], 1)
        _assert_mediabrowser_only(self, home_session.calls)

        # Empty or failing Favorites omits the Favorites row cleanly
        no_fav_session = FakeSession(
            display_prefs({'homesection0': 'latestmedia'}),
            user_me(),
            user_views(DEFAULT_VIEWS[0]),
            FakeResponse(200, [movie_dto('m9', 'Dune')]),
            query_result([], total=0),
        )
        self.assertEqual([h.title for h in make_server(no_fav_session).hubs(None, count=20)], ['Latest Movies'])

        fail_fav_session = FakeSession(
            display_prefs({'homesection0': 'latestmedia'}),
            user_me(),
            user_views(DEFAULT_VIEWS[0]),
            FakeResponse(200, [movie_dto('m9', 'Dune')]),
            problem(503, 'Service Unavailable'),
        )
        self.assertEqual([h.title for h in make_server(fail_fav_session).hubs(None, count=20)], ['Latest Movies'])
