"""
Search titles and people on Jellyfin (decision D20: this server's libraries only):
- GET /Items?searchTerm=...&recursive=true&includeItemTypes=Movie,Series,Episode (or narrowed by section_ids)
  grouped into Plex <Hub> elements by type in first-appearance order, each capped at count.
- GET /Persons?searchTerm=...&enableImages=true mapped to an actor <Hub> whose entries open the person screen.
"""

from .client import log
from .detail import element
from .errors import JellyfinAuthError, JellyfinError
from .items import buildImageUrl, hubElement, itemElement, itemThumb

DEFAULT_ITEM_TYPES = ('Movie', 'Series', 'Episode')

# Jellyfin BaseItemKind -> Plex hub type
KINDS = {
    'Movie': 'movie',
    'Series': 'show',
    'Episode': 'episode',
    'BoxSet': 'collection',
}

HUB_TITLES = {
    'movie': 'Movies',
    'show': 'Shows',
    'episode': 'Episodes',
    'collection': 'Collections',
    'actor': 'People',
}

# Explicit type filter tokens (section_ids) -> (Jellyfin BaseItemKinds, include_people).
# Music and photos stay omitted until Phase 11 (D4).
FILTER_TYPES = {
    'all': (DEFAULT_ITEM_TYPES, True),
    'movie': (('Movie',), False),
    'movies': (('Movie',), False),
    'show': (('Series', 'Episode'), False),
    'shows': (('Series', 'Episode'), False),
    'series': (('Series',), False),
    'episode': (('Episode',), False),
    'episodes': (('Episode',), False),
    'collection': (('BoxSet',), False),
    'collections': (('BoxSet',), False),
    'boxset': (('BoxSet',), False),
    'boxsets': (('BoxSet',), False),
    'people': ((), True),
    'person': ((), True),
    'persons': ((), True),
    'actor': ((), True),
    'actors': ((), True),
    'director': ((), True),
    'directors': ((), True),
}

SEARCH_FIELDS = 'Overview,Genres,DateCreated,PrimaryImageAspectRatio'
DEFAULT_COUNT = 10
TITLES_ASKED = 100


def _parseCount(count):
    if count is None:
        return DEFAULT_COUNT
    try:
        value = int(count)
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return value if value > 0 else DEFAULT_COUNT


def _resolveScope(section_ids, library_types=None, collection_types=None):
    """Map section_ids / type filter tokens to (jellyfin_item_types, include_people)."""
    if section_ids is None:
        return DEFAULT_ITEM_TYPES, True
    raw_tokens = [section_ids] if isinstance(section_ids, str) else list(section_ids)
    tokens = [str(token).strip() for token in raw_tokens if token is not None and str(token).strip()]
    if not tokens:
        return DEFAULT_ITEM_TYPES, True

    library_types = library_types or {}
    collection_types = collection_types or {}
    item_types = []
    include_people = False

    def add_types(types):
        for jf_type in types:
            if jf_type not in item_types:
                item_types.append(jf_type)

    for token in tokens:
        if token in collection_types or token in library_types:
            coll_type = collection_types.get(token, '')
            lib_type = library_types.get(token, '')
            if coll_type == 'boxsets':
                add_types(('BoxSet',))
            elif lib_type == 'movie':
                add_types(('Movie',))
            elif lib_type == 'show':
                add_types(('Series', 'Episode'))
            continue

        mapped = FILTER_TYPES.get(token.lower())
        if mapped is not None:
            add_types(mapped[0])
            include_people = include_people or mapped[1]

    return tuple(item_types), include_people


def _ask(client, path, params):
    try:
        answer = client.get(path, params=params) or {}
    except JellyfinAuthError:
        raise
    except JellyfinError as e:
        log(f'search {path} failed: {e.status}')
        return []
    if not isinstance(answer, dict):
        return []
    items = answer.get('Items')
    return items if isinstance(items, list) else []


def _personThumb(person):
    thumb = itemThumb(person)
    if thumb:
        return thumb
    pid = person.get('Id')
    primary_tag = person.get('PrimaryImageTag')
    return buildImageUrl('', pid, 'Primary', tag=primary_tag) if pid and primary_tag else ''


def _searchHub(kind, elements):
    hub = hubElement(kind, HUB_TITLES.get(kind, kind.title()), elements, '')
    hub.set('type', kind)
    return hub


def searchHubs(
    client,
    query,
    count=None,
    section=None,
    section_ids=None,
    library_types=None,
    collection_types=None,
    on_person=None,
):
    """Search Jellyfin titles (/Items) and people (/Persons), returning non-empty Plex <Hub> elements."""
    term = str(query).strip() if query is not None else ''
    if not term:
        return []

    limit = _parseCount(count)
    item_types, include_people = _resolveScope(
        section_ids,
        library_types=library_types,
        collection_types=collection_types,
    )
    hubs = []

    if item_types:
        allowed_kinds = set(item_types)
        items_limit = limit if len(item_types) == 1 else max(TITLES_ASKED, limit)
        params = {
            'searchTerm': term,
            'recursive': 'true',
            'includeItemTypes': ','.join(item_types),
            'fields': SEARCH_FIELDS,
            'enableUserData': 'true',
            'limit': items_limit,
        }
        if section:
            params['parentId'] = str(section)

        grouped = {}
        for item in _ask(client, '/Items', params):
            if not isinstance(item, dict):
                continue
            jf_type = item.get('Type')
            if jf_type not in allowed_kinds:
                continue
            kind = KINDS.get(jf_type)
            tile = itemElement(item) if kind else None
            if tile is not None and len(grouped.setdefault(kind, [])) < limit:
                grouped[kind].append(tile)

        for kind, tiles in grouped.items():
            if tiles:
                hubs.append(_searchHub(kind, tiles))

    if include_people:
        people_els = []
        people_params = {'searchTerm': term, 'limit': limit, 'enableImages': 'true'}
        for person in _ask(client, '/Persons', people_params):
            if not isinstance(person, dict):
                continue
            pid = person.get('Id')
            name = person.get('Name')
            if not pid or not name:
                continue
            pid_str, name_str = str(pid), str(name)
            if on_person is not None:
                on_person(pid_str, name_str)
            people_els.append(
                element(
                    'Directory',
                    {
                        'type': 'tag',
                        'tagType': 'actor',
                        'id': pid_str,
                        'tagKey': pid_str,
                        'tag': name_str,
                        'thumb': _personThumb(person),
                    },
                )
            )
            if len(people_els) >= limit:
                break
        if people_els:
            hubs.append(_searchHub('actor', people_els))

    return hubs
