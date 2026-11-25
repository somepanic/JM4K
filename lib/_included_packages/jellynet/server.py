"""
JellyfinServer: the server object Uno's plexnet object model and screens talk to.
query() translates Plex paths into Jellyfin API calls as Plex-shaped XML so plexnet's classes
keep working. Paths not mapped yet return an empty <MediaContainer size="0"/> and log once per path.
"""

import functools
import re
from urllib.parse import parse_qsl, urlencode, urlsplit
from xml.etree import ElementTree

from plexnet.signalsmixin import SignalsMixin

from . import home, library, lists, playback
from .client import log
from .errors import JellyfinError
from .items import buildImageUrl, container

# Jellyfin CollectionType values mapped to Plex section types.
# music is hidden until Phase 10/11 (D4); other types are left out.
SECTION_TYPES = {'movies': 'movie', 'tvshows': 'show', 'boxsets': 'movie'}

# Library grid, sorts, filters, collections tab, and jump bar paths
LIBRARY_PATH = re.compile(
    r'^/library/sections/([^/]+)/(all|sorts|firstCharacter|filters|collections|genre|year|contentRating|label|tag)$'
)
# Collection contents (/library/collections/<id>/children and /library/metadata/<id>/children)
COLLECTION_CHILDREN_PATH = re.compile(r'^/library/(?:collections|metadata)/([^/]+)/children$')
# All episodes of a show (for expanding shows inside a collection's Play All queue)
ALL_LEAVES_PATH = re.compile(r'^/library/metadata/([^/]+)/allLeaves$')
# Recommended rows for one library section
SECTION_HUBS_PATH = re.compile(r'^/hubs/sections/([^/]+)$')


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
        self._homeCapabilities = None
        self._libraryTypes = {}
        self._collectionTypes = {}
        self._jumpBarCache = {}
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
        if handler is None and route.startswith(home.ROW_PATH):
            row_spec = route[len(home.ROW_PATH) :]
            handler = functools.partial(
                home.rowItems,
                self.client,
                row_spec,
                args=args,
                offset=kwargs.get('offset'),
                limit=kwargs.get('limit'),
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
                lists.showLeaves,
                self.client,
                leaves_match.group(1),
                args=args,
                offset=kwargs.get('offset'),
                limit=kwargs.get('limit'),
            )
        if handler is None:
            if route not in self._unmapped:
                self._unmapped.add(route)
                log(f'not mapped yet: {route}')
            return container()
        try:
            return handler()
        except JellyfinError as e:
            log(f'{route} failed: {e.status}')
            return None

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
        return None

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
        if search_query:
            return []
        from plexnet import plexlibrary, plexobjects, video  # noqa: F401

        try:
            if section:
                root = self._sectionHubsContainer(str(section), count=count)
            else:
                rows = home.homeRows(
                    self.client,
                    count=count,
                    continue_watching_count=continue_watching_count,
                )
                root = container(rows)
        except JellyfinError as e:
            log(f'{"library" if section else "home"} rows failed: {e.status}')
            return []
        hubs_container = plexobjects.PlexContainer(root, initpath='/hubs', server=self, address='/hubs')
        return [plexlibrary.Hub(elem, server=self, container=hubs_container) for elem in root]

    def homeCapabilities(self):
        """Home row capabilities supported on Jellyfin (read-only layout from DisplayPreferences and UserViews)."""
        if self._homeCapabilities is None:
            self._homeCapabilities = {
                'state': 'available',
                'editable': False,
                'sections': list(home.DEFAULT_SECTIONS),
            }
        return dict(self._homeCapabilities)

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
