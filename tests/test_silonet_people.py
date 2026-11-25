"""
A person from Silo, as Uno's person screen asks for them: their details (Role.getDetails(): /library/people/<id>) from
GET /api/v2/catalog/people/{id}, and their titles in the libraries (person.libraryFilmography():
/library/people/<id>/media) from GET /api/v2/catalog?source=person&person_id=<id>. Each cast and crew member carries
Silo's person id as its id and as its tagKey, the key the person screen asks every server by (person.personKey()).
Responses are written inline in the shape of /api/v2/openapi.json's Person and CatalogItemCollection (Silo build-2987).

Importing lib.windows.* starts lib.player's monitor thread unless abort_requested is set first.
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from plexnet import plexapp

from lib.windows import person

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import MOVIE, server, tile

BASE = 'http://silo.lan:8090/api/v2'

DAISY = {
    'id': 'p1',
    'name': 'Daisy Edgar-Jones',
    'bio': 'An English actress.',
    'birth_date': '1998-05-24',
    'birthplace': 'London, England, UK',
    'photo_url': '/api/v2/artwork/tmdb/people/p1/profile/w500.x',
    'tmdb_id': '1',
}

TITLES = {
    'items': [
        {'content_id': 'movie-tmdb-718821', 'type': 'movie', 'title': 'Twisters', 'year': 2024},
        {'content_id': 'series-tvdb-9', 'type': 'series', 'title': 'Normal People', 'year': 2020},
    ],
    'page': {'has_more': False},
}


def ok(body):
    return FakeResponse(200, body)


class SiloPersonTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def role(self, *responses):
        session = FakeSession(ok(MOVIE), *responses)
        movie = tile(server(session), MOVIE).reload()
        return session, movie.roles[0]

    def test_cast_carry_their_silo_id_as_their_key(self):
        session, role = self.role()

        self.assertEqual(person.personKey(role), 'p1')
        self.assertEqual(len(session.calls), 1)  # the movie's detail, nothing more

    def test_a_persons_details(self):
        session, role = self.role(ok(DAISY))

        details = role.getDetails()

        self.assertEqual(session.calls[1]['url'], BASE + '/catalog/people/p1')
        self.assertEqual(
            (details['name'], details['summary'], details['birthDate'], details['birthPlace'], details['thumb']),
            (
                'Daisy Edgar-Jones',
                'An English actress.',
                '1998-05-24',
                'London, England, UK',
                '/api/v2/artwork/tmdb/people/p1/profile/w500.x',
            ),
        )

    def test_a_person_silo_cant_find_keeps_what_the_cast_list_had(self):
        _session, role = self.role(problem(404, 'not_found'))

        details = role.getDetails()

        self.assertEqual((details['name'], details['role']), ('Daisy Edgar-Jones', 'Kate'))

    def test_their_titles_in_the_libraries(self):
        session = FakeSession(ok(TITLES))

        items = person.libraryFilmography(server(session), 'p1')

        self.assertEqual(session.calls[0]['url'], BASE + '/catalog')
        self.assertEqual(session.calls[0]['params'], {'source': 'person', 'person_id': 'p1', 'limit': 200, 'seek': 0})
        self.assertEqual([(i.TYPE, i.title) for i in items], [('movie', 'Twisters'), ('show', 'Normal People')])

    def test_every_page_of_them(self):
        first = {'items': TITLES['items'][:1], 'page': {'has_more': True}}
        rest = {'items': TITLES['items'][1:], 'page': {'has_more': False}}
        session = FakeSession(ok(first), ok(rest))

        items = person.libraryFilmography(server(session), 'p1')

        self.assertEqual(session.calls[1]['params']['seek'], 1)
        self.assertEqual([i.title for i in items], ['Twisters', 'Normal People'])


class NoPlexTvTest(KodiTestCase):
    """SM4K has no plex.tv (D10): a person's details and titles come from Silo alone, even with a signed-in account
    and nothing found on the server, which made the screen ask plex.tv's Discover for a biography and credits."""

    def setUp(self):
        super().setUp()
        ensure_plex_interface()
        account = mock.patch.object(plexapp, 'ACCOUNT', mock.Mock(authToken='token'))
        account.start()
        self.addCleanup(account.stop)
        plexTv = mock.patch('requests.get', side_effect=AssertionError('asked plex.tv'))
        self.plexTv = plexTv.start()
        self.addCleanup(plexTv.stop)

    def test_a_person_without_a_biography(self):
        session = FakeSession(ok(MOVIE), ok(dict(DAISY, bio=None)))
        role = tile(server(session), MOVIE).reload().roles[0]

        details = role.getDetails()

        self.assertEqual(details['name'], 'Daisy Edgar-Jones')
        self.plexTv.assert_not_called()

    def test_their_titles(self):
        session = FakeSession(ok(MOVIE), ok(TITLES))
        srv = server(session)
        role = tile(srv, MOVIE).reload().roles[0]
        got = []

        task = person.PersonFilmographyTask(role, [srv], got.append)
        task.isCanceled = lambda: False
        task.run()

        self.assertEqual([i.title for i in got[0]], ['Twisters', 'Normal People'])
        self.plexTv.assert_not_called()
