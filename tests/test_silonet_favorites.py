"""
Favorites from Silo (decision D19: a menu action plus a list): an item's menu adds it to or removes it from the
profile's favorites (PUT and DELETE /api/v2/favorites/{id}, its state from GET .../membership), and Home ends with a
Favorites row (GET /api/v2/favorites) while there are any, whose See more lists them all. Responses are written inline
in the shape of /api/v2/openapi.json (Silo build-2987).

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from lib.windows import itemmenu

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile

BASE = 'http://silo.lan:8090/api/v2'
CAPABILITIES = {}
LAYOUT = {
    'sections': [{'id': '7', 'section_type': 'recently_added', 'title': 'Recently Added'}],
    'included_sections': [{'id': '7', 'items': [{'content_id': 'movie-tmdb-1', 'type': 'movie', 'title': 'A'}]}],
}
FAVORITES = {
    'items': [{'content_id': 'movie-tmdb-718821', 'type': 'movie', 'title': 'Twisters'}],
    'page': {'has_more': False},
}


def ok(body=None, status=200):
    return FakeResponse(status, body)


class SiloFavoritesRowTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def home(self, favorites):
        session = FakeSession(ok(CAPABILITIES), ok(LAYOUT), favorites)
        hubs = server(session).hubs(None, count=20)
        self.session = session
        return hubs

    def test_home_ends_with_the_favorites(self):
        hubs = self.home(ok(FAVORITES))

        self.assertEqual(
            (self.session.calls[2]['url'], self.session.calls[2]['params']), (BASE + '/favorites', {'limit': 20})
        )
        self.assertEqual([h.title for h in hubs], ['Recently Added', 'Favorites'])
        self.assertEqual([i.title for i in hubs[1].items], ['Twisters'])

    def test_no_row_without_any(self):
        self.assertEqual([h.title for h in self.home(ok({'items': []}))], ['Recently Added'])

    def test_no_row_when_silo_cant_say(self):
        self.assertEqual([h.title for h in self.home(problem(503, 'dependency_unavailable'))], ['Recently Added'])

    def test_see_more_lists_them_all(self):
        session = FakeSession(ok(FAVORITES))

        answer = server(session).query('/hubs/silo/favorites', offset=0, limit=50)

        self.assertEqual(session.calls[0]['url'], BASE + '/favorites')
        self.assertEqual([e.get('title') for e in answer], ['Twisters'])


class SiloFavoriteActionTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def mli(self, *responses):
        session = FakeSession(*responses)
        item = tile(server(session), MOVIE)
        self.session = session
        return mock.Mock(dataSource=item, getProperty=lambda name: '')

    def test_the_menu_offers_to_add_it(self):
        mli = self.mli(ok({'item_id': 'movie-tmdb-718821', 'is_member': False}))

        rows = itemmenu.options(mli)

        self.assertIn(('add_favorite', 'Add to Favorites'), rows)
        self.assertEqual(self.session.calls[0]['url'], BASE + '/favorites/movie-tmdb-718821/membership')

    def test_or_to_remove_it(self):
        mli = self.mli(ok({'item_id': 'movie-tmdb-718821', 'is_member': True}))

        self.assertIn(('remove_favorite', 'Remove from Favorites'), itemmenu.options(mli))

    def test_adding_and_removing(self):
        mli = self.mli(ok(None, 204), ok(None, 204))
        window = mock.Mock()

        self.assertTrue(itemmenu.run(window, mli, 'add_favorite'))
        self.assertTrue(itemmenu.run(window, mli, 'remove_favorite'))

        self.assertEqual(
            [(c['method'], c['url']) for c in self.session.calls],
            [('PUT', BASE + '/favorites/movie-tmdb-718821'), ('DELETE', BASE + '/favorites/movie-tmdb-718821')],
        )
