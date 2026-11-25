# coding=utf-8
"""
lib/windows/previews.py - the preview pictures a played file's index gives (Silo's trickplay sheets,
silonet.trickplay; tested in test_silonet_trickplay.py): one file each under Kodi's temp folder, written once even
when two threads ask at once (on Windows the second rename failed once Kodi had the first open - seen live,
2026-10-20), and gone after clear().
"""

from __future__ import absolute_import

import os
import shutil
import tempfile
import threading
import unittest

from kodienv import ENV

ENV.abort_requested = True
from lib.windows import previews  # noqa: E402


class Pictures(object):
    """An index as the store reads one: a picture every interval ms."""

    def __init__(self, pictures, interval=5000):
        self.pictures = pictures
        self.interval = interval
        self.count = len(pictures)

    def index_at(self, ms):
        return min(ms // self.interval, self.count - 1) if self.count else None

    def picture(self, index):
        return self.pictures[index]


class PartAtTest(unittest.TestCase):
    # a two-file item: 55 minutes, then 50
    PARTS = [(11, 3300000), (12, 3000000)]

    def test_a_single_part(self):
        self.assertEqual(previews.part_at([(7, 6000000)], 1234567), (7, 1234567))

    def test_the_first_part(self):
        self.assertEqual(previews.part_at(self.PARTS, 0), (11, 0))
        self.assertEqual(previews.part_at(self.PARTS, 3299999), (11, 3299999))

    def test_into_the_second_part(self):
        self.assertEqual(previews.part_at(self.PARTS, 3300000), (12, 0))
        self.assertEqual(previews.part_at(self.PARTS, 4200000), (12, 900000))  # 1:10:00 is 15:00 into part 2

    def test_past_the_end(self):
        self.assertIsNone(previews.part_at(self.PARTS, 6300000))
        self.assertIsNone(previews.part_at([], 0))


class PreviewStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = previews.PreviewStore()
        self.store.folder = os.path.join(self.dir, previews.FOLDER)
        os.makedirs(self.store.folder)
        self.pictures = [b'picture %d' % i for i in range(6)]
        self.store._indexes['42'] = Pictures(self.pictures)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def local(self, special):
        return os.path.join(self.store.folder, special.split(previews.FOLDER + '/', 1)[1])

    def test_a_picture_by_time(self):
        special = self.store.picture(42, 12000)
        self.assertEqual(special, 'special://temp/{0}/42/2.jpg'.format(previews.FOLDER))
        with open(self.local(special), 'rb') as f:
            self.assertEqual(f.read(), self.pictures[2])

    def test_written_once_and_reused(self):
        first = self.store.picture(42, 5000)
        mtime = os.path.getmtime(self.local(first))
        self.assertEqual(self.store.picture(42, 9999), first)
        self.assertEqual(os.path.getmtime(self.local(first)), mtime)

    def test_two_threads_asking_at_once(self):
        results, errors = [], []
        start = threading.Event()

        def ask():
            start.wait()
            try:
                results.append(self.store.picture(42, 20000))
            except Exception as e:  # pragma: no cover - the failure being guarded against
                errors.append(e)

        threads = [threading.Thread(target=ask) for _ in range(8)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(set(results), {'special://temp/{0}/42/4.jpg'.format(previews.FOLDER)})
        folder = os.path.join(self.store.folder, '42')
        self.assertEqual(os.listdir(folder), ['4.jpg'])

    def test_none_before_a_part_is_ready(self):
        self.assertIsNone(self.store.picture(7, 1000))

    def test_clear(self):
        self.store.picture(42, 0)
        self.store.clear()
        self.assertIsNone(self.store.picture(42, 0))
        self.assertEqual(os.listdir(self.store.folder), [])


if __name__ == '__main__':
    unittest.main()
