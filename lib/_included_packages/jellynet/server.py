"""
JellyfinServer: the server object Uno's plexnet object model and screens talk to.
query() translates Plex paths into Jellyfin API calls as Plex-shaped XML so plexnet's classes
keep working. Paths not mapped yet return an empty <MediaContainer size="0"/> and log once per path.
"""

import functools
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from xml.etree import ElementTree

from plexnet.signalsmixin import SignalsMixin

from . import detail, library, lists, people, playback, related, shows
from .client import log
from .errors import JellyfinError
from .items import buildImageUrl, container, ticks_to_ms

# Jellyfin CollectionType values mapped to Plex section types.
# music is hidden until Phase 10/11 (D4); other types are left out.
SECTION_TYPES = {'movies': 'movie', 'tvshows': 'show', 'boxsets': 'movie'}

# Library grid, sorts, filters, collections tab, and jump bar paths
LIBRARY_PATH = re.compile(
    r'^/library/sections/([^/]+)/(all|sorts|firstCharacter|filters|collections|genre|year|contentRating|label|tag)$'
)
# Item detail (/library/metadata/<id> or comma-separated batch /library/metadata/<id1>,<id2>)
METADATA_PATH = re.compile(r'^/library/metadata/([^/]+)$')
# Metadata children: Series -> seasons, Season -> episodes, BoxSet -> collection items
METADATA_CHILDREN_PATH = re.compile(r'^/library/metadata/([^/]+)/children$')
# Dedicated collection contents route (/library/collections/<id>/children)
COLLECTION_CHILDREN_PATH = re.compile(r'^/library/collections/([^/]+)/children$')
# All episodes of a show (/library/metadata/<id>/allLeaves)
ALL_LEAVES_PATH = re.compile(r'^/library/metadata/([^/]+)/allLeaves$')
# Similar titles and related hub (/library/metadata/<id>/similar|related)
SIMILAR_PATH = re.compile(r'^/library/metadata/([^/]+)/similar$')
RELATED_PATH = re.compile(r'^/library/metadata/([^/]+)/related$')
# Special features extras (/library/metadata/<id>/extras; NO trailers per J-D10)
EXTRAS_PATH = re.compile(r'^/library/metadata/([^/]+)/extras$')
# Person details and library filmography (/library/people/<id>[/media])
PEOPLE_PATH = re.compile(r'^/library/people/([^/]+)(/media)?$')
# Recommended rows for one library section
SECTION_HUBS_PATH = re.compile(r'^/hubs/sections/([^/]+)$')

# Plex element type -> Jellyfin BaseItemKind for caching types from XML listings
PLEX_TYPE_TO_KIND = {
    'movie': 'Movie',
    'show': 'Series',
    'season': 'Season',
    'episode': 'Episode',
    'collection': 'BoxSet',
}


class JellyfinConnection:
    """A Jellyfin server's single connection (base URL): what plexnet reads of PlexServer.activeConnection."""

    def __init__(self, address):
        self.address = address


class JellyfinServer(SignalsMixin):
    offline = False
    suspect = False
    gone = False
    owned = True
    synced = False
    isPrimary = True
    allowsMediaDeletion = False
    platform = 'jellyfin'
    pendingReachabilityRequests = 0
    supportsVideoTranscoding = True
    supportsVideoRemuxOnly = False
    supportsAudioTranscoding = True
    sessions = ()
    isLocal = False

    def __init__(self, stored, client):
        SignalsMixin.__init__(self)
        self.stored = stored
        self.client = client
        self.uuid = stored.serverId
        self.name = stored.name or stored.baseUrl
        self.url = client.base_url
        self.activeConnection = JellyfinConnection(client.base_url)
        self.session = client.session
        self._unmapped = set()
        self._routes = {'/library/sections': self._sections}
        self._libraryTypes = {}
        self._collectionTypes = {}
        self._jumpBarCache = {}
        self._itemTypes = {}
        self._seasonSeries = {}
        self._itemMeta = {}
        self._trickplayMeta = {}
        self._personNames = {}
        self._personIds = {}
        self.playback = playback.Playback(client)

    def __repr__(self):
        return f'<JellyfinServer {self.uuid}>'

    @property
    def anyLANConnection(self):
        return False

    def query(self, path, method=None, raw=False, **kwargs):
        """Return Plex-shaped XML for a mapped Plex path, empty container once-logged for unmapped paths,
        or None if Jellyfin failed to answer."""
        route, _, query_string = path.partition('?')
        route = route.rstrip('/') or '/'
        args = dict(parse_qsl(query_string))
        extra_params = kwargs.get('params')
        if isinstance(extra_params, dict):
            for key, value in extra_params.items():
                if value is not None:
                    args[str(key)] = str(value)
        handler = self._routes.get(route)
        library_match = LIBRARY_PATH.match(route) if handler is None else None
        if library_match:
            section_id, action = library_match.group(1), library_match.group(2)
            section_type = self._libraryTypes.get(section_id, '')
            collection_type = self._collectionTypes.get(section_id, '')
            if action == 'sorts':
                handler = library.sortsContainer
            elif action == 'filters':
                handler = functools.partial(library.filtersContainer, section_id)
            elif action in library.FILTER_CHOICE_KEYS:
                handler = functools.partial(
                    library.filterChoices,
                    self.client,
                    section_id,
                    action,
                    args=args,
                    section_type=section_type,
                    collection_type=collection_type,
                )
            elif action == 'collections':
                handler = functools.partial(
                    lists.collectionsGrid,
                    self.client,
                    library_id=section_id if collection_type == 'boxsets' else None,
                    args=args,
                    offset=kwargs.get('offset'),
                    limit=kwargs.get('limit'),
                    member_type=section_type or 'movie',
                    section_id=section_id,
                )
            elif action == 'firstCharacter':
                handler = functools.partial(
                    library.jumpBar,
                    self.client,
                    section_id,
                    args,
                    section_type=section_type,
                    collection_type=collection_type,
                    cache=self._jumpBarCache,
                )
            else:
                handler = functools.partial(
                    library.libraryItems,
                    self.client,
                    section_id,
                    args,
                    offset=kwargs.get('offset'),
                    limit=kwargs.get('limit'),
                    section_type=section_type,
                    collection_type=collection_type,
                )
        if handler is None and route.startswith(library.LIBRARY_ROW_PATH):
            row_section_id, _, row_kind = route[len(library.LIBRARY_ROW_PATH) :].partition('/')
            handler = functools.partial(
                library.libraryRowItems,
                self.client,
                row_section_id,
                row_kind,
                args=args,
                offset=kwargs.get('offset'),
                limit=kwargs.get('limit'),
            )
        section_hubs_match = SECTION_HUBS_PATH.match(route) if handler is None else None
        if section_hubs_match and section_hubs_match.group(1) != 'home':
            hub_section_id = section_hubs_match.group(1)
            raw_count = args.get('count')
            hub_count = int(raw_count) if raw_count and str(raw_count).isdigit() else None
            handler = functools.partial(
                self._sectionHubsContainer,
                hub_section_id,
                hub_count,
            )
        metadata_match = METADATA_PATH.match(route) if handler is None else None
        if metadata_match:
            handler = functools.partial(
                detail.itemDetail,
                self.client,
                metadata_match.group(1),
                params=args,
                on_item=self._rememberItem,
                extras_fetcher=related.extrasElement,
            )
        metadata_children_match = METADATA_CHILDREN_PATH.match(route) if handler is None else None
        if metadata_children_match:
            handler = functools.partial(
                shows.children,
                self.client,
                metadata_children_match.group(1),
                item_types=self._itemTypes,
                season_series=self._seasonSeries,
                args=args,
                offset=kwargs.get('offset'),
                limit=kwargs.get('limit'),
                on_item=self._rememberItem,
            )
        collection_match = COLLECTION_CHILDREN_PATH.match(route) if handler is None else None
        if collection_match:
            handler = functools.partial(
                lists.collectionItems,
                self.client,
                collection_match.group(1),
                args=args,
                offset=kwargs.get('offset'),
                limit=kwargs.get('limit'),
            )
        leaves_match = ALL_LEAVES_PATH.match(route) if handler is None else None
        if leaves_match:
            handler = functools.partial(
                shows.allEpisodes,
                self.client,
                leaves_match.group(1),
                args=args,
                offset=kwargs.get('offset'),
                limit=kwargs.get('limit'),
                on_item=self._rememberItem,
            )
        similar_match = SIMILAR_PATH.match(route) if handler is None else None
        if similar_match:
            handler = functools.partial(
                related.similar,
                self.client,
                similar_match.group(1),
                count=args.get('count'),
                offset=kwargs.get('offset'),
                limit=kwargs.get('limit'),
            )
        related_match = RELATED_PATH.match(route) if handler is None else None
        if related_match:
            handler = functools.partial(
                related.relatedHub,
                self.client,
                related_match.group(1),
                count=args.get('count'),
            )
        extras_match = EXTRAS_PATH.match(route) if handler is None else None
        if extras_match:
            handler = functools.partial(
                related.extras,
                self.client,
                extras_match.group(1),
            )
        people_match = PEOPLE_PATH.match(route) if handler is None else None
        if people_match:
            if people_match.group(2):
                handler = functools.partial(
                    people.media,
                    self.client,
                    people_match.group(1),
                    args=args,
                    offset=kwargs.get('offset'),
                    limit=kwargs.get('limit'),
                    person_ids=self._personIds,
                )
            else:
                handler = functools.partial(
                    people.person,
                    self.client,
                    people_match.group(1),
                    person_names=self._personNames,
                    person_ids=self._personIds,
                )
        if handler is None:
            if route not in self._unmapped:
                self._unmapped.add(route)
                log(f'not mapped yet: {route}')
            return container()
        try:
            result = handler()
        except JellyfinError as e:
            log(f'{route} failed: {e.status}')
            return None
        if isinstance(result, ElementTree.Element):
            self._cacheTypesFromXml(result)
        return result

    def _rememberItem(self, dto):
        """Cache item type, season->series linkage, chapters, trickplay metadata, and person names from a BaseItemDto."""
        if not isinstance(dto, dict):
            return
        item_id = dto.get('Id')
        if not item_id:
            return
        item_id = str(item_id)
        kind = dto.get('Type')
        if kind:
            self._itemTypes[item_id] = str(kind)
        if kind == 'Season' and dto.get('SeriesId'):
            self._seasonSeries[item_id] = str(dto['SeriesId'])
        elif kind == 'Episode':
            series_id = dto.get('SeriesId')
            season_id = dto.get('SeasonId')
            if series_id:
                self._itemTypes[str(series_id)] = 'Series'
            if season_id:
                self._itemTypes[str(season_id)] = 'Season'
                if series_id:
                    self._seasonSeries[str(season_id)] = str(series_id)

        for person in dto.get('People') or ():
            if isinstance(person, dict) and person.get('Id') and person.get('Name'):
                pid, pname = str(person['Id']), str(person['Name'])
                self._personNames[pid] = pname
                self._personIds[pname] = pid

        chapters = dto.get('Chapters')
        trickplay = dto.get('Trickplay')
        media_sources = dto.get('MediaSources')
        if chapters is not None or trickplay is not None or media_sources is not None:
            existing = self._itemMeta.get(item_id, {})
            duration_ms = ticks_to_ms(dto.get('RunTimeTicks')) or existing.get('duration_ms', 0)
            self._itemMeta[item_id] = {
                'chapters': list(chapters) if isinstance(chapters, list) else existing.get('chapters', []),
                'trickplay': dict(trickplay) if isinstance(trickplay, dict) else existing.get('trickplay', {}),
                'duration_ms': duration_ms,
                'media_sources': (
                    list(media_sources) if isinstance(media_sources, list) else existing.get('media_sources', [])
                ),
            }
        if isinstance(trickplay, dict):
            for source_id, res_map in trickplay.items():
                if source_id and isinstance(res_map, dict) and res_map:
                    self._trickplayMeta[str(source_id)] = {
                        'item_id': item_id,
                        'media_source_id': str(source_id),
                        'trickplay': dict(res_map),
                    }

    def _cacheTypesFromXml(self, root):
        """Record item types and season->series links from any Plex XML returned by query()."""
        for elem in root.iter():
            plex_type = elem.get('type')
            rating_key = elem.get('ratingKey')
            if not rating_key or plex_type not in PLEX_TYPE_TO_KIND:
                continue
            self._itemTypes.setdefault(rating_key, PLEX_TYPE_TO_KIND[plex_type])
            if plex_type == 'season' and elem.get('parentRatingKey'):
                self._seasonSeries.setdefault(rating_key, elem.get('parentRatingKey'))
            elif plex_type == 'episode':
                parent_key = elem.get('parentRatingKey')
                gp_key = elem.get('grandparentRatingKey')
                if gp_key:
                    self._itemTypes.setdefault(gp_key, 'Series')
                if parent_key:
                    self._itemTypes.setdefault(parent_key, 'Season')
                    if gp_key:
                        self._seasonSeries.setdefault(parent_key, gp_key)

    def _sections(self):
        data = self.client.get('/UserViews') or {}
        sections = []
        for entry in data.get('Items') or []:
            if not isinstance(entry, dict) or not entry.get('Id'):
                continue
            collection_type = (entry.get('CollectionType') or '').lower()
            kind = SECTION_TYPES.get(collection_type)
            if kind is None:
                continue
            item_id = str(entry['Id'])
            self._libraryTypes[item_id] = kind
            self._collectionTypes[item_id] = collection_type
            image_tags = entry.get('ImageTags') or {}
            primary_tag = image_tags.get('Primary') if isinstance(image_tags, dict) else None
            thumb = buildImageUrl('', item_id, 'Primary', tag=primary_tag) if primary_tag else ''
            sections.append(
                ElementTree.Element(
                    'Directory',
                    {
                        'key': item_id,
                        'uuid': item_id,
                        'title': entry.get('Name') or '',
                        'type': kind,
                        'thumb': thumb,
                    },
                )
            )
        return container(sections)

    def libraryLocations(self, libraryId):
        return []

    def buildUrl(self, path, includeToken=False):
        if not path:
            return ''
        if '://' in path:
            return path
        clean = path if path.startswith('/') else '/' + path
        return self.client.base_url + clean

    def getImageTranscodeURL(self, path, width, height, **extraOpts):
        if not path:
            return ''
        parsed = urlsplit(path)
        if '://' in path:
            base = f'{parsed.scheme}://{parsed.netloc}'
            route = parsed.path
        else:
            base = self.client.base_url
            route = parsed.path if parsed.path.startswith('/') else '/' + parsed.path
        params = dict(parse_qsl(parsed.query))
        for key, raw_val in (('maxWidth', width), ('maxHeight', height)):
            try:
                val = int(raw_val)
            except (TypeError, ValueError):
                val = 0
            if val > 0:
                params.setdefault(key, str(val))
        tag = extraOpts.get('tag')
        if tag:
            params.setdefault('tag', str(tag))
        query = urlencode(params)
        return f'{base}{route}?{query}' if query else f'{base}{route}'

    def isReachable(self, onlySupported=True):
        return True

    def isLocalConnection(self):
        return True

    def supportsFeature(self, feature):
        return False

    def startPlayback(
        self, item, part, seconds=0.0, audioIndex=None, subtitleIndex=None, quality='auto', bandwidthCap=None, hdr=True
    ):
        """Negotiate a Jellyfin playback session for item and its chosen part (version)."""
        item_id = str(item.ratingKey)
        media_source_id = str(part.id) if part is not None and part.id else None
        return self.playback.start(
            item_id,
            media_source_id=media_source_id,
            seconds=seconds,
            audio_index=audioIndex,
            subtitle_index=subtitleIndex,
            bandwidth_cap_kbps=bandwidthCap,
            hdr=hdr,
        )

    def fileId(self, video):
        part = _part(video)
        return part.id if part is not None else None

    def getObject(self, key, assume_container=False):
        from plexnet import plexobjects, video  # noqa: F401

        data = self.query(key)
        if data is None or len(data) == 0:
            return None
        root_container = (
            self if assume_container else plexobjects.PlexContainer(data, initpath=key, server=self, address=key)
        )
        return plexobjects.buildItem(self, data[0], key, container=root_container)

    def watchInfo(self, item, fileId):
        from plexnet import media, plexobjects

        root = container()
        markers = plexobjects.PlexItemList(root, media.Marker, media.Marker.TYPE, server=self)
        chapters = plexobjects.PlexItemList(root, media.Chapter, media.Chapter.TYPE, server=self)
        return markers, chapters

    def hasPreviews(self, fileId):
        return False

    def trickplayIndex(self, fileId):
        return None

    def isFavorite(self, item):
        return None

    def setFavorite(self, item, add):
        return None

    def themeUrl(self, item):
        """Return an authenticated Jellyfin theme song stream URL when theme_music is enabled (D15), else None."""
        from plexnet import util as pnUtil

        interface = getattr(pnUtil, 'INTERFACE', None)
        if interface is None or not interface.getThemeMusicValue():
            return None
        item_id = str(getattr(item, 'ratingKey', '') or '')
        if not item_id:
            return None
        try:
            answer = (
                self.client.get(
                    f'/Items/{quote(item_id, safe="")}/ThemeMedia',
                    params={'inheritFromParent': 'true'},
                )
                or {}
            )
        except JellyfinError as e:
            log(f'theme of {item_id} failed: {e.status}')
            return None
        songs = ((answer if isinstance(answer, dict) else {}).get('ThemeSongsResult') or {}).get('Items') or []
        theme_id = next((str(s['Id']) for s in songs if isinstance(s, dict) and s.get('Id')), None)
        if not theme_id:
            return None
        stream_url = f'{self.client.base_url}/Audio/{quote(theme_id, safe="")}/stream?static=true'
        return playback.attach_auth_header(stream_url, self.client.auth_header())

    def rememberStream(self, video, streamType, stream, streams):
        return None

    def searchSubtitles(self, video, language='en', hearingImpaired=0, forced=0):
        return []

    def downloadSubtitle(self, video, key):
        return None

    def findVideoSession(self, sessionId, ratingKey):
        """Return the active playback session in Plex /status/sessions shape, or raise NotFound."""
        from plexnet import exceptions, plexobjects, video  # noqa: F401

        session = self.playback.find_session(session_id=sessionId, rating_key=ratingKey)
        if session is None:
            raise exceptions.NotFound
        return plexobjects.buildItem(self, playback.session_element(session), '/status/sessions')

    def reportTimeline(self, params):
        """Translate a NowPlayingManager timeline update into Jellyfin session reporting."""
        if not isinstance(params, dict):
            return
        rating_key = params.get('ratingKey')
        if rating_key is None:
            return
        self.playback.timeline(str(rating_key), params.get('state'), params.get('time'))

    def updateReachability(self, force=True, allowFallback=False):
        pass

    def close(self):
        self.client.close()

    def dropIdleConnections(self):
        self.client.dropIdleConnections()

    def _sectionHubsContainer(self, section_id, count=None):
        rows = library.libraryRows(
            self.client,
            section_id,
            count=count,
            section_type=self._libraryTypes.get(section_id, ''),
            collection_type=self._collectionTypes.get(section_id, ''),
        )
        return container(rows)

    def hubs(self, section=None, count=None, search_query=None, section_ids=None, continue_watching_count=None):
        if not section:
            return []
        from plexnet import plexlibrary, plexobjects, video  # noqa: F401

        try:
            root = self._sectionHubsContainer(str(section), count=count)
        except JellyfinError as e:
            log(f'library rows failed: {e.status}')
            return []
        hubs_container = plexobjects.PlexContainer(root, initpath='/hubs', server=self, address='/hubs')
        return [plexlibrary.Hub(elem, server=self, container=hubs_container) for elem in root]

    def homeCapabilities(self):
        return {}

    def playlists(self):
        return []

    def library(self):
        from plexnet import plexlibrary

        return plexlibrary.Library(self.query('/library/'), server=self)


def _part(video):
    from plexnet.plexobjects import PlexValue

    choice = getattr(video, 'mediaChoice', None)
    if choice is not None and getattr(choice, 'part', None) is not None:
        return choice.part
    media = getattr(video, 'media', None)
    if isinstance(media, PlexValue):
        return None
    media = media() if callable(media) else media
    if media and media[0].parts:
        return media[0].parts[0]
    return None
