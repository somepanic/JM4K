"""
Collections (Jellyfin BoxSets) and personal list listings (Favorites) backed by Jellyfin's /Items endpoint.
"""

from . import library


def collectionsGrid(client, library_id=None, args=None, offset=None, limit=None, member_type='movie', section_id=None):
    """List Jellyfin BoxSet collections as Plex <Directory type="collection"> elements.
    Pass library_id when querying the dedicated Collections (boxsets) view; omit it for another
    library's Collections tab because Jellyfin stores BoxSets under the global Collections view."""
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


def showLeaves(client, show_id, args=None, offset=None, limit=None):
    """List every episode of a Jellyfin Series (for expanding shows inside a collection's Play All queue)."""
    return library.itemsListing(
        client,
        {
            'parentId': show_id,
            'includeItemTypes': 'Episode',
            'recursive': 'true',
        },
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
