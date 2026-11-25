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
from .errors import JellyfinError
from .items import buildImageUrl, container, ticks_to_ms

# Jellyfin subtitle route fallback: /Videos/<item>/<source>/Subtitles/<index>/Stream.<ext>
SUBTITLE_ROUTE_PATH = re.compile(r'^/Videos/([^/]+)/([^/]+)/Subtitles/(\d+)/Stream\.[^/?]+$')
# PlexStream.getSubtitlePath() appends ?encoding=utf-8[&format=srt][&autoAdjustSubtitle=1] to stream.key
PLEX_SUBTITLE_SUFFIX = re.compile(r'\?encoding=utf-8(?:&(?:format=[^&]*|autoAdjustSubtitle=[^&]*))*$')

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
        self._homeCapabilities = None
        self._libraryTypes = {}
        self._collectionTypes = {}
        self._jumpBarCache = {}
        self._itemTypes = {}
        self._seasonSeries = {}
        self._itemMeta = {}
        self._trickplayMeta = {}
        self._favorites = {}
        self._personNames = {}
        self._personIds = {}
        self._virtualFolders = None
        self._subtitleResults = {}
        self.trackPrefs = trackprefs.TrackPrefsStore()
        self.playback = playback.Playback(client)

    def __repr__(self):
        return f'<JellyfinServer {self.uuid}>'

    @property
    def anyLANConnection(self):
        return self.isLocalConnection()

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
        if handler is None and route in watchstate.ROUTES:
            ws_args = dict(args)
            for key in ('key', 'ratingKey', 'time', 'state'):
                if kwargs.get(key) is not None and key not in ws_args:
                    ws_args[key] = str(kwargs[key])
            handler = functools.partial(watchstate.change, self.client, route, ws_args)
        if handler is None and route in (lists.FAVORITES_PATH, lists.FAVORITES_ROW_KEY):
            handler = functools.partial(
                lists.favorites,
                self.client,
                args=args,
                offset=kwargs.get('offset'),
                limit=kwargs.get('limit'),
                include_item_types=lists.FAVORITE_ITEM_TYPES,
            )
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
        if handler is None and route == '/hubs/search':
            raw_limit = args.get('limit') or args.get('count')
            search_limit = int(raw_limit) if raw_limit and str(raw_limit).isdigit() else None
            handler = functools.partial(
                self._searchContainer,
                args.get('query', ''),
                count=search_limit,
                section=args.get('contentDirectoryID') or None,
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
                track_prefs=self.trackPrefs,
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
                track_prefs=self.trackPrefs,
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
                track_prefs=self.trackPrefs,
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

    def _rememberPerson(self, person_id, name):
        """Cache a Jellyfin person's id <-> name mapping for /library/people/<id> lookups."""
        if person_id and name:
            pid, pname = str(person_id), str(name)
            self._personNames[pid] = pname
            self._personIds[pname] = pid

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
        user_data = dto.get('UserData')
        if isinstance(user_data, dict) and 'IsFavorite' in user_data:
            self._favorites[item_id] = bool(user_data['IsFavorite'])
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

    def _loadVirtualFolders(self):
        """Fetch and cache GET /Library/VirtualFolders; 403 caches empty, transient errors return None to retry."""
        if self._virtualFolders is None:
            try:
                data = self.client.get('/Library/VirtualFolders')
            except JellyfinError as e:
                if e.status == 403:
                    self._virtualFolders = []
                    return self._virtualFolders
                log(f'virtual folders failed: {e.status}')
                return None
            self._virtualFolders = [f for f in (data or ()) if isinstance(f, dict)]
        return self._virtualFolders

    def libraryLocations(self, libraryId):
        """Return a library's server-side folder paths from GET /Library/VirtualFolders, or [] on 403/error."""
        folders = self._loadVirtualFolders()
        if not folders:
            return []
        if not libraryId:
            locations = []
            for folder in folders:
                locations.extend(str(p) for p in (folder.get('Locations') or ()) if p)
            return locations
        wanted = str(libraryId)
        wanted_norm = wanted.replace('-', '').lower()
        for folder in folders:
            item_id = str(folder.get('ItemId') or '')
            if item_id == wanted or item_id.replace('-', '').lower() == wanted_norm:
                return [str(p) for p in (folder.get('Locations') or ()) if p]
        return []

    def buildUrl(self, path, includeToken=False):
        if not path:
            return ''
        cleaned = PLEX_SUBTITLE_SUFFIX.sub('', str(path))
        cleaned = detail.cleanDeliveryUrl(cleaned)
        if '://' in cleaned:
            return cleaned
        clean_path = cleaned if cleaned.startswith('/') else '/' + cleaned
        sub_match = SUBTITLE_ROUTE_PATH.match(clean_path)
        if sub_match:
            session = self.playback.session_for(sub_match.group(2))
            if session is not None:
                delivery = session.external_subtitle_url(sub_match.group(3))
                if delivery:
                    return delivery
        return self.client.base_url + clean_path

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
        return streaminfo.is_local_url(self.client.base_url)

    def supportsFeature(self, feature):
        if feature == 'subtitleSearch':
            folders = self._loadVirtualFolders()
            if not folders:
                return False
            for folder in folders:
                opts = folder.get('LibraryOptions')
                if not isinstance(opts, dict):
                    continue
                order = [str(p) for p in (opts.get('SubtitleFetcherOrder') or ()) if p]
                disabled = {str(p) for p in (opts.get('DisabledSubtitleFetchers') or ()) if p}
                if any(provider not in disabled for provider in order):
                    return True
            return False
        return False

    def startPlayback(
        self, item, part, seconds=0.0, audioIndex=None, subtitleIndex=None, quality='auto', bandwidthCap=None, hdr=True
    ):
        """Negotiate a Jellyfin playback session for item and its chosen part (version)."""
        item_id = str(item.ratingKey)
        media_source_id = str(part.id) if part is not None and part.id else None
        video_stream_id, audio_stream_id, subtitle_stream_id = _partStreamIds(part, audioIndex, subtitleIndex)
        fallback_tracks = None
        if getattr(item, 'TYPE', None) == 'episode':
            series_id = str(item.get('grandparentRatingKey') or '')
            if series_id:
                audio_pref, sub_pref = self.trackPrefs.get(series_id)
                if audio_pref or sub_pref:
                    fallback_tracks = (audio_pref, sub_pref)
        return self.playback.start(
            item_id,
            media_source_id=media_source_id,
            seconds=seconds,
            audio_index=audioIndex,
            subtitle_index=subtitleIndex,
            bandwidth_cap_kbps=bandwidthCap,
            hdr=hdr,
            video_stream_id=video_stream_id,
            audio_stream_id=audio_stream_id,
            subtitle_stream_id=subtitle_stream_id,
            fallback_tracks=fallback_tracks,
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

    def _shouldFetchSegments(self, item_id, file_id, session):
        if session is not None:
            media_source = session.media_source if isinstance(session.media_source, dict) else {}
            if 'HasSegments' in media_source:
                return bool(media_source.get('HasSegments'))
            cached_flag = self._cachedHasSegments(item_id, file_id)
            return bool(cached_flag) if cached_flag is not None else False
        cached_flag = self._cachedHasSegments(item_id, file_id)
        return cached_flag is not False

    def _cachedHasSegments(self, item_id, file_id):
        meta = self._itemMeta.get(item_id)
        if not meta:
            return None
        sources = meta.get('media_sources') or ()
        wanted = str(file_id) if file_id is not None else None
        matched = next(
            (s for s in sources if isinstance(s, dict) and wanted and str(s.get('Id') or '') == wanted),
            None,
        )
        if matched is None:
            matched = next((s for s in sources if isinstance(s, dict)), None)
        if matched is not None and 'HasSegments' in matched:
            return bool(matched.get('HasSegments'))
        return None

    def watchInfo(self, item, fileId):
        """Return (markers, chapters) PlexItemLists for playback from /MediaSegments/{itemId} and Chapters."""
        from plexnet import media, plexobjects

        item_id = str(getattr(item, 'ratingKey', '') or '')
        if not item_id:
            root = container()
            return (
                plexobjects.PlexItemList(root, media.Marker, media.Marker.TYPE, server=self),
                plexobjects.PlexItemList(root, media.Chapter, media.Chapter.TYPE, server=self),
            )

        session = self.playback.find_session(rating_key=item_id)
        meta = self._itemMeta.get(item_id)
        duration_ms = (meta.get('duration_ms') if meta else 0) or _item_duration_ms(item)

        segments = (
            watch.fetchSegments(self.client, item_id) if self._shouldFetchSegments(item_id, fileId, session) else []
        )
        marker_els = watch.markerElements(segments, duration_ms=duration_ms)

        chapter_els = []
        if meta is not None:
            chapter_els = watch.chapterElements(item_id, meta.get('chapters'), duration_ms=duration_ms)
        elif session is None and not getattr(item, 'chapters', None):
            dto, raw_chapters, fetched_duration = watch.fetchItemChapters(self.client, item_id)
            if dto is not None:
                self._rememberItem(dto)
            chapter_els = watch.chapterElements(item_id, raw_chapters, duration_ms=fetched_duration or duration_ms)

        root = container(marker_els + chapter_els)
        markers = plexobjects.PlexItemList(root, media.Marker, media.Marker.TYPE, server=self)
        chapters = plexobjects.PlexItemList(root, media.Chapter, media.Chapter.TYPE, server=self)
        return markers, chapters

    def hasPreviews(self, fileId):
        """Whether fileId has valid cached Jellyfin trickplay metadata and Pillow is available."""
        if not fileId or not trickplay.available():
            return False
        entry = self._trickplayMeta.get(str(fileId))
        if not entry:
            return False
        return trickplay.select_resolution(entry.get('trickplay')) is not None

    def trickplayIndex(self, fileId):
        """Return a SheetIndex for fileId's chosen trickplay resolution, or None when unavailable."""
        if not fileId or not trickplay.available():
            return None
        entry = self._trickplayMeta.get(str(fileId))
        if not entry:
            return None
        chosen = trickplay.select_resolution(entry.get('trickplay'))
        if chosen is None:
            return None
        _, info = chosen

        def fetch(url):
            response = self.client.session.request(
                'GET',
                url,
                headers={'Authorization': self.client.auth_header()},
                timeout=self.client.timeout,
            )
            if response.status_code >= 400:
                raise JellyfinError(response.status_code, f'HTTP {response.status_code}')
            return response.content

        return trickplay.SheetIndex(
            info,
            self.client.base_url,
            entry['item_id'],
            entry['media_source_id'],
            fetch,
        )

    def isFavorite(self, item):
        """Whether item is in the user's Jellyfin favorites (D19), cached from DTOs or GET /UserItems/{id}/UserData."""
        item_id = str(getattr(item, 'ratingKey', '') or '')
        if not item_id:
            return None
        if item_id in self._favorites:
            return self._favorites[item_id]
        try:
            answer = self.client.get(f'/UserItems/{quote(item_id, safe="")}/UserData') or {}
        except JellyfinError as e:
            log(f'favorite state of {item_id} failed: {e.status}')
            return None
        if not isinstance(answer, dict):
            return None
        state = bool(answer.get('IsFavorite'))
        self._favorites[item_id] = state
        return state

    def setFavorite(self, item, add):
        """Add or remove item from Jellyfin favorites via POST/DELETE /UserFavoriteItems/{id}, updating cache."""
        item_id = str(getattr(item, 'ratingKey', '') or '')
        if not item_id:
            return None
        quoted = quote(item_id, safe='')
        try:
            answer = (
                self.client.post(f'/UserFavoriteItems/{quoted}')
                if add
                else self.client.delete(f'/UserFavoriteItems/{quoted}')
            )
        except JellyfinError as e:
            log(f'set favorite of {item_id} failed: {e.status}')
            return None
        state = bool(answer['IsFavorite']) if isinstance(answer, dict) and 'IsFavorite' in answer else bool(add)
        self._favorites[item_id] = state
        return state

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
        """Remember an episode's chosen audio/subtitle stream per show (D13); movies are not stored per-show."""
        series_id = (
            video is not None
            and getattr(video, 'TYPE', None) == 'episode'
            and str(video.get('grandparentRatingKey') or '')
        )
        if series_id:
            self.trackPrefs.remember(series_id, streamType, stream, streams)

    def searchSubtitles(self, video, language='en', hearingImpaired=0, forced=0):
        """Search remote subtitle providers via GET /Items/{itemId}/RemoteSearch/Subtitles/{language} (D14)."""
        from plexnet import media

        item_id = str(getattr(video, 'ratingKey', '') or '')
        if not item_id:
            return []
        lang_code = _subtitleLanguageCode(language)
        try:
            results = (
                self.client.get(f'/Items/{quote(item_id, safe="")}/RemoteSearch/Subtitles/{quote(lang_code, safe="")}')
                or []
            )
        except JellyfinError as e:
            log(f'subtitle search for {item_id} failed: {e.status}')
            return []
        if not isinstance(results, list):
            return []

        want_hi = bool(hearingImpaired)
        want_forced = bool(forced)
        found = []
        for entry in results:
            if not isinstance(entry, dict) or not entry.get('Id'):
                continue
            is_hi = bool(entry.get('HearingImpaired'))
            is_forced = bool(entry.get('Forced'))
            if want_hi and not is_hi:
                continue
            if want_forced and not is_forced:
                continue
            sub_id = str(entry['Id'])
            provider = str(entry.get('ProviderName') or '')
            raw_lang = entry.get('ThreeLetterISOLanguageName') or lang_code
            norm_lang = detail.languageAttrs(raw_lang).get('languageCode') or lang_code
            self._subtitleResults[sub_id] = dict(entry)
            attrs = {
                'key': sub_id,
                'sourceKey': f'{provider}:{sub_id}',
                'providerTitle': provider,
                'title': str(entry.get('Name') or ''),
                'score': str(_remoteSubtitleScore(entry)),
                'hearingImpaired': '1' if is_hi else '0',
                'forced': '1' if is_forced else '0',
                'downloaded': '0',
                'codec': str(entry.get('Format') or 'srt').lower(),
                'languageCode': norm_lang,
            }
            found.append(media.SubtitleStream(ElementTree.Element('Stream', attrs), server=self))
        return found

    def downloadSubtitle(self, video, key):
        """Download a remote subtitle via POST /Items/{itemId}/RemoteSearch/Subtitles/{subtitleId} and select it."""
        from plexnet import media

        item_id = str(getattr(video, 'ratingKey', '') or '')
        part = _part(video)
        if not item_id or not key or part is None:
            return None
        try:
            self.client.post(f'/Items/{quote(item_id, safe="")}/RemoteSearch/Subtitles/{quote(str(key), safe="")}')
        except JellyfinError as e:
            log(f'subtitle download for {item_id} failed: {e.status}')
            return None

        old_ids = {str(s.id) for s in part.streams if s.streamType.asInt() == 3}
        try:
            refreshed = (
                self.client.get(f'/Items/{quote(item_id, safe="")}', params={'fields': 'MediaSources,MediaStreams'})
                or {}
            )
        except JellyfinError as e:
            log(f'subtitle refresh for {item_id} failed: {e.status}')
            return None

        sources = [s for s in (refreshed.get('MediaSources') or ()) if isinstance(s, dict)]
        source = next((s for s in sources if str(s.get('Id') or '') == str(part.id)), None)
        if source is None and sources:
            source = sources[0]
        if source is None:
            return None

        media_el = detail.mediaElement(item_id, source)
        part_el = media_el.find('Part')
        if part_el is None:
            return None
        sub_elems = [el for el in part_el.findall('Stream') if el.get('streamType') == '3']
        new_ext = [el for el in sub_elems if el.get('key') and el.get('id') not in old_ids]
        chosen_el = (
            new_ext[-1] if new_ext else next((el for el in reversed(sub_elems) if el.get('id') not in old_ids), None)
        )
        if chosen_el is None:
            return None

        stream = media.SubtitleStream(chosen_el, server=self, part=part)
        part.streams.append(stream)
        part._markSelected(3, stream.id)
        if hasattr(video, '_subtitleStreams'):
            video._subtitleStreams = None
        return stream

    def findVideoSession(self, sessionId, ratingKey):
        """Return the active playback session in Plex /status/sessions shape, or raise NotFound."""
        from plexnet import exceptions, plexobjects, video  # noqa: F401

        session = self.playback.find_session(session_id=sessionId, rating_key=ratingKey)
        if session is None:
            raise exceptions.NotFound
        return plexobjects.buildItem(self, streaminfo.sessionElement(session), '/status/sessions')

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

    def _searchRows(self, query, count=None, section=None, section_ids=None):
        return search.searchHubs(
            self.client,
            query,
            count=count,
            section=section,
            section_ids=section_ids,
            library_types=self._libraryTypes,
            collection_types=self._collectionTypes,
            on_person=self._rememberPerson,
        )

    def _searchContainer(self, query, count=None, section=None, section_ids=None):
        return container(self._searchRows(query, count=count, section=section, section_ids=section_ids))

    def hubs(self, section=None, count=None, search_query=None, section_ids=None, continue_watching_count=None):
        from plexnet import plexlibrary, plexobjects, video  # noqa: F401

        if search_query is not None:
            rows = self._searchRows(search_query, count=count, section=section, section_ids=section_ids)
            if not rows:
                return []
            root = container(rows)
            self._cacheTypesFromXml(root)
            hubs_container = plexobjects.PlexContainer(
                root, initpath='/hubs/search', server=self, address='/hubs/search'
            )
            return [plexlibrary.Hub(elem, server=self, container=hubs_container) for elem in root]

        from lib.i18n import T

        try:
            if section:
                root = self._sectionHubsContainer(str(section), count=count)
            else:
                rows = home.homeRows(
                    self.client,
                    count=count,
                    continue_watching_count=continue_watching_count,
                )
                favorites = lists.favoritesRow(
                    self.client,
                    count=count,
                    title=T(35259, 'Favorites'),
                    on_item=self._rememberItem,
                )
                if favorites is not None:
                    rows.append(favorites)
                root = container(rows)
        except JellyfinError as e:
            log(f'{"library" if section else "home"} rows failed: {e.status}')
            return []
        self._cacheTypesFromXml(root)
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


def _item_duration_ms(item):
    raw = getattr(item, 'duration', None)
    if raw is None:
        return 0
    if hasattr(raw, 'asInt'):
        try:
            return raw.asInt()
        except (TypeError, ValueError):
            return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _subtitleLanguageCode(language):
    """Normalize a 2-letter or 3-letter language code to Jellyfin's 3-letter ISO-639-2 language parameter."""
    raw = str(language or '').strip().lower().replace('_', '-').split('-')[0]
    if not raw:
        return 'eng'
    if raw == 'pob':
        return 'por'
    attrs = detail.languageAttrs(raw)
    code = attrs.get('languageCode')
    if code and len(code) == 3:
        return code
    return raw if len(raw) == 3 else 'eng'


def _remoteSubtitleScore(entry):
    if entry.get('IsHashMatch'):
        return 100
    rating = entry.get('CommunityRating')
    if rating is not None:
        try:
            val = float(rating)
        except (TypeError, ValueError):
            return 0
        scaled = round(val * 10) if val <= 10 else round(val)
        return max(0, min(100, int(scaled)))
    return 0


def _partStreamIds(part, audio_index=None, subtitle_index=None):
    """Find Plex stream IDs on part matching the chosen video, audio, and subtitle streams for Stream Info."""
    if part is None:
        return None, None, None
    streams = getattr(part, 'streams', None) or ()
    video_id = None
    audio_id = None
    subtitle_id = None
    fallback_video = None
    fallback_audio = None
    for stream in streams:
        stype = stream.streamType.asInt()
        sid = str(stream.id) if stream.id else None
        if not sid:
            continue
        if stype == 1:
            if fallback_video is None:
                fallback_video = sid
            if stream.isSelected():
                video_id = sid
        elif stype == 2:
            if audio_index is not None and stream.index and stream.index.asInt() == int(audio_index):
                audio_id = sid
            elif stream.isSelected() and fallback_audio is None:
                fallback_audio = sid
        elif stype == 3:
            matches_explicit = (
                subtitle_index is not None and stream.index and stream.index.asInt() == int(subtitle_index)
            )
            matches_selected = subtitle_index is None and stream.isSelected() and subtitle_id is None
            if matches_explicit or matches_selected:
                subtitle_id = sid
    return video_id or fallback_video, audio_id or fallback_audio, subtitle_id


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
