"""
Watched state and resume position changes on Jellyfin, translated from Plex Uno's paths:
- /:/scrobble?key={itemId} (Video.markWatched(), including Season and Show) -> POST /UserPlayedItems/{itemId}
- /:/unscrobble?key={itemId} (Video.markUnwatched()) -> DELETE /UserPlayedItems/{itemId}
- /actions/removeFromContinueWatching?ratingKey={itemId} (Video.removeFromContinueWatching()) ->
  POST /UserItems/{itemId}/UserData with {"PlaybackPositionTicks": 0}
- /:/progress?key={itemId}&time={ms}&state=stopped -> POST /UserItems/{itemId}/UserData with
  {"PlaybackPositionTicks": ms_to_ticks(ms)} (0 resets the resume point).
"""

from urllib.parse import quote

from .items import container, ms_to_ticks

ROUTES = frozenset(
    (
        '/:/scrobble',
        '/:/unscrobble',
        '/actions/removeFromContinueWatching',
        '/:/progress',
    )
)


def _item_id(args):
    raw = args.get('key') or args.get('ratingKey') or ''
    return str(raw).strip()


def _progress_ticks(args):
    raw = args.get('time')
    if raw is None or raw == '':
        return 0
    try:
        return ms_to_ticks(float(raw))
    except (TypeError, ValueError):
        return 0


def change(client, route, args):
    """Apply a Plex watched-state or progress-reset route against Jellyfin and return an empty container."""
    item_id = _item_id(args or {})
    if not item_id:
        return container()

    quoted = quote(item_id, safe='')
    if route == '/:/scrobble':
        client.post(f'/UserPlayedItems/{quoted}')
    elif route == '/:/unscrobble':
        client.delete(f'/UserPlayedItems/{quoted}')
    elif route == '/actions/removeFromContinueWatching':
        client.post(f'/UserItems/{quoted}/UserData', json={'PlaybackPositionTicks': 0})
    elif route == '/:/progress':
        client.post(f'/UserItems/{quoted}/UserData', json={'PlaybackPositionTicks': _progress_ticks(args)})
    return container()
