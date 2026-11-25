# coding=utf-8
"""
A watch party's screen (decision D26): what's up next or playing, who's in the party and who's ready, and what this
member can do - the host starts what's up next for everyone, lets guests play and pause, or ends the party; a guest
says it's ready or leaves. The option picker (dialog.SelectDialog), redrawn as the room changes and closed by itself
once the room plays (watch_together.runParty() plays it). Its rows and choices are watch_together's.
"""
from __future__ import absolute_import

from lib import watch_together
from lib.i18n import T
from . import dialog


class PartyDialog(dialog.SelectDialog):
    def __init__(self, *args, **kwargs):
        dialog.SelectDialog.__init__(self, *args, **kwargs)
        self.session = kwargs.get('session')
        self.shownVersion = self.session.party.version

    def tick(self):
        session = self.session
        if not session.active or session.party.selection() is not None:
            # the room plays (or the party is over): runParty() takes it from here
            self.choice = 'play' if session.active else None
            self.doClose()
            return
        if session.party.version != self.shownVersion:
            self.shownVersion = session.party.version
            position = self.optionsList.getSelectedPosition()
            self.options = watch_together.partyRows(session.party)
            self.selectedIdx = max(0, min(position, len(self.options) - 1))
            self.showOptions()


def show(session):
    """The party screen, until it closes: 'play' (the room plays), 'end' or 'leave', or None (Back)."""

    def onChoice(key):
        if watch_together.choose(session, key):
            return watch_together.partyRows(session.party), None
        return None

    heading = T(35290, 'Watch party · {0}').format(session.party.snapshot.get('code') or '')
    w = PartyDialog.open(
        heading=heading,
        options=watch_together.partyRows(session.party),
        non_playback=True,
        menu=True,
        on_choice=onChoice,
        session=session,
    )
    choice = w.choice
    del w
    return 'play' if choice == 'start' else choice
