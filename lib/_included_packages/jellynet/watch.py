"""
Skip markers and chapters for Jellyfin playback (decision D16):
- GET /MediaSegments/{itemId} maps Jellyfin's five MediaSegmentType values (Intro, Outro, Recap,
  Preview, Commercial) into Plex <Marker> elements with 100-nanosecond ticks converted to
  milliseconds via items.ticks_to_ms(). Outro and Preview segments ending within FINAL_GAP_MS of
  the video's end are marked final="1" so skipping them advances or ends playback.
- Chapters reuse detail.chapterElements() (100ns StartPositionTicks -> ms, Chapter image route),
  reading cached item metadata with a GET /Items/{itemId}?fields=Chapters fallback when uncached.
"""

from urllib.parse import quote

from . import detail
from .client import log
from .detail import element
from .errors import JellyfinError
from .items import ticks_to_ms

SEGMENT_TYPES = {
    'intro': 'intro',
    'outro': 'credits',
    'recap': 'recap',
    'preview': 'preview',
    'commercial': 'commercial',
}
FINAL_KINDS = frozenset(('credits', 'preview'))
FINAL_GAP_MS = 5000  # 5 seconds, matching Plex / SM4K final-credits threshold


def markerElements(segments, duration_ms=0):
    """Map Jellyfin MediaSegmentDto items (or a MediaSegmentDtoQueryResult dict) to Plex <Marker> elements."""
    if isinstance(segments, dict):
        raw_items = segments.get('Items') or ()
    else:
        raw_items = segments or ()

    try:
        total_ms = max(0, int(duration_ms or 0))
    except (TypeError, ValueError):
        total_ms = 0

    parsed = []
    for seg in raw_items:
        if not isinstance(seg, dict):
            continue
        kind = SEGMENT_TYPES.get(str(seg.get('Type') or '').strip().lower())
        if kind is None:
            continue
        start_ticks = seg.get('StartTicks')
        end_ticks = seg.get('EndTicks')
        if start_ticks is None or end_ticks is None:
            continue
        try:
            start_ms = ticks_to_ms(start_ticks)
            end_ms = ticks_to_ms(end_ticks)
        except (TypeError, ValueError):
            continue
        if end_ms <= start_ms:
            continue
        final = kind in FINAL_KINDS and bool(total_ms) and end_ms >= total_ms - FINAL_GAP_MS
        parsed.append((start_ms, end_ms, kind, final))

    parsed.sort(key=lambda item: (item[0], item[1]))
    elements = []
    for idx, (start_ms, end_ms, kind, final) in enumerate(parsed, start=1):
        elements.append(
            element(
                'Marker',
                {
                    'id': idx,
                    'type': kind,
                    'startTimeOffset': start_ms,
                    'endTimeOffset': end_ms,
                    'final': '1' if final else '0',
                },
            )
        )
    return elements


def fetchSegments(client, item_id):
    """Fetch GET /MediaSegments/{itemId}; return Items list, or [] when the server fails or has none."""
    item_id = str(item_id)
    try:
        answer = client.get(f'/MediaSegments/{quote(item_id, safe="")}') or {}
    except JellyfinError as e:
        log(f'media segments of {item_id} failed: {e.status}')
        return []
    if isinstance(answer, dict):
        items = answer.get('Items')
        return list(items) if isinstance(items, list) else []
    return []


def chapterElements(item_id, chapters, duration_ms=0):
    """Build Plex <Chapter> elements from Jellyfin ChapterInfo entries."""
    return detail.chapterElements(item_id, chapters, item_duration_ms=duration_ms)


def fetchItemChapters(client, item_id):
    """Fallback GET /Items/{itemId}?fields=Chapters when chapters are not cached.
    Returns (dto_or_None, chapters_list, duration_ms)."""
    item_id = str(item_id)
    try:
        dto = client.get(f'/Items/{quote(item_id, safe="")}', params={'fields': 'Chapters'}) or {}
    except JellyfinError as e:
        log(f'chapters of {item_id} failed: {e.status}')
        return None, [], 0
    if not isinstance(dto, dict):
        return None, [], 0
    chapters = dto.get('Chapters')
    return dto, list(chapters) if isinstance(chapters, list) else [], ticks_to_ms(dto.get('RunTimeTicks'))
