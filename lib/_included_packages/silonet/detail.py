"""
An item's detail, GET /api/v2/catalog/items/{id}, as the full Plex element plexnet's reload() reads
(/library/metadata/<ratingKey>): the tile's attributes (silonet.items) plus what the pre-play, show and episode screens
show - summary, tagline, content rating, studio, genres, countries, cast and crew, ratings (as <Rating> with the
image keys Uno has icons for), and each version as Media > Part > Stream. An episode's season comes from
/catalog/series/{id}/seasons/{num} (its detail names only the number), and a show's on-deck episode, when asked
(includeOnDeck), from its play_content_id. Stream ids for choosing tracks are Phase 5's.
"""

from xml.etree import ElementTree

from .client import log
from .errors import SiloError
from .items import container, epoch, itemElement, ms

# Silo's resolution to Plex's videoResolution, what Uno's resolution badges read
RESOLUTIONS = {'2160p': '4k', '4k': '4k', '1440p': '1080', '1080p': '1080', '1080i': '1080', '720p': '720'}
# crew jobs Uno shows as writers and producers (it has no other crew rows)
WRITER_JOBS = frozenset(('Writer', 'Screenplay', 'Story', 'Teleplay', 'Novel', 'Characters', 'Author'))
PRODUCER_JOBS = frozenset(('Producer',))
# Silo's extra kinds (a free string in ItemExtraInfo) to Plex's extraType, what the extras row labels them by
# (lib/metadata.py's EXTRA_MAP); any other kind is Other (12)
EXTRA_TYPES = {
    'trailer': 1,
    'deleted_scene': 2,
    'interview': 3,
    'behind_the_scenes': 5,
    'scene': 6,
    'sample': 6,
    'featurette': 10,
    'short': 11,
}
OTHER_EXTRA = 12


def element(tag, attrs):
    """An element with the attributes that have a value, as strings."""
    return ElementTree.Element(tag, {k: str(v) for k, v in attrs.items() if v is not None and v != ''})


def flag(value):
    """A Plex boolean attribute: "1" when true, absent when false, as Plex writes them (Plex Uno tests some by their
    text, where "0" would read as true: a non-default track shown as "(default)")."""
    return '1' if value else None


def layer(value):
    """A Dolby Vision layer's presence for File info: 1 or 0."""
    return '1' if value else '0'


def languageAttrs(code):
    """A track's language as Plex names it, what Uno's language names and preferences read: languageCode in ISO
    639-2/B ('eng', 'fre', 'chi'; ISO 639-3 where there's none, 'yue') and language, the English name. Silo's code is
    ISO 639-1 ('en'), a BCP 47 tag ('zh-Hans') or ISO 639-3 ('yue'); one iso639 doesn't know stays as it is."""
    if not code:
        return {}
    base = code.strip().lower().replace('_', '-').split('-')[0]
    try:
        from iso639 import languages

        language = languages.get(part1=base) if len(base) == 2 else languages.get(part3=base)
    except (ImportError, KeyError):
        return {'languageCode': base, 'language': code}
    return {'languageCode': language.part2b or language.part3 or base, 'language': language.name}


def ratingElements(detail):
    """Silo's ratings as Plex <Rating>s (RatingsMixin.populateRatings()): IMDb, Rotten Tomatoes critic and audience
    (percentages as x/10, fresh from 60), TMDB."""
    ratings = []
    imdb, tmdb = detail.get('rating_imdb'), detail.get('rating_tmdb')
    critic, audience = detail.get('rating_rt_critic'), detail.get('rating_rt_audience')
    if imdb is not None:
        ratings.append(('imdb://image.rating', imdb, 'audience'))
    if critic is not None:
        ratings.append((f'rottentomatoes://image.rating.{"ripe" if critic >= 60 else "rotten"}', critic / 10, 'critic'))
    if audience is not None:
        image = f'rottentomatoes://image.rating.{"upright" if audience >= 60 else "spilled"}'
        ratings.append((image, audience / 10, 'audience'))
    if tmdb is not None:
        ratings.append(('themoviedb://image.rating', tmdb, 'audience'))
    return [
        element('Rating', {'image': image, 'value': f'{value:.1f}', 'type': kind}) for image, value, kind in ratings
    ]


def personElements(detail):
    """Cast as <Role>, crew as <Director>/<Writer>/<Producer>, each with its Silo person id and photo. The id is also
    its tagKey, the key the person screen asks every server by (lib/windows/person.py personKey())."""
    people = [
        element(
            'Role',
            {
                'tag': p.get('name'),
                'role': p.get('character'),
                'id': p.get('person_id'),
                'tagKey': p.get('person_id'),
                'thumb': p.get('photo_url'),
            },
        )
        for p in detail.get('cast') or []
    ]
    for p in detail.get('crew') or []:
        job = p.get('job')
        tag = (
            'Director'
            if job == 'Director'
            else 'Writer'
            if job in WRITER_JOBS
            else ('Producer' if job in PRODUCER_JOBS else None)
        )
        if tag:
            people.append(
                element(
                    tag,
                    {
                        'tag': p.get('name'),
                        'id': p.get('person_id'),
                        'tagKey': p.get('person_id'),
                        'thumb': p.get('photo_url'),
                    },
                )
            )
    return people


def mediaElement(version):
    """One version (a file) as Plex's <Media><Part><Stream/></Part></Media>: what mediaChoice, the resolution badges
    and the audio/subtitle choices read."""
    fileId = version.get('file_id')
    videoTracks = version.get('video_tracks') or []
    audioTracks = version.get('audio_tracks') or []
    video = videoTracks[0] if videoTracks else {}
    mainAudio = next((a for a in audioTracks if a.get('default')), audioTracks[0] if audioTracks else {})
    width, height = video.get('width'), video.get('height')
    duration = ms(version['duration']) if version.get('duration') else None
    media = element(
        'Media',
        {
            'id': fileId,
            'duration': duration,
            'bitrate': version.get('bitrate'),
            'width': width,
            'height': height,
            'aspectRatio': f'{width / height:.2f}' if width and height else None,
            'audioChannels': mainAudio.get('channels'),
            'audioCodec': version.get('codec_audio') or mainAudio.get('codec'),
            'videoCodec': version.get('codec_video') or video.get('codec'),
            'videoResolution': RESOLUTIONS.get(str(version.get('resolution') or '').lower(), 'sd'),
            'container': version.get('container'),
        },
    )
    part = element(
        'Part',
        {
            'id': fileId,
            'key': f'/library/parts/{fileId}/file',
            'file': version.get('file_path') or version.get('file_name'),
            'duration': duration,
            'size': version.get('file_size'),
            'container': version.get('container'),
        },
    )
    streams = [
        element(
            'Stream',
            {
                'streamType': 1,
                'id': f'{fileId}.v{n}',
                'index': n,
                'codec': v.get('codec'),
                'width': v.get('width'),
                'height': v.get('height'),
                'bitDepth': v.get('bit_depth'),
                'frameRate': v.get('frame_rate'),
                'DOVIPresent': flag(v.get('dolby_vision')),
                'DOVIProfile': v.get('dv_profile'),
                'DOVILevel': v.get('dv_level'),
                'DOVIBLCompatID': v.get('dv_bl_compat_id'),
                'DOVIBLPresent': layer(v.get('dv_bl_present')) if v.get('dolby_vision') else None,
                'DOVIELPresent': layer(v.get('dv_enhancement_layer') not in (None, '', 'none'))
                if v.get('dolby_vision')
                else None,
                'DOVIRPUPresent': layer(v.get('dv_rpu_present')) if v.get('dolby_vision') else None,
                'chromaSubsampling': chroma(v.get('pixel_format')),
                'bitrate': videoBitrate(version) if n == 0 else None,
                'colorTrc': v.get('color_transfer'),
                'colorPrimaries': v.get('color_primaries'),
                'colorSpace': v.get('color_space'),
            },
        )
        for n, v in enumerate(videoTracks)
    ]
    selected = audioTracks.index(mainAudio) if mainAudio in audioTracks else -1
    streams += [
        element(
            'Stream',
            {
                'streamType': 2,
                'id': f'{fileId}.a{n}',
                'index': n,
                'codec': a.get('codec'),
                'channels': a.get('channels'),
                'bitrate': a.get('bitrate'),
                'samplingRate': a.get('sample_rate'),
                **languageAttrs(a.get('language')),
                'title': a.get('title'),
                'displayTitle': a.get('title'),
                'default': flag(a.get('default')),
                'selected': flag(n == selected),
            },
        )
        for n, a in enumerate(audioTracks)
    ]
    # the file's own subtitle tracks first, in Kodi's order (Plex Uno selects one by its position among them), then
    # the subtitle files beside the video, whose URLs the attempt's plan lists (SiloServer.buildUrl())
    subtitles = version.get('subtitle_tracks') or []
    own = [(None, s) for s in subtitles if not s.get('external')]
    files = list(enumerate(s for s in subtitles if s.get('external')))
    streams += [
        element(
            'Stream',
            {
                'streamType': 3,
                # a file's id by its place among the files: Silo's index is optional, and files may have none
                'id': f'{fileId}.s{s.get("index")}' if n is None else f'{fileId}.x{n}',
                'index': s.get('index'),
                'key': None if n is None else f'/silo/subtitles/{fileId}/external/{n}',
                'codec': s.get('codec'),
                **languageAttrs(s.get('language')),
                'title': s.get('title'),
                'displayTitle': s.get('title'),
                'forced': flag(s.get('forced')),
                'hearingImpaired': flag(s.get('hearing_impaired')),
                'default': flag(s.get('default')),
            },
        )
        for n, s in own + files
    ]
    part.extend(streams)
    media.append(part)
    return media


def fileMediaElement(file):
    """A version as a season's episode list has it (Silo's files: no tracks) as <Media><Part/></Media>, what the
    episode list's resolution badges read."""
    fileId = file.get('file_id')
    media = element(
        'Media',
        {
            'id': fileId,
            'videoResolution': RESOLUTIONS.get(str(file.get('resolution') or '').lower(), 'sd'),
            'videoCodec': file.get('codec_video'),
            'audioChannels': file.get('audio_channels'),
            'container': file.get('container'),
        },
    )
    media.append(element('Part', {'id': fileId, 'key': f'/library/parts/{fileId}/file', 'size': file.get('file_size')}))
    return media


def extraType(kind):
    name = str(kind or '').strip().lower().replace('-', '_').replace(' ', '_')
    return EXTRA_TYPES.get(name, EXTRA_TYPES.get(name[:-1], OTHER_EXTRA) if name.endswith('s') else OTHER_EXTRA)


def extrasElement(extras):
    """The detail's extras (ItemExtraInfo) as Plex's <Extras>: clips with their title, still, length and extraType.
    Playing them is Phase 5's."""
    root = ElementTree.Element('Extras', {'size': str(len(extras))})
    for extra in extras:
        contentId = extra.get('content_id')
        if not contentId:
            continue
        root.append(
            element(
                'Video',
                {
                    'type': 'clip',
                    'ratingKey': contentId,
                    'key': '/library/metadata/' + contentId,
                    'title': extra.get('title'),
                    'thumb': extra.get('still_url'),
                    'duration': ms(extra['duration_seconds']) if extra.get('duration_seconds') else None,
                    'extraType': extraType(extra.get('kind')),
                    'subtype': extra.get('kind'),
                },
            )
        )
    return root


def setSeason(root, season):
    """An episode's season (a Silo season, as /catalog/series/{id}/seasons lists it) as its Plex parent."""
    seasonId = season.get('content_id')
    if not seasonId:
        return
    for name, value in (
        ('parentRatingKey', seasonId),
        ('parentKey', '/library/metadata/' + seasonId),
        ('parentTitle', season.get('title')),
        ('parentThumb', season.get('poster_url')),
        ('parentIndex', season.get('season_number')),
    ):
        if value is not None and value != '':
            root.set(name, str(value))


def addedAt(versions):
    """When the item's first file joined the library, as Plex's addedAt (whole seconds since the epoch); None without."""
    times = [t for t in (epoch(v.get('added_at')) for v in versions) if t is not None]
    return int(min(times)) if times else None


def chroma(pixelFormat):
    """A pixel format's chroma subsampling, as Plex names it: yuv420p10le -> 4:2:0."""
    for name, value in (('yuv420', '4:2:0'), ('yuv422', '4:2:2'), ('yuv444', '4:4:4')):
        if (pixelFormat or '').startswith(name):
            return value
    return None


def videoBitrate(version):
    """The video's kbit/s: the file's, less its audio tracks' (Silo gives no per-track video figure); None when an audio
    track's isn't known."""
    total = version.get('bitrate')
    audio = [a.get('bitrate') for a in version.get('audio_tracks') or []]
    if not total or None in audio:
        return None
    rest = total - sum(audio)
    return rest if rest > 0 else None


def detailElement(detail):
    """The item's full element: its tile's attributes and everything above. None for media Uno has no screens for."""
    root = itemElement(detail)
    if root is None:
        return None
    studios = detail.get('studios') or []
    versions = detail.get('versions') or []
    # a show's studio is its network, as Plex has it
    companies = detail.get('networks') or studios
    for name, value in (
        ('summary', detail.get('overview')),
        ('tagline', detail.get('tagline')),
        ('contentRating', detail.get('content_rating')),
        ('studio', companies[0] if companies else None),
        ('childCount', detail.get('season_count')),
        ('duration', ms(versions[0]['duration']) if versions and versions[0].get('duration') else None),
        ('addedAt', addedAt(versions)),
    ):
        if value:
            root.set(name, str(value))
    root.extend(element('Genre', {'tag': genre}) for genre in detail.get('genres') or [])
    root.extend(element('Country', {'tag': country}) for country in detail.get('countries') or [])
    root.extend(element('Studio', {'tag': studio}) for studio in studios)
    root.extend(personElements(detail))
    root.extend(ratingElements(detail))
    root.extend(mediaElement(version) for version in versions)
    if detail.get('extras'):
        root.append(extrasElement(detail['extras']))
    # the theme song's id, which SiloServer.themeUrl() asks a grant for when theme music plays
    themes = (detail.get('themes') or {}).get('items') or []
    if themes and themes[0].get('id'):
        root.set('theme', str(themes[0]['id']))
    return root


def itemDetail(client, contentIds, params=None):
    """/library/metadata/<id>: the item's detail in a container, as plexnet's reload() reads data[0]; or several,
    <id>,<id>,... (EpisodesReloadTask asks for the episodes in view at once, as Plex answers it). Silo has no such
    request, so each is asked for, and an id among several that Silo can't answer is left out, as Plex leaves out what
    it doesn't have. An episode's season and a show's on-deck episode are extra requests; when Silo can't answer one,
    the item opens without it."""
    ids = contentIds.split(',')
    seasons = {}  # (series id, season number) -> the season, asked for once for the episodes of one season
    prefs = {}  # series id -> its remembered audio and subtitle choice (silonet.trackprefs), asked for once
    elements = []
    for contentId in ids:
        try:
            root = oneDetail(client, contentId, params, seasons, prefs)
        except SiloError as e:
            if len(ids) == 1:
                raise
            log(f'{contentId} failed: {e.status} {e.type}')
            continue
        if root is not None:
            elements.append(root)
    return container(elements)


def oneDetail(client, contentId, params, seasons, prefs):
    answer = client.request('GET', f'/api/v2/catalog/items/{contentId}') or {}
    root = detailElement(answer)
    if root is None:
        return None
    if answer.get('type') == 'episode' and answer.get('series_id') and answer.get('season_number') is not None:
        place = (answer['series_id'], answer['season_number'])
        try:
            if place not in seasons:
                seasons[place] = client.request('GET', '/api/v2/catalog/series/{}/seasons/{}'.format(*place)) or {}
            setSeason(root, seasons[place])
        except SiloError as e:
            log(f'season of {contentId} failed: {e.status} {e.type}')
        from . import trackprefs  # imports this module

        if answer['series_id'] not in prefs:
            prefs[answer['series_id']] = trackprefs.load(client, answer['series_id'])
        trackprefs.apply(root, *prefs[answer['series_id']])
    if answer.get('type') == 'series' and (params or {}).get('includeOnDeck') and answer.get('play_content_id'):
        try:
            episode = itemElement(client.request('GET', f'/api/v2/catalog/items/{answer["play_content_id"]}') or {})
        except SiloError as e:
            log(f'on deck of {contentId} failed: {e.status} {e.type}')
            episode = None
        if episode is not None:
            ElementTree.SubElement(root, 'OnDeck').append(episode)
    return root
