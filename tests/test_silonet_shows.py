"""
A show, its seasons and their episodes from Silo: a series' reload() from GET /api/v2/catalog/items/{id} (with what's
on deck, its play_content_id, when asked), a show's seasons (its key, /library/metadata/<series id>/children) from
/catalog/series/{id}/seasons, a season's episodes (/library/metadata/<season id>/children) from
/catalog/items/{season id}/episodes, a show's every episode (allLeaves) season by season, and an episode's reload()
with its season (/catalog/series/{id}/seasons/{num}) and show. Responses are written inline in the shape of
/api/v2/openapi.json's CatalogItemDetail, SeasonCollection and EpisodeCollection (Silo build-2987).
"""

import copy

from plexnet import plexobjects, video

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import MOVIE, person, server, tile

BASE = 'http://silo.lan:8090/api/v2'

SERIES = {
    'content_id': 'series-tvdb-1',
    'type': 'series',
    'title': 'Harbor Lights',
    'year': 2019,
    'overview': 'A lighthouse keeper finds a ship that left a century ago.',
    'content_rating': 'TV-14',
    'genres': ['Drama', 'Mystery'],
    'studios': ['North Studio'],
    'networks': ['Coast TV'],
    'first_air_date': '2019-03-01',
    'season_count': 2,
    'rating_imdb': 8.1,
    'poster_url': '/api/v2/artwork/s/poster',
    'backdrop_url': '/api/v2/artwork/s/backdrop',
    'play_content_id': 'episode-tvdb-1-1-2',
    'play_season_number': 1,
    'cast': [person('Ana Reyes', 'p1', character='Mara', order=0)],
    'crew': [person('Tom Hale', 'p2', job='Creator')],
    'user_state': {'played': False},
    'user_data': {'watched_count': 1, 'unplayed_count': 3, 'in_progress_count': 1, 'played': False},
}

SEASON_ROW_1 = {
    'content_id': 'season-tvdb-1-1',
    'play_content_id': 'episode-tvdb-1-1-2',
    'season_number': 1,
    'title': 'Season 1',
    'overview': 'The ship returns.',
    'air_date': '2019-03-01',
    'episode_count': 2,
    'poster_url': '/api/v2/artwork/s/season1',
    'user_data': {'watched_count': 1, 'unplayed_count': 1, 'in_progress_count': 1, 'played': False},
}
SEASON_ROW_2 = {
    'content_id': 'season-tvdb-1-2',
    'season_number': 2,
    'title': 'Season 2',
    'air_date': '2020-03-01',
    'episode_count': 2,
    'poster_url': '/api/v2/artwork/s/season2',
    'user_data': {'watched_count': 0, 'unplayed_count': 2, 'in_progress_count': 0, 'played': False},
}
SEASONS = {'items': [SEASON_ROW_1, SEASON_ROW_2], 'page': None}

SEASON_1 = dict(
    SEASON_ROW_1,
    type='season',
    series_id='series-tvdb-1',
    series_title='Harbor Lights',
    backdrop_url='/api/v2/artwork/s/backdrop',
    user_state={'played': False},
)


def episodeRow(season, number, title, **extra):
    return dict(
        {
            'content_id': f'episode-tvdb-1-{season}-{number}',
            'season_number': season,
            'episode_number': number,
            'title': title,
            'overview': f'{title}.',
            'air_date': f'{2018 + season}-03-0{number}',
            'runtime': 50,
            'still_url': f'/api/v2/artwork/s/{season}/{number}/still',
            'still_is_episode_still': True,
            'files': [
                {
                    'file_id': f'{season}{number}',
                    'resolution': '1080p',
                    'codec_video': 'h264',
                    'hdr': False,
                    'audio_channels': 6,
                    'container': 'mkv',
                    'file_size': 1000,
                }
            ],
            'user_data': {'watched_count': 0, 'unplayed_count': 1, 'in_progress_count': 0, 'played': False},
        },
        **extra,
    )


EPISODES_1 = {
    'items': [
        episodeRow(
            1, 1, 'Arrival', user_data={'watched_count': 1, 'unplayed_count': 0, 'in_progress_count': 0, 'played': True}
        ),
        episodeRow(
            1,
            2,
            'Undertow',
            user_data={
                'watched_count': 0,
                'unplayed_count': 1,
                'in_progress_count': 1,
                'played': False,
                'is_in_progress': True,
                'position_seconds': 900,
                'duration_seconds': 3000,
            },
        ),
    ],
    'page': None,
}
EPISODES_2 = {'items': [episodeRow(2, 1, 'Low Tide'), episodeRow(2, 2, 'Fog')], 'page': None}

EPISODE_2 = {
    'content_id': 'episode-tvdb-1-1-2',
    'type': 'episode',
    'title': 'Undertow',
    'series_id': 'series-tvdb-1',
    'series_title': 'Harbor Lights',
    'season_number': 1,
    'episode_number': 2,
    'runtime': 50,
    'overview': 'Mara dives.',
    'air_date': '2019-03-02',
    'poster_url': '/api/v2/artwork/s/1/2/still',
    'poster_is_episode_still': True,
    'backdrop_url': '/api/v2/artwork/s/backdrop',
    'series_poster_url': '/api/v2/artwork/s/poster',
    'series_backdrop_url': '/api/v2/artwork/s/backdrop',
    'season_poster_url': '/api/v2/artwork/s/season1',
    'position_seconds': 900,
    'user_state': {'played': False},
    'cast': [person('Ana Reyes', 'p1', character='Mara', order=0)],
    'crew': [person('Jo Park', 'p3', job='Director')],
    'versions': copy.deepcopy(MOVIE['versions']),
}


def ok(body):
    return FakeResponse(200, body)


class SiloShowTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_a_shows_screen(self):
        session = FakeSession(ok(SERIES))

        show = tile(server(session), SERIES).reload(includeExtras=1, includeExtrasCount=10)

        self.assertIsInstance(show, video.Show)
        self.assertFalse(show.reloadFailed)
        self.assertEqual(session.calls[0]['url'], BASE + '/catalog/items/series-tvdb-1')
        self.assertEqual(
            (show.title, show.year.asInt(), show.summary, show.contentRating, show.studio, show.originallyAvailableAt),
            ('Harbor Lights', 2019, SERIES['overview'], 'TV-14', 'Coast TV', '2019-03-01'),
        )
        self.assertEqual((show.childCount.asInt(), show.leafCount.asInt(), show.viewedLeafCount.asInt()), (2, 4, 1))
        self.assertEqual([g.tag for g in show.genres()], ['Drama', 'Mystery'])
        self.assertEqual([(r.tag, r.role) for r in show.roles], [('Ana Reyes', 'Mara')])
        self.assertEqual([str(r.image) for r in show.ratings], ['imdb://image.rating'])
        self.assertEqual(len(session.calls), 1)  # on deck wasn't asked for

    def test_whats_on_deck(self):
        session = FakeSession(ok(SERIES), ok(EPISODE_2))

        show = tile(server(session), SERIES).reload(includeExtras=1, includeOnDeck=1)

        self.assertEqual(session.calls[1]['url'], BASE + '/catalog/items/episode-tvdb-1-1-2')
        deck = show.onDeck[0]
        self.assertEqual(
            (deck.ratingKey, deck.index.asInt(), deck.parentIndex.asInt(), deck.viewOffset.asInt()),
            ('episode-tvdb-1-1-2', 2, 1, 900000),
        )

    def test_on_deck_silo_cant_answer_leaves_the_show(self):
        session = FakeSession(ok(SERIES), problem(404, 'not_found'))

        show = tile(server(session), SERIES).reload(includeOnDeck=1)

        self.assertFalse(show.reloadFailed)
        self.assertEqual(list(show.onDeck), [])

    def test_its_seasons(self):
        session = FakeSession(ok(SERIES), ok(SEASONS))

        seasons = tile(server(session), SERIES).seasons()

        self.assertEqual(
            [c['url'] for c in session.calls],
            [BASE + '/catalog/items/series-tvdb-1', BASE + '/catalog/series/series-tvdb-1/seasons'],
        )
        self.assertTrue(all(isinstance(s, video.Season) for s in seasons))
        self.assertEqual(
            [(s.ratingKey, s.index.asInt(), s.title, s.leafCount.asInt(), s.viewedLeafCount.asInt()) for s in seasons],
            [('season-tvdb-1-1', 1, 'Season 1', 2, 1), ('season-tvdb-1-2', 2, 'Season 2', 2, 0)],
        )
        first = seasons[0]
        self.assertEqual(
            (first.thumb, first.parentRatingKey, first.parentTitle, first.summary),
            ('/api/v2/artwork/s/season1', 'series-tvdb-1', 'Harbor Lights', 'The ship returns.'),
        )

    def test_a_movie_has_no_children(self):
        session = FakeSession(ok(MOVIE))

        answer = server(session).query('/library/metadata/movie-tmdb-718821/children')

        self.assertEqual(len(answer), 0)


class SiloEpisodesTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_a_seasons_episodes(self):
        session = FakeSession(ok(SEASON_1), ok(EPISODES_1))

        episodes = tile(server(session), SEASON_1).episodes()

        self.assertEqual(
            [c['url'] for c in session.calls],
            [BASE + '/catalog/items/season-tvdb-1-1', BASE + '/catalog/items/season-tvdb-1-1/episodes'],
        )
        self.assertEqual(
            [(e.index.asInt(), e.title, e.isPlayed, e.viewOffset.asInt()) for e in episodes],
            [(1, 'Arrival', True, 0), (2, 'Undertow', False, 900000)],
        )
        second = episodes[1]
        self.assertEqual(
            (second.thumb, second.parentIndex.asInt(), second.parentRatingKey, second.parentTitle),
            ('/api/v2/artwork/s/1/2/still', 1, 'season-tvdb-1-1', 'Season 1'),
        )
        self.assertEqual((second.grandparentRatingKey, second.grandparentTitle), ('series-tvdb-1', 'Harbor Lights'))
        self.assertEqual((second.duration.asInt(), second.originallyAvailableAt), (3000000, '2019-03-02'))
        self.assertEqual(second.media[0].videoResolution, '1080')

    def test_every_episode_of_a_show(self):
        session = FakeSession(ok(SERIES), ok(SEASONS), ok(EPISODES_1), ok(EPISODES_2))

        episodes = tile(server(session), SERIES).episodes()

        self.assertEqual(
            [(e.parentIndex.asInt(), e.index.asInt(), e.grandparentTitle) for e in episodes],
            [(1, 1, 'Harbor Lights'), (1, 2, 'Harbor Lights'), (2, 1, 'Harbor Lights'), (2, 2, 'Harbor Lights')],
        )

    def test_a_page_of_a_shows_episodes(self):
        session = FakeSession(ok(SERIES), ok(SEASONS), ok(EPISODES_1), ok(EPISODES_2))

        episodes = tile(server(session), SERIES).episodes(offset=1, limit=2)

        self.assertEqual([(e.parentIndex.asInt(), e.index.asInt()) for e in episodes], [(1, 2), (2, 1)])

    def test_an_episodes_screen(self):
        session = FakeSession(
            ok(EPISODE_2), ok(SEASON_ROW_1), problem(404, 'not_found'), problem(404, 'not_found')
        )  # no remembered tracks

        episode = tile(server(session), EPISODE_2).reload(checkFiles=1, includeChapters=1)

        self.assertFalse(episode.reloadFailed)
        self.assertEqual(session.calls[1]['url'], BASE + '/catalog/series/series-tvdb-1/seasons/1')
        self.assertEqual(
            (episode.title, episode.summary, episode.index.asInt(), episode.parentIndex.asInt()),
            ('Undertow', 'Mara dives.', 2, 1),
        )
        self.assertEqual(
            (episode.parentRatingKey, episode.parentTitle, episode.grandparentRatingKey, episode.grandparentTitle),
            ('season-tvdb-1-1', 'Season 1', 'series-tvdb-1', 'Harbor Lights'),
        )
        self.assertEqual((episode.thumb, episode.originallyAvailableAt), ('/api/v2/artwork/s/1/2/still', '2019-03-02'))
        self.assertEqual([d.tag for d in episode.directors], ['Jo Park'])
        self.assertEqual(episode.media[0].videoResolution, '4k')

    def test_its_season_and_show(self):
        session = FakeSession(
            ok(EPISODE_2),
            ok(SEASON_ROW_1),
            problem(404, 'not_found'),
            problem(404, 'not_found'),
            ok(SEASON_1),
            ok(SERIES),
        )
        episode = tile(server(session), EPISODE_2).reload()

        season, show = episode.season(), episode.show()

        self.assertEqual((season.ratingKey, season.index.asInt()), ('season-tvdb-1-1', 1))
        self.assertEqual((show.ratingKey, show.title), ('series-tvdb-1', 'Harbor Lights'))

    def test_an_episode_whose_season_silo_cant_answer_still_opens(self):
        session = FakeSession(
            ok(EPISODE_2), problem(404, 'not_found'), problem(404, 'not_found'), problem(404, 'not_found')
        )  # no season, no remembered tracks

        episode = tile(server(session), EPISODE_2).reload()

        self.assertFalse(episode.reloadFailed)
        self.assertEqual((episode.title, episode.grandparentTitle), ('Undertow', 'Harbor Lights'))


class SiloEpisodesAtOnceTest(KodiTestCase):
    """EpisodesReloadTask (lib/windows/episodes.py) reloads the episodes in view with one request,
    /library/metadata/<id>,<id>,..., as Plex answers it; Silo has no such request, so each is asked for."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def batch(self, session, ids):
        return plexobjects.listItems(
            server(session),
            '/library/metadata/' + ','.join(ids),
            return_data=True,
            checkFiles=1,
            includeChapters=1,
            includeMarkers=1,
        )

    def test_several_episodes_at_once(self):
        first = dict(EPISODE_2, content_id='episode-tvdb-1-1-1', episode_number=1, title='Arrival')
        session = FakeSession(
            ok(first), ok(SEASON_ROW_1), problem(404, 'not_found'), problem(404, 'not_found'), ok(EPISODE_2)
        )

        data = self.batch(session, ['episode-tvdb-1-1-1', 'episode-tvdb-1-1-2'])

        self.assertEqual(
            [(d.get('ratingKey'), d.get('parentRatingKey')) for d in data],
            [('episode-tvdb-1-1-1', 'season-tvdb-1-1'), ('episode-tvdb-1-1-2', 'season-tvdb-1-1')],
        )
        self.assertEqual(
            [c['url'] for c in session.calls],
            [
                BASE + '/catalog/items/episode-tvdb-1-1-1',
                BASE + '/catalog/series/series-tvdb-1/seasons/1',
                BASE + '/audio-prefs/series-tvdb-1',
                BASE + '/subtitle-prefs/series-tvdb-1',
                BASE + '/catalog/items/episode-tvdb-1-1-2',
            ],
        )

    def test_an_episode_silo_cant_find_among_several_is_left_out(self):
        session = FakeSession(
            problem(404, 'not_found'),
            ok(EPISODE_2),
            ok(SEASON_ROW_1),
            problem(404, 'not_found'),
            problem(404, 'not_found'),
        )

        data = self.batch(session, ['episode-tvdb-1-1-9', 'episode-tvdb-1-1-2'])

        self.assertEqual([d.get('ratingKey') for d in data], ['episode-tvdb-1-1-2'])


class SiloEpisodeTileTest(KodiTestCase):
    """An episode on a Home row names its season by number only (Silo's home items): its season comes from
    /catalog/series/{id}/seasons/{num}, so the episodes screen opened from the tile has a season (on the Kodi box,
    2026-11-24, it raised on a None season)."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_an_episode_from_home_finds_its_season(self):
        homeItem = {
            'content_id': 'episode-tvdb-1-1-2',
            'type': 'episode',
            'title': 'Undertow',
            'series_id': 'series-tvdb-1',
            'series_title': 'Harbor Lights',
            'season_number': 1,
            'episode_number': 2,
        }
        session = FakeSession(ok(SEASON_ROW_1))

        season = tile(server(session), homeItem).season()

        self.assertEqual(session.calls[0]['url'], BASE + '/catalog/series/series-tvdb-1/seasons/1')
        self.assertEqual(
            (season.ratingKey, season.index.asInt(), season.parentRatingKey), ('season-tvdb-1-1', 1, 'series-tvdb-1')
        )
