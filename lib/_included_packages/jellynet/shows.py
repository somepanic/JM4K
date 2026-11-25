"""
Series seasons and episodes from Jellyfin's /Shows/{seriesId}/Seasons and /Shows/{seriesId}/Episodes endpoints:
- Show.seasons() -> /library/metadata/<seriesId>/children
- Season.episodes() -> /library/metadata/<seasonId>/children
- Show.episodes() -> /library/metadata/<seriesId>/allLeaves
- Smart /library/metadata/<id>/children dispatch across Series, Season, and BoxSet collections.
"""

from urllib.parse import quote

from . import detail, library, lists
from .items import container, itemElement

SEASON_FIELDS = 'Overview,PrimaryImageAspectRatio,DateCreated,ChildCount,RecursiveItemCount'
EPISODE_FIELDS = 'Overview,MediaSources,MediaStreams,Chapters,Trickplay,DateCreated,Path'


def seasons(client, series_id, on_item=None):
    """Fetch a series' seasons from GET /Shows/{seriesId}/Seasons as Plex <Directory type="season"> elements."""
    series_id = str(series_id)
    answer = (
        client.get(
            f'/Shows/{quote(series_id, safe="")}/Seasons',
            params={
                'fields': SEASON_FIELDS,
                'enableUserData': 'true',
            },
        )
        or {}
    )
    items = answer.get('Items') or []
    total = int(answer.get('TotalRecordCount') if answer.get('TotalRecordCount') is not None else len(items))
    elements = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault('Type', 'Season')
        item.setdefault('SeriesId', series_id)
        if on_item is not None:
            on_item(item)
        elem = itemElement(item)
        if elem is not None:
            elements.append(elem)
    return container(elements, totalSize=total)


def episodes(client, series_id, season_id=None, args=None, offset=None, limit=None, on_item=None, track_prefs=None):
    """Fetch episodes from GET /Shows/{seriesId}/Episodes (optionally filtered to one seasonId)."""
    series_id = str(series_id)
    start, size = library.window(args or {}, offset, limit)
    params = {
        'fields': EPISODE_FIELDS,
        'enableUserData': 'true',
        'enableTotalRecordCount': 'true',
    }
    if season_id:
        params['seasonId'] = str(season_id)

    if size == 0:
        params['limit'] = 0
        answer = client.get(f'/Shows/{quote(series_id, safe="")}/Episodes', params=params) or {}
        total = int(answer.get('TotalRecordCount') or 0)
        return container(totalSize=total, offset=start)

    if start:
        params['startIndex'] = start
    elif size is not None:
        params['startIndex'] = 0
    if size is not None:
        params['limit'] = size

    answer = client.get(f'/Shows/{quote(series_id, safe="")}/Episodes', params=params) or {}
    items = answer.get('Items') or []
    total = int(answer.get('TotalRecordCount') if answer.get('TotalRecordCount') is not None else len(items))
    elements = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault('Type', 'Episode')
        item.setdefault('SeriesId', series_id)
        if season_id:
            item.setdefault('SeasonId', str(season_id))
        elem = detail.detailElement(item, on_item=on_item, track_prefs=track_prefs)
        if elem is not None:
            elements.append(elem)
    return container(elements, totalSize=total, offset=start)


def allEpisodes(client, series_id, args=None, offset=None, limit=None, on_item=None, track_prefs=None):
    """Answer /library/metadata/<seriesId>/allLeaves via GET /Shows/{seriesId}/Episodes."""
    return episodes(
        client,
        series_id,
        season_id=None,
        args=args,
        offset=offset,
        limit=limit,
        on_item=on_item,
        track_prefs=track_prefs,
    )


def children(
    client,
    item_id,
    item_types=None,
    season_series=None,
    args=None,
    offset=None,
    limit=None,
    on_item=None,
    track_prefs=None,
):
    """Answer /library/metadata/<id>/children for Series (seasons), Season (episodes), or BoxSet (collection items).
    Uses cached item types and season->series links when known; fetches GET /Items/{id} only when unknown."""
    item_id = str(item_id)
    kind = item_types.get(item_id) if item_types is not None else None
    series_id = season_series.get(item_id) if season_series is not None else None

    if kind is None or (kind == 'Season' and not series_id):
        dto = client.get(f'/Items/{quote(item_id, safe="")}') or {}
        if isinstance(dto, dict):
            if on_item is not None:
                on_item(dto)
            kind = dto.get('Type')
            if dto.get('SeriesId'):
                series_id = str(dto['SeriesId'])

    if kind == 'Series':
        return seasons(client, item_id, on_item=on_item)
    if kind == 'Season':
        if not series_id:
            return container()
        return episodes(
            client,
            series_id,
            season_id=item_id,
            args=args,
            offset=offset,
            limit=limit,
            on_item=on_item,
            track_prefs=track_prefs,
        )
    if kind == 'BoxSet':
        return lists.collectionItems(client, item_id, args=args, offset=offset, limit=limit)
    return container()
