"""
Show Stream Info on a Silo server. The player's info panel (seekdialog.showPPIDialog(), plexnet.videosession) asks
the server for the playing item's session, as Plex's /status/sessions lists it, and compares it with the version
the item plays. SiloServer.findVideoSession() answers from the item's attempt: what its plan says Silo sends
(playback protocol v3: delivery, source, effective_recipe and transformations).
"""

import copy

from plexnet import plexplayer
from plexnet.videosession import VideoSessionInfo

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile
from .test_silonet_playback import CAPABILITIES, NO_WATCH, decision, ok

# Twisters, with its audio tracks' bitrates as Silo lists them, so the video's own bitrate is known: 29305 - 4305 - 640
FILM = copy.deepcopy(MOVIE)
FILM['versions'][0]['audio_tracks'][0]['bitrate'] = 4305
FILM['versions'][0]['audio_tracks'][1]['bitrate'] = 640

SOURCE = {
    'media_file_id': 241,
    'container': 'mkv',
    'video_codec': 'hevc',
    'width': 3840,
    'height': 2160,
    'bitrate_kbps': 29305,
    'dynamic_range': 'dolby_vision',
    'hdr10_plus': False,
    'dv_enhancement_layer': 'none',
    'audio_codec': 'truehd',
    'audio_channels': 8,
}
RECIPE = ('video_codec', 'width', 'height', 'bitrate_kbps', 'dynamic_range', 'audio_codec', 'audio_channels')
# Silo's answer about the connection the panel asks on (GET /api/v2/playback/connection)
LOCAL = ok({'connection': 'local', 'learned_limit': None})


def planned(delivery, recipe=None, transformations=(), container='mkv', source=SOURCE):
    """A playable decision sending the file as delivery: the recipe is the source's but for what recipe changes, and
    the server runs the transformations."""
    answer = decision(delivery)
    plan = answer['playback_plan']
    plan['source'] = dict(source)
    plan['effective_recipe'] = dict({key: source[key] for key in RECIPE}, **(recipe or {}))
    plan['transformations'] = [
        {'name': name, 'executor': 'server', 'recipe_version': '1', 'validated_claims': []} for name in transformations
    ]
    plan['stream']['container'] = container
    return answer


class SiloStreamInfoTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def panel(self, answer, film=FILM, connection=LOCAL):
        """The panel's lines while the film plays as answer plans it, as showPPIDialog() fills them, on connection."""
        srv = server(FakeSession(ok(film), ok(CAPABILITIES), ok(answer, 201), NO_WATCH, connection))
        movie = tile(srv, film).reload()
        plexplayer.PlexPlayer(movie, 0).build()
        found = srv.findVideoSession('the-players-session-id', movie.ratingKey)
        info = VideoSessionInfo(found, movie, srv.anyLANConnection)
        return {name: line.value for name, line in info.attributes.items()}

    def test_the_original_plays_directly(self):
        lines = self.panel(planned('original_http'))

        self.assertEqual(lines['Mode'].split(', ')[0], 'directplay')
        self.assertEqual(lines['Container'], 'mkv')
        self.assertEqual(lines['Video'], 'hevc, 4k, 24360kbit, directplay')
        self.assertEqual(lines['Audio'], 'truehd, 4305kbit, 8ch, directplay')

    def test_a_transcode_says_what_changed(self):
        lower = {'video_codec': 'h264', 'width': 1280, 'height': 720, 'bitrate_kbps': 3608, 'dynamic_range': 'sdr'}
        lower.update(audio_codec='aac', audio_channels=2)
        answer = planned(
            'server_transcode_hls', lower, ('video_to_h264', 'hdr_to_sdr_tonemap', 'audio_to_aac'), container='hls'
        )

        lines = self.panel(answer)

        self.assertEqual(lines['Mode'].split(', ')[0], 'transcode')
        self.assertEqual(lines['Container'], 'mkv->hls')
        self.assertEqual(lines['Video'], 'hevc->h264, 4k->720p, 24360->3608kbit, transcode')
        # Silo doesn't say the new audio's bitrate: "?", as Plex Uno shows one it doesn't know
        self.assertEqual(lines['Audio'], 'truehd->aac, 4305->?kbit, 8->2ch, transcode')

    def test_a_remux_copies_the_streams(self):
        lines = self.panel(planned('server_remux_hls', container='hls'))

        self.assertEqual(lines['Mode'].split(', ')[0], 'remux')
        self.assertEqual(lines['Container'], 'mkv->hls')
        self.assertEqual(lines['Video'], 'hevc, 4k, 24360kbit, copy')
        self.assertEqual(lines['Audio'], 'truehd, 4305kbit, 8ch, copy')

    def test_a_remux_that_converts_dolby_vision_copies_the_video(self):
        # seen on the box: a Dolby Vision profile 7 file Silo sends as a remux to MP4 (its session: video_decision
        # remux, adaptation dynamic_range_unsupported). A remux repackages the codecs untouched, though its recipe's
        # dynamic range differs from the source's
        lines = self.panel(planned('server_remux_progressive', {'dynamic_range': 'hdr10'}, container='mp4'))

        self.assertEqual(lines['Mode'].split(', ')[0], 'remux')
        self.assertEqual(lines['Container'], 'mkv->mp4')
        self.assertEqual(lines['Video'], 'hevc, 4k, 24360kbit, copy')
        self.assertEqual(lines['Audio'], 'truehd, 4305kbit, 8ch, copy')

    def test_audio_reencoded_alone_leaves_the_video_copied(self):
        # Silo's audio adaptation: a transcode delivery whose only transformation is the audio's
        answer = planned('server_transcode_hls', {'audio_codec': 'eac3', 'audio_channels': 6}, ('audio_to_eac3',))

        lines = self.panel(answer)

        self.assertEqual(lines['Video'], 'hevc, 4k, 24360kbit, copy')
        self.assertEqual(lines['Audio'], 'truehd->eac3, 4305->?kbit, 8->6ch, transcode')

    def test_a_value_the_source_doesnt_list_is_no_change(self):
        # the source descriptor's fields are optional (decision-response.schema.json): one it leaves out isn't a change
        answer = planned('server_transcode_hls', container='hls')
        del answer['playback_plan']['source']['dynamic_range']

        lines = self.panel(answer)

        self.assertEqual(lines['Video'], 'hevc, 4k, 24360kbit, copy')

    def test_audio_reencoded_to_its_own_codec_is_a_transcode(self):
        # as in Silo's learned-limit fixture: AAC stereo re-encoded to AAC stereo
        film = copy.deepcopy(FILM)
        film['versions'][0]['audio_tracks'][0].update(codec='aac', channels=2)
        source = dict(SOURCE, audio_codec='aac', audio_channels=2)
        answer = planned('server_transcode_hls', transformations=('audio_to_aac',), source=source)

        lines = self.panel(answer, film)

        self.assertEqual(lines['Audio'], 'aac, 4305->?kbit, 2ch, transcode')

    def test_mode_says_lan_on_a_local_connection(self):
        lines = self.panel(planned('original_http'))

        self.assertEqual(lines['Mode'], 'directplay, lan (verified)')

    def test_mode_says_remote_off_the_lan(self):
        # Silo names a connection local, remote, or the network-access provider it came through (playback-api.md)
        for connection in ('remote', 'provider:tailscale'):
            with self.subTest(connection):
                lines = self.panel(planned('original_http'), connection=ok({'connection': connection}))

                self.assertEqual(lines['Mode'], 'directplay, remote')

    def test_no_answer_about_the_connection_is_not_lan(self):
        lines = self.panel(planned('original_http'), connection=problem(404, 'not_found'))

        self.assertEqual(lines['Mode'], 'directplay, remote')

    def test_mode_says_why_silo_changed_the_file(self):
        answer = planned('server_remux_hls', {'audio_codec': 'eac3', 'audio_channels': 6}, container='hls')
        answer['playback_plan']['decision_reason'] = 'surround_bitstream_conversion'

        lines = self.panel(answer)

        self.assertEqual(lines['Mode'], 'remux, surround bitstream conversion, lan (verified)')

    def test_a_bitrate_the_file_doesnt_list_shows_the_new_one_alone(self):
        # MOVIE's audio tracks list no bitrate, so neither the audio's nor the video's own is known
        lower = {'video_codec': 'h264', 'width': 1280, 'height': 720, 'bitrate_kbps': 3608, 'dynamic_range': 'sdr'}
        lower.update(audio_codec='aac', audio_channels=2)
        answer = planned(
            'server_transcode_hls', lower, ('video_to_h264', 'hdr_to_sdr_tonemap', 'audio_to_aac'), container='hls'
        )

        lines = self.panel(answer, MOVIE)

        self.assertEqual(lines['Video'], 'hevc->h264, 4k->720p, 3608kbit, transcode')
        self.assertEqual(lines['Audio'], 'truehd->aac, ?kbit, 8->2ch, transcode')
