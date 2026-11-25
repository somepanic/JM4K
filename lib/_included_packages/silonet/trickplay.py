"""
Seek-bar previews from Silo's trickplay (decision D17): GET /api/v2/watch/{id}/trickplay answers a file's previews as
JPEG sheets of tile_columns x tile_rows thumbnails, each covering interval_ms. SheetIndex gives the preview store
(lib/windows/previews.py) what a Plex BIF index gives it - a count, an interval, the thumbnail for a position and its
picture - fetching each sheet once and cutting the thumbnail out with Pillow, which Kodi's Python has on CoreELEC and
LibreELEC; without it there are no previews.
"""

import io

# decoded sheets kept: a sheet is about 3200x1440 (10x8 thumbnails of 320), some 14 MB decoded
KEPT_SHEETS = 2


def available():
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        return False
    return True


class SheetIndex:
    def __init__(self, manifest, baseUrl, fetch):
        self.interval = int(manifest.get('interval_ms') or 1000)
        self.count = int(manifest.get('thumbnail_count') or 0)
        self.width = int(manifest.get('thumbnail_width') or 0)
        self.height = int(manifest.get('thumbnail_height') or 0)
        self.columns = max(1, int(manifest.get('tile_columns') or 1))
        self.rows = max(1, int(manifest.get('tile_rows') or 1))
        sheets = sorted(manifest.get('sheets') or [], key=lambda s: s.get('index') or 0)
        # each sheet's URL carries its own authorization
        self.urls = [baseUrl + s['url'] if s.get('url', '').startswith('/') else s.get('url') for s in sheets]
        self.fetch = fetch
        self._sheets = {}

    def index_at(self, ms):
        """The thumbnail showing ms into the file, the last one past the end; None without any."""
        if not self.count:
            return None
        return min(max(0, int(ms)) // self.interval, self.count - 1)

    def picture(self, index):
        """The thumbnail's JPEG bytes, cut from its sheet."""
        from PIL import Image

        perSheet = self.columns * self.rows
        sheetIndex, n = divmod(index, perSheet)
        if sheetIndex not in self._sheets:
            while len(self._sheets) >= KEPT_SHEETS:
                self._sheets.pop(next(iter(self._sheets)))  # the one decoded first
            self._sheets[sheetIndex] = Image.open(io.BytesIO(self.fetch(self.urls[sheetIndex])))
            self._sheets[sheetIndex].load()
        column, row = n % self.columns, n // self.columns
        box = (column * self.width, row * self.height, (column + 1) * self.width, (row + 1) * self.height)
        out = io.BytesIO()
        self._sheets[sheetIndex].crop(box).convert('RGB').save(out, 'JPEG', quality=85)
        return out.getvalue()
