"""
A title's Related row from Silo: getRelated() (plexnet's RelatedMixin) asks /library/metadata/<id>/similar with the
most the row shows (count); Silo answers GET /api/v2/recommendations/similar/{id} with at most 50 cards and no total,
so totalSize is what came back and a page (offset, limit) is cut from it.
"""

from .items import container, itemElements

MOST = 50  # the most similar cards Silo answers
DEFAULT = 20  # Silo's own default


def similar(client, contentId, count=None, offset=None, limit=None):
    try:
        want = min(int(count), MOST) if count else DEFAULT
    except ValueError:
        want = DEFAULT
    answer = client.request('GET', f'/api/v2/recommendations/similar/{contentId}', params={'limit': want}) or {}
    items = itemElements(answer.get('items'))
    start = offset or 0
    page = [] if limit == 0 else items[start : None if limit is None else start + limit]
    return container(page, totalSize=len(items))
