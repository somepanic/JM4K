"""
A show's remembered audio and subtitle choice, kept by Silo per profile and series (decision D13): an episode's
detail marks the tracks Silo remembers for its show selected (GET /api/v2/audio-prefs/{series_id} and
/api/v2/subtitle-prefs/{series_id}), which Plex Uno's media decision engine then plays, and choosing a track for an
episode saves it there (PUT). Responses are written inline in the shape of /api/v2/openapi.json's AudioPreference
and SubtitlePreference (Silo build-2987).
"""

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile
from .test_silonet_shows import EPISODE_2, SEASON_ROW_1

BASE = 'http://silo.lan:8090/api/v2'
NONE = problem(404, 'not_found')


def ok(body):
    return FakeResponse(200, body)


def audioPref(index, language='en'):
    return ok(
        {
            'profile_id': 'p1',
            'series_id': 'series-tvdb-1',
            'audio_track_index': index,
            'audio_language': language,
            'updated_at': '2026-11-24T00:00:00Z',
        }
    )


def subtitlePref(mode, language='', index=-1):
    return ok(
        {
            'profile_id': 'p1',
            'series_id': 'series-tvdb-1',
            'subtitle_language': language,
            'subtitle_track_index': index,
            'subtitle_mode': mode,
            'updated_at': '2026-11-24T00:00:00Z',
        }
    )


class SiloRememberedTracksTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def episode(self, audio, subtitle):
        session = FakeSession(ok(EPISODE_2), ok(SEASON_ROW_1), audio, subtitle)
        episode = tile(server(session), EPISODE_2).reload()
        self.session = session
        return episode

    def selected(self, episode, kind):
        return [s.title for s in episode.media[0].parts[0].streams if s.streamType.asInt() == kind and s.isSelected()]

    def test_its_shows_remembered_audio_track(self):
        episode = self.episode(audioPref(1), NONE)

        self.assertEqual(
            [c['url'] for c in self.session.calls[2:]],
            [BASE + '/audio-prefs/series-tvdb-1', BASE + '/subtitle-prefs/series-tvdb-1'],
        )
        self.assertEqual(self.selected(episode, 2), ['Commentary'])

    def test_its_shows_remembered_subtitles(self):
        episode = self.episode(NONE, subtitlePref('always', 'fr', 1))

        self.assertEqual(self.selected(episode, 3), ['French'])
        self.assertEqual(self.selected(episode, 2), ['Dolby TrueHD Atmos 7.1'])  # the file's default

    def test_subtitles_remembered_off(self):
        episode = self.episode(NONE, subtitlePref('off'))

        self.assertEqual(self.selected(episode, 3), [])

    def test_nothing_remembered(self):
        episode = self.episode(NONE, NONE)

        self.assertEqual(self.selected(episode, 2), ['Dolby TrueHD Atmos 7.1'])
        self.assertEqual(self.selected(episode, 3), [])


class SiloRememberTracksTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def episode(self, *responses):
        session = FakeSession(ok(EPISODE_2), ok(SEASON_ROW_1), NONE, NONE, *responses)
        episode = tile(server(session), EPISODE_2).reload()
        self.session = session
        return episode

    def streams(self, episode, kind):
        return [s for s in episode.media[0].parts[0].streams if s.streamType.asInt() == kind]

    def test_choosing_an_audio_track_remembers_it_for_the_show(self):
        episode = self.episode(audioPref(1))

        episode.selectStream(self.streams(episode, 2)[1], _async=False)

        put = self.session.calls[4]
        self.assertEqual((put['method'], put['url']), ('PUT', BASE + '/audio-prefs/series-tvdb-1'))
        self.assertEqual((put['json']['audio_track_index'], put['json']['audio_language']), (1, 'eng'))
        self.assertEqual(put['json']['track_signature']['codec'], 'ac3')

    def test_choosing_subtitles_remembers_them(self):
        episode = self.episode(subtitlePref('always', 'fre', 1))

        episode.selectStream(self.streams(episode, 3)[1], _async=False)

        put = self.session.calls[4]
        self.assertEqual((put['method'], put['url']), ('PUT', BASE + '/subtitle-prefs/series-tvdb-1'))
        self.assertEqual(
            (put['json']['subtitle_mode'], put['json']['subtitle_language'], put['json']['track_signature']['forced']),
            ('always', 'fre', True),
        )

    def test_turning_subtitles_off_remembers_that(self):
        from plexnet import plexstream

        episode = self.episode(subtitlePref('off'))

        episode.selectStream(plexstream.NoneStream(), _async=False)

        put = self.session.calls[4]
        self.assertEqual(
            (put['url'], put['json']),
            (BASE + '/subtitle-prefs/series-tvdb-1', {'subtitle_track_index': -1, 'subtitle_mode': 'off'}),
        )

    def test_a_movies_choice_isnt_kept_for_a_show(self):
        session = FakeSession(ok(MOVIE))
        movie = tile(server(session), MOVIE).reload()

        movie.selectStream([s for s in movie.media[0].parts[0].streams if s.streamType.asInt() == 2][1], _async=False)

        self.assertEqual(len(session.calls), 1)


class SubtitleSourceTest(KodiTestCase):
    def test_a_subtitle_file_is_remembered_as_external(self):
        from plexnet import plexstream
        from silonet import detail, trackprefs

        file_ = plexstream.PlexStream(
            detail.element('Stream', {'streamType': 3, 'id': '241.x0', 'key': '/silo/subtitles/241/external/0'})
        )
        own = plexstream.PlexStream(detail.element('Stream', {'streamType': 3, 'id': '241.s2', 'index': 2}))

        self.assertEqual(
            (
                trackprefs.subtitleUpdate(file_, 1)['track_signature']['source'],
                trackprefs.subtitleUpdate(own, 0)['track_signature']['source'],
            ),
            ('external', 'embedded'),
        )
