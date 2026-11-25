"""
JellyfinServer: the server object Uno's plexnet object model and screens talk to.
query() translates Plex paths into Jellyfin API calls as Plex-shaped XML so plexnet's classes
keep working. Paths not mapped yet return an empty <MediaContainer size="0"/> and log once per path.
"""

from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from xml.etree import ElementTree

from plexnet.signalsmixin import SignalsMixin

from . import playback
from .client import log
from .errors import JellyfinError

# Jellyfin CollectionType values mapped to Plex section types.
# boxsets is mapped in Phase 3; music is hidden until Phase 10/11 (D4); other types are left out.
SECTION_TYPES = {'movies': 'movie', 'tvshows': 'show'}


def container(children=()):
    root = ElementTree.Element('MediaContainer', {'size': str(len(children))})
    for child in children:
        root.append(child)
    return root


def buildImageUrl(base_url, item_id, image_type='Primary', tag=None, max_width=None, max_height=None):
    """Build a public Jellyfin /Items/{itemId}/Images/{imageType} URL; never includes an auth token."""
    params = {}
    if max_width and int(max_width) > 0:
        params['maxWidth'] = str(int(max_width))
    if max_height and int(max_height) > 0:
        params['maxHeight'] = str(int(max_height))
    if tag:
        params['tag'] = str(tag)
    route = f'/Items/{quote(str(item_id), safe="")}/Images/{quote(str(image_type), safe="")}'
    base = base_url.rstrip('/') if base_url else ''
    query = urlencode(params)
    return f'{base}{route}?{query}' if query else f'{base}{route}'


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
        self.playback = playback.Playback(client)

    def __repr__(self):
        return f'<JellyfinServer {self.uuid}>'

    @property
    def anyLANConnection(self):
        return False

    def query(self, path, method=None, raw=False, **kwargs):
        """Return Plex-shaped XML for a mapped Plex path, empty container once-logged for unmapped paths,
        or None if Jellyfin failed to answer."""
        route, _, _query_string = path.partition('?')
        route = route.rstrip('/') or '/'
        handler = self._routes.get(route)
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

    def hubs(self, section=None, count=None, search_query=None, section_ids=None, continue_watching_count=None):
        return []

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
