"""
Item detail mapping from Jellyfin's GET /Items/{itemId} (and batch GET /Items?ids=...) into the full
Plex XML element plexnet's reload() expects (/library/metadata/<ratingKey>):
- movie, show, season, and episode metadata, taglines, studios, countries, ratings, provider IDs, clearLogo
- cast & crew (<Role>, <Director>, <Writer>, <Producer>) with Jellyfin person IDs and Primary image paths
- chapters (<Chapter>) with millisecond offsets and Chapter image paths
- multiple MediaSources -> multiple <Media><Part><Stream/></Part></Media> versions with video/audio/subtitle
  attributes, Dolby Vision layers, chroma subsampling, ISO 639-2/B languages, and distinct stream IDs
"""

from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from . import library
from .client import log
from .errors import JellyfinAuthError, JellyfinError
from .items import buildImageUrl, container, itemArt, itemElement, itemThumb, ticks_to_ms

DETAIL_FIELDS = (
    'Overview,Genres,People,Studios,MediaSources,MediaStreams,Chapters,Trickplay,ProviderIds,Taglines,DateCreated,Path'
)

WRITER_KINDS = frozenset(('Writer', 'Author'))
PRODUCER_KINDS = frozenset(('Producer',))

SUBTITLE_FORMAT_MAP = {
    'subrip': 'srt',
    'webvtt': 'vtt',
    'ass': 'ass',
    'ssa': 'ssa',
    'sami': 'smi',
    'smi': 'smi',
    'mov_text': 'srt',
    'tx3g': 'srt',
}


def element(tag, attrs):
    """Build an XML element keeping only non-None, non-empty attribute values as strings."""
    return ElementTree.Element(tag, {k: str(v) for k, v in attrs.items() if v is not None and v != ''})


def flag(value):
    """Plex boolean attribute: '1' when truthy, omitted (None) when falsy (Plex Uno checks string truthiness)."""
    return '1' if value else None


def layer(value):
    """Dolby Vision layer flag for File info: '1' or '0'."""
    return '1' if value else '0'


def kbps(bps):
    """Convert bits/sec to integer kbit/sec via integer division, or None when absent/non-positive."""
    if bps is None or bps == '':
        return None
    try:
        value = int(bps)
    except (TypeError, ValueError):
        return None
    return value // 1000 if value > 0 else None


def videoResolution(width, height):
    """Classify frame dimensions into Plex's videoResolution buckets ('4k', '1080', '720', '480', 'sd')."""
    try:
        w = int(width or 0)
        h = int(height or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 and h <= 0:
        return None
    if w >= 3800 or h >= 2100:
        return '4k'
    if w >= 1900 or h >= 1000:
        return '1080'
    if w >= 1200 or h >= 700:
        return '720'
    if w >= 700 or h >= 480:
        return '480'
    return 'sd'


def chroma(pixel_format):
    """Map ffmpeg pixel format strings to Plex chromaSubsampling ('4:2:0', '4:2:2', '4:4:4')."""
    fmt = (pixel_format or '').lower()
    for prefix, value in (
        ('yuv420', '4:2:0'),
        ('yuvj420', '4:2:0'),
        ('p010', '4:2:0'),
        ('nv12', '4:2:0'),
        ('yuv422', '4:2:2'),
        ('yuvj422', '4:2:2'),
        ('yuv444', '4:4:4'),
        ('yuvj444', '4:4:4'),
    ):
        if fmt.startswith(prefix):
            return value
    return None


def formatFrameRate(rate):
    """Format a Jellyfin float frame rate cleanly (e.g. 23.976025 -> '23.976', 24.0 -> '24')."""
    if rate is None or rate == '':
        return None
    try:
        val = float(rate)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return f'{val:.3f}'.rstrip('0').rstrip('.')


def mediaFrameRate(rate):
    """Map a frame rate to Plex's <Media videoFrameRate> convention ('24p', 'PAL', 'NTSC', '50p', '60p')."""
    if rate is None or rate == '':
        return None
    try:
        val = float(rate)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    if 23.5 <= val <= 24.5:
        return '24p'
    if 24.5 < val <= 25.5:
        return 'PAL'
    if 29.0 <= val <= 30.5:
        return 'NTSC'
    if 49.5 <= val <= 50.5:
        return '50p'
    if 59.0 <= val <= 60.5:
        return '60p'
    return f'{round(val)}p'


def languageAttrs(code):
    """Normalize a Jellyfin language code (ISO 639-1, 639-2/B, 639-2/T, 639-3, or BCP 47) into Plex's
    ISO 639-2/B languageCode and English language name."""
    if not code:
        return {}
    base = str(code).strip().lower().replace('_', '-').split('-')[0]
    if not base:
        return {}
    if base == 'und':
        return {'languageCode': 'und', 'language': 'Unknown'}
    try:
        from iso639 import languages
    except ImportError:
        return {'languageCode': base, 'language': str(code)}

    language = None
    if len(base) == 2:
        try:
            language = languages.get(part1=base)
        except KeyError:
            language = None
    elif len(base) == 3:
        for field in ('part2b', 'part2t', 'part3'):
            try:
                language = languages.get(**{field: base})
                break
            except KeyError:
                continue
    if language is None:
        return {'languageCode': base, 'language': str(code)}
    return {'languageCode': language.part2b or language.part3 or base, 'language': language.name}


def _fallbackColorTrc(video_range_type):
    vrt = str(video_range_type or '').upper()
    if vrt in ('HDR10', 'HDR10PLUS', 'DOVIWITHHDR10', 'DOVIWITHHDR10PLUS', 'DOVIWITHELHDR10PLUS'):
        return 'smpte2084'
    if vrt in ('HLG', 'DOVIWITHHLG'):
        return 'arib-std-b67'
    return None


def doviAttrs(stream):
    """Dolby Vision attributes for a video <Stream>; empty dict when not Dolby Vision.
    DOVIVersion is emitted only when DvVersionMajor is present so File info never prints an empty 'Version:'."""
    dv_profile = stream.get('DvProfile')
    vrt = str(stream.get('VideoRangeType') or '')
    is_dovi = dv_profile is not None or (vrt.startswith('DOVI') and vrt != 'DOVIInvalid')
    if not is_dovi:
        return {}
    attrs = {
        'DOVIPresent': '1',
        'DOVIProfile': dv_profile,
        'DOVILevel': stream.get('DvLevel'),
        'DOVIBLPresent': layer(stream.get('BlPresentFlag')),
        'DOVIELPresent': layer(stream.get('ElPresentFlag')),
        'DOVIRPUPresent': layer(stream.get('RpuPresentFlag')),
        'DOVIBLCompatID': stream.get('DvBlSignalCompatibilityId'),
    }
    major = stream.get('DvVersionMajor')
    if major is not None:
        minor = stream.get('DvVersionMinor')
        attrs['DOVIVersion'] = f'{major}.{minor}' if minor is not None else str(major)
    return attrs


def _streamId(source_id, raw_index, pos, seen):
    """Generate a strictly unique stream id within a MediaSource (SM4K #49: external subtitles never collide)."""
    suffix = str(raw_index) if raw_index is not None else f'p{pos}'
    candidate = f'{source_id}:{suffix}'
    if candidate in seen:
        candidate = f'{source_id}:{suffix}.{pos}'
    seen.add(candidate)
    return candidate


def _fallbackVideoBitrateKbps(source, audio_streams):
    total = source.get('Bitrate')
    if not total or int(total) <= 0:
        return None
    audio_bps = [a.get('BitRate') for a in audio_streams]
    if any(b is None for b in audio_bps):
        return None
    rest = int(total) - sum(int(b) for b in audio_bps)
    return rest // 1000 if rest > 0 else None


def cleanDeliveryUrl(url):
    """Normalize a Jellyfin DeliveryUrl, stripping any query credential parameters."""
    if not url:
        return ''
    parsed = urlsplit(str(url))
    if not parsed.query:
        return str(url)
    kept = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().endswith(('key', 'token'))
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(kept), parsed.fragment))


def _externalSubtitleKey(item_id, source_id, stream_index, stream):
    delivery = stream.get('DeliveryUrl')
    if delivery:
        return cleanDeliveryUrl(delivery)
    raw_codec = str(stream.get('Codec') or '').lower()
    fmt = SUBTITLE_FORMAT_MAP.get(raw_codec, raw_codec or 'srt')
    return (
        f'/Videos/{quote(str(item_id), safe="")}/{quote(str(source_id), safe="")}/Subtitles/{stream_index}/Stream.{fmt}'
    )


def mediaElement(item_id, source, item_duration_ms=0):
    """Map one Jellyfin MediaSourceInfo dict to Plex's <Media><Part><Stream/></Part></Media>."""
    source_id = str(source.get('Id') or item_id)
    raw_streams = [s for s in (source.get('MediaStreams') or ()) if isinstance(s, dict)]

    video_streams = [(pos, s) for pos, s in enumerate(raw_streams) if s.get('Type') == 'Video']
    audio_streams = [(pos, s) for pos, s in enumerate(raw_streams) if s.get('Type') == 'Audio']
    sub_streams = [(pos, s) for pos, s in enumerate(raw_streams) if s.get('Type') == 'Subtitle']

    primary_video = video_streams[0][1] if video_streams else {}
    default_audio_idx = source.get('DefaultAudioStreamIndex')
    main_audio = next(
        (s for _, s in audio_streams if default_audio_idx is not None and s.get('Index') == default_audio_idx),
        None,
    )
    if main_audio is None:
        main_audio = next((s for _, s in audio_streams if s.get('IsDefault')), None)
    if main_audio is None and audio_streams:
        main_audio = audio_streams[0][1]
    if main_audio is None:
        main_audio = {}

    raw_default_sub = source.get('DefaultSubtitleStreamIndex')
    default_sub_idx = int(raw_default_sub) if raw_default_sub is not None and int(raw_default_sub) >= 0 else None

    duration_ms = ticks_to_ms(source.get('RunTimeTicks')) or item_duration_ms or None
    width, height = primary_video.get('Width'), primary_video.get('Height')
    aspect_ratio = f'{width / height:.2f}' if width and height else primary_video.get('AspectRatio')
    primary_fr = (
        primary_video.get('AverageFrameRate')
        or primary_video.get('RealFrameRate')
        or primary_video.get('ReferenceFrameRate')
    )

    media_el = element(
        'Media',
        {
            'id': source_id,
            'duration': duration_ms,
            'bitrate': kbps(source.get('Bitrate')),
            'container': source.get('Container'),
            'width': width,
            'height': height,
            'videoResolution': videoResolution(width, height),
            'videoCodec': primary_video.get('Codec'),
            'videoFrameRate': mediaFrameRate(primary_fr),
            'audioCodec': main_audio.get('Codec'),
            'audioChannels': main_audio.get('Channels'),
            'aspectRatio': aspect_ratio,
        },
    )
    part_el = element(
        'Part',
        {
            'id': source_id,
            'key': source_id,
            'file': source.get('Path') or source.get('Name'),
            'size': source.get('Size'),
            'duration': duration_ms,
            'container': source.get('Container'),
        },
    )

    seen_ids = set()
    stream_els = []

    for pos, v in video_streams:
        raw_index = v.get('Index')
        v_fr = v.get('AverageFrameRate') or v.get('RealFrameRate') or v.get('ReferenceFrameRate')
        v_bitrate = kbps(v.get('BitRate'))
        if v_bitrate is None and v is primary_video:
            v_bitrate = _fallbackVideoBitrateKbps(source, [a for _, a in audio_streams])
        display_title = v.get('DisplayTitle') or v.get('Title')
        stream_els.append(
            element(
                'Stream',
                {
                    'streamType': 1,
                    'id': _streamId(source_id, raw_index, pos, seen_ids),
                    'index': raw_index if raw_index is not None else pos,
                    'codec': v.get('Codec'),
                    'width': v.get('Width'),
                    'height': v.get('Height'),
                    'bitrate': v_bitrate,
                    'frameRate': formatFrameRate(v_fr),
                    'bitDepth': v.get('BitDepth'),
                    'chromaSubsampling': chroma(v.get('PixelFormat')),
                    'profile': v.get('Profile'),
                    'level': v.get('Level'),
                    'colorRange': v.get('ColorRange'),
                    'colorSpace': v.get('ColorSpace'),
                    'colorTrc': v.get('ColorTransfer') or _fallbackColorTrc(v.get('VideoRangeType')),
                    'colorPrimaries': v.get('ColorPrimaries'),
                    **doviAttrs(v),
                    'displayTitle': display_title,
                    'extendedDisplayTitle': v.get('DisplayTitle'),
                    'default': flag(v.get('IsDefault')),
                    'selected': flag(v is primary_video),
                },
            )
        )

    for pos, a in audio_streams:
        raw_index = a.get('Index')
        display_title = a.get('DisplayTitle') or a.get('Title')
        stream_els.append(
            element(
                'Stream',
                {
                    'streamType': 2,
                    'id': _streamId(source_id, raw_index, pos, seen_ids),
                    'index': raw_index if raw_index is not None else pos,
                    'codec': a.get('Codec'),
                    'channels': a.get('Channels'),
                    'audioChannelLayout': a.get('ChannelLayout'),
                    'bitrate': kbps(a.get('BitRate')),
                    'samplingRate': a.get('SampleRate'),
                    **languageAttrs(a.get('Language')),
                    'title': a.get('Title'),
                    'displayTitle': display_title,
                    'extendedDisplayTitle': a.get('DisplayTitle'),
                    'default': flag(a.get('IsDefault')),
                    'selected': flag(a is main_audio),
                },
            )
        )

    # Embedded subtitles before external so Uno's embedded position matches Kodi's embedded track order
    embedded_subs = [(pos, s) for pos, s in sub_streams if not s.get('IsExternal')]
    external_subs = [(pos, s) for pos, s in sub_streams if s.get('IsExternal')]
    used_sub_indices = set()
    for pos, s in embedded_subs + external_subs:
        raw_index = s.get('Index')
        parsed_index = None
        if raw_index is not None and raw_index != '':
            try:
                parsed_index = int(raw_index)
            except (TypeError, ValueError):
                parsed_index = None
        if parsed_index is not None and parsed_index not in used_sub_indices:
            stream_index = parsed_index
        else:
            stream_index = pos
            while stream_index in used_sub_indices:
                stream_index += 1
        used_sub_indices.add(stream_index)
        is_external = bool(s.get('IsExternal'))
        display_title = s.get('DisplayTitle') or s.get('Title')
        sub_key = _externalSubtitleKey(item_id, source_id, stream_index, s) if is_external else None
        is_selected = default_sub_idx is not None and parsed_index is not None and parsed_index == default_sub_idx
        stream_els.append(
            element(
                'Stream',
                {
                    'streamType': 3,
                    'id': _streamId(source_id, raw_index, pos, seen_ids),
                    'index': stream_index,
                    'codec': s.get('Codec'),
                    **languageAttrs(s.get('Language')),
                    'title': s.get('Title'),
                    'displayTitle': display_title,
                    'extendedDisplayTitle': s.get('DisplayTitle'),
                    'default': flag(s.get('IsDefault')),
                    'forced': flag(s.get('IsForced')),
                    'hearingImpaired': flag(s.get('IsHearingImpaired')),
                    'external': flag(is_external),
                    'key': sub_key,
                    'selected': flag(is_selected),
                },
            )
        )

    part_el.extend(stream_els)
    media_el.append(part_el)
    return media_el


def personElements(item):
    """Map Jellyfin BaseItemDto.People to Plex <Role>, <Director>, <Writer>, <Producer> elements."""
    elements = []
    for person in item.get('People') or ():
        if not isinstance(person, dict):
            continue
        name = person.get('Name')
        if not name:
            continue
        kind = person.get('Type') or ''
        person_id = str(person['Id']) if person.get('Id') else None
        primary_tag = person.get('PrimaryImageTag')
        thumb = buildImageUrl('', person_id, 'Primary', tag=primary_tag) if person_id and primary_tag else None
        common = {'id': person_id, 'tagKey': person_id, 'tag': name, 'thumb': thumb}

        if kind in ('Actor', 'GuestStar') or (not kind and person.get('Role')):
            elements.append(element('Role', {**common, 'role': person.get('Role')}))
        elif kind == 'Director':
            elements.append(element('Director', common))
        elif kind in WRITER_KINDS:
            elements.append(element('Writer', common))
        elif kind in PRODUCER_KINDS:
            elements.append(element('Producer', common))
    return elements


def ratingElements(item):
    """Map CommunityRating (0-10) and CriticRating (0-100) to Plex <Rating> elements."""
    elements = []
    community = item.get('CommunityRating')
    if community is not None:
        try:
            elements.append(
                element(
                    'Rating',
                    {
                        'image': 'themoviedb://image.rating',
                        'value': f'{float(community):.1f}',
                        'type': 'audience',
                    },
                )
            )
        except (TypeError, ValueError):
            pass
    critic = item.get('CriticRating')
    if critic is not None:
        try:
            score = float(critic)
            icon = 'rottentomatoes://image.rating.ripe' if score >= 60 else 'rottentomatoes://image.rating.rotten'
            elements.append(element('Rating', {'image': icon, 'value': f'{score / 10:.1f}', 'type': 'critic'}))
        except (TypeError, ValueError):
            pass
    return elements


def chapterElements(item_id, chapters, item_duration_ms=0):
    """Map Jellyfin ChapterInfo[] to Plex <Chapter> elements with millisecond start/end offsets and chapter thumbs."""
    valid = [c for c in (chapters or ()) if isinstance(c, dict)]
    elements = []
    for idx, ch in enumerate(valid):
        start_ms = ticks_to_ms(ch.get('StartPositionTicks'))
        if idx + 1 < len(valid):
            next_start = ticks_to_ms(valid[idx + 1].get('StartPositionTicks'))
            end_ms = max(start_ms, next_start)
        else:
            end_ms = max(start_ms, item_duration_ms)
        img_tag = ch.get('ImageTag')
        thumb = (
            f'/Items/{quote(str(item_id), safe="")}/Images/Chapter/{idx}?tag={quote(str(img_tag), safe="")}'
            if img_tag
            else None
        )
        elements.append(
            element(
                'Chapter',
                {
                    'id': idx,
                    'index': idx,
                    'tag': ch.get('Name') or f'Chapter {idx + 1}',
                    'startTimeOffset': start_ms,
                    'endTimeOffset': end_ms,
                    'thumb': thumb,
                },
            )
        )
    return elements


def _studioPairs(item):
    pairs = []
    for entry in item.get('Studios') or ():
        if isinstance(entry, dict) and entry.get('Name'):
            pairs.append((str(entry['Name']), str(entry['Id']) if entry.get('Id') else None))
        elif isinstance(entry, str) and entry:
            pairs.append((entry, None))
    return pairs


def _clearLogoUrl(item):
    item_id = item.get('Id')
    image_tags = item.get('ImageTags')
    if item_id and isinstance(image_tags, dict) and image_tags.get('Logo'):
        return buildImageUrl('', item_id, 'Logo', tag=image_tags['Logo'])
    parent_logo_id = item.get('ParentLogoItemId')
    parent_logo_tag = item.get('ParentLogoImageTag')
    if parent_logo_id and parent_logo_tag:
        return buildImageUrl('', parent_logo_id, 'Logo', tag=parent_logo_tag)
    return ''


def _mediaSourcesFor(item):
    sources = [s for s in (item.get('MediaSources') or ()) if isinstance(s, dict)]
    if sources:
        return sources
    if item.get('MediaStreams'):
        return [
            {
                'Id': item.get('Id'),
                'Path': item.get('Path'),
                'Container': item.get('Container'),
                'RunTimeTicks': item.get('RunTimeTicks'),
                'MediaStreams': item.get('MediaStreams'),
            }
        ]
    return []


def _clipBaseElement(item):
    """Build a base Plex <Video type="clip"> for a non-trailer Jellyfin Video extra on detail reload."""
    if item.get('Type') != 'Video' or not item.get('Id'):
        return None
    if str(item.get('ExtraType') or '').strip().lower() == 'trailer':
        return None
    item_id = str(item['Id'])
    duration_ms = ticks_to_ms(item.get('RunTimeTicks'))
    return element(
        'Video',
        {
            'type': 'clip',
            'ratingKey': item_id,
            'key': f'/library/metadata/{item_id}',
            'title': item.get('Name') or '',
            'summary': item.get('Overview'),
            'thumb': itemThumb(item),
            'art': itemArt(item),
            'duration': duration_ms or None,
            'subtype': item.get('ExtraType'),
        },
    )


def detailElement(item, on_item=None, track_prefs=None, section_id=None, section_title=None, section_type=None):
    """Build a full Plex detail element from one Jellyfin BaseItemDto, or None if unsupported."""
    if not isinstance(item, dict):
        return None
    if on_item is not None:
        on_item(item)
    root = itemElement(item, section_id=section_id, section_title=section_title, section_type=section_type)
    if root is None:
        root = _clipBaseElement(item)
    if root is None:
        return None

    item_id = str(item['Id'])
    item_duration_ms = ticks_to_ms(item.get('RunTimeTicks'))
    sources = _mediaSourcesFor(item)
    if not item_duration_ms and sources:
        item_duration_ms = ticks_to_ms(sources[0].get('RunTimeTicks'))
        if item_duration_ms:
            root.set('duration', str(item_duration_ms))

    taglines = item.get('Taglines') or ()
    if taglines and taglines[0]:
        root.set('tagline', str(taglines[0]))

    studios = _studioPairs(item)
    if studios:
        root.set('studio', studios[0][0])
        for name, studio_id in studios:
            root.append(element('Studio', {'tag': name, 'id': studio_id}))

    for loc in item.get('ProductionLocations') or ():
        if loc:
            root.append(element('Country', {'tag': loc}))

    provider_ids = item.get('ProviderIds')
    if isinstance(provider_ids, dict):
        for provider, ext_id in provider_ids.items():
            if provider and ext_id:
                root.append(element('Guid', {'id': f'{str(provider).lower()}://{ext_id}'}))

    logo_url = _clearLogoUrl(item)
    if logo_url:
        root.append(element('Image', {'type': 'clearLogo', 'url': logo_url}))

    root.extend(ratingElements(item))
    root.extend(personElements(item))
    root.extend(mediaElement(item_id, source, item_duration_ms=item_duration_ms) for source in sources)
    root.extend(chapterElements(item_id, item.get('Chapters'), item_duration_ms=item_duration_ms))
    if track_prefs is not None and item.get('Type') == 'Episode' and item.get('SeriesId'):
        from . import trackprefs

        audio_pref, sub_pref = track_prefs.get(str(item['SeriesId']))
        trackprefs.apply(root, audio_pref, sub_pref)
    return root


def _isTruthy(value):
    return str(value or '').lower() in ('1', 'true', 'yes')


def _attachOnDeck(client, series_id, root, on_item=None):
    try:
        answer = (
            client.get(
                '/Shows/NextUp',
                params={
                    'seriesId': series_id,
                    'limit': 1,
                    'fields': library.GRID_FIELDS,
                    'enableUserData': 'true',
                },
            )
            or {}
        )
    except JellyfinAuthError:
        raise
    except JellyfinError as e:
        log(f'on deck of {series_id} failed: {e.status}')
        return
    items = answer.get('Items') or ()
    if not items:
        return
    ep = items[0]
    if on_item is not None and isinstance(ep, dict):
        on_item(ep)
    ep_el = itemElement(ep)
    if ep_el is not None:
        ElementTree.SubElement(root, 'OnDeck').append(ep_el)


def itemDetail(
    client,
    item_ids,
    params=None,
    on_item=None,
    extras_fetcher=None,
    track_prefs=None,
    section_resolver=None,
):
    """Answer /library/metadata/<id> (single item) or /library/metadata/<id1>,<id2>,... (batch reload)."""
    params = params or {}
    ids = [part.strip() for part in str(item_ids).split(',') if part.strip()]
    if not ids:
        return container()

    def _section_for(dto):
        if section_resolver is None:
            return None, None, None
        return section_resolver(dto)

    if len(ids) == 1:
        item_id = ids[0]
        dto = client.get(f'/Items/{quote(item_id, safe="")}', params={'fields': DETAIL_FIELDS}) or {}
        sec_id, sec_title, sec_type = _section_for(dto)
        root = detailElement(
            dto,
            on_item=on_item,
            track_prefs=track_prefs,
            section_id=sec_id,
            section_title=sec_title,
            section_type=sec_type,
        )
        if root is None:
            return container()
        if dto.get('Type') == 'Series' and _isTruthy(params.get('includeOnDeck')):
            _attachOnDeck(client, item_id, root, on_item=on_item)
        if (
            extras_fetcher is not None
            and _isTruthy(params.get('includeExtras'))
            and int(dto.get('SpecialFeatureCount') or 0) > 0
        ):
            extras_el = extras_fetcher(client, item_id)
            if extras_el is not None:
                root.append(extras_el)
        return container([root])

    answer = (
        client.get(
            '/Items',
            params={
                'ids': ','.join(ids),
                'fields': DETAIL_FIELDS,
                'enableUserData': 'true',
            },
        )
        or {}
    )
    by_id = {}
    for dto in answer.get('Items') or ():
        if isinstance(dto, dict) and dto.get('Id'):
            by_id[str(dto['Id'])] = dto

    elements = []
    for item_id in ids:
        dto = by_id.get(item_id)
        if dto is None:
            continue
        sec_id, sec_title, sec_type = _section_for(dto)
        elem = detailElement(
            dto,
            on_item=on_item,
            track_prefs=track_prefs,
            section_id=sec_id,
            section_title=sec_title,
            section_type=sec_type,
        )
        if elem is not None:
            elements.append(elem)
    return container(elements)
