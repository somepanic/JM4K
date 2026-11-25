"""
Silo catalog items as the Plex elements plexnet builds tiles and screens from: movies and episodes become <Video>,
series and seasons <Directory>, with Silo's artwork paths (relative /api/v2/artwork/...), progress and played state.
Media Uno has no screens for (audiobooks, ebooks, podcasts) map to nothing. Shared by Home's rows (silonet.home) and
the library grid (silonet.server).
"""

import re
from datetime import datetime
from xml.etree import ElementTree


def container(children=(), **attrs):
    """A Plex <MediaContainer> of these elements, with any further attributes (totalSize, offset)."""
    root = ElementTree.Element('MediaContainer', {'size': str(len(children))})
    root.extend(children)
    for name, value in attrs.items():
        root.set(name, str(value))
    return root


def ms(seconds):
    return str(round(seconds * 1000))


def epoch(text):
    """Seconds since the epoch of an RFC 3339 time (Go writes up to nine fraction digits); None when unreadable."""
    match = re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$', text or '')
    if not match:
        return None
    zone = '+00:00' if match.group(3) == 'Z' else match.group(3)
    whole = datetime.fromisoformat(match.group(1) + zone).timestamp()
    return whole + float('0.' + match.group(2)) if match.group(2) else whole


def setIf(attrs, key, value):
    if value is not None and value != '':
        attrs[key] = str(value)


def itemElement(item):
    """A Silo catalog item as the Plex element its tile is built from; None for media Uno has no screens for."""
    kind = item.get('type')
    contentId = item.get('content_id')
    if kind not in ('movie', 'episode', 'series', 'season') or not contentId:
        return None
    state = item.get('user_state') or {}
    # a detail's, a season's or an episode's watch state (WatchRollup); a grid's is in user_state
    progress = item.get('user_data') or {}
    attrs = {'ratingKey': contentId, 'key': '/library/metadata/' + contentId, 'title': item.get('title') or ''}
    # what Plex Uno's watchlist code names an item by (its guid's last part: lib/windows/mixins/watchlist.py)
    attrs['guid'] = f'silo://{kind}/{contentId}'
    setIf(attrs, 'year', item.get('year'))
    setIf(attrs, 'thumb', item.get('poster_url'))
    setIf(attrs, 'art', item.get('backdrop_url'))
    setIf(
        attrs, 'originallyAvailableAt', item.get('release_date') or item.get('first_air_date') or item.get('air_date')
    )

    if kind in ('movie', 'episode'):
        attrs['type'] = kind
        duration = item.get('duration_seconds') or (item.get('runtime') or 0) * 60
        if duration:
            attrs['duration'] = ms(duration)
        position = item.get('position_seconds') or progress.get('position_seconds')
        if position:
            attrs['viewOffset'] = ms(position)
        if state.get('played') or progress.get('played'):
            attrs['viewCount'] = '1'
        if kind == 'episode':
            setIf(attrs, 'grandparentTitle', item.get('series_title'))
            setIf(attrs, 'grandparentThumb', item.get('series_poster_url'))
            setIf(attrs, 'grandparentRatingKey', item.get('series_id'))
            if item.get('series_id'):
                attrs['grandparentKey'] = '/library/metadata/' + item['series_id']
                if item.get('season_number') is not None:
                    # its season by number (silonet.shows.seasonByNumber()): Silo's home and grid items name no
                    # season id; a reloaded episode's detail gives the season's own key instead
                    attrs['parentKey'] = f'/library/metadata/{item["series_id"]}/season/{item["season_number"]}'
            setIf(attrs, 'parentIndex', item.get('season_number'))
            setIf(attrs, 'index', item.get('episode_number'))
        return ElementTree.Element('Video', attrs)

    # a show's and a season's children: its seasons, its episodes (silonet.shows)
    attrs['key'] += '/children'
    counts = progress or state
    watched = counts.get('watched_count') or 0
    attrs['leafCount'] = str(watched + (counts.get('unplayed_count') or 0))
    attrs['viewedLeafCount'] = str(watched)
    if kind == 'series':
        attrs['type'] = 'show'
    else:
        attrs['type'] = 'season'
        setIf(attrs, 'thumb', item.get('season_poster_url'))
        setIf(attrs, 'parentTitle', item.get('series_title'))
        setIf(attrs, 'parentThumb', item.get('series_poster_url'))
        setIf(attrs, 'parentRatingKey', item.get('series_id'))
        if item.get('series_id'):
            attrs['parentKey'] = '/library/metadata/' + item['series_id']
        setIf(attrs, 'index', item.get('season_number'))
    return ElementTree.Element('Directory', attrs)


def itemElements(items):
    return [e for e in (itemElement(i) for i in items or []) if e is not None]
