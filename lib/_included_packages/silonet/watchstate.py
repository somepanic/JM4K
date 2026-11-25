"""
Watched state on Silo, from Plex's paths: /:/scrobble and /:/unscrobble (Mark as played and unplayed: plexnet
Video.markWatched(), markUnwatched()) are POST and DELETE /api/v2/watched/{id} - a season or a show marks every
episode - and /actions/removeFromContinueWatching clears the item's resume position, DELETE
/api/v2/progress/{id}/position, keeping its watched state and history.
"""

from .items import container

# Plex's path -> (method, Silo path) for the item named by the path's key or ratingKey
ROUTES = {
    '/:/scrobble': ('POST', '/api/v2/watched/{0}'),
    '/:/unscrobble': ('DELETE', '/api/v2/watched/{0}'),
    '/actions/removeFromContinueWatching': ('DELETE', '/api/v2/progress/{0}/position'),
}


def change(client, route, args):
    contentId = args.get('key') or args.get('ratingKey')
    if contentId:
        method, path = ROUTES[route]
        client.request(method, path.format(contentId))
    return container()
