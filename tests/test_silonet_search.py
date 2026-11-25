"""
Search from Silo (decision D20: the Silo library only, titles and people): Plex Uno's search asks each server for
its search hubs (server.hubs(search_query=...)); a Silo server answers from GET /api/v2/catalog?q= (titles, in
relevance order, grouped as Plex's movie, show and episode hubs) and GET /api/v2/catalog/people?q= (an actor hub,
whose people open the person screen). Responses are written inline in the shape of /api/v2/openapi.json's
CatalogItemCollection and PersonCollection (Silo build-2987).
"""

from .base import KodiTestCase, ensure_plex_interface
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_detail import server

BASE = 'http://silo.lan:8090/api/v2'

TITLES = {
    'items': [
        {'content_id': 'series-tvdb-1', 'type': 'series', 'title': 'Harbor Lights', 'year': 2019},
        {
            'content_id': 'episode-tvdb-1-1-2',
            'type': 'episode',
            'title': 'Harbor Night',
            'series_id': 'series-tvdb-1',
            'series_title': 'Harbor Lights',
            'season_number': 1,
            'episode_number': 2,
        },
        {'content_id': 'movie-tmdb-5', 'type': 'movie', 'title': 'Harbor', 'year': 2001},
        {'content_id': 'series-tvdb-2', 'type': 'series', 'title': 'Harbor Patrol', 'year': 2010},
    ],
    'page': {'has_more': False},
}
PEOPLE = {'items': [{'id': 'p7', 'name': 'Harbor Smith', 'photo_url': '/api/v2/artwork/people/p7'}]}


class SiloSearchTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def search(self, *responses, count=10):
        session = FakeSession(*responses)
        hubs = server(session).hubs(count=count, search_query='Harbor')
        self.session = session
        return hubs

    def test_titles_and_people(self):
        hubs = self.search(FakeResponse(200, TITLES), FakeResponse(200, PEOPLE))

        titles, people = self.session.calls
        self.assertEqual((titles['url'], titles['params']['q']), (BASE + '/catalog', 'Harbor'))
        self.assertEqual((people['url'], people['params']), (BASE + '/catalog/people', {'q': 'Harbor', 'limit': 10}))
        self.assertEqual(
            [(h.type, [i.title or i.tag for i in h.items]) for h in hubs],  # a person's name is its tag
            [
                ('show', ['Harbor Lights', 'Harbor Patrol']),
                ('episode', ['Harbor Night']),
                ('movie', ['Harbor']),
                ('actor', ['Harbor Smith']),
            ],
        )

    def test_each_kind_up_to_the_count(self):
        hubs = self.search(FakeResponse(200, TITLES), FakeResponse(200, PEOPLE), count=1)

        self.assertEqual([len(h.items) for h in hubs], [1, 1, 1, 1])

    def test_a_person_opens_as_a_cast_member(self):
        hubs = self.search(FakeResponse(200, TITLES), FakeResponse(200, PEOPLE))

        person = hubs[-1].items[0]
        self.assertEqual(
            (person.tag, person.id, person.get('tagKey'), person.thumb),
            ('Harbor Smith', 'p7', 'p7', '/api/v2/artwork/people/p7'),
        )

    def test_nothing_when_silo_cant_answer(self):
        hubs = self.search(problem(503, 'dependency_unavailable'), problem(503, 'dependency_unavailable'))

        self.assertEqual(hubs, [])
