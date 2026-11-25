"""
A library's screens from Silo's catalog (GET /api/v2/catalog?source=query&library_id=...): the grid's count and its
chunks (plexlibrary.LibrarySection.all()) and its sort menu (/library/sections/<id>/sorts), as the Plex elements
plexnet reads.

A chunk is Silo's explicit window jump (seek=<start>&limit=<size>): the grid asks for chunks out of order, which a
cursor chain can't serve, and on the user's server seek windows and the next_cursor chain gave the same items in the
same order (Review Focus #5). One past CATALOG_PAGE_MAX is asked in pieces.
"""

from xml.etree import ElementTree

from . import home
from .items import container, itemElements

# a library row's own path, its <Hub> key: /hubs/silo/library/<library id>/<section id> (SiloServer's route)
LIBRARY_ROW_PATH = home.ROW_PATH + 'library/'

# the most items /api/v2/catalog answers a request
CATALOG_PAGE_MAX = 200
# plexobjects.SEARCHTYPES['collection']: the type a grid's Collections tab asks for
COLLECTION_TYPE = '18'
# Plex's episode type: a library's every episode, which Silo's catalog lists as type=episode (Play All's queue,
# plexnet playqueue.showEpisodes())
EPISODE_TYPE = '4'

# The sorts Uno's sort menu knows (library_grid.SORT_KEYS) that Silo's catalog sorts by: (Plex key, Silo field, Plex
# default direction, title). Checked on the user's server: Silo refuses rating, random, last_watched, play_count,
# duration and user_rating, so Date Viewed, My Rating, Plays, Resolution and Randomly aren't offered.
SORTS = (
    ('titleSort', 'title', 'asc', 'Title'),
    ('addedAt', 'added_at', 'desc', 'Date Added'),
    ('originallyAvailableAt', 'release_date', 'desc', 'Release Date'),
    ('year', 'year', 'desc', 'Year'),
    ('rating', 'rating_rt_critic', 'desc', 'Critic Rating'),
    ('audienceRating', 'rating_rt_audience', 'desc', 'Audience Rating'),
    ('contentRating', 'content_rating', 'asc', 'Content Rating'),
    ('duration', 'runtime', 'desc', 'Duration'),
    ('viewOffset', 'progress', 'desc', 'Progress'),
)
SILO_FIELDS = {key: field for key, field, _, _ in SORTS}

# The value filters Uno's Filter menu offers that Silo's catalog takes: (Plex filter key, catalog parameter, title,
# GET /api/v2/catalog/filters facet with its values). Silo has no years facet and no unwatched parameter, so Year,
# Decade and Unplayed aren't offered.
FILTERS = (
    ('genre', 'genre', 'Genre', 'genres'),
    ('contentRating', 'content_rating', 'Content Rating', 'content_ratings'),
)
FILTER_PARAMS = {key: param for key, param, _, _ in FILTERS}
FILTER_FACETS = {key: facet for key, _, _, facet in FILTERS}


def catalogSort(plexSort):
    """A grid's sort= ('key:dir', plexlibrary.sortArg()) as Silo's ('field' or '-field'); None for one Silo lacks."""
    if not plexSort:
        return None
    key, _, direction = plexSort.split(',')[0].partition(':')
    field = SILO_FIELDS.get(key)
    if field is None:
        return None
    return '-' + field if direction == 'desc' else field


def sortsContainer():
    """/library/sections/<id>/sorts: one <Directory> per sort Silo has, as Plex's /sorts answers."""
    return container(
        [
            ElementTree.Element('Directory', {'key': key, 'title': title, 'defaultDirection': direction})
            for key, _, direction, title in SORTS
        ]
    )


def window(args, offset=None, limit=None):
    """The (start, size) a listing asks for: Plex's X-Plex-Container-Start/Size, or listItems()' offset/limit.
    size None: everything; 0: the count alone."""
    start = int(args.get('X-Plex-Container-Start', offset or 0) or 0)
    size = args.get('X-Plex-Container-Size', limit)
    return start, None if size is None else int(size)


def catalogListing(client, query, args, offset=None, limit=None):
    """
    A catalog listing (query: source and its scope) as a Plex container: its count (size 0: totalSize alone) or a
    chunk, sorted as asked when Silo sorts by it. No size: the whole listing.
    """
    start, size = window(args, offset, limit)
    query = dict(query)
    for key, param in FILTER_PARAMS.items():
        if args.get(key):
            query[param] = args[key]
    sort = catalogSort(args.get('sort'))
    if sort:
        query['sort'] = sort
    if size == 0:
        answer = client.request('GET', '/api/v2/catalog', params=dict(query, limit=1)) or {}
        return container(totalSize=answer.get('total') or 0)

    elements, total, position = [], 0, start
    while size is None or position < start + size:
        want = CATALOG_PAGE_MAX if size is None else min(start + size - position, CATALOG_PAGE_MAX)
        answer = client.request('GET', '/api/v2/catalog', params=dict(query, seek=position, limit=want)) or {}
        items = answer.get('items') or []
        total = answer.get('total') or 0
        elements += itemElements(items)
        position += len(items)
        if len(items) < want or position >= total:
            break
    return container(elements, totalSize=total, offset=start)


def libraryItems(client, libraryId, args, offset=None, limit=None, memberType=''):
    """A library's grid: its titles from the catalog, or on the Collections tab (type=18) its collections; type=4 its
    episodes."""
    if args.get('type') == COLLECTION_TYPE:
        return collectionsTab(client, libraryId, args, offset, limit, memberType)
    query = {'source': 'query', 'library_id': libraryId}
    if args.get('type') == EPISODE_TYPE:
        query['type'] = 'episode'
    listing = catalogListing(client, query, args, offset, limit)
    for element in listing:
        element.set('librarySectionID', libraryId)
    return listing


def collectionElement(collection, memberType):
    """A Silo collection as the <Directory type="collection"> plexlibrary.Collection is built from; its key is its
    titles' path (SiloServer's route), which Collection.all() lists."""
    attrs = {
        'type': 'collection',
        'ratingKey': collection['id'],
        'key': f'/library/collections/{collection["id"]}/children',
        'title': collection.get('title') or '',
        'childCount': str(collection.get('item_count') or 0),
    }
    for name, value in (
        ('thumb', collection.get('poster_url')),
        ('art', collection.get('backdrop_url')),
        ('summary', collection.get('description')),
        ('subtype', memberType),
    ):
        if value:
            attrs[name] = value
    return ElementTree.Element('Directory', attrs)


def collectionsTab(client, libraryId, args, offset=None, limit=None, memberType=''):
    """
    A library's Collections tab from GET /api/v2/library/{id}/collections: its collections in Silo's order, then any
    in its groups (personal ones, shared from other profiles) not listed yet, each once. Uno has no groups.
    """
    answer = client.request('GET', f'/api/v2/library/{libraryId}/collections') or {}
    collections = list(answer.get('collections') or [])
    for group in answer.get('groups') or []:
        collections += group.get('collections') or []
    seen, elements = set(), []
    for collection in collections:
        if collection.get('id') and collection['id'] not in seen:
            seen.add(collection['id'])
            elements.append(collectionElement(collection, memberType))
    start, size = window(args, offset, limit)
    page = [] if size == 0 else elements[start : None if size is None else start + size]
    return container(page, totalSize=len(elements), offset=start)


def collectionItems(client, collectionId, args, offset=None, limit=None):
    """A collection's titles (plexlibrary.Collection.all()) from the catalog's library_collection source."""
    return catalogListing(client, {'source': 'library_collection', 'collection_id': collectionId}, args, offset, limit)


def jumpBar(client, libraryId, args):
    """
    /library/sections/<id>/firstCharacter (LibrarySection.jumpList()) from POST /api/v2/catalog/letters: one
    <Directory> per run, Silo's blocks of consecutive titles under one letter, covering the list in order. Uno adds up
    the sizes to place each entry, so a run's size is the distance to the next one and every letter starts where
    Silo says. A letter in two blocks keeps both, the second with its own key.
    """
    if args.get('type') == COLLECTION_TYPE:
        return container()  # Silo's letters count a library's titles, not its collections: no bar on that tab
    if any(args.get(key) for key in FILTER_PARAMS):
        return container()  # nor with a filter on: its letters would count the unfiltered list
    order = 'desc' if (args.get('sort') or '').split(',')[0].endswith(':desc') else 'asc'
    answer = client.request('POST', '/api/v2/catalog/letters', json={'library_id': libraryId, 'order': order}) or {}
    runs = answer.get('runs') or []
    total = answer.get('total') or 0
    entries, seen = [], {}
    for index, run in enumerate(runs):
        end = runs[index + 1]['position'] if index + 1 < len(runs) else total
        letter = run.get('letter') or '#'
        seen[letter] = seen.get(letter, 0) + 1
        key = letter if seen[letter] == 1 else f'{letter}~{seen[letter]}'
        entries.append(
            ElementTree.Element('Directory', {'key': key, 'title': letter, 'size': str(end - run['position'])})
        )
    return container(entries)


def libraryRows(client, libraryId, count):
    """
    A library's Recommended rows (SiloServer.hubs(section)) from Silo's own rows for it (D8): GET
    /api/v2/library/{id}/sections, the ones with items for the profile, count items each. Silo doesn't say whether a
    row has more (has_more is null), so a full row says so; its See more lists the row's own items.
    """
    answer = client.request(
        'GET', f'/api/v2/library/{libraryId}/sections', params={'item_limit': count} if count else None
    )
    rows = []
    for section in (answer or {}).get('sections') or []:
        full = bool(count) and len(section.get('items') or []) >= count
        rows.append(
            home.hubElement(
                dict(section, has_more=section.get('has_more') or full),
                key=f'{LIBRARY_ROW_PATH}{libraryId}/{section["id"]}',
            )
        )
    return rows


def libraryRowItems(client, libraryId, sectionId):
    """Every item element of one library row: GET /api/v2/library/{id}/sections/{section_id}/items."""
    answer = client.request('GET', f'/api/v2/library/{libraryId}/sections/{sectionId}/items')
    return itemElements((answer or {}).get('items'))


def filtersContainer(libraryId):
    """/library/sections/<id>/filters (LibrarySection.listFilters()): one <Directory> per filter Silo's catalog takes,
    its key the path of its values."""
    return container(
        [
            ElementTree.Element(
                'Directory',
                {'filter': key, 'filterType': 'string', 'key': f'/library/sections/{libraryId}/{key}', 'title': title},
            )
            for key, _, title, _ in FILTERS
        ]
    )


def filterChoices(client, libraryId, filterKey):
    """/library/sections/<id>/<filter> (LibrarySection.listChoices()): the library's values for the filter, from GET
    /api/v2/catalog/filters, each a <Directory> keyed by the value the catalog takes."""
    answer = client.request(
        'GET', '/api/v2/catalog/filters', params={'source': 'query', 'library_id': libraryId, 'skip_technical': 'true'}
    )
    values = (answer or {}).get(FILTER_FACETS[filterKey]) or []
    return container([ElementTree.Element('Directory', {'key': value, 'title': value}) for value in values if value])
