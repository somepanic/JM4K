"""
People details (GET /Persons/{name}) and their library filmography (GET /Items?personIds=...,
decision D10: this server's libraries only, never plex.tv):
- Role.getDetails() -> /library/people/<id>
- person.libraryFilmography() / Role.getFilmography() -> /library/people/<id>/media
"""

from urllib.parse import quote, unquote

from . import detail, library
from .items import buildImageUrl, container, itemElements

PAGE = 200
MOST = 1000


def _isoDate(text):
    return text[:10] if isinstance(text, str) and len(text) >= 10 else None


def person(client, person_key, person_names=None, person_ids=None):
    """Answer /library/people/<id-or-name> from Jellyfin GET /Persons/{name} as Plex's <Directory type="person">."""
    key_str = str(person_key)
    lookup_name = person_names.get(key_str) if person_names is not None else None
    if not lookup_name:
        lookup_name = unquote(key_str)

    answer = client.get(f'/Persons/{quote(lookup_name, safe="")}') or {}
    if not isinstance(answer, dict) or not (answer.get('Id') or answer.get('Name')):
        return container()

    pid = str(answer.get('Id') or key_str)
    name = str(answer.get('Name') or lookup_name)
    if person_names is not None:
        person_names[pid] = name
    if person_ids is not None:
        person_ids[name] = pid

    image_tags = answer.get('ImageTags')
    primary_tag = image_tags.get('Primary') if isinstance(image_tags, dict) else None
    thumb = buildImageUrl('', pid, 'Primary', tag=primary_tag) if primary_tag else None
    locations = answer.get('ProductionLocations') or ()
    birthplace = str(locations[0]) if locations and locations[0] else None

    return container(
        [
            detail.element(
                'Directory',
                {
                    'type': 'person',
                    'id': pid,
                    'ratingKey': pid,
                    'tagKey': pid,
                    'tag': name,
                    'thumb': thumb,
                    'summary': answer.get('Overview'),
                    'birthDate': _isoDate(answer.get('PremiereDate')),
                    'deathDate': _isoDate(answer.get('EndDate')),
                    'birthPlace': birthplace,
                },
            )
        ]
    )


def media(client, person_key, args=None, offset=None, limit=None, person_ids=None):
    """Answer /library/people/<id>/media from GET /Items?personIds=<id>&recursive=true&includeItemTypes=Movie,Series."""
    key_str = unquote(str(person_key))
    person_id = person_ids.get(key_str, key_str) if person_ids is not None else key_str
    start, size = library.window(args or {}, offset, limit)

    base_params = {
        'personIds': person_id,
        'recursive': 'true',
        'includeItemTypes': 'Movie,Series',
        'fields': library.GRID_FIELDS,
        'enableUserData': 'true',
        'enableTotalRecordCount': 'true',
    }

    if size is not None:
        params = dict(base_params, startIndex=start, limit=size)
        answer = client.get('/Items', params=params) or {}
        items = answer.get('Items') or []
        total = int(answer.get('TotalRecordCount') if answer.get('TotalRecordCount') is not None else len(items))
        return container(itemElements(items), totalSize=total, offset=start)

    items = []
    position = start
    while len(items) < MOST:
        batch_limit = min(PAGE, MOST - len(items))
        params = dict(base_params, startIndex=position, limit=batch_limit)
        answer = client.get('/Items', params=params) or {}
        page = answer.get('Items') or []
        items.extend(page)
        position += len(page)
        total_raw = answer.get('TotalRecordCount')
        if not page:
            break
        if total_raw is not None and position >= int(total_raw):
            break
        if total_raw is None and len(page) < batch_limit:
            break

    elements = itemElements(items)
    return container(elements, totalSize=len(elements), offset=start)
