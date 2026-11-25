"""
Playing from Silo (playback protocol v3: silo-server's docs/architecture/playback-protocol-v3.md and
docs/playback-api.md): the player's PlexPlayer.build() starts a Silo attempt and plays the plan's stream - for nearly
every file the original, which Kodi plays and seeks itself (decision D11) - and the player's timelines become the
attempt's progress and stop. Responses are written inline in the shape of /api/v2/openapi.json's PlaybackDecision
(Silo build-2987).
"""

import re
from unittest import mock

from plexnet import nowplayingmanager, plexplayer, serverdecision, util

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile

BASE = 'http://silo.lan:8090/api/v2'
CAPABILITIES = {'installation_id': 'inst-1', 'protocol_versions': [3], 'state': 'available', 'allowed': True}


def decision(delivery='original_http', url='/api/v2/stream/S1?st=t', start=0.0):
    return {
        'protocol_version': 3,
        'server_features': ['playback_plan_v3'],
        'outcome': 'playable',
        'session_id': 'S1',
        'playback_plan': {
            'protocol_version': 3,
            'delivery': delivery,
            'stream': {
                'url': url,
                'protocol': 'http_progressive',
                'container': 'mkv',
                'headers': {},
                'header_refresh': 'none',
            },
            'timeline': {
                'source_start_seconds': start,
                'stream_origin_seconds': 0,
                'player_start_seconds': start,
                'timeline_offset_seconds': 0,
                'can_seek_anywhere': True,
                'seek_restoration': 'player_position',
            },
            'selected_tracks': {'audio': {'id': 'file:241:audio:0', 'index': 0}},
            'decision_reason': 'validated_original_playback',
        },
    }


def ok(body, status=200):
    return FakeResponse(status, body)


# the played file's markers and chapters (silonet.watch), none
NO_WATCH = FakeResponse(200, {'versions': []})


class SiloPlayTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def built(self, *responses, offset=0):
        session = FakeSession(ok(MOVIE), ok(CAPABILITIES), *responses, NO_WATCH)
        srv = server(session)
        movie = tile(srv, MOVIE).reload()
        player = plexplayer.PlexPlayer(movie, offset)
        player.build()
        self.session, self.server = session, srv
        return player

    def test_a_watch_party_plays_the_rooms_file(self):
        # D26: every member plays the room's file, and Silo keeps to it (allow_alternate_versions false)
        session = FakeSession(ok(MOVIE), ok(CAPABILITIES), ok(decision(), 201), NO_WATCH)
        srv = server(session)
        movie = tile(srv, MOVIE).reload()
        srv.pinnedFiles[str(movie.ratingKey)] = 777

        plexplayer.PlexPlayer(movie, 0).build()

        body = session.calls[2]['json']
        self.assertEqual((body['file_id'], body['allow_alternate_versions']), ('777', False))

    def test_a_movie_plays_its_original(self):
        player = self.built(ok(decision(), 201))

        start = self.session.calls[2]
        self.assertEqual(
            (self.session.calls[1]['url'], start['url']), (BASE + '/playback/capabilities', BASE + '/playback/start')
        )
        body = start['json']
        self.assertEqual(
            (body['installation_id'], body['protocol_version'], body['file_id'], body['profile_id']),
            ('inst-1', 3, '241', 'p1'),
        )
        self.assertEqual(
            (body['progress_persistence'], body['quality_preference'], body['subtitle_fidelity_preference']),
            ('server', 'auto', 'preserve'),
        )
        self.assertRegex(body['playback_attempt_id'], r'^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$')
        caps = body['client_capabilities']
        self.assertEqual(caps['video_evidence'], 'declared')
        self.assertTrue({'hevc', 'h264'} <= set(caps['codecs_video']))
        self.assertTrue({'truehd', 'dts', 'eac3'} <= set(caps['codecs_audio']))
        self.assertIn('mkv', caps['containers'])
        original = body['client_playback_context']['deliveries']['original_http']
        self.assertTrue(original['enabled'] and original['supported_on_device'])
        self.assertEqual(body['audio_track_index'], 0)
        # Kodi renders the file's own subtitles; Silo's subtitle index is another numbering (protocol section 8)
        self.assertNotIn('subtitle_track_index', body)

        meta = player.metadata
        self.assertEqual(meta.streamUrls, ['http://silo.lan:8090/api/v2/stream/S1?st=t'])
        self.assertFalse(meta.isTranscoded)
        self.assertEqual(meta.playStart, 0)
        self.assertIs(player.getServerDecision(), player)

    def built_with(self, quality=16, disableHdr=False):
        session = FakeSession(ok(MOVIE), ok(CAPABILITIES), ok(decision(), 201), NO_WATCH)
        movie = tile(server(session), MOVIE).reload()
        settings = type(movie.settings)
        preference = settings.getPreference

        def getPreference(self, name, default=None):
            return disableHdr if name == 'disable_hdr' else preference(self, name, default)

        with (
            mock.patch.object(settings, 'getQualityIndex', lambda self, kind: quality),
            mock.patch.object(settings, 'getPreference', getPreference),
        ):
            plexplayer.PlexPlayer(movie, 0).build()
        return session.calls[2]['json']

    def test_original_quality_has_no_cap(self):
        body = self.built_with(16)

        self.assertEqual(body['quality_preference'], 'auto')
        self.assertNotIn('bandwidth_cap_kbps', body)

    def test_a_lower_quality_is_a_bitrate_cap(self):
        body = self.built_with(7)  # 3 Mbps on Uno's ladder

        self.assertEqual((body['quality_preference'], body['bandwidth_cap_kbps']), ('auto', 3000))

    def test_hdr_turned_off_asks_for_sdr(self):
        body = self.built_with(disableHdr=True)

        caps = body['client_capabilities']
        self.assertFalse(caps['hdr'])
        self.assertEqual(caps['hdr_details']['dolby_vision_profiles'], [])
        self.assertFalse(caps['hdr_details']['hdr10'])

    def test_resuming_starts_where_it_was(self):
        player = self.built(ok(decision(start=596.5), 201), offset=600000)

        self.assertAlmostEqual(self.session.calls[2]['json']['start_position'], 596.5)
        self.assertEqual(player.metadata.playStart, 596)

    def test_a_transcode_with_the_whole_timeline_seeks_as_the_original_does(self):
        player = self.built(ok(decision('server_transcode_hls', '/api/v2/playback/transcode/S1/master.m3u8'), 201))

        self.assertFalse(player.metadata.isTranscoded)
        self.assertEqual(player.metadata.streamUrls, ['http://silo.lan:8090/api/v2/playback/transcode/S1/master.m3u8'])

    def test_a_growing_window_plays_as_a_transcode(self):
        growing = decision('server_remux_hls', '/api/v2/playback/transcode/S1/master.m3u8')
        growing['playback_plan']['timeline'].update(can_seek_anywhere=False, stream_origin_seconds=598.0)

        player = self.built(ok(growing, 201))

        self.assertTrue(player.metadata.isTranscoded)

    def test_a_refusal_says_why(self):
        refusal = {
            'protocol_version': 3,
            'server_features': [],
            'outcome': 'terminal',
            'terminal': {'reason': 'stream_limit_reached', 'message': 'Too many streams', 'retryable': True},
        }

        with self.assertRaises(serverdecision.DecisionFailure) as raised:
            self.built(ok(refusal, 201))

        self.assertEqual(raised.exception.reason, 'Too many streams')

    def test_a_new_installation_is_asked_for_again(self):
        player = self.built(
            problem(409, 'installation_changed'), ok(dict(CAPABILITIES, installation_id='inst-2')), ok(decision(), 201)
        )

        self.assertEqual(self.session.calls[4]['json']['installation_id'], 'inst-2')
        self.assertEqual(player.metadata.streamUrls, ['http://silo.lan:8090/api/v2/stream/S1?st=t'])


class SiloProgressTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()
        session = FakeSession(ok(MOVIE), ok(CAPABILITIES), ok(decision(), 201), NO_WATCH)
        self.server = server(session)
        movie = tile(self.server, MOVIE).reload()
        plexplayer.PlexPlayer(movie, 0).build()
        self.session = session

    def timeline(self, state, ms):
        self.server.reportTimeline({'ratingKey': 'movie-tmdb-718821', 'state': state, 'time': ms})

    def test_playing_and_paused_are_progress(self):
        self.session.queue(ok({'outcome': 'applied'}), ok({'outcome': 'applied'}))

        self.timeline('playing', 60000)
        self.timeline('paused', 61500)

        self.assertEqual([c['url'] for c in self.session.calls[4:]], [BASE + '/playback/S1/progress'] * 2)
        self.assertEqual(
            [c['json'] for c in self.session.calls[4:]],
            [
                {'installation_id': 'inst-1', 'sequence': 1, 'position': 60.0, 'is_paused': False},
                {'installation_id': 'inst-1', 'sequence': 2, 'position': 61.5, 'is_paused': True},
            ],
        )

    def test_stopped_stops_the_attempt_once(self):
        self.session.queue(ok({'outcome': 'stopped'}))

        self.timeline('stopped', 90000)
        self.timeline('stopped', 90000)

        self.assertEqual(len(self.session.calls), 5)
        stop = self.session.calls[4]
        self.assertEqual((stop['method'], stop['url']), ('DELETE', BASE + '/playback/S1'))
        self.assertTrue(
            re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', stop['json']['stop_id'])
        )
        self.assertEqual(
            (stop['json']['position'], stop['json']['sequence'], stop['json']['is_paused']), (90.0, 1, False)
        )

    def test_an_ended_session_is_let_go(self):
        self.session.queue(problem(404, 'not_found'))

        self.timeline('playing', 60000)
        self.timeline('playing', 75000)

        self.assertEqual(len(self.session.calls), 5)  # the 404'd progress; nothing after


class SiloTimelinesTest(KodiTestCase):
    """The player's timelines (nowplayingmanager) go to a Silo server's attempt, not to Plex's /:/timeline."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_a_timeline_is_progress(self):
        session = FakeSession(ok(MOVIE), ok(CAPABILITIES), ok(decision(), 201), NO_WATCH, ok({'outcome': 'applied'}))
        srv = server(session)
        movie = tile(srv, MOVIE).reload()
        plexplayer.PlexPlayer(movie, 0).build()
        manager = nowplayingmanager.NowPlayingManager()
        item = util.AttributeDict(ratingKey='movie-tmdb-718821', duration=7344000, key=movie.key)

        manager.updatePlaybackState('video', item, 'playing', 42000, server=srv)
        manager.waitForTimelines(5)

        self.assertEqual(session.calls[4]['url'], BASE + '/playback/S1/progress')
        self.assertEqual(session.calls[4]['json']['position'], 42.0)

    def test_the_worker_ends_when_idle(self):
        """The add-on's exit waits for every thread (lib/main.py waitForThreads()): an idle worker that never ended
        made it hard-exit, and Kodi then reopened the half-closed add-on (seen on the box, 2026-11-24)."""
        import threading
        import time

        session = FakeSession(ok(MOVIE), ok(CAPABILITIES), ok(decision(), 201), NO_WATCH, ok({'outcome': 'applied'}))
        srv = server(session)
        movie = tile(srv, MOVIE).reload()
        plexplayer.PlexPlayer(movie, 0).build()
        manager = nowplayingmanager.NowPlayingManager()
        item = util.AttributeDict(ratingKey='movie-tmdb-718821', duration=7344000, key=movie.key)
        manager.updatePlaybackState('video', item, 'playing', 42000, server=srv)
        manager.waitForTimelines(5)

        deadline = time.time() + 5
        while time.time() < deadline and any(t.name == 'silo.timelines' for t in threading.enumerate()):
            time.sleep(0.1)

        self.assertFalse(any(t.name == 'silo.timelines' for t in threading.enumerate()))


class SiloPlayerSeamsTest(KodiTestCase):
    """What the player's seek dialog asks of the item's server: its signals (the timeline and stream-terminated
    callbacks it subscribes), and for the playback info panel a Plex transcode session, which Silo has none of."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_signals(self):
        srv = server(FakeSession())
        heard = []

        def callback(**kwargs):
            heard.append(kwargs)

        srv.on('np:timelineResponse', callback)
        srv.trigger('np:timelineResponse', response='r')
        srv.off('np:timelineResponse', callback)
        srv.trigger('np:timelineResponse', response='again')

        self.assertEqual(heard, [{'response': 'r'}])

    def test_no_transcode_session(self):
        from plexnet import exceptions

        srv = server(FakeSession())

        with self.assertRaises(exceptions.NotFound):
            srv.findVideoSession('session', 'movie-tmdb-718821')
        self.assertEqual(list(srv.sessions), [])

    def test_a_stream_urls_token_is_kept_out_of_the_log(self):
        cleaned = util.cleanToken('http://silo.lan:8090/api/v2/stream/S1?st=eyJhbGci.secret&seek=5')

        self.assertEqual(cleaned, 'http://silo.lan:8090/api/v2/stream/S1?st=****&seek=5')
