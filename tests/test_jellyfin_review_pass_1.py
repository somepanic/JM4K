"""
Regression tests for JM4K review pass 1:
- JellyfinClient non-JSON 2xx body -> JellyfinError
- Admin library scan & metadata refresh routes (/library/sections/<id>/refresh, /all/refresh, /metadata/<id>/refresh)
- Library label/tag filter choices fallback from empty /Items/Filters2 Tags to /Items/Filters
- JellyfinAuthError (401) propagation without cascading requests
- Cold person UUID lookup via /Items/{id} and uncached person-name filmography via ?person=
- UserData Played=False with PlayCount>0 stays unwatched
- Search resolveCollections preserves Jellyfin collections, and single-library section_ids sets parentId
- UserSelectWindow keeps 'Other user' tile label on failed sign-in
"""

import copy
from unittest import mock

from kodienv import ENV

ENV.abort_requested = True

from jellynet import detail, items, library, people, related, watch
from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.errors import JellyfinAuthError, JellyfinError
from plexnet import plexlibrary, plexobjects, video  # noqa: F401

from lib.windows import search as search_win
from lib.windows import userselect

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem
from .test_jellynet_detail import BASE, MOVIE_DETAIL, make_server, make_tile

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Kodi', 'device-uuid-1')

USER_VIEWS = FakeResponse(
    200,
    {
        'Items': [
            {'Id': 'view-movies', 'Name': 'Movies', 'CollectionType': 'movies', 'ImageTags': {}},
            {'Id': 'view-shows', 'Name': 'TV Shows', 'CollectionType': 'tvshows', 'ImageTags': {}},
            {'Id': 'view-4k', 'Name': '4K Movies', 'CollectionType': 'movies', 'ImageTags': {}},
        ],
        'TotalRecordCount': 3,
    },
)


class ReviewPassOneTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_client_invalid_json_200_raises_jellyfin_error(self):
        session = FakeSession(FakeResponse(200, raw='<html>Bad Gateway Proxy Page</html>'))
        client = JellyfinClient(BASE, IDENTITY, token='TOK-1', username='alice', session=session)

        with self.assertRaises(JellyfinError) as caught:
            client.get('/UserViews')
        self.assertEqual(caught.exception.status, 200)

    def test_admin_library_scan_and_metadata_refresh_routes(self):
        session = FakeSession(
            USER_VIEWS,
            FakeResponse(204),
            FakeResponse(204),
            FakeResponse(204),
        )
        srv = make_server(session)
        movies = plexlibrary.Library(None, server=srv).sections()[0]
        movie = make_tile(srv, MOVIE_DETAIL)

        movies.refresh()
        plexlibrary.Library(None, server=srv).refresh()
        movie.refresh()

        self.assertEqual(
            [(c['method'], c['url'], c['params']) for c in session.calls[1:]],
            [
                (
                    'POST',
                    f'{BASE}/Items/view-movies/Refresh',
                    {
                        'metadataRefreshMode': 'Default',
                        'imageRefreshMode': 'Default',
                        'replaceAllMetadata': 'false',
                        'replaceAllImages': 'false',
                    },
                ),
                ('POST', f'{BASE}/Library/Refresh', None),
                (
                    'POST',
                    f'{BASE}/Items/mov-1/Refresh',
                    {
                        'metadataRefreshMode': 'Default',
                        'imageRefreshMode': 'Default',
                        'replaceAllMetadata': 'false',
                        'replaceAllImages': 'false',
                    },
                ),
            ],
        )

    def test_label_filter_falls_back_to_legacy_filters_when_filters2_tags_empty(self):
        session = FakeSession(
            USER_VIEWS,
            FakeResponse(200, {'Genres': [{'Name': 'Action', 'Id': 'g1'}], 'Tags': []}),
            FakeResponse(200, {'Genres': ['Action'], 'Tags': ['4k', 'remux'], 'OfficialRatings': [], 'Years': []}),
        )
        srv = make_server(session)
        movies = plexlibrary.Library(None, server=srv).sections()[0]

        labels = movies.listChoices('label', libtype='movie')

        self.assertEqual([str(item.key) for item in labels], ['4k', 'remux'])
        self.assertEqual(session.calls[1]['url'], f'{BASE}/Items/Filters2')
        self.assertEqual(session.calls[2]['url'], f'{BASE}/Items/Filters')
        self.assertEqual(session.calls[2]['params']['parentId'], 'view-movies')
        self.assertEqual(session.calls[2]['params']['includeItemTypes'], 'Movie')

    def test_auth_error_propagates_without_cascading_requests(self):
        # libraryRows stops after first 401 instead of issuing NextUp / Latest
        lib_session = FakeSession(problem(401, 'Unauthorized'))
        lib_client = JellyfinClient(BASE, IDENTITY, token='TOK-1', username='alice', session=lib_session)
        with self.assertRaises(JellyfinAuthError):
            library.libraryRows(lib_client, 'view-shows', section_type='show')
        self.assertEqual(len(lib_session.calls), 1)

        # detail._attachOnDeck re-raises 401
        od_session = FakeSession(problem(401, 'Unauthorized'))
        od_client = JellyfinClient(BASE, IDENTITY, token='TOK-1', username='alice', session=od_session)
        with self.assertRaises(JellyfinAuthError):
            detail._attachOnDeck(od_client, 'ser-1', detail.element('Directory', {'ratingKey': 'ser-1'}))

        # related.extrasElement re-raises 401
        ex_session = FakeSession(problem(401, 'Unauthorized'))
        ex_client = JellyfinClient(BASE, IDENTITY, token='TOK-1', username='alice', session=ex_session)
        with self.assertRaises(JellyfinAuthError):
            related.extrasElement(ex_client, 'mov-1')

        # watch.fetchSegments and fetchItemChapters re-raise 401
        seg_session = FakeSession(problem(401, 'Unauthorized'))
        seg_client = JellyfinClient(BASE, IDENTITY, token='TOK-1', username='alice', session=seg_session)
        with self.assertRaises(JellyfinAuthError):
            watch.fetchSegments(seg_client, 'mov-1')

        ch_session = FakeSession(problem(401, 'Unauthorized'))
        ch_client = JellyfinClient(BASE, IDENTITY, token='TOK-1', username='alice', session=ch_session)
        with self.assertRaises(JellyfinAuthError):
            watch.fetchItemChapters(ch_client, 'mov-1')

    def test_cold_person_uuid_lookup_uses_items_endpoint_and_name_media_uses_person_param(self):
        person_uuid = 'd95ff10af4c28fa8dee1a2766fb80a1d'
        person_dto = {
            'Id': person_uuid,
            'Name': 'Rebecca Ferguson',
            'Type': 'Person',
            'Overview': 'Swedish actress.',
            'PremiereDate': '1983-10-19T00:00:00.0000000Z',
            'ProductionLocations': ['Stockholm, Sweden'],
            'ImageTags': {'Primary': 'ptag-rf'},
        }
        # Cold UUID lookup must hit GET /Items/{uuid} instead of 404ing on /Persons/{uuid}
        session = FakeSession(
            FakeResponse(200, person_dto),
            FakeResponse(200, {'Items': [{'Id': 'm1', 'Name': 'Dune', 'Type': 'Movie'}], 'TotalRecordCount': 1}),
        )
        srv = make_server(session)

        xml = srv.query(f'/library/people/{person_uuid}')
        self.assertEqual(len(list(xml)), 1)
        self.assertEqual(xml[0].get('tag'), 'Rebecca Ferguson')
        self.assertEqual(xml[0].get('ratingKey'), person_uuid)
        self.assertEqual(session.calls[0]['url'], f'{BASE}/Items/{person_uuid}')

        # Uncached person name on /media passes ?person=<name> instead of bogus personIds=<name>
        media_xml = people.media(srv.client, 'Timothee Chalamet', person_ids={})
        self.assertEqual(len(list(media_xml)), 1)
        self.assertEqual(session.calls[1]['params'].get('person'), 'Timothee Chalamet')
        self.assertNotIn('personIds', session.calls[1]['params'])

    def test_played_false_with_nonzero_playcount_stays_unwatched(self):
        dto = copy.deepcopy(MOVIE_DETAIL)
        dto['UserData'] = {'Played': False, 'PlayCount': 2, 'PlaybackPositionTicks': 0, 'IsFavorite': False}

        elem = items.itemElement(dto)
        self.assertIsNone(elem.get('viewCount'))

        # Played=True with PlayCount=2 still sets viewCount='2'
        dto['UserData']['Played'] = True
        played_elem = items.itemElement(dto)
        self.assertEqual(played_elem.get('viewCount'), '2')

    def test_search_resolve_collections_preserves_jellyfin_and_section_id_sets_parent_id(self):
        collections_resp = {
            'Items': [
                {'Id': 'box-1', 'Name': 'Alien Collection', 'Type': 'BoxSet', 'ChildCount': 4},
            ],
            'TotalRecordCount': 1,
        }
        session = FakeSession(
            USER_VIEWS,
            FakeResponse(200, {'Items': [{'Id': 'm9', 'Name': 'Alien', 'Type': 'Movie'}], 'TotalRecordCount': 1}),
            FakeResponse(200, collections_resp),
        )
        srv = make_server(session)
        srv.query('/library/sections')

        # Single library view in section_ids passes parentId so search stays inside that library
        hubs = srv.hubs(count=10, search_query='Alien', section_ids=['view-4k'])
        self.assertEqual([h.type for h in hubs], ['movie'])
        self.assertEqual(session.calls[1]['params'].get('parentId'), 'view-4k')
        self.assertEqual(session.calls[1]['params'].get('includeItemTypes'), 'Movie')

        # SearchWindow.resolveCollections keeps Jellyfin BoxSet collections intact
        coll_hubs = srv.hubs(count=10, search_query='Alien', section_ids=['collection'])
        resolved = search_win.resolveCollections(coll_hubs)
        self.assertEqual(len(resolved), 1)
        self.assertEqual([c.title for c in resolved[0].items], ['Alien Collection'])

    def test_userselect_other_user_keeps_label_on_wrong_password(self):
        win = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
        win.selected = False
        props = {'pin': 'Other user'}
        item = mock.Mock(
            dataSource=userselect.OTHER_USER,
            getLabel=lambda: 'Other user',
            setProperty=lambda k, v: props.__setitem__(k, v),
        )
        account = mock.Mock(switchHomeUser=mock.Mock(return_value=False))
        with (
            mock.patch.object(userselect.plexapp, 'ACCOUNT', account),
            mock.patch.object(userselect.util, 'messageDialog'),
            mock.patch.object(userselect.busy.BusyWindow, 'create', return_value=mock.Mock()),
        ):
            win.userSelected(item, pin='wrong', username='hidden')

        self.assertEqual(props['pin'], 'Other user')
