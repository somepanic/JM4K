"""
A title's Related row and extras from Silo: getRelated() (/library/metadata/<id>/similar, with its count) from
GET /api/v2/recommendations/similar/{id} (limit at most 50), and the detail's extras (ItemExtraInfo: title, kind,
still, duration) as Plex's <Extras> the extras row lists. Responses are written inline in the shape of
/api/v2/openapi.json's CatalogItemCollection and CatalogItemDetail (Silo build-2987).
"""

import copy

from plexnet import media

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile

BASE = 'http://silo.lan:8090/api/v2'

SIMILAR = {
    'items': [
        {'content_id': 'movie-tmdb-1', 'type': 'movie', 'title': 'Twister', 'year': 1996, 'poster_url': '/p/1'},
        {'content_id': 'series-tvdb-2', 'type': 'series', 'title': 'Storm Chasers', 'year': 2007, 'poster_url': '/p/2'},
        {'content_id': 'movie-tmdb-3', 'type': 'movie', 'title': 'Into the Storm', 'year': 2014, 'poster_url': '/p/3'},
    ]
}


def ok(body):
    return FakeResponse(200, body)


def rowSize(item):
    """The Related row's size as its screens read relatedCount (preplay.py and subitems.py fillRelated())."""
    try:
        return int(item.relatedCount)
    except ValueError:
        return 0


class SiloRelatedTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_similar_titles(self):
        session = FakeSession(ok(SIMILAR))

        related = tile(server(session), MOVIE).getRelated(0, 8)

        self.assertEqual(session.calls[0]['url'], BASE + '/recommendations/similar/movie-tmdb-718821')
        self.assertEqual(session.calls[0]['params'], {'limit': 36})
        self.assertEqual(
            [(r.title, r.type) for r in related],
            [('Twister', 'movie'), ('Storm Chasers', 'show'), ('Into the Storm', 'movie')],
        )

    def test_how_many_there_are(self):
        session = FakeSession(ok(SIMILAR))

        self.assertEqual(rowSize(tile(server(session), MOVIE)), 3)

    def test_a_page_of_them(self):
        session = FakeSession(ok(SIMILAR))

        related = tile(server(session), MOVIE).getRelated(offset=1, limit=1)

        self.assertEqual([r.title for r in related], ['Storm Chasers'])

    def test_silo_asked_for_no_more_than_it_answers(self):
        session = FakeSession(ok(SIMILAR))

        server(session).query('/library/metadata/movie-tmdb-718821/similar?count=100')

        self.assertEqual(session.calls[0]['params'], {'limit': 50})

    def test_none_when_silo_cant_answer(self):
        session = FakeSession(problem(503, 'unavailable'))

        self.assertEqual(rowSize(tile(server(session), MOVIE)), 0)


class SiloExtrasTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_extras_row(self):
        detail = copy.deepcopy(MOVIE)
        detail['extras'] = [
            {
                'content_id': 'extra-1',
                'kind': 'trailer',
                'title': 'Trailer',
                'duration_seconds': 150,
                'file_id': 9,
                'still_url': '/api/v2/artwork/extras/1/still',
            },
            {'content_id': 'extra-2', 'kind': 'behind_the_scenes', 'title': 'Making the storms'},
            {'content_id': 'extra-3', 'kind': 'gag reel', 'title': 'Bloopers'},
        ]
        session = FakeSession(ok(detail))

        movie = tile(server(session), detail).reload(includeExtras=1, includeExtrasCount=10)

        self.assertEqual(
            [(e.title, e.extraType.asInt(), e.duration.asInt()) for e in movie.extras],
            [
                ('Trailer', media.METADATA_RELATED_TRAILER, 150000),
                ('Making the storms', media.METADATA_RELATED_BEHIND_THE_SCENES, 0),
                ('Bloopers', media.METADATA_RELATED_OTHER, 0),
            ],
        )
        self.assertEqual(movie.extras[0].thumb, '/api/v2/artwork/extras/1/still')

    def test_none(self):
        session = FakeSession(ok(MOVIE))

        movie = tile(server(session), MOVIE).reload(includeExtras=1)

        self.assertEqual(len(movie.extras), 0)
