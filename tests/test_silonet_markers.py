"""
Skip markers and chapters from Silo (decision D16): when an item plays, the played file's markers - intro, recap,
credits and preview - and chapters come from GET /api/v2/watch/{id} (WatchFileVersion), as the Plex <Marker> and
<Chapter> elements Plex Uno's player reads; a credits or preview marker that runs to the end is the final one, whose
skip ends the video. The player's skip button and auto-skip know all four. Responses are written inline in the shape
of /api/v2/openapi.json's WatchDetail (Silo build-2987).

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from plexnet import plexplayer

from lib.windows import seekdialog

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile
from .test_silonet_playback import CAPABILITIES, decision

BASE = 'http://silo.lan:8090/api/v2'

WATCH = {
    'content_id': 'movie-tmdb-718821',
    'type': 'movie',
    'title': 'Twisters',
    'subtitles': [],
    'versions': [
        {
            'file_id': '241',
            'duration_seconds': 7344,
            'intro': {'start_seconds': 30.0, 'end_seconds': 95.5},
            'recap': {'start_seconds': 0.0, 'end_seconds': 29.0},
            'credits': {'start_seconds': 7000.0, 'end_seconds': 7342.0},
            'chapters': [
                {
                    'index': 0,
                    'title': 'Opening',
                    'start_seconds': 0,
                    'end_seconds': 600,
                    'source': 'file',
                    'thumbnail_url': '/api/v2/artwork/chapters/241/0',
                },
                {'index': 1, 'title': 'The Storm', 'start_seconds': 600, 'end_seconds': 1800, 'source': 'file'},
            ],
        }
    ],
}


def ok(body, status=200):
    return FakeResponse(status, body)


class SiloMarkersTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def played(self, watch):
        session = FakeSession(ok(MOVIE), ok(CAPABILITIES), ok(decision(), 201), watch)
        movie = tile(server(session), MOVIE).reload()
        plexplayer.PlexPlayer(movie, 0).build()
        self.session = session
        return movie

    def test_the_played_files_markers(self):
        movie = self.played(ok(WATCH))

        self.assertEqual(self.session.calls[3]['url'], BASE + '/watch/movie-tmdb-718821')
        self.assertEqual(
            [(m.type, m.startTimeOffset.asInt(), m.endTimeOffset.asInt(), m.final.asBool()) for m in movie.markers],
            [('intro', 30000, 95500, False), ('recap', 0, 29000, False), ('credits', 7000000, 7342000, True)],
        )

    def test_its_chapters(self):
        movie = self.played(ok(WATCH))

        self.assertEqual([(c.tag, c.startTime()) for c in movie.chapters], [('Opening', 0), ('The Storm', 600000)])
        self.assertEqual(movie.chapters[0].thumb, '/api/v2/artwork/chapters/241/0')

    def test_none_when_silo_cant_say(self):
        movie = self.played(problem(503, 'dependency_unavailable'))

        self.assertEqual(list(movie.markers), [])

    def test_the_skip_button_knows_every_kind(self):
        movie = self.played(ok(WATCH))
        dialog = seekdialog.SeekDialog.__new__(seekdialog.SeekDialog)
        dialog._enableMarkerSkip = True
        dialog._markers = None
        dialog.handler = mock.Mock()
        dialog.handler.player.video = movie
        with mock.patch.object(type(dialog), 'duration', 7344000, create=True):
            defs = dialog.markers

        self.assertEqual([d['marker_type'] for d in defs], ['intro', 'recap', 'credits'])
        self.assertEqual([str(d['name']) for d in defs][1], 'Skip recap')
