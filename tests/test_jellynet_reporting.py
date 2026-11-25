"""
Playback progress reporting to Jellyfin (Task 5.2): POST /Sessions/Playing on start,
POST /Sessions/Playing/Progress on playing/paused updates, POST /Sessions/Playing/Stopped on stop,
POST /Sessions/Playing/Ping during active transcodes, millisecond-to-tick conversion (Review Focus 5),
NowPlayingManager's ordered worker thread, findVideoSession(), and error isolation so failed reports
never stop playback or leak tokens.
"""

import threading
import time
from unittest import mock

import requests
from jellynet import playback
from kodienv import ENV
from plexnet import exceptions, nowplayingmanager, plexplayer, util, videosession

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem
from .test_jellynet_playback import BASE, SECRET_TOKEN, make_movie, make_server, playback_info_response


def transcode_info_response(play_session_id='ps-tc'):
    return playback_info_response(
        sources=[
            {
                'Id': 'src-1',
                'Container': 'mkv',
                'TranscodingContainer': 'ts',
                'TranscodingSubProtocol': 'hls',
                'TranscodingUrl': f'/Videos/item-1/master.m3u8?MediaSourceId=src-1&PlaySessionId={play_session_id}',
                'Bitrate': 20_000_000,
                'SupportsDirectPlay': False,
                'SupportsDirectStream': False,
                'SupportsTranscoding': True,
                'DefaultAudioStreamIndex': 1,
                'MediaStreams': [],
            }
        ],
        play_session_id=play_session_id,
    )


class JellyfinReportingTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def _start_movie(self, *responses, info_response=None):
        session = FakeSession(info_response or playback_info_response(), *responses)
        srv = make_server(session, token=SECRET_TOKEN)
        movie = make_movie(srv)
        plexplayer.PlexPlayer(movie, 0).build()
        return session, srv, movie

    def test_start_progress_stop_in_order(self):
        session, srv, movie = self._start_movie(
            FakeResponse(204),
            FakeResponse(204),
            FakeResponse(204),
        )
        # Active session is visible via findVideoSession() and works with VideoSessionInfo.
        vs = srv.findVideoSession('player-session-1', 'item-1')
        info = videosession.VideoSessionInfo(vs, movie, srv.anyLANConnection)
        self.assertEqual(info.details.session.partDecision, 'directplay')

        manager = nowplayingmanager.NowPlayingManager()
        item = util.AttributeDict(ratingKey='item-1', duration=7_200_000, key=movie.key)

        with mock.patch.object(nowplayingmanager.NowPlayingManager, 'SILO_IDLE', 0.05):
            manager.updatePlaybackState('video', item, 'playing', 1000, server=srv)
            manager.updatePlaybackState('video', item, 'playing', 30_000, force=True, server=srv)
            manager.updatePlaybackState('video', item, 'stopped', 90_000, force_time=True, server=srv)
            # Duplicate stopped must not send another /Sessions/Playing/Stopped.
            manager.updatePlaybackState('video', item, 'stopped', 90_000, force=True, force_time=True, server=srv)
            manager.waitForTimelines(5)

            deadline = time.time() + 5
            while time.time() < deadline and any(t.name == 'silo.timelines' for t in threading.enumerate()):
                time.sleep(0.02)

        self.assertFalse(any(t.name == 'silo.timelines' for t in threading.enumerate()))

        report_calls = session.calls[1:]
        self.assertEqual(
            [(c['method'], c['url']) for c in report_calls],
            [
                ('POST', f'{BASE}/Sessions/Playing'),
                ('POST', f'{BASE}/Sessions/Playing/Progress'),
                ('POST', f'{BASE}/Sessions/Playing/Stopped'),
            ],
        )

        start_body, progress_body, stop_body = [c['json'] for c in report_calls]
        self.assertEqual(
            start_body,
            {
                'ItemId': 'item-1',
                'MediaSourceId': 'src-1',
                'PlaySessionId': 'ps-1',
                'PositionTicks': 10_000_000,
                'IsPaused': False,
                'PlayMethod': 'DirectPlay',
                'CanSeek': True,
                'AudioStreamIndex': 1,
            },
        )
        self.assertEqual(
            progress_body,
            {
                'ItemId': 'item-1',
                'MediaSourceId': 'src-1',
                'PlaySessionId': 'ps-1',
                'PositionTicks': 300_000_000,
                'IsPaused': False,
                'PlayMethod': 'DirectPlay',
                'CanSeek': True,
                'AudioStreamIndex': 1,
            },
        )
        self.assertEqual(
            stop_body,
            {
                'ItemId': 'item-1',
                'MediaSourceId': 'src-1',
                'PlaySessionId': 'ps-1',
                'PositionTicks': 900_000_000,
            },
        )

        # Once stopped, findVideoSession() raises NotFound.
        with self.assertRaises(exceptions.NotFound):
            srv.findVideoSession('player-session-1', 'item-1')

    def test_position_ms_to_ticks(self):
        # 10,000,000 ticks/second = 10,000 ticks/millisecond.
        self.assertEqual(playback.ms_to_ticks(0), 0)
        self.assertEqual(playback.ms_to_ticks(1), 10_000)
        self.assertEqual(playback.ms_to_ticks(1_000), 10_000_000)
        self.assertEqual(playback.ms_to_ticks(61_500), 615_000_000)
        self.assertEqual(playback.ms_to_ticks(3_600_000), 36_000_000_000)
        self.assertEqual(playback.ms_to_ticks(-500), 0)
        self.assertEqual(playback.ms_to_ticks(None), 0)

        session, srv, _ = self._start_movie(FakeResponse(204), FakeResponse(204), FakeResponse(204))

        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 1_500})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 61_500})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'stopped', 'time': 90_250})

        self.assertEqual(
            [c['json']['PositionTicks'] for c in session.calls[1:]],
            [15_000_000, 615_000_000, 902_500_000],
        )

    def test_pause_reported(self):
        session, srv, _ = self._start_movie(FakeResponse(204), FakeResponse(204), FakeResponse(204))

        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 10_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'paused', 'time': 25_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 25_000})

        report_calls = session.calls[1:]
        self.assertEqual(
            [(c['url'], c['json']['IsPaused'], c['json']['PositionTicks']) for c in report_calls],
            [
                (f'{BASE}/Sessions/Playing', False, 100_000_000),
                (f'{BASE}/Sessions/Playing/Progress', True, 250_000_000),
                (f'{BASE}/Sessions/Playing/Progress', False, 250_000_000),
            ],
        )

    def test_transcode_pinged(self):
        session, srv, movie = self._start_movie(
            FakeResponse(204),  # /Sessions/Playing
            FakeResponse(204),  # /Sessions/Playing/Progress (playing)
            FakeResponse(204),  # /Sessions/Playing/Ping
            FakeResponse(204),  # /Sessions/Playing/Progress (paused)
            FakeResponse(204),  # /Sessions/Playing/Ping
            FakeResponse(204),  # /Sessions/Playing/Stopped
            info_response=transcode_info_response('ps-tc'),
        )

        vs = srv.findVideoSession('ps-tc', 'item-1')
        info = videosession.VideoSessionInfo(vs, movie, srv.anyLANConnection)
        self.assertEqual(info.details.session.partDecision, 'transcode')

        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 5_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 15_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'paused', 'time': 20_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'stopped', 'time': 20_000})

        report_calls = session.calls[1:]
        self.assertEqual(
            [(c['method'], c['url'], c['params']) for c in report_calls],
            [
                ('POST', f'{BASE}/Sessions/Playing', None),
                ('POST', f'{BASE}/Sessions/Playing/Progress', None),
                ('POST', f'{BASE}/Sessions/Playing/Ping', {'playSessionId': 'ps-tc'}),
                ('POST', f'{BASE}/Sessions/Playing/Progress', None),
                ('POST', f'{BASE}/Sessions/Playing/Ping', {'playSessionId': 'ps-tc'}),
                ('POST', f'{BASE}/Sessions/Playing/Stopped', None),
            ],
        )
        self.assertEqual(report_calls[0]['json']['PlayMethod'], 'Transcode')

    def test_failed_report_does_not_stop_playback(self):
        session, srv, _ = self._start_movie(
            problem(500, 'Internal Server Error'),  # /Sessions/Playing fails
            requests.ConnectionError(f'socket lost {SECRET_TOKEN}'),  # /Sessions/Playing/Progress fails
            problem(503, 'Service Unavailable'),  # /Sessions/Playing/Ping fails
            FakeResponse(204),  # subsequent /Sessions/Playing/Progress succeeds
            FakeResponse(204),  # /Sessions/Playing/Ping succeeds
            problem(500, 'Stop error'),  # /Sessions/Playing/Stopped fails
            info_response=transcode_info_response('ps-err'),
        )

        # None of these may raise or prematurely drop the session.
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 1_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 10_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 20_000})
        # Session is still active before stop.
        self.assertIsNotNone(srv.findVideoSession('any', 'item-1'))
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'stopped', 'time': 30_000})

        # After stop (even when HTTP stop failed), the local session is cleared.
        with self.assertRaises(exceptions.NotFound):
            srv.findVideoSession('any', 'item-1')

        # Token must never leak into logs even when reporting fails.
        self.assertFalse(ENV.logged(SECRET_TOKEN))
        self.assertEqual(len(session.calls), 7)

    def test_buffering_and_subtitle_index_and_unknown_item(self):
        versions = [
            {
                'id': 'src-1',
                'container': 'mkv',
                'videoResolution': '1080',
                'bitrate': '12000',
                'streams': [
                    {
                        'streamType': '1',
                        'id': 'src-1.v0',
                        'index': '0',
                        'codec': 'hevc',
                        'width': '1920',
                        'height': '1080',
                    },
                    {
                        'streamType': '2',
                        'id': 'src-1.a1',
                        'index': '1',
                        'codec': 'dts',
                        'channels': '6',
                        'languageCode': 'jpn',
                        'selected': '1',
                    },
                    {
                        'streamType': '3',
                        'id': 'src-1.s3',
                        'index': '3',
                        'codec': 'ass',
                        'languageCode': 'eng',
                        'selected': '1',
                    },
                ],
            }
        ]
        session = FakeSession(playback_info_response(), FakeResponse(204), FakeResponse(204))
        srv = make_server(session, token=SECRET_TOKEN)
        movie = make_movie(srv, versions=versions)
        plexplayer.PlexPlayer(movie, 0).build()

        # Unknown item timeline is a clean no-op.
        srv.reportTimeline({'ratingKey': 'missing', 'state': 'playing', 'time': 1_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'playing', 'time': 2_000})
        srv.reportTimeline({'ratingKey': 'item-1', 'state': 'buffering', 'time': 5_000})

        self.assertEqual(len(session.calls), 3)
        self.assertEqual(session.calls[1]['json']['SubtitleStreamIndex'], 3)
        self.assertEqual(session.calls[2]['url'], f'{BASE}/Sessions/Playing/Progress')
        self.assertFalse(session.calls[2]['json']['IsPaused'])
        self.assertEqual(session.calls[2]['json']['SubtitleStreamIndex'], 3)
