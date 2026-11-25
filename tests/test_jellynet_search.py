"""
Search titles and people against Jellyfin 10.11.5 (Task 8.1, decision D20: this server's libraries only):
- GET /Items?searchTerm=...&recursive=true&includeItemTypes=Movie,Series,Episode (or narrowed by section_ids)
- GET /Persons?searchTerm=...&enableImages=true

Responses are written inline in the shape of Jellyfin 10.11.5 OpenAPI BaseItemDtoQueryResult.
"""

from kodienv import ENV

ENV.abort_requested = True
from jellynet.errors import JellyfinAuthError
from plexnet import media

from lib.windows import person as person_win
from lib.windows import search as search_win

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem
from .test_jellynet_detail import BASE, make_server

TITLES = {
    'Items': [
        {
            'Id': 'ser-1',
            'Name': 'Harbor Lights',
            'Type': 'Series',
            'ProductionYear': 2019,
            'Overview': 'Coastal drama.',
            'Genres': ['Drama'],
            'ImageTags': {'Primary': 'ptag-ser1'},
        },
        {
            'Id': 'ep-1',
            'Name': 'Harbor Night',
            'Type': 'Episode',
            'SeriesId': 'ser-1',
            'SeriesName': 'Harbor Lights',
            'SeasonId': 'sea-1',
            'SeasonName': 'Season 1',
            'ParentIndexNumber': 1,
            'IndexNumber': 2,
            'ImageTags': {'Primary': 'ptag-ep1'},
        },
        {
            'Id': 'mov-5',
            'Name': 'Harbor',
            'Type': 'Movie',
            'ProductionYear': 2001,
            'Overview': 'Feature film.',
            'ImageTags': {'Primary': 'ptag-mov5'},
        },
        {
            'Id': 'ser-2',
            'Name': 'Harbor Patrol',
            'Type': 'Series',
            'ProductionYear': 2010,
        },
        {
            # Unsupported music type must stay omitted until Phase 11
            'Id': 'aud-9',
            'Name': 'Harbor Song',
            'Type': 'Audio',
        },
    ],
    'TotalRecordCount': 5,
    'StartIndex': 0,
}

COLLECTIONS = {
    'Items': [
        {
            'Id': 'box-1',
            'Name': 'Harbor Collection',
            'Type': 'BoxSet',
            'ChildCount': 3,
            'ImageTags': {'Primary': 'ptag-box1'},
        },
    ],
    'TotalRecordCount': 1,
    'StartIndex': 0,
}

PEOPLE = {
    'Items': [
        {
            'Id': 'p-7',
            'Name': 'Harbor Smith',
            'Type': 'Person',
            'ImageTags': {'Primary': 'ptag-p7'},
        },
        {
            'Id': 'p-8',
            'Name': 'Harbor Jones',
            'Type': 'Person',
        },
    ],
    'TotalRecordCount': 2,
    'StartIndex': 0,
}

PERSON_DETAIL = {
    'Id': 'p-7',
    'Name': 'Harbor Smith',
    'Type': 'Person',
    'Overview': 'Veteran character actor.',
    'PremiereDate': '1970-03-14T00:00:00.0000000Z',
    'ProductionLocations': ['Bristol, UK'],
    'ImageTags': {'Primary': 'ptag-p7'},
}

PERSON_MEDIA = {
    'Items': [
        {
            'Id': 'mov-5',
            'Name': 'Harbor',
            'Type': 'Movie',
            'ProductionYear': 2001,
        },
    ],
    'TotalRecordCount': 1,
    'StartIndex': 0,
}


def _search_window(section='all'):
    win = search_win.SearchWindow.__new__(search_win.SearchWindow)
    win.props = {'search.section': section}
    win.getProperty = lambda key: win.props.get(key, '')
    win.setProperty = win.props.__setitem__
    win.noTitles = False
    win.noImages = False
    win.shown = []

    class FakeGrid:
        def reset(self):
            win.shown.clear()

        def addItems(self, items):
            win.shown.extend(items)

    win.resultsList = FakeGrid()
    win.historyList = FakeGrid()
    win.edit = type('Edit', (), {'getText': lambda self: 'Harbor'})()
    win.createListItem = lambda hub_item, art_type: (hub_item.title or hub_item.tag, hub_item.TYPE, art_type)
    return win


class JellyfinSearchTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_results_grouped_by_type(self):
        session = FakeSession(FakeResponse(200, TITLES), FakeResponse(200, PEOPLE))
        srv = make_server(session)

        hubs = srv.hubs(count=10, search_query='  Harbor  ')

        self.assertEqual(len(session.calls), 2)
        titles_call, people_call = session.calls
        self.assertEqual(titles_call['method'], 'GET')
        self.assertEqual(titles_call['url'], f'{BASE}/Items')
        self.assertEqual(
            titles_call['params'],
            {
                'searchTerm': 'Harbor',
                'recursive': 'true',
                'includeItemTypes': 'Movie,Series,Episode',
                'fields': 'Overview,Genres,DateCreated,PrimaryImageAspectRatio',
                'enableUserData': 'true',
                'limit': 100,
            },
        )
        self.assertTrue(titles_call['headers']['Authorization'].startswith('MediaBrowser Token='))
        self.assertEqual(people_call['method'], 'GET')
        self.assertEqual(people_call['url'], f'{BASE}/Persons')
        self.assertEqual(
            people_call['params'],
            {'searchTerm': 'Harbor', 'limit': 10, 'enableImages': 'true'},
        )

        self.assertEqual(
            [(h.type, h.hubIdentifier, h.title, [i.title or i.tag for i in h.items]) for h in hubs],
            [
                ('show', 'show', 'Shows', ['Harbor Lights', 'Harbor Patrol']),
                ('episode', 'episode', 'Episodes', ['Harbor Night']),
                ('movie', 'movie', 'Movies', ['Harbor']),
                ('actor', 'actor', 'People', ['Harbor Smith', 'Harbor Jones']),
            ],
        )

        # Movie and episode attributes map cleanly and image URLs never carry tokens
        episode = hubs[1].items[0]
        self.assertEqual(
            (episode.grandparentTitle, episode.parentIndex.asInt(), episode.index.asInt()),
            ('Harbor Lights', 1, 2),
        )
        movie = hubs[2].items[0]
        self.assertEqual(movie.year.asInt(), 2001)
        self.assertEqual(movie.thumb, '/Items/mov-5/Images/Primary?tag=ptag-mov5')
        self.assertNotIn('TOK-1', movie.thumb)
        self.assertEqual(srv._itemTypes.get('ser-1'), 'Series')
        self.assertEqual(srv._seasonSeries.get('sea-1'), 'ser-1')

    def test_type_button_narrows(self):
        # 1. SearchWindow type buttons (SM4K #21: All, Movies, Shows, People; no Music or Photos)
        self.assertEqual(
            list(search_win.SearchWindow.SECTION_BUTTONS.values()),
            ['all', 'movie', 'show', 'people'],
        )
        session = FakeSession(FakeResponse(200, TITLES), FakeResponse(200, PEOPLE))
        srv = make_server(session)
        hubs = srv.hubs(count=10, search_query='Harbor')

        win = _search_window('all')
        win._shownRows = ('Harbor', hubs)
        win.showHubs(hubs)
        self.assertEqual(
            [row[0] for row in win.shown],
            ['Harbor Lights', 'Harbor Patrol', 'Harbor Night', 'Harbor', 'Harbor Smith', 'Harbor Jones'],
        )

        win.typeClicked(912)  # Movies
        self.assertEqual(win.shown, [('Harbor', 'movie', 'poster')])

        win.typeClicked(913)  # Shows (includes shows and episodes)
        self.assertEqual(
            win.shown,
            [
                ('Harbor Lights', 'show', 'poster'),
                ('Harbor Patrol', 'show', 'poster'),
                ('Harbor Night', 'episode', 'ar16x9'),
            ],
        )

        win.typeClicked(916)  # People
        self.assertEqual(
            win.shown,
            [('Harbor Smith', 'Role', 'circle'), ('Harbor Jones', 'Role', 'circle')],
        )
        # Switching type buttons narrowed already-fetched hubs without issuing extra requests
        self.assertEqual(len(session.calls), 2)

        # 2. Server-side narrowing via section_ids
        movie_only = FakeSession(FakeResponse(200, {'Items': [TITLES['Items'][2]], 'TotalRecordCount': 1}))
        movie_hubs = make_server(movie_only).hubs(count=10, search_query='Harbor', section_ids=['movie'])
        self.assertEqual(len(movie_only.calls), 1)
        self.assertEqual(movie_only.calls[0]['url'], f'{BASE}/Items')
        self.assertEqual(movie_only.calls[0]['params']['includeItemTypes'], 'Movie')
        self.assertEqual(movie_only.calls[0]['params']['limit'], 10)
        self.assertEqual([h.type for h in movie_hubs], ['movie'])

        show_only = FakeSession(FakeResponse(200, {'Items': TITLES['Items'][:2], 'TotalRecordCount': 2}))
        show_hubs = make_server(show_only).hubs(count=10, search_query='Harbor', section_ids=['show'])
        self.assertEqual(len(show_only.calls), 1)
        self.assertEqual(show_only.calls[0]['params']['includeItemTypes'], 'Series,Episode')
        self.assertEqual([h.type for h in show_hubs], ['show', 'episode'])

        ep_only = FakeSession(FakeResponse(200, {'Items': [TITLES['Items'][1]], 'TotalRecordCount': 1}))
        ep_hubs = make_server(ep_only).hubs(count=10, search_query='Harbor', section_ids=['episode'])
        self.assertEqual(len(ep_only.calls), 1)
        self.assertEqual(ep_only.calls[0]['params']['includeItemTypes'], 'Episode')
        self.assertEqual([h.type for h in ep_hubs], ['episode'])

        coll_only = FakeSession(FakeResponse(200, COLLECTIONS))
        coll_hubs = make_server(coll_only).hubs(count=10, search_query='Harbor', section_ids=['collection'])
        self.assertEqual(len(coll_only.calls), 1)
        self.assertEqual(coll_only.calls[0]['params']['includeItemTypes'], 'BoxSet')
        self.assertEqual(
            [(h.type, h.title, [i.title for i in h.items]) for h in coll_hubs],
            [
                ('collection', 'Collections', ['Harbor Collection']),
            ],
        )

        people_only = FakeSession(FakeResponse(200, PEOPLE))
        people_hubs = make_server(people_only).hubs(count=10, search_query='Harbor', section_ids=['people'])
        self.assertEqual(len(people_only.calls), 1)
        self.assertEqual(people_only.calls[0]['url'], f'{BASE}/Persons')
        self.assertEqual([h.type for h in people_hubs], ['actor'])

        # Music stays omitted until Phase 11 (zero HTTP calls)
        music_session = FakeSession()
        self.assertEqual(make_server(music_session).hubs(count=10, search_query='Harbor', section_ids=['music']), [])
        self.assertEqual(music_session.calls, [])

        # Library section ID from /UserViews narrows to that section's Jellyfin item types
        lib_session = FakeSession(
            FakeResponse(
                200,
                {'Items': [{'Id': 'view-movies', 'Name': 'Movies', 'CollectionType': 'movies'}]},
            ),
            FakeResponse(200, {'Items': [TITLES['Items'][2]], 'TotalRecordCount': 1}),
        )
        lib_srv = make_server(lib_session)
        lib_srv.query('/library/sections')
        lib_hubs = lib_srv.hubs(count=10, search_query='Harbor', section_ids=['view-movies'])
        self.assertEqual(lib_session.calls[1]['params']['includeItemTypes'], 'Movie')
        self.assertEqual([h.type for h in lib_hubs], ['movie'])

    def test_search_servers_pipeline(self):
        session = FakeSession(FakeResponse(200, TITLES), FakeResponse(200, PEOPLE))
        srv = make_server(session)

        rows, missing = search_win.searchServers('Harbor', [srv])

        self.assertEqual(missing, [])
        self.assertEqual(
            [(h.type, [i.title or i.tag for i in h.items]) for h in rows],
            [
                ('show', ['Harbor Lights', 'Harbor Patrol']),
                ('episode', ['Harbor Night']),
                ('movie', ['Harbor']),
                ('actor', ['Harbor Smith', 'Harbor Jones']),
            ],
        )
        # Only /Items and /Persons were sent to Jellyfin (lookupCopies' /library/all is local no-op)
        self.assertEqual(len(session.calls), 2)

    def test_people_results(self):
        session = FakeSession(
            FakeResponse(200, {'Items': [], 'TotalRecordCount': 0}),
            FakeResponse(200, PEOPLE),
            FakeResponse(200, PERSON_DETAIL),
            FakeResponse(200, PERSON_MEDIA),
        )
        srv = make_server(session)

        hubs = srv.hubs(count=10, search_query='Harbor')

        self.assertEqual(len(hubs), 1)
        people_hub = hubs[0]
        self.assertEqual((people_hub.type, people_hub.hubIdentifier, people_hub.title), ('actor', 'actor', 'People'))
        self.assertTrue(all(isinstance(item, media.Role) for item in people_hub.items))

        first, second = people_hub.items
        self.assertEqual(
            (first.tag, first.id, first.get('tagKey'), first.thumb),
            ('Harbor Smith', 'p-7', 'p-7', '/Items/p-7/Images/Primary?tag=ptag-p7'),
        )
        self.assertEqual(
            (second.tag, second.id, second.get('tagKey'), second.thumb), ('Harbor Jones', 'p-8', 'p-8', '')
        )
        self.assertNotIn('TOK-1', first.thumb)

        # Person lookup caches are populated so opening a search person works directly with people.py
        self.assertEqual(srv._personNames['p-7'], 'Harbor Smith')
        self.assertEqual(srv._personIds['Harbor Smith'], 'p-7')
        self.assertEqual(srv._personNames['p-8'], 'Harbor Jones')
        self.assertEqual(srv._personIds['Harbor Jones'], 'p-8')

        details = first.getDetails()
        self.assertEqual(session.calls[2]['url'], f'{BASE}/Persons/Harbor%20Smith')
        self.assertEqual(
            (details['name'], details['summary'], details['birthDate'], details['birthPlace']),
            ('Harbor Smith', 'Veteran character actor.', '1970-03-14', 'Bristol, UK'),
        )

        filmography = person_win.libraryFilmography(srv, person_win.personKey(first))
        self.assertEqual(session.calls[3]['url'], f'{BASE}/Items')
        self.assertEqual(session.calls[3]['params']['personIds'], 'p-7')
        self.assertEqual([item.title for item in filmography], ['Harbor'])

    def test_empty_query_no_request(self):
        session = FakeSession()
        srv = make_server(session)

        self.assertEqual(srv.hubs(count=10, search_query=''), [])
        self.assertEqual(srv.hubs(count=10, search_query='   \t\n  '), [])
        empty_xml = srv.query('/hubs/search?query=%20%20')
        self.assertEqual(empty_xml.tag, 'MediaContainer')
        self.assertEqual(len(list(empty_xml)), 0)
        self.assertEqual(session.calls, [])

    def test_each_kind_capped_by_count(self):
        session = FakeSession(FakeResponse(200, TITLES), FakeResponse(200, PEOPLE))
        srv = make_server(session)

        hubs = srv.hubs(count=1, search_query='Harbor')

        self.assertEqual([len(h.items) for h in hubs], [1, 1, 1, 1])
        self.assertEqual(session.calls[1]['params']['limit'], 1)

    def test_partial_failure_returns_other_results(self):
        # /Items fails with 503 -> /Persons still runs and returns People hub
        items_fail = FakeSession(problem(503, 'Unavailable'), FakeResponse(200, PEOPLE))
        hubs = make_server(items_fail).hubs(count=10, search_query='Harbor')
        self.assertEqual(
            [(h.type, [i.tag for i in h.items]) for h in hubs],
            [
                ('actor', ['Harbor Smith', 'Harbor Jones']),
            ],
        )

        # /Persons fails with 500 -> /Items results are still returned
        people_fail = FakeSession(FakeResponse(200, TITLES), problem(500, 'Server Error'))
        hubs = make_server(people_fail).hubs(count=10, search_query='Harbor')
        self.assertEqual([h.type for h in hubs], ['show', 'episode', 'movie'])

        # Both fail with non-auth errors -> empty list cleanly
        both_fail = FakeSession(problem(503, 'Unavailable'), problem(503, 'Unavailable'))
        self.assertEqual(make_server(both_fail).hubs(count=10, search_query='Harbor'), [])
        self.assertFalse(ENV.logged('TOK-1'))

    def test_auth_error_propagates(self):
        # 401 on /Items raises immediately without calling /Persons
        items_401 = FakeSession(problem(401, 'Unauthorized'))
        with self.assertRaises(JellyfinAuthError):
            make_server(items_401).hubs(count=10, search_query='Harbor')
        self.assertEqual(len(items_401.calls), 1)

        # 401 on /Persons raises
        people_401 = FakeSession(FakeResponse(200, TITLES), problem(401, 'Unauthorized'))
        with self.assertRaises(JellyfinAuthError):
            make_server(people_401).hubs(count=10, search_query='Harbor')
        self.assertEqual(len(people_401.calls), 2)

    def test_hubs_search_query_route(self):
        session = FakeSession(FakeResponse(200, TITLES), FakeResponse(200, PEOPLE))
        srv = make_server(session)

        xml = srv.query('/hubs/search?query=Harbor&limit=10')

        self.assertEqual(xml.tag, 'MediaContainer')
        self.assertEqual([hub.get('type') for hub in xml], ['show', 'episode', 'movie', 'actor'])
