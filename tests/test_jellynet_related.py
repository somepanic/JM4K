"""
Similar titles, extras (SpecialFeatures only — NO trailers per J-D10), people (/Persons/{name} and library
filmography via /Items?personIds= per D10), and theme music (/Items/{itemId}/ThemeMedia gated on theme_music
setting per D15) against Jellyfin 10.11.5 (Task 4.2).

Responses are written inline in the shape of Jellyfin 10.11.5 OpenAPI (BaseItemDto, BaseItemDtoQueryResult,
AllThemeMediaResult).
"""

import copy
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

from kodienv import ENV

ENV.abort_requested = True
from plexnet import media, plexapp
from plexnet import util as pnUtil

from lib.windows import person

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem
from .test_jellynet_detail import BASE, MOVIE_DETAIL, make_server, make_tile

SIMILAR_RESPONSE = {
    'Items': [
        {
            'Id': 'mov-sim-1',
            'Name': 'Twister',
            'Type': 'Movie',
            'ProductionYear': 1996,
            'ImageTags': {'Primary': 'ptag-sim-1'},
        },
        {
            'Id': 'ser-sim-2',
            'Name': 'Storm Chasers',
            'Type': 'Series',
            'ProductionYear': 2007,
            'ImageTags': {'Primary': 'ptag-sim-2'},
        },
        {
            'Id': 'mov-sim-3',
            'Name': 'Into the Storm',
            'Type': 'Movie',
            'ProductionYear': 2014,
            'ImageTags': {'Primary': 'ptag-sim-3'},
        },
    ],
    'TotalRecordCount': 3,
}

SPECIAL_FEATURES = [
    {
        'Id': 'ex-1',
        'Name': 'Making the Storms',
        'Type': 'Video',
        'ExtraType': 'BehindTheScenes',
        'RunTimeTicks': 4_500_000_000,  # 450s = 450,000 ms
        'ImageTags': {'Primary': 'ex-tag-1'},
    },
    {
        'Id': 'ex-2',
        'Name': 'Cut Ending',
        'Type': 'Video',
        'ExtraType': 'DeletedScene',
        'RunTimeTicks': 1_200_000_000,
    },
    {
        'Id': 'ex-3',
        'Name': 'Cast Q&A',
        'Type': 'Video',
        'ExtraType': 'Interview',
    },
    {
        'Id': 'ex-4',
        'Name': 'VFX Breakdown',
        'Type': 'Video',
        'ExtraType': 'Featurette',
    },
    {
        'Id': 'ex-5',
        'Name': 'Gag Reel',
        'Type': 'Video',
        'ExtraType': 'Clip',
    },
    {
        # Trailers must be excluded per J-D10 even if returned by SpecialFeatures
        'Id': 'ex-trailer',
        'Name': 'Official Trailer',
        'Type': 'Video',
        'ExtraType': 'Trailer',
        'RunTimeTicks': 1_500_000_000,
    },
]

DAISY_DTO = {
    'Id': 'p1',
    'Name': 'Daisy Edgar-Jones',
    'Type': 'Person',
    'Overview': 'An English actress.',
    'PremiereDate': '1998-05-24T00:00:00.0000000Z',
    'ProductionLocations': ['London, England, UK'],
    'ImageTags': {'Primary': 'img-p1-full'},
}

PERSON_TITLES = {
    'Items': [
        {
            'Id': 'mov-1',
            'Name': 'Twisters',
            'Type': 'Movie',
            'ProductionYear': 2024,
            'ImageTags': {'Primary': 'ptag-mov1'},
        },
        {
            'Id': 'ser-9',
            'Name': 'Normal People',
            'Type': 'Series',
            'ProductionYear': 2020,
            'ImageTags': {'Primary': 'ptag-ser9'},
        },
    ],
    'TotalRecordCount': 2,
}

THEME_MEDIA = {
    'ThemeSongsResult': {
        'Items': [
            {
                'Id': 'theme-song-1',
                'Name': 'Main Theme',
                'Type': 'Audio',
                'RunTimeTicks': 300_000_000,
            }
        ],
        'TotalRecordCount': 1,
        'OwnerId': 'mov-1',
    },
    'ThemeVideosResult': {'Items': [], 'TotalRecordCount': 0},
    'SoundtrackSongsResult': {'Items': [], 'TotalRecordCount': 0},
}


def row_size(item):
    try:
        return int(item.relatedCount)
    except ValueError:
        return 0


class JellyfinRelatedTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_similar_row(self):
        session = FakeSession(FakeResponse(200, SIMILAR_RESPONSE))
        srv = make_server(session)

        related = make_tile(srv, MOVIE_DETAIL).getRelated(0, 8)

        self.assertEqual(session.calls[0]['url'], f'{BASE}/Items/mov-1/Similar')
        self.assertEqual(session.calls[0]['params']['limit'], 36)
        self.assertEqual(
            [(r.title, r.type) for r in related],
            [('Twister', 'movie'), ('Storm Chasers', 'show'), ('Into the Storm', 'movie')],
        )

        # Count & pagination
        count_srv = make_server(FakeSession(FakeResponse(200, SIMILAR_RESPONSE)))
        self.assertEqual(row_size(make_tile(count_srv, MOVIE_DETAIL)), 3)

        page_srv = make_server(FakeSession(FakeResponse(200, SIMILAR_RESPONSE)))
        paged = make_tile(page_srv, MOVIE_DETAIL).getRelated(offset=1, limit=1)
        self.assertEqual([r.title for r in paged], ['Storm Chasers'])

        # Limit capped at 50
        cap_session = FakeSession(FakeResponse(200, SIMILAR_RESPONSE))
        make_server(cap_session).query('/library/metadata/mov-1/similar?count=100')
        self.assertEqual(cap_session.calls[0]['params']['limit'], 50)

        # /library/metadata/{id}/related returns a <MediaContainer> with a <Hub> of similar items
        hub_session = FakeSession(FakeResponse(200, SIMILAR_RESPONSE))
        hub_xml = make_server(hub_session).query('/library/metadata/mov-1/related?count=12')
        self.assertEqual(hub_xml.tag, 'MediaContainer')
        self.assertEqual(len(list(hub_xml)), 1)
        self.assertEqual(hub_xml[0].tag, 'Hub')
        self.assertEqual(hub_xml[0].get('title'), 'Similar')
        self.assertEqual(len(list(hub_xml[0])), 3)
        self.assertEqual(hub_session.calls[0]['url'], f'{BASE}/Items/mov-1/Similar')

        # Server failure yields empty count cleanly
        err_srv = make_server(FakeSession(problem(503, 'Unavailable')))
        self.assertEqual(row_size(make_tile(err_srv, MOVIE_DETAIL)), 0)

    def test_extras_row(self):
        # 1. Reload with includeExtras=1 when SpecialFeatureCount > 0 populates movie.extras
        detail = copy.deepcopy(MOVIE_DETAIL)
        detail['SpecialFeatureCount'] = len(SPECIAL_FEATURES)
        session = FakeSession(FakeResponse(200, detail), FakeResponse(200, SPECIAL_FEATURES))
        srv = make_server(session)

        movie = make_tile(srv, detail).reload(includeExtras=1, includeExtrasCount=10)

        self.assertEqual(
            [c['url'] for c in session.calls], [f'{BASE}/Items/mov-1', f'{BASE}/Items/mov-1/SpecialFeatures']
        )
        # J-D10: NEVER call /LocalTrailers and NEVER expose trailers in extras
        self.assertFalse(any('LocalTrailers' in c['url'] for c in session.calls))
        self.assertEqual(
            [(e.title, e.extraType.asInt(), e.duration.asInt()) for e in movie.extras],
            [
                ('Making the Storms', media.METADATA_RELATED_BEHIND_THE_SCENES, 450_000),
                ('Cut Ending', media.METADATA_RELATED_DELETED_SCENE, 120_000),
                ('Cast Q&A', media.METADATA_RELATED_INTERVIEW, 0),
                ('VFX Breakdown', media.METADATA_RELATED_FEATURETTE, 0),
                ('Gag Reel', media.METADATA_RELATED_OTHER, 0),
            ],
        )
        self.assertTrue(all(e.extraType.asInt() != media.METADATA_RELATED_TRAILER for e in movie.extras))
        self.assertEqual(movie.extras[0].thumb, '/Items/ex-1/Images/Primary?tag=ex-tag-1')

        # 2. Direct /library/metadata/{id}/extras (fetchExternalExtras) calls SpecialFeatures and filters trailers
        ext_session = FakeSession(FakeResponse(200, SPECIAL_FEATURES), FakeResponse(200, SPECIAL_FEATURES[0]))
        ext_srv = make_server(ext_session)
        ext_movie = make_tile(ext_srv, MOVIE_DETAIL)
        ext_movie.fetchExternalExtras()
        self.assertEqual(ext_session.calls[0]['url'], f'{BASE}/Items/mov-1/SpecialFeatures')
        self.assertEqual(len(ext_movie.extras), 5)
        self.assertFalse(any(e.extraType.asInt() == media.METADATA_RELATED_TRAILER for e in ext_movie.extras))
        # Reloading an extra clip works via /library/metadata/{extra_id}
        reloaded_extra = ext_movie.extras[0].reload()
        self.assertFalse(reloaded_extra.reloadFailed)
        self.assertEqual(reloaded_extra.title, 'Making the Storms')

        # 3. Item with no SpecialFeatureCount makes no extra SpecialFeatures call on reload
        no_extra_session = FakeSession(FakeResponse(200, MOVIE_DETAIL))
        no_extra_movie = make_tile(make_server(no_extra_session), MOVIE_DETAIL).reload(includeExtras=1)
        self.assertEqual(len(no_extra_session.calls), 1)
        self.assertEqual(len(no_extra_movie.extras), 0)

        # 4. SpecialFeatures failure during reload still loads movie cleanly
        fail_session = FakeSession(FakeResponse(200, detail), problem(503, 'Unavailable'))
        ok_movie = make_tile(make_server(fail_session), detail).reload(includeExtras=1)
        self.assertFalse(ok_movie.reloadFailed)
        self.assertEqual(len(ok_movie.extras), 0)

    def test_person_titles_in_library(self):
        # Reload movie -> get role -> role.getDetails() calls GET /Persons/{name} -> libraryFilmography calls /Items?personIds=
        session = FakeSession(
            FakeResponse(200, MOVIE_DETAIL),
            FakeResponse(200, DAISY_DTO),
            FakeResponse(200, PERSON_TITLES),
        )
        srv = make_server(session)
        movie = make_tile(srv, MOVIE_DETAIL).reload()
        role = movie.roles[0]

        self.assertEqual(person.personKey(role), 'p1')
        self.assertEqual(len(session.calls), 1)

        details = role.getDetails()
        self.assertEqual(session.calls[1]['url'], f'{BASE}/Persons/Daisy%20Edgar-Jones')
        self.assertEqual(
            (details['name'], details['summary'], details['birthDate'], details['birthPlace'], details['thumb']),
            (
                'Daisy Edgar-Jones',
                'An English actress.',
                '1998-05-24',
                'London, England, UK',
                '/Items/p1/Images/Primary?tag=img-p1-full',
            ),
        )

        # Filmography from this server's libraries only (D10: never asks plex.tv)
        with (
            mock.patch.object(plexapp, 'ACCOUNT', mock.Mock(authToken='token')),
            mock.patch('requests.get', side_effect=AssertionError('asked plex.tv')) as plex_tv,
        ):
            got = []
            task = person.PersonFilmographyTask(role, [srv], got.append)
            task.isCanceled = lambda: False
            task.run()
            plex_tv.assert_not_called()

        self.assertEqual(session.calls[2]['url'], f'{BASE}/Items')
        self.assertEqual(session.calls[2]['params']['personIds'], 'p1')
        self.assertEqual(session.calls[2]['params']['recursive'], 'true')
        self.assertEqual(session.calls[2]['params']['includeItemTypes'], 'Movie,Series')
        self.assertEqual([(i.TYPE, i.title) for i in got[0]], [('movie', 'Twisters'), ('show', 'Normal People')])

        # Multi-page filmography fetches subsequent pages via startIndex
        page1 = {'Items': PERSON_TITLES['Items'][:1], 'TotalRecordCount': 2}
        page2 = {'Items': PERSON_TITLES['Items'][1:], 'TotalRecordCount': 2}
        paged_session = FakeSession(FakeResponse(200, page1), FakeResponse(200, page2))
        paged_items = person.libraryFilmography(make_server(paged_session), 'p1')
        self.assertEqual(len(paged_session.calls), 2)
        self.assertEqual(paged_session.calls[1]['params']['startIndex'], 1)
        self.assertEqual([i.title for i in paged_items], ['Twisters', 'Normal People'])

        # Direct lookup by name (/library/people/Daisy%20Edgar-Jones) resolves and caches id for /media
        name_session = FakeSession(FakeResponse(200, DAISY_DTO), FakeResponse(200, PERSON_TITLES))
        name_srv = make_server(name_session)
        person_xml = name_srv.query('/library/people/Daisy%20Edgar-Jones')
        self.assertEqual(person_xml[0].get('ratingKey'), 'p1')
        self.assertEqual(name_session.calls[0]['url'], f'{BASE}/Persons/Daisy%20Edgar-Jones')
        media_xml = name_srv.query('/library/people/Daisy%20Edgar-Jones/media')
        self.assertEqual(len(list(media_xml)), 2)
        self.assertEqual(name_session.calls[1]['params']['personIds'], 'p1')

        # 404 on /Persons/... preserves basic cast details without raising
        err_session = FakeSession(FakeResponse(200, MOVIE_DETAIL), problem(404, 'Not Found'))
        err_role = make_tile(make_server(err_session), MOVIE_DETAIL).reload().roles[0]
        fallback = err_role.getDetails()
        self.assertEqual((fallback['name'], fallback['role']), ('Daisy Edgar-Jones', 'Kate'))

    def test_theme_url_only_when_setting_on(self):
        # Default setting is OFF (D15): themeUrl returns None with ZERO HTTP requests
        self.assertEqual(pnUtil.INTERFACE.getThemeMusicValue(), 0)
        off_session = FakeSession()
        off_srv = make_server(off_session)
        movie = make_tile(off_srv, MOVIE_DETAIL)

        self.assertIsNone(off_srv.themeUrl(movie))
        self.assertEqual(len(off_session.calls), 0)

        # Turn theme_music ON -> calls GET /Items/{itemId}/ThemeMedia and returns /Audio/{id}/stream?static=true|Authorization=...
        on_session = FakeSession(FakeResponse(200, THEME_MEDIA))
        on_srv = make_server(on_session)
        on_movie = make_tile(on_srv, MOVIE_DETAIL)

        with mock.patch.object(pnUtil.INTERFACE, 'getThemeMusicValue', return_value=50):
            url = on_srv.themeUrl(on_movie)

        self.assertEqual(len(on_session.calls), 1)
        self.assertEqual(on_session.calls[0]['method'], 'GET')
        self.assertEqual(on_session.calls[0]['url'], f'{BASE}/Items/mov-1/ThemeMedia')
        self.assertEqual(on_session.calls[0]['params']['inheritFromParent'], 'true')

        self.assertIsNotNone(url)
        base_part, sep, header_part = url.partition('|')
        self.assertEqual(sep, '|')
        parsed = urlsplit(base_part)
        self.assertEqual(f'{parsed.scheme}://{parsed.netloc}{parsed.path}', f'{BASE}/Audio/theme-song-1/stream')
        self.assertEqual(parse_qs(parsed.query).get('static'), ['true'])
        self.assertNotIn('api_key', parsed.query.lower())
        self.assertNotIn('TOK-1', base_part)
        self.assertTrue(header_part.startswith('Authorization='))
        self.assertEqual(unquote(header_part[len('Authorization=') :]), on_srv.client.auth_header())
        self.assertFalse(ENV.logged('TOK-1'))

        # Empty ThemeSongsResult -> None
        empty_theme = {'ThemeSongsResult': {'Items': [], 'TotalRecordCount': 0}}
        empty_srv = make_server(FakeSession(FakeResponse(200, empty_theme)))
        with mock.patch.object(pnUtil.INTERFACE, 'getThemeMusicValue', return_value=50):
            self.assertIsNone(empty_srv.themeUrl(make_tile(empty_srv, MOVIE_DETAIL)))

        # Server error -> None without raising
        err_srv = make_server(FakeSession(problem(403, 'Forbidden')))
        with mock.patch.object(pnUtil.INTERFACE, 'getThemeMusicValue', return_value=50):
            self.assertIsNone(err_srv.themeUrl(make_tile(err_srv, MOVIE_DETAIL)))
