"""
Collections (Jellyfin BoxSets) and personal list listings (Favorites) backed by Jellyfin's /Items endpoint.
"""

from . import library
from .client import log
from .errors import JellyfinAuthError, JellyfinError
from .items import hubElement, itemElements

FAVORITES_HUB_ID = 'jellyfin.home.favorites'
FAVORITES_PATH = '/jellyfin/favorites'
FAVORITES_ROW_KEY = '/jellyfin/home/favorites'
FAVORITE_ITEM_TYPES = 'Movie,Series,Season,Episode'


def collectionsGrid(client, library_id=None, args=None, offset=None, limit=None, member_type='movie', section_id=None):
    """List Jellyfin BoxSet collections as Plex <Directory type="collection"> elements.
    Pass library_id for the dedicated Collections (boxsets) view, and for another library's Collections tab on
    Jellyfin 12, which answers a library's own BoxSets; omit it on 10.11, which keeps them all in that view."""
    base_params = {
        'includeItemTypes': 'BoxSet',
        'recursive': 'true',
    }
    if library_id:
        base_params['parentId'] = library_id
    return library.itemsListing(
        client,
        base_params,
        args or {},
        offset=offset,
        limit=limit,
        section_id=section_id or library_id,
        member_type=member_type or 'movie',
    )


def collectionItems(client, collection_id, args=None, offset=None, limit=None):
    """List the items inside one Jellyfin BoxSet collection (GET /Items?parentId={collection_id})."""
    return library.itemsListing(
        client,
        {'parentId': collection_id},
        args or {},
        offset=offset,
        limit=limit,
    )


def favorites(client, parent_id=None, args=None, offset=None, limit=None, include_item_types=None):
    """List the user's Jellyfin favorite items (GET /Items?isFavorite=true&recursive=true)."""
    base_params = {
        'isFavorite': 'true',
        'recursive': 'true',
    }
    if parent_id:
        base_params['parentId'] = parent_id
    if include_item_types:
        base_params['includeItemTypes'] = include_item_types
    return library.itemsListing(
        client,
        base_params,
        args or {},
        offset=offset,
        limit=limit,
    )


def favoritesRow(client, count=None, title='Favorites', on_item=None):
    """Home's Favorites row (decision D19): favorite movies/shows/seasons/episodes from /Items?isFavorite=true.
    Returns None when empty or on non-auth JellyfinError; re-raises JellyfinAuthError."""
    params = {
        'isFavorite': 'true',
        'recursive': 'true',
        'includeItemTypes': FAVORITE_ITEM_TYPES,
        'fields': library.GRID_FIELDS,
        'enableUserData': 'true',
        'enableTotalRecordCount': 'true',
        'limit': int(count) if count else 20,
    }
    try:
        answer = client.get('/Items', params=params) or {}
    except JellyfinAuthError:
        raise
    except JellyfinError as e:
        log(f'favorites row failed: {e.status}')
        return None
    if not isinstance(answer, dict):
        return None
    items = answer.get('Items') or []
    if on_item is not None:
        for dto in items:
            if isinstance(dto, dict):
                on_item(dto)
    elements = itemElements(items)
    if not elements:
        return None
    raw_total = answer.get('TotalRecordCount')
    total = int(raw_total) if raw_total is not None else len(elements)
    has_more = total > len(items)
    return hubElement(
        FAVORITES_HUB_ID,
        title or 'Favorites',
        elements,
        FAVORITES_ROW_KEY,
        total=max(total, len(elements)) if has_more else len(elements),
        more=has_more,
    )
