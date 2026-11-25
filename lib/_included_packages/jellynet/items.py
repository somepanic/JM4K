"""
Canonical Jellyfin BaseItemDto -> Plex XML element mapper shared across jellynet modules
(library, home, detail, shows, lists, search).

Jellyfin expresses durations and playback positions in 100-nanosecond ticks (10,000 ticks/ms;
10,000,000 ticks/s). Image URLs point to Jellyfin's public /Items/{itemId}/Images/{imageType}
route and never carry an auth token.
"""

import re
from datetime import datetime
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

TICKS_PER_MS = 10_000
TICKS_PER_SECOND = 10_000_000

_ISO_RE = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$')

# The rating icons Uno draws for a Jellyfin score, as Plex names them: TMDb for CommunityRating
AUDIENCE_RATING_IMAGE = 'themoviedb://image.rating'


def criticRatingImage(score):
    """Rotten Tomatoes' ripe or rotten tomato for a 0-100 CriticRating."""
    return 'rottentomatoes://image.rating.ripe' if score >= 60 else 'rottentomatoes://image.rating.rotten'


# Jellyfin BaseItemKind -> (Plex XML tag, Plex type attribute)
ITEM_TYPES = {
    'Movie': ('Video', 'movie'),
    'Episode': ('Video', 'episode'),
    'Series': ('Directory', 'show'),
    'Season': ('Directory', 'season'),
    'BoxSet': ('Directory', 'collection'),
}


def ticks_to_ms(ticks):
    """Convert Jellyfin 100ns ticks to Plex milliseconds; 0 for missing or non-positive values."""
    if not ticks:
        return 0
    value = int(ticks)
    return value // TICKS_PER_MS if value > 0 else 0


def ms_to_ticks(ms):
    """Convert Plex milliseconds to Jellyfin 100ns ticks; 0 for missing or non-positive values."""
    if not ms:
        return 0
    if isinstance(ms, int):
        return ms * TICKS_PER_MS if ms > 0 else 0
    value = float(ms)
    return round(value * TICKS_PER_MS) if value > 0 else 0


def epoch(text):
    """Parse an ISO-8601/RFC-3339 timestamp into Unix epoch seconds (int), or None if unreadable."""
    match = _ISO_RE.match(text or '')
    if not match:
        return None
    zone = '+00:00' if match.group(3) == 'Z' else match.group(3)
    try:
        return int(datetime.fromisoformat(match.group(1) + zone).timestamp())
    except ValueError:
        return None


def setIf(attrs, key, value):
    """Set attrs[key] = str(value) when value is neither None nor empty string (preserves 0)."""
    if value is not None and value != '':
        attrs[key] = str(value)


def buildImageUrl(base_url, item_id, image_type='Primary', tag=None, max_width=None, max_height=None):
    """Build a public Jellyfin /Items/{itemId}/Images/{imageType} URL; never includes an auth token."""
    params = {}
    if max_width and int(max_width) > 0:
        params['maxWidth'] = str(int(max_width))
    if max_height and int(max_height) > 0:
        params['maxHeight'] = str(int(max_height))
    if tag:
        params['tag'] = str(tag)
    route = f'/Items/{quote(str(item_id), safe="")}/Images/{quote(str(image_type), safe="")}'
    base = base_url.rstrip('/') if base_url else ''
    query = urlencode(params)
    return f'{base}{route}?{query}' if query else f'{base}{route}'


def container(children=(), **attrs):
    """Build a Plex <MediaContainer size="..."> element with optional extra attributes (totalSize, offset)."""
    root = ElementTree.Element('MediaContainer', {'size': str(len(children))})
    root.extend(children)
    for name, value in attrs.items():
        root.set(name, str(value))
    return root


def itemThumb(item):
    """Relative Primary image path for a BaseItemDto, or '' when the item has no Primary tag."""
    item_id = item.get('Id')
    image_tags = item.get('ImageTags')
    if not item_id or not isinstance(image_tags, dict):
        return ''
    tag = image_tags.get('Primary')
    return buildImageUrl('', item_id, 'Primary', tag=tag) if tag else ''


def itemArt(item):
    """Relative Backdrop image path for a BaseItemDto (falling back to parent backdrop), or ''."""
    item_id = item.get('Id')
    backdrops = item.get('BackdropImageTags') or []
    if item_id and backdrops:
        return buildImageUrl('', item_id, 'Backdrop', tag=backdrops[0])
    parent_id = item.get('ParentBackdropItemId')
    parent_backdrops = item.get('ParentBackdropImageTags') or []
    if parent_id and parent_backdrops:
        return buildImageUrl('', parent_id, 'Backdrop', tag=parent_backdrops[0])
    return ''


def _leafCounts(item, user_data, is_season=False):
    """Compute (leafCount, viewedLeafCount) strings for Series and Season items, or (None, None)."""
    if is_season:
        total = item.get('ChildCount')
        if total is None:
            total = item.get('RecursiveItemCount')
    else:
        total = item.get('RecursiveItemCount')
        if total is None:
            total = item.get('EpisodeCount')
        if total is None:
            total = item.get('ChildCount')

    unplayed = user_data.get('UnplayedItemCount')
    played = bool(user_data.get('Played'))

    if total is not None:
        total_int = max(0, int(total))
        if unplayed is not None:
            unplayed_int = max(0, int(unplayed))
            leaf_int = max(total_int, unplayed_int)
            viewed_int = max(0, leaf_int - unplayed_int)
            return str(leaf_int), str(viewed_int)
        return str(total_int), str(total_int if played else 0)
    if unplayed is not None:
        return str(max(0, int(unplayed))), '0'
    if played:
        return '1', '1'
    return None, None


def _genreNames(item):
    genres = item.get('Genres') or []
    if genres:
        return [str(g) for g in genres if g]
    genre_items = item.get('GenreItems') or []
    names = []
    for entry in genre_items:
        if isinstance(entry, dict) and entry.get('Name'):
            names.append(str(entry['Name']))
    return names


def itemElement(item, section_id=None, member_type=None, section_title=None, section_type=None):
    """Convert one Jellyfin BaseItemDto dict into its Plex <Video> or <Directory> element, or None if unsupported."""
    if not isinstance(item, dict):
        return None
    kind = item.get('Type')
    item_id = item.get('Id')
    mapping = ITEM_TYPES.get(kind)
    if mapping is None or not item_id:
        return None

    tag, plex_type = mapping
    item_id = str(item_id)
    user_data = item.get('UserData')
    if not isinstance(user_data, dict):
        user_data = {}

    is_container = tag == 'Directory'
    attrs = {
        'type': plex_type,
        'ratingKey': item_id,
        'key': f'/library/metadata/{item_id}/children' if is_container else f'/library/metadata/{item_id}',
        'title': item.get('Name') or '',
        'guid': f'jellyfin://{plex_type}/{item_id}',
    }

    setIf(attrs, 'originalTitle', item.get('OriginalTitle'))
    sort_name = item.get('SortName') or item.get('ForcedSortName')
    setIf(attrs, 'titleSort', sort_name)
    setIf(attrs, 'sortTitle', sort_name)
    setIf(attrs, 'year', item.get('ProductionYear'))

    premiere = item.get('PremiereDate') or ''
    if len(premiere) >= 10:
        attrs['originallyAvailableAt'] = premiere[:10]

    setIf(attrs, 'addedAt', epoch(item.get('DateCreated')))
    setIf(attrs, 'lastViewedAt', epoch(user_data.get('LastPlayedDate')))
    setIf(attrs, 'summary', item.get('Overview'))
    setIf(attrs, 'contentRating', item.get('OfficialRating'))
    # Plex's rating is the critic score (0-10), its audienceRating the audience one
    critic = item.get('CriticRating')
    if critic is not None:
        attrs['rating'] = f'{float(critic) / 10:.1f}'
        attrs['ratingImage'] = criticRatingImage(float(critic))
    if item.get('CommunityRating') is not None:
        attrs['audienceRating'] = str(item['CommunityRating'])
        attrs['audienceRatingImage'] = AUDIENCE_RATING_IMAGE
    setIf(attrs, 'thumb', itemThumb(item))
    setIf(attrs, 'art', itemArt(item))
    setIf(attrs, 'librarySectionID', section_id)
    setIf(attrs, 'librarySectionTitle', section_title)
    setIf(attrs, 'librarySectionType', section_type)

    if kind in ('Movie', 'Episode'):
        duration_ms = ticks_to_ms(item.get('RunTimeTicks'))
        if duration_ms:
            attrs['duration'] = str(duration_ms)
        offset_ms = ticks_to_ms(user_data.get('PlaybackPositionTicks'))
        if offset_ms:
            attrs['viewOffset'] = str(offset_ms)
        played = user_data.get('Played')
        play_count = user_data.get('PlayCount')
        if played is True:
            attrs['viewCount'] = str(max(1, int(play_count or 0)))
        elif played is None and play_count and int(play_count) > 0:
            attrs['viewCount'] = str(int(play_count))

        if kind == 'Episode':
            setIf(attrs, 'parentIndex', item.get('ParentIndexNumber'))
            setIf(attrs, 'index', item.get('IndexNumber'))
            series_id = item.get('SeriesId')
            if series_id:
                series_id = str(series_id)
                attrs['grandparentRatingKey'] = series_id
                attrs['grandparentKey'] = f'/library/metadata/{series_id}'
                setIf(attrs, 'grandparentTitle', item.get('SeriesName'))
                if item.get('SeriesPrimaryImageTag'):
                    attrs['grandparentThumb'] = buildImageUrl(
                        '', series_id, 'Primary', tag=item['SeriesPrimaryImageTag']
                    )
            season_id = item.get('SeasonId')
            if season_id:
                season_id = str(season_id)
                attrs['parentRatingKey'] = season_id
                attrs['parentKey'] = f'/library/metadata/{season_id}'
            setIf(attrs, 'parentTitle', item.get('SeasonName'))
            parent_img_id = item.get('ParentPrimaryImageItemId')
            parent_img_tag = item.get('ParentPrimaryImageTag')
            if parent_img_id and parent_img_tag:
                attrs['parentThumb'] = buildImageUrl('', str(parent_img_id), 'Primary', tag=parent_img_tag)

    elif kind in ('Series', 'Season'):
        leaf_count, viewed_count = _leafCounts(item, user_data, is_season=(kind == 'Season'))
        setIf(attrs, 'leafCount', leaf_count)
        setIf(attrs, 'viewedLeafCount', viewed_count)
        setIf(attrs, 'childCount', item.get('ChildCount'))
        if kind == 'Season':
            setIf(attrs, 'index', item.get('IndexNumber'))
            series_id = item.get('SeriesId')
            if series_id:
                series_id = str(series_id)
                attrs['parentRatingKey'] = series_id
                attrs['parentKey'] = f'/library/metadata/{series_id}'
                setIf(attrs, 'parentTitle', item.get('SeriesName'))
                if item.get('SeriesPrimaryImageTag'):
                    attrs['parentThumb'] = buildImageUrl('', series_id, 'Primary', tag=item['SeriesPrimaryImageTag'])

    elif kind == 'BoxSet':
        child_count = item.get('ChildCount')
        if child_count is None:
            child_count = item.get('RecursiveItemCount')
        attrs['childCount'] = str(int(child_count)) if child_count is not None else '0'
        attrs['subtype'] = member_type or 'movie'

    elem = ElementTree.Element(tag, attrs)
    for genre in _genreNames(item):
        ElementTree.SubElement(elem, 'Genre', {'tag': genre})
    return elem


def itemElements(items, section_id=None, member_type=None, section_title=None, section_type=None):
    """Map a sequence of BaseItemDto dicts to Plex elements, dropping unsupported kinds."""
    elements = []
    for item in items or ():
        elem = itemElement(
            item,
            section_id=section_id,
            member_type=member_type,
            section_title=section_title,
            section_type=section_type,
        )
        if elem is not None:
            elements.append(elem)
    return elements


def hubElement(hub_id, title, elements, key, total=None, more=False):
    """Build a Plex <Hub> element from already-mapped child elements."""
    kinds = {e.get('type') for e in elements if e.get('type')}
    hub = ElementTree.Element(
        'Hub',
        {
            'hubIdentifier': str(hub_id),
            'title': title or '',
            'type': kinds.pop() if len(kinds) == 1 else 'mixed',
            'size': str(len(elements)),
            'totalSize': str(total if total is not None else len(elements)),
            'more': '1' if more else '0',
            'key': key,
        },
    )
    hub.extend(elements)
    return hub
