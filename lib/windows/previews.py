# coding=utf-8
"""
Seek-bar preview pictures: a played file's index (Jellyfin's trickplay sheets, jellynet.trickplay.SheetIndex, asked of the
server soon after playback starts) gives the thumbnail for a position, written as needed into Kodi's temp folder.

Kodi loads images under special://temp directly, without copying them into its texture cache and database
(CTextureCache::IsCachedImage, Kodi 21), so the pictures are written there, one file each, named by part and
picture so a name never shows a different image. All of it goes when the item's playback ends, and anything a crash
left behind at the first playback of the session.
"""
from __future__ import absolute_import

import os
import shutil
import threading

from lib import util
from lib.kodi_util import ADDON_ID

FOLDER = '{0}-previews'.format(ADDON_ID)


def part_at(parts, offset):
    """
    Which part offset (ms into the whole item) falls in, and how far into it: (part id, ms) - parts, (part id,
    duration ms) pairs in playing order, as a multi-part item's files follow each other. None past the end.
    """
    start = 0
    for part_id, duration in parts:
        if start <= offset < start + duration:
            return part_id, offset - start
        start += duration
    return None


class PreviewStore(object):
    def __init__(self):
        self.folder = os.path.join(util.translatePath('special://temp/'), FOLDER)
        self.special = 'special://temp/{0}/'.format(FOLDER)
        self._lock = threading.Lock()
        # one picture written at a time: the scan thread and the dialog's can ask for the same one at once, and on
        # Windows the second rename onto it fails once Kodi has the first open
        self._writeLock = threading.Lock()
        # bumped by clear(): a download from before it is dropped when it finishes
        self._generation = 0
        self._indexes = {}  # part id -> its index (jellynet.trickplay.SheetIndex)
        self._swept = False

    def _sweepOnce(self):
        if self._swept:
            return
        self._swept = True
        shutil.rmtree(self.folder, ignore_errors=True)

    def start(self, parts, server, on_ready=None):
        """
        Reads each part's index in a thread of its own (JellyfinServer.trickplayIndex()): parts, (part id, index path)
        pairs - the path as PlexPart.getIndexPath() gives it. on_ready() is called (on that thread) as each one
        becomes usable.
        """
        with self._lock:
            generation = self._generation
        self._sweepOnce()
        for part_id, path in parts:
            thread = threading.Thread(target=self._download, args=(generation, str(part_id), path, server, on_ready),
                                      name='PreviewStore.download')
            thread.daemon = True
            thread.start()

    def _stale(self, generation):
        return generation != self._generation or util.MONITOR.abortRequested()

    def _download(self, generation, part_id, path, server, on_ready):
        self._sheets(generation, part_id, server, on_ready)

    def _sheets(self, generation, part_id, server, on_ready):
        """A Jellyfin file's previews: sheets of thumbnails, cut out as they're asked for (JellyfinServer.trickplayIndex())."""
        try:
            index = server.trickplayIndex(part_id)
        except Exception as e:
            util.LOG("Previews: part {0}'s previews not read: {1}", part_id, e)
            return
        if index is None:
            return
        with self._lock:
            if self._stale(generation):
                return
            self._indexes[part_id] = index
        util.DEBUG_LOG("Previews: part {0}'s previews ready: {1} pictures, every {2} ms", part_id, index.count,
                       index.interval)
        if on_ready:
            try:
                on_ready()
            except Exception:
                util.ERROR()

    def picture(self, part_id, ms):
        """
        The special:// path of the picture at or before ms into the part, written to disk the first time it's
        asked for; None while the part's index isn't ready (or the picture can't be had).
        """
        part_id = str(part_id)
        index = self._indexes.get(part_id)
        if index is None:
            return None
        i = index.index_at(ms)
        if i is None:
            return None
        name = '{0}/{1}.jpg'.format(part_id, i)
        path = os.path.join(self.folder, part_id, '{0}.jpg'.format(i))
        if os.path.exists(path):
            return self.special + name
        with self._writeLock:
            if not os.path.exists(path):
                partial = path + '.part'
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    # written under another name, then renamed: Kodi never reads half a picture
                    with open(partial, 'wb') as f:
                        f.write(index.picture(i))
                    os.replace(partial, path)
                except Exception:
                    if os.path.exists(partial):
                        try:
                            os.remove(partial)
                        except OSError:
                            pass
                    if not os.path.exists(path):
                        util.ERROR()
                        return None
        return self.special + name

    def _remove(self, part_id):
        shutil.rmtree(os.path.join(self.folder, part_id), ignore_errors=True)

    def clear(self):
        """Forgets every part's index and deletes its files; downloads still running are dropped when they end."""
        with self._lock:
            self._generation += 1
            indexes = list(self._indexes.items())
            self._indexes = {}
        for part_id, index in indexes:
            if hasattr(index, 'close'):
                index.close()
            self._remove(part_id)


store = PreviewStore()
