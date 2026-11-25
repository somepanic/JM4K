"""
Jellyfin home rows as Uno's Home reads them:
- Continue Watching (GET /UserItems/Resume?mediaTypes=Video)
- Next Up (GET /Shows/NextUp)
- Latest in each non-excluded video library (GET /Items/Latest?parentId=...&groupItems=true)

Section order follows the user's Jellyfin web DisplayPreferences (homesection0..9 under
/DisplayPreferences/usersettings?client=emby), falling back to Continue Watching, Next Up,
then Latest per library ordered by User.Configuration.OrderedViews (omitting
LatestItemsExcludes and MyMediaExcludes). Empty or failing rows are omitted, row identifiers
are deterministic across refreshes, and each row's key supports See more paging.
"""

from urllib.parse import quote, unquote

from . import library
from .client import log
from .errors import JellyfinAuthError, JellyfinError
from .items import container, hubElement, itemElements

# section_ids.CONTINUE_WATCHING_ID: Home merges the row with this identifier across servers
CONTINUE_WATCHING_ID = 'continueWatching'
NEXT_UP_ID = 'jellyfin.home.nextup'

# Prefix for a Home row's "See more" path (<Hub key="...">), answered by JellyfinServer.query()
ROW_PATH = '/jellyfin/home/'

# Default home row section order when DisplayPreferences has no supported homesection* entries
DEFAULT_SECTIONS = ('resume', 'nextup', 'latestmedia')

# Jellyfin web homesection* custom preference values mapped to supported JM4K row kinds
SECTION_ALIASES = {
    'resume': 'resume',
    'resumevideo': 'resume',
    'nextup': 'nextup',
    'latestmedia': 'latestmedia',
}

HOME_SECTION_SLOTS = 10

# Video library collection types eligible for Latest rows (music hidden until Phase 11;
# boxsets, livetv, and playlists have no home Latest video row).
LATEST_COLLECTION_TYPES = frozenset(('movies', 'tvshows'))

HOME_FIELDS = library.GRID_FIELDS
LATEST_SEE_MORE_MIN = library.LATEST_SEE_MORE_MIN


def latestHubId(library_id):
    """Deterministic hubIdentifier for a library's Home Latest row.

    Places library_id before 'latest' so Plex BaseHub.getCleanHubIdentifier() (which strips
    trailing numeric segments) never collapses two numeric library IDs into one row (SM4K #7).
    """
    return f'jellyfin.home.{library_id}.latest'


def sectionOrder(client):
    """Read the user's Jellyfin home section order from DisplayPreferences/usersettings?client=emby.

    Best-effort: non-auth JellyfinError falls back to DEFAULT_SECTIONS; empty or unsupported-only
    homesection* values also fall back to DEFAULT_SECTIONS.
    """
    try:
        prefs = client.get('/DisplayPreferences/usersettings', params={'client': 'emby'}) or {}
    except JellyfinAuthError:
        raise
    except JellyfinError as e:
        log(f'home display preferences failed: {e.status}')
        return list(DEFAULT_SECTIONS)

    custom_prefs = prefs.get('CustomPrefs') if isinstance(prefs, dict) else None
    if not isinstance(custom_prefs, dict):
        return list(DEFAULT_SECTIONS)

    order = []
    for index in range(HOME_SECTION_SLOTS):
        raw = custom_prefs.get(f'homesection{index}')
        if not raw:
            continue
        mapped = SECTION_ALIASES.get(str(raw).strip().lower())
        if mapped and mapped not in order:
            order.append(mapped)

    return order or list(DEFAULT_SECTIONS)


def latestLibraries(client):
    """Return eligible (library_id, name) pairs for Home Latest rows in the user's OrderedViews order,
    skipping music/boxsets/livetv/playlists and any ID in LatestItemsExcludes or MyMediaExcludes."""
    try:
        me = client.get('/Users/Me') or {}
    except JellyfinAuthError:
        raise
    except JellyfinError as e:
        log(f'home user config failed: {e.status}')
        me = {}

    config = me.get('Configuration') if isinstance(me, dict) else None
    if not isinstance(config, dict):
        config = {}

    excluded = {
        str(vid).strip().lower()
        for key in ('LatestItemsExcludes', 'MyMediaExcludes')
        for vid in (config.get(key) or ())
        if vid
    }
    ordered_index = {}
    for vid in config.get('OrderedViews') or ():
        if not vid:
            continue
        norm = str(vid).strip().lower()
        if norm not in ordered_index:
            ordered_index[norm] = len(ordered_index)

    try:
        views_resp = client.get('/UserViews') or {}
    except JellyfinAuthError:
        raise
    except JellyfinError as e:
        log(f'home user views failed: {e.status}')
        return []

    eligible = []
    for idx, entry in enumerate(views_resp.get('Items') or ()):
        if not isinstance(entry, dict) or not entry.get('Id'):
            continue
        collection_type = (entry.get('CollectionType') or '').lower()
        if collection_type not in LATEST_COLLECTION_TYPES:
            continue
        lib_id = str(entry['Id'])
        norm_id = lib_id.strip().lower()
        if norm_id in excluded:
            continue
        eligible.append((ordered_index.get(norm_id, len(ordered_index)), idx, lib_id, str(entry.get('Name') or '')))

    eligible.sort(key=lambda item: (item[0], item[1]))
    return [(lib_id, name) for _, _, lib_id, name in eligible]


def _resumeHub(client, limit):
    params = {
        'mediaTypes': 'Video',
        'fields': HOME_FIELDS,
        'enableUserData': 'true',
        'enableTotalRecordCount': 'true',
    }
    if limit:
        params['limit'] = int(limit)
    answer = library.fetchRow(client, '/UserItems/Resume', params, 'resume')
    items, total = library.itemsAndTotal(answer)
    elements = itemElements(items)
    if not elements:
        return None
    has_more = total > len(items)
    return hubElement(
        CONTINUE_WATCHING_ID,
        'Continue Watching',
        elements,
        f'{ROW_PATH}resume',
        total=max(total, len(elements)) if has_more else len(elements),
        more=has_more,
    )


def _nextUpHub(client, limit):
    params = {
        'fields': HOME_FIELDS,
        'enableUserData': 'true',
        'enableTotalRecordCount': 'true',
    }
    if limit:
        params['limit'] = int(limit)
    answer = library.fetchRow(client, '/Shows/NextUp', params, 'nextup')
    items, total = library.itemsAndTotal(answer)
    elements = itemElements(items)
    if not elements:
        return None
    has_more = total > len(items)
    return hubElement(
        NEXT_UP_ID,
        'Next Up',
        elements,
        f'{ROW_PATH}nextup',
        total=max(total, len(elements)) if has_more else len(elements),
        more=has_more,
    )


def _latestHubs(client, limit):
    hubs = []
    for library_id, library_name in latestLibraries(client):
        params = {
            'parentId': library_id,
            'groupItems': 'true',
            'fields': HOME_FIELDS,
            'enableUserData': 'true',
        }
        if limit:
            params['limit'] = int(limit)
        answer = library.fetchRow(client, '/Items/Latest', params, f'latest:{library_id}')
        items, _ = library.itemsAndTotal(answer)
        elements = itemElements(items, section_id=library_id, section_title=library_name or None)
        if not elements:
            continue
        title = f'Latest {library_name}' if library_name else 'Recently Added'
        key = f'{ROW_PATH}latest/{quote(library_id, safe="")}'
        full = bool(limit) and len(items) >= int(limit)
        hubs.append(
            hubElement(
                latestHubId(library_id),
                title,
                elements,
                key,
                total=len(elements),
                more=full,
            )
        )
    return hubs


def homeRows(client, count=None, continue_watching_count=None):
    """Fetch Home's rows in the user's Jellyfin order as Plex <Hub> elements (empty rows omitted)."""
    rows = []
    resume_limit = continue_watching_count if continue_watching_count else count
    for kind in sectionOrder(client):
        if kind == 'resume':
            hub = _resumeHub(client, resume_limit)
            if hub is not None:
                rows.append(hub)
        elif kind == 'nextup':
            hub = _nextUpHub(client, count)
            if hub is not None:
                rows.append(hub)
        elif kind == 'latestmedia':
            rows.extend(_latestHubs(client, count))
    return rows


def rowItems(client, row_spec, args=None, offset=None, limit=None):
    """Answer See more paging for one Home row (/jellyfin/home/resume, /nextup, /latest/<library_id>)."""
    row_kind, _, raw_target = row_spec.partition('/')
    start, size = library.window(args or {}, offset, limit)
    start = max(0, start)
    if size is not None:
        size = max(0, size)

    if row_kind in ('resume', 'nextup') and not raw_target:
        endpoint = '/UserItems/Resume' if row_kind == 'resume' else '/Shows/NextUp'
        params = {'enableTotalRecordCount': 'true'}
        if row_kind == 'resume':
            params['mediaTypes'] = 'Video'
        if size == 0:
            params['limit'] = 0
            answer = client.get(endpoint, params=params) or {}
            return container(totalSize=int(answer.get('TotalRecordCount') or 0), offset=start)
        params['fields'] = HOME_FIELDS
        params['enableUserData'] = 'true'
        if start or size is not None:
            params['startIndex'] = start
        if size is not None:
            params['limit'] = size
        answer = client.get(endpoint, params=params) or {}
        items, total = library.itemsAndTotal(answer)
        return container(itemElements(items), totalSize=total, offset=start)

    if row_kind == 'latest' and raw_target and '/' not in raw_target:
        library_id = unquote(raw_target)
        want = LATEST_SEE_MORE_MIN if size is None else max(LATEST_SEE_MORE_MIN, start + size)
        answer = client.get(
            '/Items/Latest',
            params={
                'parentId': library_id,
                'groupItems': 'true',
                'fields': HOME_FIELDS,
                'enableUserData': 'true',
                'limit': want,
            },
        )
        items, _ = library.itemsAndTotal(answer)
        elements = itemElements(items, section_id=library_id)
        page = [] if size == 0 else elements[start : None if size is None else start + size]
        return container(page, totalSize=len(elements), offset=start)

    return container()
