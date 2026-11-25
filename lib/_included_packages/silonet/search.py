"""
Search on Silo (decision D20: the Silo library only, titles and people), as the hubs Plex Uno's search reads
(server.hubs(search_query=...)): GET /api/v2/catalog?q= answers titles in relevance order, grouped here as Plex's
movie, show and episode hubs (in the order a kind first appears, each up to count); GET /api/v2/catalog/people?q=
answers people, an actor hub whose people open the person screen (silonet.people; their Silo id is their tagKey).
"""

from .client import log
from .detail import element
from .errors import SiloError
from .items import itemElement

KINDS = {'movie': 'movie', 'series': 'show', 'episode': 'episode'}  # Silo's type -> Plex's hub type
TITLES_ASKED = 100  # titles asked for, so each kind fills its hub


def _ask(client, path, params):
    try:
        return (client.request('GET', path, params=params) or {}).get('items') or []
    except SiloError as e:
        log(f'search {path} failed: {e.status} {e.type}')
        return []


def hubElement(kind, elements):
    hub = element('Hub', {'type': kind, 'hubIdentifier': kind, 'size': len(elements), 'more': '0'})
    hub.extend(elements)
    return hub


def searchHubs(client, query, count):
    count = max(1, int(count or 10))
    grouped = {}
    for item in _ask(client, '/api/v2/catalog', {'q': query, 'limit': TITLES_ASKED}):
        kind = KINDS.get(item.get('type'))
        tile = itemElement(item) if kind else None
        if tile is not None and len(grouped.setdefault(kind, [])) < count:
            grouped[kind].append(tile)
    hubs = [hubElement(kind, tiles) for kind, tiles in grouped.items()]
    people = [
        element(
            'Directory',
            {
                'type': 'tag',
                'tagType': 'actor',
                'id': person.get('id'),
                'tagKey': person.get('id'),
                'tag': person.get('name'),
                'thumb': person.get('photo_url'),
            },
        )
        for person in _ask(client, '/api/v2/catalog/people', {'q': query, 'limit': count})
        if person.get('id')
    ]
    if people:
        hubs.append(hubElement('actor', people))
    return hubs
