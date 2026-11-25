"""
A show's seasons and a season's episodes from Silo, as the Plex children plexnet lists: Show.seasons() and
Season.episodes() (their key, /library/metadata/<id>/children) and Show.episodes() (/library/metadata/<id>/allLeaves).
Silo answers a series' seasons from /catalog/series/{id}/seasons and a season's episodes from
/catalog/items/{season id}/episodes; the item's own detail says which of the two it is and names the show.
"""

from .detail import fileMediaElement, setSeason
from .items import container, itemElement


def item(client, contentId):
    return client.request('GET', f'/api/v2/catalog/items/{contentId}') or {}


def seasons(client, seriesId):
    return (client.request('GET', f'/api/v2/catalog/series/{seriesId}/seasons') or {}).get('items') or []


def episodes(client, seasonId):
    return (client.request('GET', f'/api/v2/catalog/items/{seasonId}/episodes') or {}).get('items') or []


def showOf(detail):
    """The show a series' or a season's detail names, as an episode's grandparent."""
    if detail.get('type') == 'series':
        return {
            'series_id': detail.get('content_id'),
            'series_title': detail.get('title'),
            'series_poster_url': detail.get('poster_url'),
            'backdrop_url': detail.get('backdrop_url'),
        }
    return {
        'series_id': detail.get('series_id'),
        'series_title': detail.get('series_title'),
        'backdrop_url': detail.get('backdrop_url'),
    }


def seasonElements(series, rows):
    """A series' seasons (SeasonCollection's items) as Plex season <Directory>s."""
    show = showOf(series)
    elements = []
    for row in rows:
        element = itemElement(dict(row, type='season', **show))
        if element is not None:
            if row.get('overview'):
                element.set('summary', row['overview'])
            if show.get('series_poster_url'):
                element.set('parentThumb', show['series_poster_url'])
            elements.append(element)
    return elements


def episodeElements(show, season, rows):
    """A season's episodes (EpisodeCollection's items) as Plex episode <Video>s, each with its season and show, its
    still as its thumb and its files as Media."""
    elements = []
    for row in rows:
        element = itemElement(dict(row, type='episode', poster_url=row.get('still_url'), **show))
        if element is None:
            continue
        setSeason(element, season)
        if row.get('overview'):
            element.set('summary', row['overview'])
        element.extend(fileMediaElement(file) for file in row.get('files') or [])
        elements.append(element)
    return elements


def children(client, contentId):
    """/library/metadata/<id>/children: a series' seasons or a season's episodes; nothing for other items."""
    detail = item(client, contentId)
    if detail.get('type') == 'series':
        return container(seasonElements(detail, seasons(client, contentId)))
    if detail.get('type') == 'season':
        return container(episodeElements(showOf(detail), detail, episodes(client, contentId)))
    return container()


def allLeaves(client, contentId, offset=None, limit=None):
    """/library/metadata/<series id>/allLeaves: every episode of a show, season by season; a page (offset, limit) is
    cut from them and totalSize says how many there are."""
    detail = item(client, contentId)
    if detail.get('type') != 'series':
        return container()
    show = showOf(detail)
    leaves = []
    for season in seasons(client, contentId):
        if season.get('content_id'):
            leaves.extend(episodeElements(show, season, episodes(client, season['content_id'])))
    start = offset or 0
    page = [] if limit == 0 else leaves[start : None if limit is None else start + limit]
    return container(page, totalSize=len(leaves))


def seasonByNumber(client, seriesId, number):
    """/library/metadata/<series id>/season/<num>: an episode tile's season (silonet.items), from
    /catalog/series/{id}/seasons/{num}."""
    row = client.request('GET', f'/api/v2/catalog/series/{seriesId}/seasons/{number}') or {}
    if not row.get('content_id'):
        return container()
    return container(seasonElements({'type': 'series', 'content_id': seriesId}, [row]))
