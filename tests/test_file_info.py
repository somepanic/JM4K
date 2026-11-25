"""
Pre-play's File info (lib/windows/info.formatMediaDetails()) for a Silo film: the added date, the video and audio
bitrates, the sampling rate and the Dolby Vision layers come from Silo's file details, and a Dolby Vision version
Silo doesn't give isn't shown as an empty "Version:". Before, a Silo film showed "Added: 12/31/1969", "@ kBit" and
"Hz" with nothing in front.

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

import copy

from kodienv import ENV

ENV.abort_requested = True
from lib.windows import info

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession
from .test_silonet_detail import MOVIE, server, tile


def silo_film():
    detail = copy.deepcopy(MOVIE)
    version = detail['versions'][0]
    version.update(
        added_at='2026-11-20T18:54:17.808Z',
        file_name='Twisters (2024).mkv',
        file_path='/data/media/movies/Twisters (2024)/Twisters (2024).mkv',
    )
    version['video_tracks'][0].update(
        pixel_format='yuv420p10le', dv_bl_present=True, dv_rpu_present=True, dv_enhancement_layer='none'
    )
    version['audio_tracks'][0].update(bitrate=5200, sample_rate=48000)
    version['audio_tracks'][1].update(bitrate=640, sample_rate=48000)
    return tile(server(FakeSession(FakeResponse(200, detail))), detail).reload()


class SiloFileInfoTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_what_silo_gives_is_shown(self):
        text = info.formatMediaDetails(silo_film())

        self.assertNotIn('1969', text)
        self.assertIn('2026', text)
        self.assertIn('/4:2:0/bt2020@23465 kBit', text)
        self.assertIn('TRUEHD/8ch@5200 kBit, 48000 Hz', text)
        self.assertIn('BL: 1 (compat ID: 1), EL: 0, RPU: 1', text)
        self.assertNotIn('Version: ,', text)
        self.assertIn('File: Twisters (2024).mkv', text)
        self.assertIn('Audio: English, AC3/2ch@640 kBit', text)  # the commentary isn't the default track

    def test_a_film_without_dolby_vision_has_no_dolby_vision_line(self):
        detail = copy.deepcopy(MOVIE)
        track = detail['versions'][0]['video_tracks'][0]
        for key in ('dolby_vision', 'dv_profile', 'dv_level', 'dv_bl_compat_id'):
            track.pop(key)
        film = tile(server(FakeSession(FakeResponse(200, detail))), detail).reload()

        self.assertNotIn('DoVi', info.formatMediaDetails(film))
