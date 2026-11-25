"""
Subtitles from Silo beyond the file's own (decision D14): a subtitle file stored beside the video (an external track)
plays from the URL the attempt's plan lists for it (playback protocol v3, section 8: a client never composes one), and
Plex Uno's subtitle download dialog searches and downloads through Silo (POST /api/v2/subtitles/search and
/download), then shows the new subtitle from the attempt's refreshed plan (a track_change replan). Responses are
written inline in the shape of /api/v2/openapi.json (Silo build-2987).
"""

import copy

from plexnet import plexplayer

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession
from .test_silonet_detail import MOVIE, server, tile
from .test_silonet_playback import CAPABILITIES, NO_WATCH, decision

BASE = 'http://silo.lan:8090/api/v2'
HOST = 'http://silo.lan:8090'


def ok(body, status=200):
    return FakeResponse(status, body)


def withExternal():
    detail = copy.deepcopy(MOVIE)
    detail['versions'][0]['subtitle_tracks'] = [
        {'index': 7, 'language': 'de', 'codec': 'subrip', 'title': 'German (file)', 'external': True},
        {'index': 4, 'language': 'en', 'codec': 'subrip', 'title': 'English', 'external': False},
    ]
    return detail


def planWith(*entries):
    answer = decision()
    answer['playback_plan']['plan_id'] = 'plan:1'
    answer['playback_plan']['plan_attempt_key'] = 'v3:1'
    answer['playback_plan']['subtitle'] = {'mode': 'off', 'inventory': list(entries)}
    return answer


EXTERNAL = {
    'track_id': 'file:241:subtitle:0',
    'combined_index': 0,
    'source': 'external',
    'delivery': 'sidecar',
    'language': 'deu',
    'url': '/api/v2/stream/S1/subtitles/0.vtt?file_id=241&external_subtitle_key=k1',
}
EMBEDDED = {
    'track_id': 'file:241:subtitle:1',
    'combined_index': 1,
    'source': 'embedded',
    'delivery': 'sidecar',
    'stream_index': 4,
    'url': '/api/v2/stream/S1/subtitles/1.vtt?file_id=241&embedded_stream_index=4',
}
DOWNLOADED = {
    'track_id': 'file:241:subtitle:2',
    'combined_index': 2,
    'source': 'downloaded',
    'delivery': 'sidecar',
    'url': '/api/v2/stream/S1/subtitles/2.vtt?file_id=241&downloaded_subtitle_id=st1',
}


RESULT = {
    'id': 'r1',
    'provider': 'opensubtitles',
    'language': 'en',
    'release_name': 'Twisters.2024.1080p',
    'format': 'srt',
    'score': 92.5,
    'downloads': 10,
    'hearing_impaired': False,
}


class SiloSubtitleFilesTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_a_subtitle_file_beside_the_video_plays_from_the_plan(self):
        session = FakeSession(ok(withExternal()), ok(CAPABILITIES), ok(planWith(EXTERNAL, EMBEDDED), 201), NO_WATCH)
        movie = tile(server(session), withExternal()).reload()
        plexplayer.PlexPlayer(movie, 0).build()

        subtitles = movie.subtitleStreams

        self.assertEqual([s.title for s in subtitles], ['English', 'German (file)'])  # Kodi's own tracks first
        self.assertEqual(subtitles[0].typeIndex, 0)
        self.assertEqual(subtitles[1].getSubtitleServerPath(), HOST + EXTERNAL['url'])


class SiloSubtitleSearchTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def movie(self, *responses):
        session = FakeSession(ok(MOVIE), *responses)
        movie = tile(server(session), MOVIE).reload()
        self.session = session
        return movie

    def test_search(self):
        movie = self.movie(ok({'results': [RESULT], 'warnings': []}))

        found = movie.findSubtitles(language='en')

        call = self.session.calls[1]
        self.assertEqual(
            (call['method'], call['url'], call['json']),
            ('POST', BASE + '/subtitles/search', {'media_file_id': '241', 'languages': ['en']}),
        )
        self.assertEqual(
            [(f.providerTitle, f.title, f.score.asInt(), f.downloaded.asBool()) for f in found],
            [('opensubtitles', 'Twisters.2024.1080p', 92, False)],
        )

    def test_a_download_shows_from_the_refreshed_plan(self):
        stored = {
            'subtitle': {
                'id': 'st1',
                'media_file_id': '241',
                'provider': 'opensubtitles',
                'language': 'en',
                'format': 'srt',
                'release_name': 'Twisters.2024.1080p',
                'score': 92.5,
                'hearing_impaired': False,
                'created_at': '2026-11-24T00:00:00.000Z',
                'timing': {},
            }
        }
        movie = self.movie(
            ok({'results': [RESULT], 'warnings': []}),
            ok(CAPABILITIES),
            ok(planWith(), 201),
            NO_WATCH,
            ok(stored),
            ok(planWith(DOWNLOADED)),
        )
        found = movie.findSubtitles(language='en')
        plexplayer.PlexPlayer(movie, 0).build()

        stream = movie.downloadSubtitles(found[0].key)

        download, replan = self.session.calls[5], self.session.calls[6]
        self.assertEqual(
            (download['url'], download['json']['provider'], download['json']['subtitle_id']),
            (BASE + '/subtitles/download', 'opensubtitles', 'r1'),
        )
        self.assertEqual((replan['url'], replan['json']['operation']), (BASE + '/playback/S1/replan', 'track_change'))
        self.assertTrue(stream.isSelected())
        self.assertEqual(stream.getSubtitleServerPath(), HOST + DOWNLOADED['url'])


class SubtitleStreamIdsTest(KodiTestCase):
    def test_subtitle_files_without_an_index_keep_apart(self):
        # Silo's index is optional on a track (VersionSubtitleTrack): files beside the video may have none
        from silonet import detail

        version = {
            'file_id': '241',
            'subtitle_tracks': [
                {'index': 2, 'language': 'de', 'external': False},
                {'language': 'en', 'external': True},
                {'language': 'fr', 'external': True},
            ],
        }

        streams = [s for s in detail.mediaElement(version).iter('Stream') if s.get('streamType') == '3']

        self.assertEqual(len({s.get('id') for s in streams}), 3)
        self.assertEqual(
            [s.get('key') for s in streams],
            [None, '/silo/subtitles/241/external/0', '/silo/subtitles/241/external/1'],
        )
