"""
What the player shows of the file it plays, from GET /api/v2/watch/{id} (WatchDetail): the file's skip markers -
intro, recap, credits and preview - and its chapters, as the Plex <Marker> and <Chapter> elements Plex Uno's player
reads (decision D16). Offsets are milliseconds; a credits or preview marker that runs to within FINAL_GAP of the end
is the final one, whose skip ends the video.
"""

from .detail import element
from .items import ms

MARKER_KINDS = ('intro', 'recap', 'credits', 'preview')
FINAL_KINDS = ('credits', 'preview')
FINAL_GAP = 5  # seconds


def playedFile(answer, fileId):
    """The version of a WatchDetail answer with the file id, else its first; {} without one."""
    versions = answer.get('versions') or []
    return next((v for v in versions if str(v.get('file_id')) == str(fileId)), versions[0] if versions else {})


def markerElements(answer, fileId):
    version = playedFile(answer, fileId)
    duration = version.get('duration_seconds') or 0
    elements = []
    for n, kind in enumerate(MARKER_KINDS):
        marker = version.get(kind) or answer.get(kind)
        if not marker or marker.get('end_seconds') is None or marker.get('start_seconds') is None:
            continue
        final = kind in FINAL_KINDS and bool(duration) and marker['end_seconds'] >= duration - FINAL_GAP
        elements.append(
            element(
                'Marker',
                {
                    'id': n + 1,
                    'type': kind,
                    'startTimeOffset': ms(marker['start_seconds']),
                    'endTimeOffset': ms(marker['end_seconds']),
                    'final': '1' if final else '0',
                },
            )
        )
    return elements


def chapterElements(answer, fileId):
    return [
        element(
            'Chapter',
            {
                'id': chapter.get('index'),
                'index': chapter.get('index'),
                'tag': chapter.get('title'),
                'startTimeOffset': ms(chapter.get('start_seconds') or 0),
                'endTimeOffset': ms(chapter.get('end_seconds') or 0),
                'thumb': chapter.get('thumbnail_url'),
            },
        )
        for chapter in playedFile(answer, fileId).get('chapters') or []
    ]


def hasPreviews(answer, fileId):
    """Whether the played file has seek-bar previews (silonet.trickplay)."""
    return bool(playedFile(answer, fileId).get('trickplay_available'))
