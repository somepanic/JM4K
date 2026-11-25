"""
Library browsing against Jellyfin's /Items, /Items/Filters2, /Items/Filters, /UserItems/Resume,
/Shows/NextUp, and /Items/Latest endpoints:
- paginated grids (/library/sections/<id>/all)
- Uno sort mapping (/library/sections/<id>/sorts)
- A-Z jump bar counts via nameLessThan (/library/sections/<id>/firstCharacter)
- library filters and choices (/library/sections/<id>/filters, /genre, /year, /contentRating, /label, /tag)
- per-library Recommended rows (/hubs/sections/<id> and server.hubs(section=...)) and their See more grids
"""

from xml.etree import ElementTree

from .client import log
from .errors import JellyfinAuthError, JellyfinError
from .items import container, hubElement, itemElements

# Plex SEARCHTYPES values relevant to library listings
MOVIE_TYPE = '1'
SHOW_TYPE = '2'
EPISODE_TYPE = '4'
COLLECTION_TYPE = '18'

PLEX_TYPE_TO_JELLYFIN = {
    MOVIE_TYPE: 'Movie',
    SHOW_TYPE: 'Series',
    EPISODE_TYPE: 'Episode',
    COLLECTION_TYPE: 'BoxSet',
}

SECTION_TYPE_TO_JELLYFIN = {
    'movie': 'Movie',
    'show': 'Series',
}

# A library of films and shows together, which Jellyfin gives no CollectionType: one grid, a film library's whose
# tiles open as their own type (SiloMod's D24), listing both kinds
MIXED = 'mixed'
MIXED_ITEM_TYPES = 'Movie,Series'


def collectionType(view):
    """A library view's CollectionType, MIXED for a library of films and shows together (Jellyfin gives it none)."""
    collection_type = (view.get('CollectionType') or '').lower()
    if not collection_type and view.get('Type') == 'CollectionFolder':
        return MIXED
    return collection_type


# ItemFields requested on /Items, /UserItems/Resume, /Shows/NextUp, and /Items/Latest listings
# (every value verified in Jellyfin 10.11.5 OpenAPI ItemFields enum)
GRID_FIELDS = 'Overview,Genres,DateCreated,OriginalTitle,SortName,RecursiveItemCount,ChildCount'

# Sorts advertised by /library/sections/<id>/sorts: (Plex key, Jellyfin ItemSortBy, default dir, display title)
SORTS = (
    ('titleSort', 'SortName', 'asc', 'Title'),
    ('addedAt', 'DateCreated', 'desc', 'Date Added'),
    ('originallyAvailableAt', 'PremiereDate', 'desc', 'Release Date'),
    ('year', 'ProductionYear', 'desc', 'Year'),
    ('rating', 'CriticRating', 'desc', 'Critic Rating'),
    ('audienceRating', 'CommunityRating', 'desc', 'Audience Rating'),
    ('contentRating', 'OfficialRating', 'asc', 'Content Rating'),
    ('duration', 'Runtime', 'desc', 'Duration'),
    ('lastViewedAt', 'DatePlayed', 'desc', 'Date Viewed'),
    ('viewCount', 'PlayCount', 'desc', 'Play Count'),
    ('random', 'Random', 'desc', 'Randomly'),
)

# Full lookup including show/episode-specific Uno sort keys
SORT_FIELDS = {key: jf_field for key, jf_field, _, _ in SORTS}
SORT_FIELDS.update(
    {
        'show.titleSort': 'SeriesSortName',
        'episode.addedAt': 'DateLastContentAdded',
        'unwatched': 'IsUnplayed',
        'unviewedLeafCount': 'IsUnplayed',
    }
)
SORT_DEFAULTS = {key: direction for key, _, direction, _ in SORTS}

# Filters advertised by /library/sections/<id>/filters:
# (Plex filter key, filterType, title, Jellyfin /Items query param)
BOOLEAN_FILTERS = (
    ('unwatched', 'boolean', 'Unplayed'),
    ('favorite', 'boolean', 'Favorites'),
)
VALUE_FILTERS = (
    ('genre', 'string', 'Genre', 'genres'),
    ('year', 'integer', 'Year', 'years'),
    ('contentRating', 'string', 'Content Rating', 'officialRatings'),
    ('label', 'string', 'Labels', 'tags'),
)
VALUE_FILTER_PARAMS = {key: jf_param for key, _, _, jf_param in VALUE_FILTERS}
VALUE_FILTER_PARAMS['tag'] = 'tags'
FILTER_CHOICE_KEYS = frozenset(VALUE_FILTER_PARAMS)

# Plex LibrarySection._applyBoolFilters() emits one of these keys when 'unwatched' is active
UNWATCHED_ARGS = ('unwatched', 'show.unwatchedLeaves', 'episode.unwatched', 'unwatchedLeaves')

JUMP_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

# Prefix for a library Recommended row's "See more" path (<Hub key="...">)
LIBRARY_ROW_PATH = '/hubs/jellyfin/library/'
LATEST_SEE_MORE_MIN = 100


def isTruthy(value):
    return str(value or '').lower() in ('1', 'true', 'yes')


def extractFilters(args):
    """Translate Plex filter query params into Jellyfin GET /Items filter params."""
    params = {}
    for plex_key, jf_param in VALUE_FILTER_PARAMS.items():
        val = args.get(plex_key)
        if val:
            params[jf_param] = val
    if any(isTruthy(args.get(key)) for key in UNWATCHED_ARGS):
        params['isPlayed'] = 'false'
    if isTruthy(args.get('favorite')):
        params['isFavorite'] = 'true'
    return params


def jellyfinSort(plex_sort):
    """Translate a Plex sort string ('key:dir' or 'key') into (sortBy, sortOrder), or (None, None) if unknown."""
    if not plex_sort:
        return None, None
    first = plex_sort.split(',')[0]
    key, _, direction = first.partition(':')
    field = SORT_FIELDS.get(key)
    if field is None:
        return None, None
    if direction == 'desc':
        order = 'Descending'
    elif direction == 'asc':
        order = 'Ascending'
    else:
        order = 'Descending' if SORT_DEFAULTS.get(key) == 'desc' else 'Ascending'
    return field, order


def sortsContainer():
    """/library/sections/<id>/sorts: one <Directory> per supported sort."""
    return container(
        [
            ElementTree.Element('Directory', {'key': key, 'title': title, 'defaultDirection': direction})
            for key, _, direction, title in SORTS
        ]
    )


def filtersContainer(library_id):
    """/library/sections/<id>/filters: boolean toggles (unwatched, favorite) and value filters."""
    entries = [
        ElementTree.Element(
            'Directory',
            {
                'filter': key,
                'filterType': ftype,
                'key': f'/library/sections/{library_id}/{key}',
                'title': title,
            },
        )
        for key, ftype, title in BOOLEAN_FILTERS
    ]
    entries.extend(
        ElementTree.Element(
            'Directory',
            {
                'filter': key,
                'filterType': ftype,
                'key': f'/library/sections/{library_id}/{key}',
                'title': title,
            },
        )
        for key, ftype, title, _ in VALUE_FILTERS
    )
    return container(entries)


def filterChoices(client, library_id, filter_key, args=None, section_type='', collection_type=''):
    """/library/sections/<id>/<filter>: facet choices for genre, label/tag, contentRating, and year.

    Jellyfin 10.11.5 OpenAPI splits these across two FilterController endpoints:
    - GET /Items/Filters2 (QueryFilters) returns Genres (NameGuidPair[]) and Tags (string[]).
    - GET /Items/Filters (QueryFiltersLegacy) returns OfficialRatings (string[]) and Years (int[]).
    """
    include_types = resolveItemTypes(args or {}, section_type=section_type, collection_type=collection_type)
    params = {'parentId': library_id}
    if include_types:
        params['includeItemTypes'] = include_types

    values = []
    if filter_key in ('genre', 'label', 'tag'):
        f2_params = dict(params, recursive='true')
        answer = client.get('/Items/Filters2', params=f2_params) or {}
        if filter_key == 'genre':
            for entry in answer.get('Genres') or ():
                name = entry.get('Name') if isinstance(entry, dict) else entry
                if name:
                    values.append(str(name))
        else:
            values = [str(tag) for tag in (answer.get('Tags') or ()) if tag]
            if not values:
                # Jellyfin 10.11 /Items/Filters2 returns empty Tags on many libraries; /Items/Filters returns them
                legacy = client.get('/Items/Filters', params=params) or {}
                values = [str(tag) for tag in (legacy.get('Tags') or ()) if tag]
    elif filter_key == 'contentRating':
        answer = client.get('/Items/Filters', params=params) or {}
        values = [str(r) for r in (answer.get('OfficialRatings') or ()) if r]
    elif filter_key == 'year':
        answer = client.get('/Items/Filters', params=params) or {}
        values = [str(y) for y in (answer.get('Years') or ()) if y is not None and str(y)]

    return container([ElementTree.Element('Directory', {'key': val, 'title': val}) for val in values])


def window(args, offset=None, limit=None):
    """Extract (start, size) from Plex X-Plex-Container-Start/Size args or offset/limit kwargs.
    size is None for the whole listing, 0 for count-only."""
    start = int(args.get('X-Plex-Container-Start', offset or 0) or 0)
    size = args.get('X-Plex-Container-Size', limit)
    return start, None if size is None else int(size)


def resolveItemTypes(args, section_type='', collection_type=''):
    """Determine Jellyfin includeItemTypes for a section query."""
    if collection_type == 'boxsets':
        return 'BoxSet'
    raw_type = args.get('type')
    if raw_type:
        parts = [p.strip() for p in str(raw_type).split(',') if p.strip()]
        # Uno sends '1,18' or '2,18' on jumpList when collectionMode==2; keep the library's main item type
        # so jump bar counts match /all (Jellyfin keeps BoxSets in their own virtual view).
        if len(parts) > 1 and COLLECTION_TYPE in parts:
            parts = [p for p in parts if p != COLLECTION_TYPE]
        mapped = [PLEX_TYPE_TO_JELLYFIN[p] for p in parts if p in PLEX_TYPE_TO_JELLYFIN]
        if mapped == ['Movie'] and collection_type == MIXED:
            return MIXED_ITEM_TYPES  # a mixed library's grid asks for its film type
        if mapped:
            return ','.join(mapped)
    if collection_type == MIXED:
        return MIXED_ITEM_TYPES
    return SECTION_TYPE_TO_JELLYFIN.get(section_type, '')


def itemsListing(client, base_params, args, offset=None, limit=None, section_id=None, member_type=None):
    """Fetch a paginated Jellyfin GET /Items listing and return a Plex <MediaContainer>."""
    start, size = window(args, offset, limit)
    params = dict(base_params)
    params.update(extractFilters(args))
    params.setdefault('enableTotalRecordCount', 'true')

    sort_by, sort_order = jellyfinSort(args.get('sort'))
    if sort_by:
        params['sortBy'] = sort_by
        params['sortOrder'] = sort_order

    if size == 0:
        params['limit'] = 0
        answer = client.get('/Items', params=params) or {}
        total = int(answer.get('TotalRecordCount') or 0)
        return container(totalSize=total, offset=start)

    params.setdefault('fields', GRID_FIELDS)
    params.setdefault('enableUserData', 'true')
    if start:
        params['startIndex'] = start
    elif size is not None:
        params['startIndex'] = 0
    if size is not None:
        params['limit'] = size

    answer = client.get('/Items', params=params) or {}
    items = answer.get('Items') or []
    total = int(answer.get('TotalRecordCount') if answer.get('TotalRecordCount') is not None else len(items))
    elements = itemElements(items, section_id=section_id, member_type=member_type)
    return container(elements, totalSize=total, offset=start)


def libraryItems(client, library_id, args, offset=None, limit=None, section_type='', collection_type=''):
    """Answer /library/sections/<id>/all from Jellyfin GET /Items (or its collections in a boxsets library)."""
    from . import lists

    if collection_type == 'boxsets':
        return lists.collectionsGrid(
            client,
            library_id=library_id,
            args=args,
            offset=offset,
            limit=limit,
            member_type='movie',
            section_id=library_id,
        )
    base_params = {
        'parentId': library_id,
        'recursive': 'true',
    }
    include_types = resolveItemTypes(args, section_type=section_type, collection_type=collection_type)
    if include_types:
        base_params['includeItemTypes'] = include_types
    return itemsListing(
        client,
        base_params,
        args,
        offset=offset,
        limit=limit,
        section_id=library_id,
        member_type=section_type or 'movie',
    )


def _countItems(client, params):
    answer = client.get('/Items', params=params) or {}
    return int(answer.get('TotalRecordCount') or 0)


def jumpBar(client, library_id, args, section_type='', collection_type='', cache=None, filter_params=None):
    """Answer /library/sections/<id>/firstCharacter with '#' and A-Z counts via Jellyfin nameLessThan & limit=0.
    Counts are computed by cumulative differences so they always sum to the section total, and cached per
    (library_id, item_types, filters)."""
    if args.get('type') == COLLECTION_TYPE:
        return container()

    include_types = resolveItemTypes(args, section_type=section_type, collection_type=collection_type)
    extra_filters = extractFilters(args) if filter_params is None else dict(filter_params)
    descending = (args.get('sort') or '').split(',')[0].endswith(':desc')
    cache_key = (library_id, include_types, tuple(sorted(extra_filters.items())))

    buckets = cache.get(cache_key) if cache is not None else None
    if buckets is None:
        base_params = {
            'parentId': library_id,
            'recursive': 'true',
            'limit': 0,
            'enableTotalRecordCount': 'true',
        }
        if include_types:
            base_params['includeItemTypes'] = include_types
        base_params.update(extra_filters)

        total = _countItems(client, base_params)
        if total <= 0:
            buckets = []
        else:
            buckets = []
            prev_label = '#'
            prev_cum = 0
            for letter in JUMP_LETTERS:
                cum = _countItems(client, dict(base_params, nameLessThan=letter))
                size = max(0, cum - prev_cum)
                if size:
                    buckets.append((prev_label, size))
                prev_cum = max(prev_cum, cum)
                prev_label = letter
            z_size = max(0, total - prev_cum)
            if z_size:
                buckets.append(('Z', z_size))
        if cache is not None:
            cache[cache_key] = buckets

    ordered = list(reversed(buckets)) if descending else list(buckets)
    return container(
        [
            ElementTree.Element('Directory', {'key': letter, 'title': letter, 'size': str(size)})
            for letter, size in ordered
        ]
    )


def fetchRow(client, path, params, row_name):
    """A Home or library row's answer, or None when Jellyfin couldn't give it (the row is left out); a refused
    session still ends."""
    try:
        return client.get(path, params=params)
    except JellyfinAuthError:
        raise
    except JellyfinError as e:
        log(f'row {row_name} failed: {e.status}')
        return None


def itemsAndTotal(answer):
    if isinstance(answer, list):
        return answer, len(answer)
    if isinstance(answer, dict):
        items = answer.get('Items') or []
        total = answer.get('TotalRecordCount')
        return items, int(total) if total is not None else len(items)
    return [], 0


def libraryRows(client, library_id, count=None, section_type='', collection_type=''):
    """Recommended rows for one Jellyfin library as Plex <Hub> elements (empty rows omitted):
    - Continue Watching (/UserItems/Resume?parentId=...&mediaTypes=Video)
    - Next Up (/Shows/NextUp?parentId=..., show libraries only)
    - Recently Added (/Items/Latest?parentId=...&groupItems=true)
    """
    rows = []
    limit_param = {'limit': int(count)} if count else {}

    if collection_type != 'boxsets':
        resume_params = {
            'parentId': library_id,
            'mediaTypes': 'Video',
            'fields': GRID_FIELDS,
            'enableUserData': 'true',
            'enableTotalRecordCount': 'true',
            **limit_param,
        }
        resume_ans = fetchRow(client, '/UserItems/Resume', resume_params, 'continue_watching')
        resume_items, resume_total = itemsAndTotal(resume_ans)
        resume_els = itemElements(resume_items, section_id=library_id)
        if resume_els:
            rows.append(
                hubElement(
                    f'jellyfin.{library_id}.continue_watching',
                    'Continue Watching',
                    resume_els,
                    f'{LIBRARY_ROW_PATH}{library_id}/continue_watching',
                    total=resume_total,
                    more=resume_total > len(resume_els),
                )
            )

        if section_type == 'show' or collection_type == MIXED:
            next_up_params = {
                'parentId': library_id,
                'fields': GRID_FIELDS,
                'enableUserData': 'true',
                'enableTotalRecordCount': 'true',
                **limit_param,
            }
            next_up_ans = fetchRow(client, '/Shows/NextUp', next_up_params, 'next_up')
            next_up_items, next_up_total = itemsAndTotal(next_up_ans)
            next_up_els = itemElements(next_up_items, section_id=library_id)
            if next_up_els:
                rows.append(
                    hubElement(
                        f'jellyfin.{library_id}.next_up',
                        'Next Up',
                        next_up_els,
                        f'{LIBRARY_ROW_PATH}{library_id}/next_up',
                        total=next_up_total,
                        more=next_up_total > len(next_up_els),
                    )
                )

    latest_params = {
        'parentId': library_id,
        'groupItems': 'true',
        'fields': GRID_FIELDS,
        'enableUserData': 'true',
        **limit_param,
    }
    latest_ans = fetchRow(client, '/Items/Latest', latest_params, 'latest')
    latest_items, _ = itemsAndTotal(latest_ans)
    latest_els = itemElements(latest_items, section_id=library_id)
    if latest_els:
        full = bool(count) and len(latest_els) >= int(count)
        rows.append(
            hubElement(
                f'jellyfin.{library_id}.latest',
                'Recently Added',
                latest_els,
                f'{LIBRARY_ROW_PATH}{library_id}/latest',
                total=len(latest_els),
                more=full,
            )
        )

    return rows


def libraryRowItems(client, library_id, row_kind, args=None, offset=None, limit=None):
    """Answer See more for one library Recommended row (/hubs/jellyfin/library/<id>/<kind>)."""
    start, size = window(args or {}, offset, limit)
    if row_kind in ('continue_watching', 'next_up'):
        endpoint = '/UserItems/Resume' if row_kind == 'continue_watching' else '/Shows/NextUp'
        params = {
            'parentId': library_id,
            'enableTotalRecordCount': 'true',
        }
        if row_kind == 'continue_watching':
            params['mediaTypes'] = 'Video'
        if size == 0:
            params['limit'] = 0
            answer = client.get(endpoint, params=params) or {}
            return container(totalSize=int(answer.get('TotalRecordCount') or 0), offset=start)
        params['fields'] = GRID_FIELDS
        params['enableUserData'] = 'true'
        if start or size is not None:
            params['startIndex'] = start
        if size is not None:
            params['limit'] = size
        answer = client.get(endpoint, params=params) or {}
        items, total = itemsAndTotal(answer)
        return container(itemElements(items, section_id=library_id), totalSize=total, offset=start)

    if row_kind == 'latest':
        want = LATEST_SEE_MORE_MIN if size is None else max(LATEST_SEE_MORE_MIN, start + size)
        answer = client.get(
            '/Items/Latest',
            params={
                'parentId': library_id,
                'groupItems': 'true',
                'fields': GRID_FIELDS,
                'enableUserData': 'true',
                'limit': want,
            },
        )
        items, _ = itemsAndTotal(answer)
        elements = itemElements(items, section_id=library_id)
        page = [] if size == 0 else elements[start : None if size is None else start + size]
        return container(page, totalSize=len(elements), offset=start)

    return container()
