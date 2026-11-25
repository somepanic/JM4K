"""
Similar titles (GET /Items/{itemId}/Similar) and extras (GET /Items/{itemId}/SpecialFeatures).
Trailers are skipped for v1 (decision J-D10): /Items/{itemId}/LocalTrailers is never called and
any item whose ExtraType is Trailer is excluded.
"""

from urllib.parse import quote
from xml.etree import ElementTree

from . import detail, library
from .client import log
from .errors import JellyfinAuthError, JellyfinError
from .items import container, hubElement, itemElements, itemThumb, ticks_to_ms

MOST = 50
DEFAULT = 20

# Jellyfin ExtraType (normalized lowercase) -> Plex extraType (lib/metadata.py EXTRA_MAP).
# Trailer (1) is intentionally excluded per J-D10.
EXTRA_TYPES = {
    'deletedscene': 2,
    'deleted_scene': 2,
    'interview': 3,
    'behindthescenes': 5,
    'behind_the_scenes': 5,
    'scene': 6,
    'sample': 6,
    'featurette': 10,
    'short': 11,
}
OTHER_EXTRA = 12


def _parseLimit(count):
    if not count:
        return DEFAULT
    try:
        parsed = int(count)
    except (TypeError, ValueError):
        return DEFAULT
    if parsed <= 0:
        return DEFAULT
    return min(parsed, MOST)


def _extraKind(raw_extra_type):
    normalized = str(raw_extra_type or '').strip().lower().replace('-', '').replace(' ', '').replace('_', '')
    return EXTRA_TYPES.get(normalized, OTHER_EXTRA)


def _isTrailer(item):
    return str(item.get('ExtraType') or '').strip().lower() == 'trailer'


def similar(client, item_id, count=None, offset=None, limit=None):
    """Answer /library/metadata/<id>/similar from GET /Items/{itemId}/Similar."""
    want = _parseLimit(count)
    answer = (
        client.get(
            f'/Items/{quote(str(item_id), safe="")}/Similar',
            params={'limit': want, 'fields': library.GRID_FIELDS},
        )
        or {}
    )
    items = itemElements(answer.get('Items') or ())
    start = int(offset or 0)
    page = [] if limit == 0 else items[start : None if limit is None else start + int(limit)]
    return container(page, totalSize=len(items), offset=start)


def relatedHub(client, item_id, count=None):
    """Answer /library/metadata/<id>/related as a <MediaContainer> holding a <Hub> of similar items."""
    want = _parseLimit(count)
    answer = (
        client.get(
            f'/Items/{quote(str(item_id), safe="")}/Similar',
            params={'limit': want, 'fields': library.GRID_FIELDS},
        )
        or {}
    )
    items = itemElements(answer.get('Items') or ())
    if not items:
        return container()
    hub = hubElement(
        'jellyfin.similar',
        'Similar',
        items,
        f'/library/metadata/{item_id}/similar?count={want}',
        total=len(items),
        more=False,
    )
    return container([hub])


def extraElements(items):
    """Map Jellyfin SpecialFeatures BaseItemDto list to Plex clip <Video> elements, dropping trailers (J-D10)."""
    elements = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        item_id = item.get('Id')
        if not item_id or _isTrailer(item):
            continue
        item_id = str(item_id)
        duration_ms = ticks_to_ms(item.get('RunTimeTicks'))
        raw_extra = item.get('ExtraType')
        elem = detail.element(
            'Video',
            {
                'type': 'clip',
                'ratingKey': item_id,
                'key': f'/library/metadata/{item_id}',
                'title': item.get('Name') or '',
                'thumb': itemThumb(item),
                'duration': duration_ms or None,
                'extraType': _extraKind(raw_extra),
                'subtype': raw_extra,
            },
        )
        for source in item.get('MediaSources') or ():
            if isinstance(source, dict):
                elem.append(detail.mediaElement(item_id, source, item_duration_ms=duration_ms))
        elements.append(elem)
    return elements


def extrasElement(client, item_id):
    """Fetch GET /Items/{itemId}/SpecialFeatures and wrap as <Extras> for detail reload; None if empty/error."""
    try:
        answer = client.get(f'/Items/{quote(str(item_id), safe="")}/SpecialFeatures') or []
    except JellyfinAuthError:
        raise
    except JellyfinError as e:
        log(f'extras of {item_id} failed: {e.status}')
        return None
    elements = extraElements(answer if isinstance(answer, list) else [])
    if not elements:
        return None
    root = ElementTree.Element('Extras', {'size': str(len(elements))})
    root.extend(elements)
    return root


def extras(client, item_id):
    """Answer /library/metadata/<id>/extras from GET /Items/{itemId}/SpecialFeatures (NO trailers per J-D10)."""
    answer = client.get(f'/Items/{quote(str(item_id), safe="")}/SpecialFeatures') or []
    elements = extraElements(answer if isinstance(answer, list) else [])
    return container(elements)
