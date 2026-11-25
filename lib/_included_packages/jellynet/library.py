"""
Library browsing against Jellyfin's /Items endpoint: paginated grids (/library/sections/<id>/all),
Uno sort mapping (/library/sections/<id>/sorts), and the A-Z jump bar (/library/sections/<id>/firstCharacter).
"""

from xml.etree import ElementTree

from .items import container, itemElements

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

# ItemFields requested on /Items grid listings (every value verified in Jellyfin 10.11.5 OpenAPI ItemFields enum)
GRID_FIELDS = 'Overview,Genres,DateCreated,OriginalTitle,SortName,RecursiveItemCount,ChildCount'

# Sorts advertised by /library/sections/<id>/sorts: (Plex key, Jellyfin ItemSortBy, default dir, display title)
SORTS = (
    ('titleSort', 'SortName', 'asc', 'Title'),
    ('addedAt', 'DateCreated', 'desc', 'Date Added'),
    ('originallyAvailableAt', 'PremiereDate', 'desc', 'Release Date'),
    ('year', 'ProductionYear', 'desc', 'Year'),
    ('rating', 'CommunityRating', 'desc', 'Critic Rating'),
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

JUMP_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


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
        if mapped:
            return ','.join(mapped)
    return SECTION_TYPE_TO_JELLYFIN.get(section_type, '')


def itemsListing(client, base_params, args, offset=None, limit=None, section_id=None, member_type=None):
    """Fetch a paginated Jellyfin GET /Items listing and return a Plex <MediaContainer>."""
    start, size = window(args, offset, limit)
    params = dict(base_params)
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
    """Answer /library/sections/<id>/all from Jellyfin GET /Items."""
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
    extra_filters = dict(filter_params or {})
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
