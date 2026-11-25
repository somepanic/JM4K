"""
Silo's personal lists - the watchlist (decision D18) and favorites (D19): GET /api/v2/<list> (catalog cards, newest
first, every page), GET /api/v2/<list>/{id}/membership, and PUT and DELETE /api/v2/<list>/{id}. The watchlist answers
Plex Uno's watchlist paths (plex.tv Discover's, which SM4K has none of): the Watchlist screen's items, an item's
userState and the add and remove actions.
"""

from .client import log
from .detail import element
from .errors import SiloError
from .home import hubElement
from .items import container, itemElements

WATCHLIST, FAVORITES = 'watchlist', 'favorites'
# the Watchlist screen's section key (plexlibrary.WatchlistSection), which it asks hubs() for
WATCHLIST_SECTION = '/library/sections/watchlist'
PAGE = 200  # the most a list answers at once
MOST = 1000  # items asked for at most
# Plex's watchlist actions (server.query(path, ratingKey=...)) -> whether they add
ACTIONS = {'/actions/addToWatchlist': True, '/actions/removeFromWatchlist': False}


def cards(client, name):
    """Every item on the list (up to MOST) as Silo's catalog cards, newest first."""
    found, cursor = [], None
    while len(found) < MOST:
        params = {'limit': PAGE}
        if cursor:
            params['cursor'] = cursor
        answer = client.request('GET', f'/api/v2/{name}', params=params) or {}
        found.extend(answer.get('items') or [])
        page = answer.get('page') or {}
        cursor = page.get('next_cursor')
        if not page.get('has_more') or not cursor:
            break
    return found


def allItems(client, name):
    """Every item on the list (up to MOST) as Plex elements, newest first."""
    return itemElements(cards(client, name))


def items(client, name, offset=None, limit=None):
    """The list's items as Plex elements; a page (offset, limit) is cut from them and totalSize says how many."""
    elements = allItems(client, name)
    start = offset or 0
    shown = [] if limit == 0 else elements[start : None if limit is None else start + limit]
    return container(shown, totalSize=len(elements))


def watchlistRows(client, count, titles):
    """The Watchlist screen's rows (its section's hubs; Discover's were its own rows): a row of each kind of title on
    it, in titles' order ({card type: row title}), count items each; See more (rowItems()) lists the rest."""
    found = cards(client, WATCHLIST)
    rows = []
    for kind, title in titles.items():
        items = [c for c in found if c.get('type') == kind]
        if not items:
            continue
        shown = items[:count] if count else items
        section = {
            'id': f'{WATCHLIST}.{kind}',
            'section_type': WATCHLIST,
            'title': title,
            'items': shown,
            'has_more': len(items) > len(shown),
            'total_count': len(items),
        }
        rows.append(hubElement(section))
    return rows


def rowItems(client, rowId):
    """A list row's whole list (SiloServer._row()): Home's Favorites, or a Watchlist row's kind of title; None for
    another row."""
    if rowId == FAVORITES:
        return allItems(client, FAVORITES)
    if rowId.startswith(WATCHLIST + '.'):
        kind = rowId[len(WATCHLIST) + 1 :]
        return itemElements([c for c in cards(client, WATCHLIST) if c.get('type') == kind])
    return None


def isMember(client, name, contentId):
    """Whether the item is on the list; None when Silo couldn't say."""
    try:
        answer = client.request('GET', f'/api/v2/{name}/{contentId}/membership') or {}
    except SiloError as e:
        log(f'{name} membership of {contentId} failed: {e.status} {e.type}')
        return None
    return bool(answer.get('is_member'))


def userState(client, contentId):
    """/library/metadata/<id>/userState, as Discover answers it: a UserState with watchlistedAt while on the list."""
    member = isMember(client, WATCHLIST, contentId)
    if member is None:
        return None
    attrs = {'watchlistedAt': '1'} if member else {}
    return container([element('UserState', attrs)])


def change(client, name, contentId, add):
    client.request('PUT' if add else 'DELETE', f'/api/v2/{name}/{contentId}')
    return container()


def favoritesRow(client, count, title):
    """Home's Favorites row (decision D19): the newest count favorites, its See more the rest (SiloServer._row());
    None without any, or when Silo can't say."""
    try:
        answer = client.request('GET', f'/api/v2/{FAVORITES}', params={'limit': count or 20}) or {}
    except SiloError as e:
        log(f'favorites row failed: {e.status} {e.type}')
        return None
    if not answer.get('items'):
        return None
    section = {
        'id': FAVORITES,
        'section_type': FAVORITES,
        'title': title,
        'items': answer['items'],
        'has_more': (answer.get('page') or {}).get('has_more'),
    }
    return hubElement(section)
