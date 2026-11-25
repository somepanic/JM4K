"""
A person from Silo, as Uno's person screen asks for them: their details (Role.getDetails(): /library/people/<id>)
from GET /api/v2/catalog/people/{id}, and their titles in the libraries (lib/windows/person.py libraryFilmography():
/library/people/<id>/media) from the catalog's person source, GET /api/v2/catalog?source=person&person_id=<id>, every
page. The id is Silo's person id, which each cast and crew member carries as its id and tagKey (silonet.detail).
"""

from .detail import element
from .items import container, itemElements

PAGE = 200  # the most /api/v2/catalog answers at once
MOST = 1000  # titles asked for at most


def person(client, personId):
    """/library/people/<id>: the person as Plex's <Directory> (tag, thumb, summary, birthDate, deathDate, birthPlace)."""
    answer = client.request('GET', f'/api/v2/catalog/people/{personId}') or {}
    if not answer.get('id'):
        return container()
    return container(
        [
            element(
                'Directory',
                {
                    'type': 'person',
                    'id': answer['id'],
                    'ratingKey': answer['id'],
                    'tagKey': answer['id'],
                    'tag': answer.get('name'),
                    'thumb': answer.get('photo_url'),
                    'summary': answer.get('bio'),
                    'birthDate': answer.get('birth_date'),
                    'deathDate': answer.get('death_date'),
                    'birthPlace': answer.get('birthplace'),
                },
            )
        ]
    )


def media(client, personId):
    """/library/people/<id>/media: the person's movies and shows in the libraries, every page up to MOST."""
    items, position = [], 0
    while position < MOST:
        answer = (
            client.request(
                'GET',
                '/api/v2/catalog',
                params={'source': 'person', 'person_id': personId, 'limit': PAGE, 'seek': position},
            )
            or {}
        )
        page = answer.get('items') or []
        items.extend(page)
        position += len(page)
        if not page or not (answer.get('page') or {}).get('has_more'):
            break
    elements = itemElements(items)
    return container(elements, totalSize=len(elements))
