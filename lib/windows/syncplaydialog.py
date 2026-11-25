"""
Live-redrawing SyncPlay group dialog (Phase 9, J-D9): displays group status, playback controls,
and participants via dialog.SelectDialog, redraws automatically on controller version updates,
and closes when playback starts or the group ends.
"""

from lib import syncplay
from lib.i18n import T

from . import dialog


class SyncPlayDialog(dialog.SelectDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = kwargs.get('session')
        self.shownVersion = self.session.controller.version

    def tick(self):
        session = self.session
        if not session.active or session.controller.selection() is not None:
            self.choice = 'play' if session.active else None
            self.doClose()
            return
        if session.controller.version != self.shownVersion:
            self.shownVersion = session.controller.version
            position = self.optionsList.getSelectedPosition()
            self.options = syncplay.group_rows(session.controller)
            self.selectedIdx = max(0, min(position, len(self.options) - 1))
            self.showOptions()


def show(session):
    """Show the SyncPlay group dialog until it closes ('play', 'leave', or None on Back)."""

    def onChoice(key):
        if syncplay.choose(session, key):
            return syncplay.group_rows(session.controller), None
        return None

    group_name = session.controller.group_name
    heading = T(35355, 'Watch together · {0}').format(group_name) if group_name else T(35330, 'Watch together')
    w = SyncPlayDialog.open(
        heading=heading,
        options=syncplay.group_rows(session.controller),
        non_playback=True,
        menu=True,
        on_choice=onChoice,
        session=session,
    )
    choice = w.choice
    del w
    return choice
