"""
Seek-bar previews from Jellyfin's trickplay sheets (decision D17):
- Resolution selection picks the widest width <= MAX_WIDTH (320), or the smallest available when
  every resolution exceeds 320.
- TrickplayInfoDto.Interval is already in milliseconds (no tick conversion).
- SheetIndex serves lib/windows/previews.py (count, interval, index_at, picture, close), fetching
  JPEG sprite sheets from /Videos/{itemId}/Trickplay/{width}/{index}.jpg?mediaSourceId=... and
  retaining at most KEPT_SHEETS = 2 decoded sheets via LRU eviction (closing evicted images).
"""

import io
import threading
from collections import OrderedDict
from urllib.parse import quote, urlencode

# Decoded sheets retained in memory: a 10x10 sheet of 320x180 tiles is ~17 MB decoded.
KEPT_SHEETS = 2
MAX_WIDTH = 320


def available():
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        return False
    return True


def _positive_int(value):
    if value is None or value == '':
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def select_resolution(res_map, max_width=MAX_WIDTH):
    """Pick the widest valid TrickplayInfoDto with width <= max_width (or smallest if all exceed it).
    Returns (width, normalized_info) or None when no valid trickplay resolution exists."""
    if not isinstance(res_map, dict):
        return None

    candidates = []
    for key, info in res_map.items():
        if not isinstance(info, dict):
            continue
        width = _positive_int(info.get('Width')) or _positive_int(key)
        height = _positive_int(info.get('Height'))
        count = _positive_int(info.get('ThumbnailCount'))
        interval = _positive_int(info.get('Interval'))
        if not (width and height and count and interval):
            continue
        normalized = dict(info)
        normalized['Width'] = width
        normalized['Height'] = height
        normalized['ThumbnailCount'] = count
        normalized['Interval'] = interval
        normalized['TileWidth'] = _positive_int(info.get('TileWidth')) or 1
        normalized['TileHeight'] = _positive_int(info.get('TileHeight')) or 1
        candidates.append((width, normalized))

    if not candidates:
        return None

    under_cap = [c for c in candidates if c[0] <= max_width]
    if under_cap:
        return max(under_cap, key=lambda c: c[0])
    return min(candidates, key=lambda c: c[0])


class SheetIndex:
    """Jellyfin trickplay sheet index matching lib/windows/previews.py's preview index interface."""

    def __init__(self, info, base_url, item_id, media_source_id, fetch):
        self.interval = int(info.get('Interval') or 0)
        self.count = int(info.get('ThumbnailCount') or 0)
        self.width = int(info.get('Width') or 0)
        self.height = int(info.get('Height') or 0)
        self.columns = max(1, int(info.get('TileWidth') or 1))
        self.rows = max(1, int(info.get('TileHeight') or 1))
        self.base_url = str(base_url).rstrip('/')
        self.item_id = str(item_id)
        self.media_source_id = str(media_source_id) if media_source_id else ''
        self.fetch = fetch
        self._sheets = OrderedDict()
        self._lock = threading.Lock()

    def index_at(self, ms):
        """Thumbnail index showing ms into the file, clamped to [0, count - 1]; None when empty."""
        if self.count <= 0 or self.interval <= 0:
            return None
        return min(max(0, int(ms)) // self.interval, self.count - 1)

    def sheet_url(self, sheet_index):
        """Build Jellyfin's /Videos/{itemId}/Trickplay/{width}/{index}.jpg URL (no auth in query)."""
        route = f'/Videos/{quote(self.item_id, safe="")}/Trickplay/{self.width}/{int(sheet_index)}.jpg'
        url = f'{self.base_url}{route}'
        if self.media_source_id:
            url += f'?{urlencode({"mediaSourceId": self.media_source_id})}'
        return url

    def _crop(self, sheet_img, local_index):
        row, col = divmod(local_index, self.columns)
        box = (col * self.width, row * self.height, (col + 1) * self.width, (row + 1) * self.height)
        out = io.BytesIO()
        sheet_img.crop(box).convert('RGB').save(out, 'JPEG', quality=85)
        return out.getvalue()

    def picture(self, index):
        """Return JPEG bytes for thumbnail index, fetching its sheet lazily with LRU eviction."""
        from PIL import Image

        if index < 0 or index >= self.count:
            raise IndexError(f'thumbnail index out of range: {index}')

        per_sheet = self.columns * self.rows
        sheet_index, local = divmod(index, per_sheet)

        with self._lock:
            cached = self._sheets.get(sheet_index)
            if cached is not None:
                self._sheets.move_to_end(sheet_index)
                return self._crop(cached, local)

        raw = self.fetch(self.sheet_url(sheet_index))
        decoded = Image.open(io.BytesIO(raw))
        try:
            decoded.load()
        except Exception:
            decoded.close()
            raise

        with self._lock:
            existing = self._sheets.get(sheet_index)
            if existing is not None:
                decoded.close()
                self._sheets.move_to_end(sheet_index)
                return self._crop(existing, local)
            while len(self._sheets) >= KEPT_SHEETS:
                _, evicted = self._sheets.popitem(last=False)
                evicted.close()
            self._sheets[sheet_index] = decoded
            return self._crop(decoded, local)
