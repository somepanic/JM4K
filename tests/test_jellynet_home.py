"""
Home rows from Jellyfin 10.11.5: Continue Watching (/UserItems/Resume), Next Up (/Shows/NextUp),
and Latest per non-excluded video library (/Items/Latest), ordered by the user's DisplayPreferences
homesection0..6 and User.Configuration.OrderedViews / LatestItemsExcludes, plus See more paging.
"""

from types import SimpleNamespace

from jellynet.client import ClientIdentity, JellyfinClient
from jellynet.server import JellyfinServer
from plexnet import plexobjects, video  # noqa: F401 (video registers plexnet item types)

from lib.jellyfin import StoredServer
from lib.windows import section_ids

from .base import KodiTestCase, ensure_plex_interface
from .jellynet_fakes import FakeResponse, FakeSession, problem

IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Living room', 'device-uuid-1')
BASE = 'http://jellyfin.lan:8096'


def make_server(session):
    client = JellyfinClient(BASE, IDENTITY, token='TOK-1', username='alice', session=session)
    return JellyfinServer(StoredServer(BASE, 'srv-1', 'Home Jellyfin'), client)


def display_prefs(custom_prefs=None):
    return FakeResponse(
        200,
        {
            'Id': 'usersettings',
            'Client': 'emby',
            'CustomPrefs': {} if custom_prefs is None else dict(custom_prefs),
        },
    )


def user_me(ordered_views=(), latest_excludes=(), my_media_excludes=()):
    return FakeResponse(
        200,
        {
            'Id': 'u1',
            'Name': 'alice',
            'ServerId': 'srv-1',
            'Configuration': {
                'OrderedViews': list(ordered_views),
                'LatestItemsExcludes': list(latest_excludes),
                'MyMediaExcludes': list(my_media_excludes),
            },
        },
    )


def user_views(*views):
    return FakeResponse(200, {'Items': list(views), 'TotalRecordCount': len(views)})


def view_dto(vid, name, collection_type):
    return {'Id': vid, 'Name': name, 'CollectionType': collection_type, 'ImageTags': {}}


def query_result(items, total=None, start=0):
    return FakeResponse(
        200,
        {
            'Items': list(items),
            'TotalRecordCount': len(items) if total is None else total,
            'StartIndex': start,
        },
    )


def movie_dto(mid, title, runtime_ticks=72_000_000_000, position_ticks=0, played=False, last_played=None):
    user_data = {
        'PlaybackPositionTicks': position_ticks,
        'PlayCount': 1 if played else 0,
        'Played': played,
        'IsFavorite': False,
    }
    if last_played:
        user_data['LastPlayedDate'] = last_played
    return {
        'Id': mid,
        'Name': title,
        'Type': 'Movie',
        'ProductionYear': 2024,
        'PremiereDate': '2024-05-01T00:00:00.0000000Z',
        'RunTimeTicks': runtime_ticks,
        'Overview': f'Plot of {title}.',
        'ImageTags': {'Primary': f'ptag-{mid}'},
        'BackdropImageTags': [f'btag-{mid}'],
        'UserData': user_data,
    }


def series_dto(sid, title, unplayed=4, total=10):
    return {
        'Id': sid,
        'Name': title,
        'Type': 'Series',
        'ProductionYear': 2022,
        'RecursiveItemCount': total,
        'ChildCount': 2,
        'ImageTags': {'Primary': f'stag-{sid}'},
        'UserData': {'UnplayedItemCount': unplayed, 'Played': unplayed == 0},
    }


def episode_dto(
    eid,
    title,
    series_id,
    series_name,
    season=1,
    number=1,
    runtime_ticks=30_000_000_000,
    position_ticks=0,
    last_played=None,
):
    user_data = {
        'PlaybackPositionTicks': position_ticks,
        'PlayCount': 0,
        'Played': False,
    }
    if last_played:
        user_data['LastPlayedDate'] = last_played
    return {
        'Id': eid,
        'Name': title,
        'Type': 'Episode',
        'SeriesId': series_id,
        'SeriesName': series_name,
        'SeriesPrimaryImageTag': f'sptag-{series_id}',
        'SeasonId': f'{series_id}-s{season}',
        'SeasonName': f'Season {season}',
        'ParentIndexNumber': season,
        'IndexNumber': number,
        'RunTimeTicks': runtime_ticks,
        'ImageTags': {'Primary': f'etag-{eid}'},
        'UserData': user_data,
    }


DEFAULT_VIEWS = (
    view_dto('view-movies', 'Movies', 'movies'),
    view_dto('view-shows', 'TV Shows', 'tvshows'),
)


class JellyfinHomeRowsTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        ensure_plex_interface()

    def test_rows_in_the_users_order(self):
        # Custom DisplayPreferences homesection0..6 order (skipping unsupported kinds) and OrderedViews order
        prefs = display_prefs(
            {
                'homesection0': 'smalllibrarytiles',
                'homesection1': 'latestmedia',
                'homesection2': 'livetv',
                'homesection3': 'nextup',
                'homesection4': 'resumevideo',
                'homesection5': 'myrequests',
                'homesection6': 'none',
            }
        )
        session = FakeSession(
            prefs,
            user_me(ordered_views=['view-shows', 'view-movies']),
            user_views(*DEFAULT_VIEWS),
            FakeResponse(200, [series_dto('sh1', 'Andor')]),
            FakeResponse(200, [movie_dto('m1', 'Dune')]),
            query_result([episode_dto('e2', 'Episode 2', 'sh1', 'Andor', 1, 2)], total=1),
            query_result([movie_dto('m0', 'Arrival', position_ticks=6_000_000_000)], total=1),
        )
        srv = make_server(session)

        hubs = srv.hubs(None, count=20, continue_watching_count=40)

        self.assertEqual([h.title for h in hubs], ['Latest TV Shows', 'Latest Movies', 'Next Up', 'Continue Watching'])
        self.assertEqual(
            [c['url'].replace(BASE, '') for c in session.calls],
            [
                '/DisplayPreferences/usersettings',
                '/Users/Me',
                '/UserViews',
                '/Items/Latest',
                '/Items/Latest',
                '/Shows/NextUp',
                '/UserItems/Resume',
            ],
        )
        self.assertEqual(session.calls[0]['params'], {'client': 'emby'})
        self.assertEqual(session.calls[3]['params']['parentId'], 'view-shows')
        self.assertEqual(session.calls[3]['params']['groupItems'], 'true')
        self.assertEqual(session.calls[3]['params']['limit'], 20)
        self.assertEqual(session.calls[4]['params']['parentId'], 'view-movies')
        self.assertEqual(session.calls[5]['params']['limit'], 20)
        self.assertEqual(session.calls[6]['params']['mediaTypes'], 'Video')
        self.assertEqual(session.calls[6]['params']['limit'], 40)
        # MediaBrowser auth header only, never legacy params
        for call in session.calls:
            self.assertTrue(call['headers']['Authorization'].startswith('MediaBrowser '))

        # Fallback order when DisplayPreferences fails (swallows JellyfinError): Resume, Next Up, Latest per library
        fallback_session = FakeSession(
            problem(404, 'Not Found'),
            query_result([movie_dto('m0', 'Arrival', position_ticks=6_000_000_000)], total=1),
            query_result([episode_dto('e2', 'Episode 2', 'sh1', 'Andor', 1, 2)], total=1),
            user_me(),
            user_views(*DEFAULT_VIEWS),
            FakeResponse(200, [movie_dto('m1', 'Dune')]),
            FakeResponse(200, [series_dto('sh1', 'Andor')]),
        )
        fallback_hubs = make_server(fallback_session).hubs(None, count=20)
        self.assertEqual(
            [h.title for h in fallback_hubs],
            ['Continue Watching', 'Next Up', 'Latest Movies', 'Latest TV Shows'],
        )

        # Fallback order when homesection* keys are absent from CustomPrefs
        empty_prefs_session = FakeSession(
            display_prefs({'dashboardTheme': 'dark'}),
            query_result([movie_dto('m0', 'Arrival', position_ticks=6_000_000_000)], total=1),
            query_result([episode_dto('e2', 'Episode 2', 'sh1', 'Andor', 1, 2)], total=1),
            user_me(),
            user_views(*DEFAULT_VIEWS),
            FakeResponse(200, [movie_dto('m1', 'Dune')]),
            FakeResponse(200, [series_dto('sh1', 'Andor')]),
        )
        empty_prefs_hubs = make_server(empty_prefs_session).hubs(None, count=20)
        self.assertEqual(
            [h.title for h in empty_prefs_hubs],
            ['Continue Watching', 'Next Up', 'Latest Movies', 'Latest TV Shows'],
        )

        # homeCapabilities reports available read-only layout with supported sections
        caps = srv.homeCapabilities()
        self.assertEqual(caps.get('state'), 'available')
        self.assertFalse(caps.get('editable'))
        self.assertEqual(caps.get('sections'), ['resume', 'nextup', 'latestmedia'])

    def test_latest_skips_excluded_libraries(self):
        views = (
            view_dto('view-movies', 'Movies', 'movies'),
            view_dto('view-anime', 'Anime', 'tvshows'),
            view_dto('view-docs', 'Documentaries', 'movies'),
            view_dto('view-shows', 'TV Shows', 'tvshows'),
            view_dto('view-music', 'Music', 'music'),
            view_dto('view-boxsets', 'Collections', 'boxsets'),
            view_dto('view-livetv', 'Live TV', 'livetv'),
            view_dto('view-playlists', 'Playlists', 'playlists'),
        )
        session = FakeSession(
            display_prefs({'homesection0': 'latestmedia'}),
            user_me(
                ordered_views=['view-shows', 'view-docs'],
                latest_excludes=['view-anime'],
                my_media_excludes=['view-docs'],
            ),
            user_views(*views),
            # OrderedViews puts view-shows first (view-docs excluded by MyMediaExcludes), then remaining view-movies;
            # view-anime (LatestItemsExcludes) and music/boxsets/livetv/playlists are never queried.
            FakeResponse(200, [series_dto('sh1', 'Andor')]),
            FakeResponse(200, [movie_dto('m1', 'Dune')]),
        )
        srv = make_server(session)

        hubs = srv.hubs(None, count=20)

        self.assertEqual([h.title for h in hubs], ['Latest TV Shows', 'Latest Movies'])
        latest_calls = [c for c in session.calls if c['url'].endswith('/Items/Latest')]
        self.assertEqual([c['params']['parentId'] for c in latest_calls], ['view-shows', 'view-movies'])

    def test_row_ids_stable(self):
        # Numeric library IDs verify SM4K #7: getCleanHubIdentifier(is_home=True) must not collapse them
        numeric_views = (
            view_dto('152896533422071873', 'Movies', 'movies'),
            view_dto('152896533422137409', 'TV Shows', 'tvshows'),
        )

        # First fetch: Continue Watching -> Next Up -> Latest Movies -> Latest TV Shows
        session_a = FakeSession(
            display_prefs({'homesection0': 'resume', 'homesection1': 'nextup', 'homesection2': 'latestmedia'}),
            query_result([movie_dto('m0', 'Arrival', position_ticks=3_000_000_000)], total=1),
            query_result([episode_dto('e1', 'Pilot', 'sh1', 'Andor', 1, 1)], total=1),
            user_me(ordered_views=['152896533422071873', '152896533422137409']),
            user_views(*numeric_views),
            FakeResponse(200, [movie_dto('m1', 'Dune')]),
            FakeResponse(200, [series_dto('sh1', 'Andor')]),
        )
        hubs_a = make_server(session_a).hubs(None, count=20)

        # Second fetch: completely reversed order (Latest TV Shows -> Latest Movies -> Next Up -> Continue Watching)
        session_b = FakeSession(
            display_prefs({'homesection0': 'latestmedia', 'homesection1': 'nextup', 'homesection2': 'resume'}),
            user_me(ordered_views=['152896533422137409', '152896533422071873']),
            user_views(*numeric_views),
            FakeResponse(200, [series_dto('sh1', 'Andor')]),
            FakeResponse(200, [movie_dto('m1', 'Dune')]),
            query_result([episode_dto('e1', 'Pilot', 'sh1', 'Andor', 1, 1)], total=1),
            query_result([movie_dto('m0', 'Arrival', position_ticks=3_000_000_000)], total=1),
        )
        hubs_b = make_server(session_b).hubs(None, count=20)

        home_section = SimpleNamespace(key=None)
        ids_a = {h.title: h.hubIdentifier for h in hubs_a}
        ids_b = {h.title: h.hubIdentifier for h in hubs_b}
        clean_a = {h.title: h.getCleanHubIdentifier(is_home=True) for h in hubs_a}
        clean_b = {h.title: h.getCleanHubIdentifier(is_home=True) for h in hubs_b}
        cat_a = {h.title: section_ids.hubCatalogId(h, home_section) for h in hubs_a}
        cat_b = {h.title: section_ids.hubCatalogId(h, home_section) for h in hubs_b}

        self.assertEqual(ids_a, ids_b)
        self.assertEqual(clean_a, clean_b)
        self.assertEqual(cat_a, cat_b)
        self.assertEqual(len(set(ids_a.values())), 4)
        self.assertEqual(len(set(clean_a.values())), 4, clean_a)
        self.assertEqual(len(set(cat_a.values())), 4, cat_a)

    def test_empty_row_left_out(self):
        # Empty Resume, empty Latest Movies, failing Latest TV Shows -> only Next Up returned
        session = FakeSession(
            display_prefs(),
            query_result([], total=0),
            query_result([episode_dto('e1', 'Pilot', 'sh1', 'Andor', 1, 1)], total=1),
            user_me(),
            user_views(*DEFAULT_VIEWS),
            FakeResponse(200, []),
            problem(500, 'Internal Server Error'),
        )
        srv = make_server(session)

        hubs = srv.hubs(None, count=20)

        self.assertEqual([h.title for h in hubs], ['Next Up'])

        # Unsupported item types only (e.g. Audio/Book) count as an empty row and are omitted
        unsupported_only = FakeSession(
            display_prefs({'homesection0': 'resume'}),
            query_result([{'Id': 'a1', 'Name': 'Track 1', 'Type': 'Audio'}], total=1),
        )
        self.assertEqual(make_server(unsupported_only).hubs(None, count=20), [])

        # 401 auth error stops immediately without issuing further row calls
        auth_session = FakeSession(problem(401, 'Unauthorized'))
        self.assertEqual(make_server(auth_session).hubs(None, count=20), [])
        self.assertEqual(len(auth_session.calls), 1)

    def test_see_more_pages_the_row(self):
        # First get hubs with more=True on all three row types
        session = FakeSession(
            display_prefs(),
            query_result([movie_dto(f'r{i}', f'Resume {i}', position_ticks=1_000_000_000) for i in range(2)], total=5),
            query_result([episode_dto(f'n{i}', f'Next {i}', 'sh1', 'Andor', 1, i + 1) for i in range(2)], total=4),
            user_me(),
            user_views(view_dto('view-movies', 'Movies', 'movies')),
            FakeResponse(200, [movie_dto(f'm{i}', f'Film {i}') for i in range(2)]),
            # See more: Resume limit=0 count, then page offset=2 limit=2
            query_result([], total=5),
            query_result([movie_dto('r2', 'Resume 2'), movie_dto('r3', 'Resume 3')], total=5, start=2),
            # See more: Next Up limit=0 count, then page offset=2 limit=2
            query_result([], total=4),
            query_result(
                [episode_dto('n2', 'Next 2', 'sh1', 'Andor', 1, 3), episode_dto('n3', 'Next 3', 'sh1', 'Andor', 1, 4)],
                total=4,
                start=2,
            ),
            # See more: Latest limit=0 count, then page offset=20 limit=10
            FakeResponse(200, [movie_dto(f'm{i}', f'Film {i}') for i in range(35)]),
            FakeResponse(200, [movie_dto(f'm{i}', f'Film {i}') for i in range(35)]),
        )
        srv = make_server(session)

        hubs = srv.hubs(None, count=2)
        self.assertEqual([h.more.asBool() for h in hubs], [True, True, True])
        resume_hub, nextup_hub, latest_hub = hubs
        self.assertEqual(resume_hub.key, '/jellyfin/home/resume')
        self.assertEqual(nextup_hub.key, '/jellyfin/home/nextup')
        self.assertEqual(latest_hub.key, '/jellyfin/home/latest/view-movies')

        # Resume count & page
        resume_count = srv.query(resume_hub.key, limit=0)
        self.assertEqual(resume_count.get('totalSize'), '5')
        self.assertEqual(len(resume_count), 0)
        self.assertEqual(session.calls[6]['params']['limit'], 0)
        self.assertEqual(session.calls[6]['params']['mediaTypes'], 'Video')

        resume_page = plexobjects.listItems(srv, resume_hub.key, offset=2, limit=2)
        self.assertEqual([i.title for i in resume_page], ['Resume 2', 'Resume 3'])
        self.assertEqual(resume_page.totalSize.asInt(), 5)
        self.assertEqual(resume_page.offset.asInt(), 2)
        self.assertEqual(session.calls[7]['params']['startIndex'], 2)
        self.assertEqual(session.calls[7]['params']['limit'], 2)

        # Next Up count & page
        nextup_count = srv.query(nextup_hub.key, limit=0)
        self.assertEqual(nextup_count.get('totalSize'), '4')
        self.assertEqual(len(nextup_count), 0)

        nextup_page = plexobjects.listItems(srv, nextup_hub.key, offset=2, limit=2)
        self.assertEqual([i.title for i in nextup_page], ['Next 2', 'Next 3'])
        self.assertEqual(nextup_page.totalSize.asInt(), 4)
        self.assertEqual(nextup_page.offset.asInt(), 2)
        self.assertEqual(session.calls[9]['params']['startIndex'], 2)
        self.assertEqual(session.calls[9]['params']['limit'], 2)

        # Latest count & page (sliced client-side since /Items/Latest has no startIndex)
        latest_count = srv.query(latest_hub.key, limit=0)
        self.assertEqual(latest_count.get('totalSize'), '35')
        self.assertEqual(len(latest_count), 0)

        latest_page = plexobjects.listItems(srv, latest_hub.key, offset=20, limit=10)
        self.assertEqual([i.title for i in latest_page], [f'Film {i}' for i in range(20, 30)])
        self.assertEqual(latest_page.totalSize.asInt(), 35)
        self.assertEqual(latest_page.offset.asInt(), 20)

        # Jellyfin error on See more returns None (same contract as PlexServer.query)
        err_srv = make_server(FakeSession(problem(503, 'Service Unavailable')))
        self.assertIsNone(err_srv.query('/jellyfin/home/resume', limit=0))

        # Unknown home row route returns empty container without calling Jellyfin
        unknown = make_server(FakeSession()).query('/jellyfin/home/unknown')
        self.assertEqual(unknown.get('size'), '0')

    def test_resume_point_from_ticks(self):
        # 10,000 ticks = 1 ms; 9,000,000,000 ticks = 900,000 ms (15 min); 72,000,000,000 ticks = 7,200,000 ms (120 min)
        resumed_movie = movie_dto(
            'm1',
            'Dune',
            runtime_ticks=72_000_000_000,
            position_ticks=9_000_000_000,
            played=False,
            last_played='2026-11-24T20:30:00.0000000Z',
        )
        resumed_ep = episode_dto(
            'e1',
            'Good News About Hell',
            'sh1',
            'Severance',
            season=1,
            number=1,
            runtime_ticks=30_000_000_000,
            position_ticks=12_345_678_900,  # integer floor -> 1,234,567 ms
            last_played='2026-11-24T19:00:00.0000000Z',
        )
        session = FakeSession(
            display_prefs({'homesection0': 'resume'}),
            query_result([resumed_movie, resumed_ep], total=2),
        )
        srv = make_server(session)

        hubs = srv.hubs(None, count=20)
        self.assertEqual(len(hubs), 1)
        dune, ep = hubs[0].items

        self.assertEqual((dune.type, dune.ratingKey, dune.title), ('movie', 'm1', 'Dune'))
        self.assertEqual((dune.duration.asInt(), dune.viewOffset.asInt()), (7_200_000, 900_000))
        self.assertTrue(dune.in_progress)
        self.assertFalse(dune.isPlayed)
        self.assertEqual(
            dune.defaultThumb.asTranscodedImageURL(300, 450),
            f'{BASE}/Items/m1/Images/Primary?tag=ptag-m1&maxWidth=300&maxHeight=450',
        )

        self.assertEqual((ep.type, ep.ratingKey, ep.title), ('episode', 'e1', 'Good News About Hell'))
        self.assertEqual((ep.grandparentTitle, ep.parentIndex.asInt(), ep.index.asInt()), ('Severance', 1, 1))
        self.assertEqual((ep.duration.asInt(), ep.viewOffset.asInt()), (3_000_000, 1_234_567))
        self.assertTrue(ep.in_progress)
        self.assertEqual(ep.grandparentThumb, '/Items/sh1/Images/Primary?tag=sptag-sh1')
