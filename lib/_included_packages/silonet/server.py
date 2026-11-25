"""
SiloServer: the server object Uno's plexnet object model and screens talk to. query() answers the Plex paths they
ask for from Silo's API, as Plex-shaped XML, so plexnet's classes keep working. A path not mapped yet answers an
empty container (logged once): each port phase maps the paths its screens need.
"""

import functools
import re
from urllib.parse import parse_qsl
from xml.etree import ElementTree

from plexnet.signalsmixin import SignalsMixin

from . import (
    detail,
    home,
    library,
    lists,
    people,
    playback,
    related,
    search,
    shows,
    streaminfo,
    trackprefs,
    trickplay,
    watch,
    watchstate,
)
from .client import log
from .errors import SiloError
from .items import container

# Silo library types to the Plex section types plexlibrary knows. A mixed library (movies and series together) is one
# grid (D24): a film library's, whose tiles open as their own type
SECTION_TYPES = {'movies': 'movie', 'series': 'show', 'mixed': 'movie'}
# a library's grid (plexlibrary.LibrarySection.all()) and its sort menu: /library/sections/<library id>/all|sorts
LIBRARY_PATH = re.compile(r'^/library/sections/([^/]+)/(all|sorts|firstCharacter|filters|genre|contentRating)$')
# an item's detail (PlexObject.reload())
METADATA_PATH = re.compile(r'^/library/metadata/([^/]+)$')
# pre-play's Refresh metadata (an admin's): Silo refreshes the item's metadata itself
REFRESH_PATH = re.compile(r'^/library/metadata/([^/]+)/refresh$')
# a library's Scan Library Files (an admin's, the sidebar's library menu): Silo scans the library
SCAN_PATH = re.compile(r'^/library/sections/([^/]+)/refresh$')
# a show's seasons or a season's episodes (their key), and a show's every episode (Show.episodes())
CHILDREN_PATH = re.compile(r'^/library/metadata/([^/]+)/(children|allLeaves)$')
# an episode tile's season, by number (silonet.items)
SEASON_PATH = re.compile(r'^/library/metadata/([^/]+)/season/(\d+)$')
# a title's Related row (RelatedMixin.getRelated())
SIMILAR_PATH = re.compile(r'^/library/metadata/([^/]+)/similar$')
# a person, and their titles in the libraries (Role.getDetails(), lib/windows/person.py libraryFilmography())
PEOPLE_PATH = re.compile(r'^/library/people/([^/]+)(/media)?$')
# an item's watchlist state (the watchlist code's is_watchlisted(): Discover's path)
USERSTATE_PATH = re.compile(r'^/library/metadata/([^/]+)/userState$')
# a subtitle file's stream key (silonet.detail), resolved by buildUrl() from the playing attempt's plan
SUBTITLE_KEY = '/silo/subtitles/'
# a collection's titles (plexlibrary.Collection.all(): its key)
COLLECTION_PATH = re.compile(r'^/library/collections/([^/]+)/children$')


class SiloConnection:
    """A Silo server's one connection, its base URL: what plexnet reads of PlexServer.activeConnection."""

    def __init__(self, address):
        self.address = address


class SiloServer(SignalsMixin):
    offline = False
    suspect = False
    gone = False
    owned = True
    allowsMediaDeletion = False  # no deleting through Silo (pre-play's More menu reads it)
    platform = 'silo'
    pendingReachabilityRequests = 0  # Plex's connection tests; a Silo server has one address
    # What Plex Uno's media decision engine asks a server (plexnet mediadecisionengine.py). Silo decides each attempt
    # itself (silonet.playback), so these only let the engine pick a version and its tracks.
    supportsVideoTranscoding = True
    supportsVideoRemuxOnly = False
    supportsAudioTranscoding = True
    # the player's playback info panel (seekdialog.py) reads this and anyLANConnection; the playing item's session
    # comes from findVideoSession(), as Silo lists no Plex sessions
    sessions = ()
    # theme music (lib/windows/mixins/thememusic.py) downloads a theme first unless the server is local
    isLocal = False
    # the formats Kodi plays a theme song in (ThemePlaybackRequest): Silo sends the original when one matches
    THEME_FORMATS = ('mp3', 'mp4', 'm4a', 'flac', 'ogg', 'opus', 'wav', 'aac')

    def __init__(self, stored, client):
        SignalsMixin.__init__(self)  # the seek dialog's timeline and stream-terminated callbacks
        self.stored = stored
        self.client = client
        self.uuid = stored.serverId
        self.name = stored.name or stored.baseUrl
        self.activeConnection = SiloConnection(client.baseUrl)
        self.session = client.session  # plexnet passes its methods to query() (method=server.session.put)
        self._unmapped = set()
        self._routes = {'/library/sections': self._sections}
        self._homeCapabilities = None
        self._libraryTypes = {}  # library id -> Plex section type, from the library list (a collection's subtype)
        self._libraryPaths = None  # library id -> its folders on the server (libraryLocations())
        self.playback = playback.Playback(client, getattr(client.device, 'clientVersion', ''))
        self._subtitleResults = {}  # a subtitle search result's key -> the result, for its download
        self._previews = {}  # file id -> its item's id, for a played file with seek-bar previews
        # item id -> the file a watch party plays of it (D26): every member plays the room's file, and only it
        self.pinnedFiles = {}

    def __repr__(self):
        return f'<SiloServer {self.name}>'

    @property
    def anyLANConnection(self):
        """Whether Silo sees this connection as local (GET /api/v2/playback/connection: local, remote or
        provider:<name>), what the playback info panel's Mode shows as "lan (verified)" or "remote". Not when Silo
        doesn't say."""
        try:
            answer = self.client.request('GET', '/api/v2/playback/connection') or {}
        except SiloError:
            return False
        return answer.get('connection') == 'local'

    def query(self, path, method=None, raw=False, **kwargs):
        """The answer to a Plex path as XML; None when Silo couldn't answer (as PlexServer.query returns)."""
        route, _, queryString = path.partition('?')
        route = route.rstrip('/') or '/'
        handler = self._routes.get(route)
        if handler is None and route in watchstate.ROUTES:
            handler = functools.partial(watchstate.change, self.client, route, dict(parse_qsl(queryString)))
        if handler is None and route in lists.ACTIONS and kwargs.get('ratingKey'):
            handler = functools.partial(
                lists.change, self.client, lists.WATCHLIST, kwargs['ratingKey'], lists.ACTIONS[route]
            )
        if handler is None and route == '/library/sections/watchlist/all':
            handler = functools.partial(
                lists.items, self.client, lists.WATCHLIST, kwargs.get('offset'), kwargs.get('limit')
            )
        userState = USERSTATE_PATH.match(route) if handler is None else None
        if userState:
            handler = functools.partial(lists.userState, self.client, userState.group(1))
        if handler is None and route.startswith(home.ROW_PATH):
            handler = functools.partial(self._row, route, kwargs.get('offset'), kwargs.get('limit'))
        libraryPath = LIBRARY_PATH.match(route) if handler is None else None
        if libraryPath and libraryPath.group(2) == 'sorts':
            handler = library.sortsContainer
        elif libraryPath and libraryPath.group(2) == 'filters':
            handler = functools.partial(library.filtersContainer, libraryPath.group(1))
        elif libraryPath and libraryPath.group(2) in library.FILTER_PARAMS:
            handler = functools.partial(library.filterChoices, self.client, libraryPath.group(1), libraryPath.group(2))
        elif libraryPath and libraryPath.group(2) == 'firstCharacter':
            handler = functools.partial(
                library.jumpBar, self.client, libraryPath.group(1), dict(parse_qsl(queryString))
            )
        elif libraryPath:
            handler = functools.partial(
                library.libraryItems,
                self.client,
                libraryPath.group(1),
                dict(parse_qsl(queryString)),
                kwargs.get('offset'),
                kwargs.get('limit'),
                self._libraryTypes.get(libraryPath.group(1), ''),
            )
        scanPath = SCAN_PATH.match(route) if handler is None else None
        if scanPath:
            handler = functools.partial(
                self.client.request, 'POST', '/api/v2/scan', json={'library_id': scanPath.group(1)}
            )
        refreshPath = REFRESH_PATH.match(route) if handler is None else None
        if refreshPath:
            handler = functools.partial(
                self.client.request, 'POST', f'/api/v2/admin/items/{refreshPath.group(1)}/refresh-metadata'
            )
        metadataPath = METADATA_PATH.match(route) if handler is None else None
        if metadataPath:
            handler = functools.partial(detail.itemDetail, self.client, metadataPath.group(1), kwargs.get('params'))
        childrenPath = CHILDREN_PATH.match(route) if handler is None else None
        if childrenPath and childrenPath.group(2) == 'children':
            handler = functools.partial(shows.children, self.client, childrenPath.group(1))
        elif childrenPath:
            handler = functools.partial(
                shows.allLeaves, self.client, childrenPath.group(1), kwargs.get('offset'), kwargs.get('limit')
            )
        seasonPath = SEASON_PATH.match(route) if handler is None else None
        if seasonPath:
            handler = functools.partial(shows.seasonByNumber, self.client, seasonPath.group(1), seasonPath.group(2))
        similarPath = SIMILAR_PATH.match(route) if handler is None else None
        if similarPath:
            count = (kwargs.get('params') or {}).get('count') or dict(parse_qsl(queryString)).get('count')
            handler = functools.partial(
                related.similar, self.client, similarPath.group(1), count, kwargs.get('offset'), kwargs.get('limit')
            )
        peoplePath = PEOPLE_PATH.match(route) if handler is None else None
        if peoplePath:
            handler = functools.partial(
                people.media if peoplePath.group(2) else people.person, self.client, peoplePath.group(1)
            )
        collectionPath = COLLECTION_PATH.match(route) if handler is None else None
        if collectionPath:
            handler = functools.partial(
                library.collectionItems,
                self.client,
                collectionPath.group(1),
                dict(parse_qsl(queryString)),
                kwargs.get('offset'),
                kwargs.get('limit'),
            )
        if handler is None:
            if route not in self._unmapped:
                self._unmapped.add(route)
                log(f'not mapped yet: {route}')
            return container()
        try:
            return handler()
        except SiloError as e:
            log(f'{route} failed: {e.status} {e.type}')
            return None

    def _row(self, route, offset=None, limit=None):
        """A row's items for its See more grid, a Home row's or a library's (its key, silonet.home/library): totalSize
        is what Silo answers of the row, and a page (offset, limit) is cut from that; limit 0 asks for the count."""
        if route.startswith(library.LIBRARY_ROW_PATH):
            libraryId, _, sectionId = route[len(library.LIBRARY_ROW_PATH) :].partition('/')
            items = library.libraryRowItems(self.client, libraryId, sectionId)
        else:
            rowId = route[len(home.ROW_PATH) :]
            items = lists.rowItems(self.client, rowId)
            if items is None:
                items = home.rowItems(self.client, rowId, self.homeCapabilities())
        start = offset or 0
        page = [] if limit == 0 else items[start : None if limit is None else start + limit]
        root = container(page)
        root.set('totalSize', str(len(items)))
        return root

    def _sections(self):
        data = self.client.request('GET', '/api/v2/user/libraries') or {}
        sections = []
        for entry in data.get('items') or []:
            kind = SECTION_TYPES.get(entry.get('type'))
            if kind is None:
                continue
            self._libraryTypes[entry['id']] = kind
            sections.append(
                ElementTree.Element(
                    'Directory',
                    {
                        'key': entry['id'],
                        'uuid': entry['id'],
                        'title': entry.get('name') or '',
                        'type': kind,
                        'thumb': entry.get('poster_url') or '',
                        # a mixed library's grid is a film library's; its count says "Movies & Shows" (library_grid)
                        **({'mixed': '1'} if entry.get('type') == 'mixed' else {}),
                    },
                )
            )
        return container(sections)

    def libraryLocations(self, libraryId):
        """A library's folders on the server, for path mapping (plexlibrary.LibrarySection.locations): GET
        /api/v2/libraries, read once. None when Silo couldn't answer, asked again next time; none when it refuses an
        account that isn't an admin (403)."""
        if self._libraryPaths is None:
            try:
                data = self.client.request('GET', '/api/v2/libraries') or {}
            except SiloError as e:
                return [] if e.status == 403 else None
            self._libraryPaths = {str(entry.get('id')): entry.get('paths') or [] for entry in data.get('items') or []}
        return list(self._libraryPaths.get(str(libraryId), []))

    def buildUrl(self, path, includeToken=False):
        """An absolute URL for a path. A subtitle file's key (silonet.detail: /silo/subtitles/<file>/<source>/<ref>)
        is the URL the playing attempt's plan lists for it."""
        if path.startswith(SUBTITLE_KEY):
            fileId, source, ref = (path[len(SUBTITLE_KEY) :].split('?')[0].split('/') + ['', '', ''])[:3]
            attempt = self.playback.attemptFor(fileId)
            url = attempt.sidecarUrl(source, ref) if attempt is not None else None
            if url:
                return url
        return path if '://' in path else self.client.baseUrl + path

    def getImageTranscodeURL(self, path, width, height, **extraOpts):
        return self.buildUrl(path)

    def isReachable(self, onlySupported=True):
        return True

    def isLocalConnection(self):
        return True  # Silo applies its own local and remote stream limits to each attempt

    def supportsFeature(self, feature):
        return False  # Plex server features (transcoder, BIF indexes); Silo's are asked of it directly

    def startPlayback(
        self, item, part, seconds=0.0, audioIndex=None, subtitleIndex=None, quality='auto', bandwidthCap=None, hdr=True
    ):
        """Starts a Silo attempt for the item's version (part: its Media's Part, whose id is Silo's file id) and
        returns it (silonet.playback.Attempt); PlaybackRefused when Silo decides it can't play."""
        key = str(item.ratingKey)
        pinned = self.pinnedFiles.get(key)
        return self.playback.start(
            key, pinned or part.id, seconds, audioIndex, subtitleIndex, quality, bandwidthCap, hdr, pinned=bool(pinned)
        )

    def fileId(self, video):
        """Silo's id of the file video plays (its chosen version's, else its first)."""
        part = _part(video)
        return part.id if part is not None else None

    def getObject(self, key, assume_container=False):
        """The item at a Plex path (/library/metadata/<id>), as PlexServer.getObject() builds it."""
        from plexnet import plexobjects, video  # noqa: F401 (video registers the item types)

        data = self.query(key)
        container = (
            self if assume_container else plexobjects.PlexContainer(data, initpath=key, server=self, address=key)
        )
        return plexobjects.buildItem(self, data[0], key, container=container)

    def watchInfo(self, item, fileId):
        """The played file's skip markers and chapters (silonet.watch), as plexnet Marker and Chapter lists; empty
        when Silo can't say."""
        from plexnet import media, plexobjects

        try:
            answer = self.client.request('GET', f'/api/v2/watch/{item.ratingKey}') or {}
        except SiloError as e:
            log(f'watch info of {item.ratingKey} failed: {e.status} {e.type}')
            answer = {}
        if watch.hasPreviews(answer, fileId):
            self._previews[str(fileId)] = str(item.ratingKey)
        root = container(watch.markerElements(answer, fileId) + watch.chapterElements(answer, fileId))
        markers = plexobjects.PlexItemList(root, media.Marker, media.Marker.TYPE, server=self)
        chapters = plexobjects.PlexItemList(root, media.Chapter, media.Chapter.TYPE, server=self)
        return markers, chapters

    def hasPreviews(self, fileId):
        """Whether the file being played has seek-bar previews Kodi can show (Silo's, and Pillow to cut them)."""
        return str(fileId) in self._previews and trickplay.available()

    def trickplayIndex(self, fileId):
        """The file's previews (GET /api/v2/watch/{id}/trickplay) as an index the preview store reads
        (silonet.trickplay.SheetIndex), or None."""
        contentId = self._previews.get(str(fileId))
        if not contentId or not trickplay.available():
            return None
        try:
            manifest = self.client.request(
                'GET', f'/api/v2/watch/{contentId}/trickplay', params={'file_id': str(fileId)}
            )
        except SiloError as e:
            log(f'previews of {fileId} failed: {e.status} {e.type}')
            return None

        def fetch(url):
            response = self.client.session.request('GET', url, timeout=self.client.timeout)
            response.raise_for_status()
            return response.content

        return trickplay.SheetIndex(manifest or {}, self.client.baseUrl, fetch)

    def isFavorite(self, item):
        """Whether the item is one of the profile's favorites (decision D19); None when Silo can't say."""
        return lists.isMember(self.client, lists.FAVORITES, item.ratingKey)

    def setFavorite(self, item, add):
        lists.change(self.client, lists.FAVORITES, item.ratingKey, add)

    def themeUrl(self, item):
        """The item's theme song (decision D15) as a short-lived URL (POST .../themes/{id}/playback), or None: no
        theme, or Silo refused. Not logged: the URL is its own credential."""
        themeId = str(item.get('theme') or '')
        if not themeId:
            return None
        body = {'accepted_formats': [{'container': container} for container in self.THEME_FORMATS]}
        try:
            grant = self.client.request(
                'POST', f'/api/v2/catalog/items/{item.ratingKey}/themes/{themeId}/playback', json=body
            )
        except SiloError as e:
            log(f'theme of {item.ratingKey} failed: {e.status} {e.type}')
            return None
        url = (grant or {}).get('url') or ''
        return self.client.baseUrl + url if url.startswith('/') else url or None

    def rememberStream(self, video, streamType, stream, streams):
        """A track chosen for an episode, kept for its show (silonet.trackprefs; plexnet's
        PlexPart.setSelectedStream()). stream None is subtitles off; a movie's choice isn't kept."""
        seriesId = video is not None and video.TYPE == 'episode' and str(video.get('grandparentRatingKey') or '')
        if seriesId:
            trackprefs.remember(self.client, seriesId, streamType, stream, streams)

    def searchSubtitles(self, video, language='en', hearingImpaired=0, forced=0):
        """Subtitles for the video's file from Silo's providers (POST /api/v2/subtitles/search), as the Plex
        SubtitleStreams Plex Uno's download dialog lists (plexnet video.findSubtitles())."""
        from plexnet import media

        part = _part(video)
        if part is None:
            return []
        body = {'media_file_id': str(part.id), 'languages': [language]}
        try:
            answer = self.client.request('POST', '/api/v2/subtitles/search', json=body) or {}
        except SiloError as e:
            log(f'subtitle search failed: {e.status} {e.type}')
            return []
        found = []
        for result in answer.get('results') or []:
            key = f'silo-subtitle:{result.get("id")}'
            self._subtitleResults[key] = dict(result, media_file_id=str(part.id))
            attrs = {
                'key': key,
                'sourceKey': f'{result.get("provider")}:{result.get("id")}',
                'providerTitle': result.get('provider') or '',
                'title': result.get('release_name') or '',
                'score': str(int(result.get('score') or 0)),
                'hearingImpaired': '1' if result.get('hearing_impaired') else '0',
                'forced': '0',
                'downloaded': '0',
                'codec': result.get('format') or '',
                'languageCode': result.get('language') or language,
            }
            found.append(media.SubtitleStream(ElementTree.Element('Stream', attrs), server=self))
        return found

    def downloadSubtitle(self, video, key):
        """Downloads a search result into Silo (POST /api/v2/subtitles/download) and, while the video plays,
        refreshes the attempt's plan and adds the subtitle to the video's tracks, selected: the stream returned.
        None when Silo can't download it, or nothing plays the file."""
        from plexnet import plexstream

        result = self._subtitleResults.get(key)
        part = _part(video)
        if not result or part is None:
            return None
        body = {
            'media_file_id': result['media_file_id'],
            'provider': result.get('provider'),
            'subtitle_id': result.get('id'),
            'language': result.get('language'),
            'release_name': result.get('release_name') or '',
            'score': result.get('score') or 0,
            'hearing_impaired': bool(result.get('hearing_impaired')),
        }
        try:
            stored = (self.client.request('POST', '/api/v2/subtitles/download', json=body) or {}).get('subtitle') or {}
        except SiloError as e:
            log(f'subtitle download failed: {e.status} {e.type}')
            return None
        attempt = self.playback.attemptFor(result['media_file_id'])
        if not stored.get('id') or attempt is None or not attempt.refresh():
            return None
        attrs = {
            'streamType': '3',
            'id': f'{result["media_file_id"]}.d{stored["id"]}',
            'key': f'{SUBTITLE_KEY}{result["media_file_id"]}/downloaded/{stored["id"]}',
            'codec': stored.get('format') or 'srt',
            'title': stored.get('release_name') or '',
            'displayTitle': stored.get('release_name') or '',
            **detail.languageAttrs(stored.get('language')),
        }
        stream = plexstream.PlexStream(ElementTree.Element('Stream', attrs), server=self)
        part.streams.append(stream)
        part._markSelected(3, stream.id)
        if hasattr(video, '_subtitleStreams'):
            video._subtitleStreams = None
        return stream

    def findVideoSession(self, sessionId, ratingKey):
        """The playing item's session for the player's info panel (Show Stream Info), as Plex's /status/sessions
        lists it: what the item's attempt sends (silonet.streaminfo). sessionId is the player's own, which Silo
        doesn't know."""
        from plexnet import exceptions, plexobjects, video  # noqa: F401 (video registers the item types)

        attempt = self.playback.attempts.get(str(ratingKey))
        if attempt is None:
            raise exceptions.NotFound  # the panel says the info isn't available
        return plexobjects.buildItem(self, streaminfo.sessionElement(attempt), '/status/sessions')

    def reportTimeline(self, params):
        """A player timeline (plexnet nowplayingmanager): progress or stop for the item's attempt."""
        self.playback.timeline(str(params.get('ratingKey')), params.get('state'), params.get('time'))

    def updateReachability(self, force=True, allowFallback=False):
        pass  # no connection to test: a request that fails says so itself

    def close(self):
        """The add-on is ending (plexapp.App.preShutdown()): its connections to the server close."""
        self.client.session.close()

    def dropIdleConnections(self):
        pass  # requests' session opens new connections itself after a sleep

    def hubs(self, section=None, count=None, search_query=None, section_ids=None, continue_watching_count=None):
        """Home's rows from Silo's home layout (silonet.home), a library's (section: its id) from Silo's rows for it
        (silonet.library), in Silo's order, or a search's (search_query: silonet.search). Silo's home rows aren't per
        library, so section_ids doesn't narrow them (D8)."""
        from plexnet import plexlibrary, plexobjects, video  # noqa: F401 (video registers the item types)

        from lib.i18n import T

        try:
            if search_query:
                rows = search.searchHubs(self.client, search_query, count)
            elif section == lists.WATCHLIST_SECTION:
                titles = {'movie': T(32348, 'Movies'), 'series': T(32350, 'Shows')}
                rows = lists.watchlistRows(self.client, count, titles)
            elif section:
                rows = library.libraryRows(self.client, section, count)
            else:
                rows = home.homeRows(self.client, count, continue_watching_count, self.homeCapabilities())
                favorites = lists.favoritesRow(self.client, count, T(35259, 'Favorites'))
                if favorites is not None:
                    rows.append(favorites)
        except SiloError as e:
            log(f'{"library" if section else "home"} rows failed: {e.status} {e.type}')
            return []
        root = container(rows)
        hubsContainer = plexobjects.PlexContainer(root, initpath='/hubs', server=self, address='/hubs')
        return [plexlibrary.Hub(elem, server=self, container=hubsContainer) for elem in root]

    def homeCapabilities(self):
        """What this Silo's home layout accepts (/api/v2/home/capabilities), asked once; {} while it can't answer."""
        if self._homeCapabilities is None:
            try:
                self._homeCapabilities = self.client.request('GET', '/api/v2/home/capabilities') or {}
            except SiloError as e:
                log(f'home capabilities failed: {e.status} {e.type}')
                return {}
        return self._homeCapabilities

    def playlists(self):
        return []  # Silo has no playlists

    def library(self):
        from plexnet import plexlibrary

        return plexlibrary.Library(self.query('/library/'), server=self)


def _part(video):
    """The part (file) the video plays: its chosen version's, else its first."""
    from plexnet.plexobjects import PlexValue

    choice = getattr(video, 'mediaChoice', None)
    if choice is not None and getattr(choice, 'part', None) is not None:
        return choice.part
    media = getattr(video, 'media', None)
    if isinstance(media, PlexValue):
        return None  # an item not loaded with its files (a card): plexnet's stand-in for an attribute it lacks
    media = media() if callable(media) else media
    if media and media[0].parts:
        return media[0].parts[0]
    return None
