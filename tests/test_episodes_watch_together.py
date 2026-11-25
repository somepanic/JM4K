"""
Watch together from the Episodes screen (decision D26): an episode card's More actions offer Watch together on a Silo
server, as a film's pre-play screen does, and choosing it puts that episode up next in the host's party
(watch_together.pickItem()). The season card has none: a party plays an episode, not a season.

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from lib.windows import episodes

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeSession
from .test_silonet_detail import server, tile

EPISODE = {
    'content_id': 'episode-tvdb-78857-1-2',
    'type': 'episode',
    'title': 'My Name Is Konohamaru!',
    'series_id': 'series-tvdb-78857',
    'series_title': 'Naruto',
    'season_number': 1,
    'episode_number': 2,
    'user_state': {'played': False},
}


class EpisodesWatchTogetherTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()
        self.episode = tile(server(FakeSession()), EPISODE)
        self.window = episodes.EpisodesWindow.__new__(episodes.EpisodesWindow)
        self.window.season = None
        self.window.show_ = None
        self.mli = mock.Mock(dataSource=self.episode)

    def actions(self, isSeasonCard=False):
        with mock.patch.object(episodes.plexapp, 'ACCOUNT', mock.Mock(isAdmin=False)):
            return [a[0] for a in self.window.moreActions(self.mli, isSeasonCard)]

    def test_an_episode_card_offers_it(self):
        self.assertIn('watch_together', self.actions())

    def test_the_season_card_does_not(self):
        self.window.show_ = self.episode  # stands in for the show the season card falls back to
        self.assertNotIn('watch_together', self.actions(isSeasonCard=True))

    def test_choosing_it_hosts_the_episode(self):
        with mock.patch.object(episodes.watch_together, 'pickItem') as pickItem:
            self.window.runMoreAction('watch_together', self.mli, False)

        video, fileId, play = pickItem.call_args[0]
        self.assertIs(video, self.episode)
        self.assertIsNone(fileId)  # a card not loaded with its files: Silo picks the file for the room
        self.assertTrue(callable(play))
