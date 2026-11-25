"""
Home (a kodigui.MultiWindow) closed before its first view was set up - a skin-reload restart as SiloMod starts
(windowutils.restartAfterSkinReload(), seen in the box's log) - crashed twice with "'NoneType' object has no
attribute 'doClose'": in doClose() and at the end of _open(). Closing it then only marks it closed.
"""

from kodienv import ENV

ENV.abort_requested = True
from lib.windows import kodigui

from .base import KodiTestCase


class MultiWindowCloseTest(KodiTestCase):
    def window(self):
        mw = kodigui.MultiWindow.__new__(kodigui.MultiWindow)
        mw._current = None  # no view set up yet
        mw._next = None
        mw._allClosed = False
        mw.onCloseSignal = lambda *args, **kwargs: None
        return mw

    def test_closing_before_the_first_view(self):
        mw = self.window()

        mw.doClose()

        self.assertTrue(mw._allClosed)

    def test_opening_after_an_early_close_returns(self):
        mw = self.window()
        mw.doClose()

        mw._open()  # the view loop is skipped, and its clean-up finds no view
