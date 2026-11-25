# coding=utf-8
from __future__ import absolute_import

import datetime
import threading
import time

from kodi_six import xbmcgui

from lib import backgroundthread
from lib import util
from lib.util import T
from plexnet import plexapp
from . import busy
from . import grid_labels
from . import kodigui
from . import opener
from . import windowutils
from . import sidebar_model
from . import copies
from .dialog import showOptionsDialog
from .mixins.row_restore import RowRestoreMixin

class PersonDetailsTask(backgroundthread.Task):
    def __init__(self, role, callback):
        super(PersonDetailsTask, self).__init__()
        self.role = role
        self.callback = callback

    def run(self):
        if self.isCanceled():
            return
        details = self.role.getDetails()
        if not self.isCanceled():
            self.callback(details)


# How long the person screen waits for a server's answer (filmography, library presence) before
# going on without it, as search does (search.SEARCH_TIMEOUT).
SERVER_TIMEOUT = 10.0


def personServers(role):
    """The servers whose libraries a person's films are looked for in: the sidebar's, in its order,
    those answering - and the one the role came from, if it isn't among them (a library opened
    from the picker, say). Not Discover's server, which has no library."""
    servers = [server for server in sidebar_model.sidebarServers() if not server.offline and not server.gone]
    own = role.server
    known = plexapp.SERVERMANAGER.serversByUuid
    if own is not None and own.uuid in known and own.uuid not in [server.uuid for server in servers]:
        servers.append(own)
    return servers


def personKey(role):
    """The person's plex.tv key (tagKey): the same on every server, so it's what asks each one for
    them (checked live: Alan Rickman's on Animal and Oscar). A role without one has it looked up on
    its own server; None if that doesn't know either."""
    key = role.get('tagKey')
    if key:
        return key
    try:
        data = role.server.query('/library/people/{0}'.format(role.id))
        for directory in data.findall('Directory') if data is not None else ():
            if directory.get('tagKey'):
                return directory.get('tagKey')
    except Exception as e:
        util.DEBUG_LOG('Person: no tagKey for {0}: {1}', role.tag, e)
    return None


def libraryFilmography(server, key):
    """A person's movies and shows in server's libraries by its own credits:
    /library/people/{key}/media, whole - it only pages when asked (checked live, PMS 1.43.4). One in
    two libraries comes twice."""
    data = server.query('/library/people/{0}/media'.format(key))
    if data is None:
        return []
    elems = _dropRepeatedItems(list(data.findall('Video')) + list(data.findall('Metadata')) +
                               list(data.findall('Directory')))
    for elem in elems:
        _dropRepeatedVersions(elem)
    return libraryItems(server, elems, '/library/people')


def _dropRepeatedItems(elems):
    """Asked by the person's Discover id, /library/people/{key}/media lists an item once for each credit the person
    has on it: Ryan Reynolds is actor, writer and producer on Animal's Films copies of Deadpool 2, each of which came
    three times - the card read "Animal + 3" and Open from offered each version three times (live, 2026-10-24). One
    element per item (ratingKey), with any version a repeat has that the first hadn't."""
    kept, byKey = [], {}
    for elem in elems:
        key = elem.get('ratingKey')
        first = byKey.get(key) if key else None
        if first is None:
            if key:
                byKey[key] = elem
            kept.append(elem)
            continue
        have = set(media.get('id') for media in first.findall('Media'))
        for media in elem.findall('Media'):
            if media.get('id') not in have:
                first.append(media)
    return kept


def _dropRepeatedVersions(elem):
    """/library/people/{key}/media lists an item's versions (Media) once for each credit the person
    has on it: Stan Lee is cast and writer on Animal's Films copy of The Avengers, which came with
    its two versions twice, and Open from offered four (live, 2026-10-08). Repeats go, by id."""
    seen = set()
    for media in list(elem.findall('Media')):
        if media.get('id') in seen:
            elem.remove(media)
        else:
            seen.add(media.get('id'))


def libraryItems(server, elems, initpath):
    """The movies and shows among elems, server's XML elements, as items."""
    from plexnet import video
    items = []
    for elem in elems:
        kind = elem.get('type', '')
        if kind == 'movie':
            items.append(video.Movie(elem, initpath, server))
        elif kind == 'show':
            items.append(video.Show(elem, initpath, server))
    return items


def _askServers(servers, fn):
    """fn(server) on each server together, a thread each, for up to SERVER_TIMEOUT: {uuid: answer}
    for those that answered. One that fails or is too slow is left out."""
    answers = {}

    def ask(server):
        try:
            answers[server.uuid] = fn(server)
        except Exception as e:
            util.DEBUG_LOG('Person: no answer from {0}: {1}', server.name, e)

    threads = []
    for server in servers:
        thread = threading.Thread(target=ask, args=(server,), name='person.' + server.name)
        thread.daemon = True
        thread.start()
        threads.append(thread)
    started = time.time()
    for thread in threads:
        thread.join(max(0, SERVER_TIMEOUT - (time.time() - started)))
    return answers


class PersonFilmographyTask(backgroundthread.Task):
    """A person's movies and shows in every server's libraries (personServers()), in the servers'
    order - the person screen's filmography (the user, 2026-10-06: it showed only the library
    the person was opened from, and another server's films under "Not in Library"). Each server
    answers by its own credits (libraryFilmography()); plex.tv's Discover credits are gone with
    plex.tv (SM4K decision D10)."""

    def __init__(self, role, servers, callback):
        super(PersonFilmographyTask, self).__init__()
        self.role = role
        self.servers = servers
        self.callback = callback

    def run(self):
        if self.isCanceled():
            return
        key = personKey(self.role)
        # without a key, only the role's own server can be asked, by its own id
        servers = self.servers if key else [self.role.server]
        answers = _askServers(servers, lambda server: libraryFilmography(server, key or self.role.id))
        items = [item for server in servers for item in answers.get(server.uuid, ())]
        if not self.isCanceled():
            self.callback(items)


class PersonWindow(kodigui.ControlledWindow, windowutils.UtilMixin, windowutils.SidebarMixin, RowRestoreMixin):
    xmlFile = 'script-plex-person.xml'
    path = util.ADDON.getAddonInfo('path')
    theme = 'Main'
    res = '1080i'
    width = 1920
    height = 1080

    # The photo, as a 2:3 poster, 376x564 - the text column's height at its longest,
    # name to Filmography button (on request, 2026-10-08)
    THUMB_DIM = util.scaleResolution(376, 564)
    # a Recommended row's poster request (library_hubs.py's THUMB_POSTER_DIM), for the same card
    POSTER_DIM = util.scaleResolution(240, 360)

    FILMOGRAPHY_LIST_ID = 400
    FILTER_BUTTON_ID = 300
    # the click target over the bio, opening the whole of it (pre-play's SUMMARY_BUTTON_ID recipe)
    SUMMARY_BUTTON_ID = 310

    def __init__(self, *args, **kwargs):
        kodigui.ControlledWindow.__init__(self, *args, **kwargs)
        self.setProperty('loading', '1')
        self.role = kwargs.get('role')
        self.sectionId = kwargs.get('section_id')
        self.cameFromWatchlist = kwargs.get('from_watchlist', False)
        self.personDetails = None
        self.filmographyItems = []
        self.filmographyAllItems = []
        self.filmographyByGuid = {}
        # All (None), 'movie' or 'show' - kept through Back (restoreState())
        self.filmographyFilter = kwargs.get('filmography_filter')
        # (row list id, position) to focus once that row is filled: Back from a screen this one
        # opened (RowRestoreMixin)
        self.restoreFocus = kwargs.get('restore_focus')
        # the servers asked for the person's films (personServers())
        self.servers = []
        self.tasks = backgroundthread.Tasks()
        self.exitCommand = None
        self.initialized = False
        self.lastFocusID = None
        # hashed-orbiting-pizza.md Phase 2: None here means "build my own sectionList" (a
        # standalone/un-hosted open) - a hosted open (LibraryWindow._setupCurrent(), library.py)
        # overwrites this with the host's own sectionList object before onFirstInit() runs.
        self.sectionList = None

    def restoreRows(self):
        """The row Back returns focus to (RowRestoreMixin): the filmography, which opens a screen
        in this one's place (on request, 2026-10-07)."""
        return {self.FILMOGRAPHY_LIST_ID: self.filmographyListControl}

    def asyncRestoreRows(self):
        # filled by a background task's callback
        return (self.FILMOGRAPHY_LIST_ID,)

    def restoreDefaultFocusIds(self):
        # where the screen puts focus itself (focusDefault()): a Back restore takes it from these
        return (0, self.SUMMARY_BUTTON_ID, self.FILMOGRAPHY_LIST_ID)

    def restoreState(self):
        """RowRestoreMixin's row item, plus the filmography's filter (All, Movies, Shows), which the
        item's position is in."""
        state = RowRestoreMixin.restoreState(self)
        state['filmography_filter'] = self.filmographyFilter
        return state

    def paintInitialBackground(self):
        """No art, and no colours of a person's own: the neutral colour panel from the first
        frame (kodigui's setNeutralPanel())."""
        super(PersonWindow, self).paintInitialBackground()
        self.setNeutralPanel()

    def onFirstInit(self):
        self.setProperty('loading', '1')
        self.filmographyListControl = kodigui.ManagedControlList(self, self.FILMOGRAPHY_LIST_ID, 5)

        if self.sectionList is None:
            self.sectionList = kodigui.ManagedControlList(self, self.SECTION_LIST_ID, 15)
            self.buildSectionList()
        else:
            self.sectionList.newControl(self)
        self._selectActiveSection()
        self.displayServerAndUser()

        # Credits are looked up in the library of the server the role came from. A Discover
        # role's server is plex.tv's, which has no library: the first sidebar server answering
        # stands in.
        local_server = next((s for s in sidebar_model.sidebarServers() if not s.offline), None)
        role_server = self.role.server
        if local_server and not (role_server and role_server.uuid in plexapp.SERVERMANAGER.serversByUuid):
            self.role.server = local_server
        self.servers = personServers(self.role)

        self.setProperty('person.name', self.role.tag or '')
        self.setProperty('filmography.filter', self.filterLabel(self.filmographyFilter))
        self.focusDefault()

        self.fetchPersonDetails()
        self.fetchFilmography()

        self.initialized = True

    def onReInit(self):
        pass

    def onAction(self, action):
        # Hosted: the host sees the action first (kodigui.BaseWindow.routeActionToHost()).
        if self.routeActionToHost(action):
            return
        try:
            if action in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU):
                self.doClose()
                return
            if action == xbmcgui.ACTION_CONTEXT_MENU and self.getFocusId() == self.FILMOGRAPHY_LIST_ID:
                if self.openFrom():
                    return

        except Exception:
            util.ERROR()

        kodigui.ControlledWindow.onAction(self, action)

    def onClick(self, controlID):
        # Not live on its host yet, or any more (kodigui.BaseWindow.ignoresInput()).
        # Hosted: the host handles the sidebar's clicks (kodigui.BaseWindow.routeClickToHost()).
        if self.routeClickToHost(controlID):
            return
        if controlID == self.SECTION_LIST_ID:
            self.sectionClicked()
        elif controlID == self.FILTER_BUTTON_ID:
            self.filterButtonClicked()
        elif controlID == self.SUMMARY_BUTTON_ID:
            self.summaryButtonClicked()
        elif controlID == self.FILMOGRAPHY_LIST_ID:
            self.filmographyItemClicked()

    def onFocus(self, controlID):
        # Not live on its host yet, or any more (kodigui.BaseWindow.ignoresInput()).
        # first, whether or not this is live yet: the sidebar rows read it (SidebarMixin)
        self.trackSidebarFocus(controlID)
        if self.ignoresInput():
            return
        self.reselectActiveSection(controlID, self.lastFocusID)
        self.lastFocusID = controlID

    def doClose(self, **kw):
        self.tasks.kill()
        kodigui.ControlledWindow.doClose(self)

    def fetchPersonDetails(self):
        task = PersonDetailsTask(self.role, self.onPersonDetails)
        self.tasks.add(task)
        backgroundthread.BGThreader.addTask(task)

    def fetchFilmography(self):
        self.setProperty('loading', '1')
        task = PersonFilmographyTask(self.role, self.servers, self.onFilmography)
        self.tasks.add(task)
        backgroundthread.BGThreader.addTask(task)

    def onPersonDetails(self, details):
        if not details:
            util.DEBUG_LOG('PersonWindow: No details returned')
            return

        self.personDetails = details
        self.setProperty('person.name', details.get('name', ''))
        # pre-play's summary box (util.summaryForBox()): cut to its three lines, the whole of it
        # a click away (summaryButtonClicked())
        self.setProperty('person.summary', util.summaryForBox(details.get('summary', '')))
        born, died = self.lifeLines(details.get('birthDate', ''), details.get('deathDate', ''))
        self.setProperty('person.born', born)
        self.setProperty('person.died', died)
        # the bio may be the first focus now
        self.focusDefault()

        # the photo, cropped to the poster's shape by the template
        thumb = details.get('thumb', '')
        if thumb:
            self.setProperty('person.thumb', self.role.server.getImageTranscodeURL(thumb, *self.THUMB_DIM))

        tag_key = details.get('tagKey', '')
        if tag_key and not getattr(self.role, 'tagKey', None):
            self.role.tagKey = tag_key

    def focusDefault(self):
        """The screen's first focus (on request, 2026-10-08): the bio, not the poster row - focusing
        that slid the screen down to it as it opened. The bio shows once its property is set, which
        comes after the skin's own default is tried, so it's focused here, once visible
        (kodigui.waitForVisibility()). Only while nothing has focus: never away from where the user
        has gone, or from a Back restore (RowRestoreMixin)."""
        if self.getFocusId() or not self.getProperty('person.summary'):
            return
        kodigui.waitForVisibility(self.SUMMARY_BUTTON_ID, amount=1)
        self.setFocusId(self.SUMMARY_BUTTON_ID)

    def lifeLines(self, birthDate, deathDate):
        """The dates for the born and died lines under the credit types (the template puts "Born"
        and "Died" before them), '' for one without a date: "2 April 1975 (51)" - the age with the
        birth while they're alive - or "28 December 1922" then "12 November 2018 (95)" (on request,
        2026-10-08)."""
        age = self.calculateAge(birthDate, deathDate) if birthDate else None
        born = self.formatDate(birthDate) if birthDate else ''
        died = self.formatDate(deathDate) if deathDate else ''
        if age is not None:
            if died:
                died = u'{0} ({1})'.format(died, age)
            elif born:
                born = u'{0} ({1})'.format(born, age)
        return born, died

    def summaryButtonClicked(self):
        """The whole bio, as pre-play's summary box opens the whole summary."""
        from . import info
        details = self.personDetails or {}
        if details.get('summary'):
            info.showSummary(details.get('name') or self.role.tag, details.get('summary'))

    def onFilmography(self, items):
        """Every server's movies and shows for the person (PersonFilmographyTask). The filter button
        shows only for a row of both (filmography.mixed; on request, 2026-10-08) - with one kind
        the filter is All, whatever a Back restore kept."""
        types = set(getattr(item, 'type', None) for item in items)
        mixed = 'movie' in types and 'show' in types
        if not mixed and self.filmographyFilter:
            self.filmographyFilter = None
            self.setProperty('filmography.filter', self.filterLabel(None))
        self.setBoolProperty('filmography.mixed', mixed)
        self.setProperty('loading', '')
        self.filmographyAllItems = items
        self.applyFilmographyFilter()

    def createFilmographyListItem(self, item):
        title = item.title if hasattr(item, 'title') else item.get('title', '')
        year = ''
        if hasattr(item, 'year'):
            year = str(item.year) if item.year else ''

        thumb = ''
        if hasattr(item, 'thumb') and item.thumb:
            thumb = item.thumb.asTranscodedImageURL(*self.POSTER_DIM)
        elif hasattr(item, 'defaultThumb') and item.defaultThumb:
            thumb = item.defaultThumb.asTranscodedImageURL(*self.POSTER_DIM)

        mli = kodigui.ManagedListItem(title, thumbnailImage=thumb, data_source=item)
        # under the card in the movie grid's way, as its 'year' (script-plex-person.xml.tpl): after the
        # edition, if it has one (grid_labels.editionLine())
        mli.setProperty('year', grid_labels.editionLine(item, year) if hasattr(item, 'get') else year)

        item_type = item.type if hasattr(item, 'type') else item.TYPE if hasattr(item, 'TYPE') else ''
        mli.setProperty('media.type', item_type)

        # the card's watched tick, unwatched count and progress pill, as a Recommended row's
        # (library_hubs.py)
        played = bool(getattr(item, 'isPlayed', False))
        mli.setBoolProperty('watched', played)
        if item_type == 'show' and not played:
            mli.setProperty('unwatched.count', str(item.unViewedLeafCount))
            mli.setBoolProperty('unwatched.count.large', item.unViewedLeafCount > 999)
        mli.setProperty('progress', util.getProgressImage(item))

        # Where it is, on every card (on request, 2026-10-08): the server of the copy the card opens,
        # and for a title in more than one library or on more than one server how many others -
        # Search's line for one with copies, "Animal + 1" (search.placesLine()). Unlike Search,
        # the server shows on a one-server account too.
        server = getattr(item.server, 'name', '')
        others = len(self.copiesOf(item)) - 1
        mli.setProperty('copies', u'{0} + {1}'.format(server, others) if others else server)

        mli.setProperty('thumb.fallback', 'script.plex/thumb_fallbacks/{0}.png'.format(
            item_type in ('show', 'season', 'episode') and 'show' or 'movie'))

        return mli

    def fillFilmography(self):
        listItems = []
        for item in self.filmographyItems:
            mli = self.createFilmographyListItem(item)
            listItems.append(mli)

        self.filmographyListControl.reset()
        self.filmographyListControl.addItems(listItems)
        self.setProperty('filmography.count', str(len(self.filmographyItems)))
        # a Back restore to the film or show opened, if there is one (RowRestoreMixin)
        if self._restoreRowFocus(filled=self.FILMOGRAPHY_LIST_ID):
            return
        # nothing else to focus (no bio): the row, as the screen's first focus was
        if not self.getFocusId() and not self.getProperty('person.summary') and self.filmographyItems:
            kodigui.waitForVisibility(self.FILMOGRAPHY_LIST_ID, amount=1)
            self.setFocusId(self.FILMOGRAPHY_LIST_ID)

    def filterOptions(self):
        return [
            {'key': None,    'display': T(32345, 'All')},
            {'key': 'movie', 'display': T(32348, 'Movies')},
            {'key': 'show',  'display': T(32350, 'Shows')},
        ]

    def filterLabel(self, key):
        for option in self.filterOptions():
            if option['key'] == key:
                return option['display']
        return T(32345, 'All')

    def filterButtonClicked(self):
        """The filmography's filter (All, Movies, Shows): the option picker (dialog.SelectDialog), as the library
        grid's Filter - the one showing ticked. It was a dropdown beside the button."""
        options = self.filterOptions()
        current = next((i for i, o in enumerate(options) if o['key'] == self.filmographyFilter), 0)
        choice = showOptionsDialog(T(35212, 'Filter'), [(i, o['display']) for i, o in enumerate(options)],
                                   non_playback=True, menu=True, selected_idx=current,
                                   dialog_props=getattr(self, 'carriedProps', None))
        if choice is None:
            return
        self.filmographyFilter = options[choice]['key']
        self.setProperty('filmography.filter', options[choice]['display'])
        self.applyFilmographyFilter()

    def applyFilmographyFilter(self):
        """The filmography as the filter has it (All, Movies, Shows): from what's loaded, one entry
        for copies of a film on several servers or in several libraries (groupFilmographyByGuid()),
        newest first (on request, 2026-10-08 - it was by title): by release date, or year without
        one, those with neither last, and by title within a date."""
        items = self.filmographyAllItems
        if self.filmographyFilter:
            items = [item for item in items if getattr(item, 'type', None) == self.filmographyFilter]
        self.filmographyItems, self.filmographyByGuid = self.groupFilmographyByGuid(items)
        self.filmographyItems.sort(key=lambda item: u'{0}'.format(item.get('titleSort') or item.get('title')).lower())
        # Stable, reversed too, so titles stay A-Z within a date. A bare year sorts before that
        # year's dates.
        self.filmographyItems.sort(key=lambda item: u'{0}'.format(item.get('originallyAvailableAt') or item.get('year')),
                                   reverse=True)
        self.fillFilmography()

    def filmographyItemClicked(self, item=None):
        """Open a film or show - the copy the pick rule chose for its card (groupFilmographyByGuid()),
        or item (openFrom()). It used to ask which copy first, every time (the user, 2026-10-07)."""
        if item is None:
            mli = self.filmographyListControl.getSelectedItem()
            if not mli or not mli.dataSource:
                return
            item = mli.dataSource
        self.processCommand(opener.open(item, context=self,
                                        entry_section_id=self.sectionId,
                                        entry_from_watchlist=self.cameFromWatchlist))

    def copiesOf(self, item):
        """The copies of item's title in the filmography, the one shown first; [item] if alone."""
        key = copies.sameTitleKey(item)
        found = self.filmographyByGuid.get(key) if key is not None else None
        return [item] + [copy for copy in found if copy is not item] if found else [item]

    def openFrom(self):
        """The filmography's context menu: Open from, every version of every copy of the focused
        title (copies.versionEntries()), the one picked opened with that version chosen - as Search
        does. True if it was one."""
        mli = self.filmographyListControl.getSelectedItem()
        if not mli or not mli.dataSource:
            return False
        entries = copies.versionEntries(self.copiesOf(mli.dataSource))
        if len(entries) < 2:
            return False
        picked = copies.chooseFrom(entries, len(plexapp.SERVERMANAGER.getServers()) > 1)
        if picked is not None:
            self.filmographyItemClicked(picked[0])
        return True

    def sidebarActiveSection(self, entries):
        # A person isn't tied to one library section (their filmography can span several), but
        # the screen the role was clicked from is: self.sectionId carries it through
        # (RolesMixin.roleSectionId()). None (nothing highlighted) when reached some other way,
        # e.g. Search.
        return sidebar_model.matchSection(entries, self.sectionId, self.cameFromWatchlist, server=self.role.server)

    def formatDate(self, dateStr):
        if not dateStr:
            return ''
        try:
            parts = dateStr.split('-')
            if len(parts) == 3:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                # "2 April 1975": day, month, year, no commas, the day unpadded (on request,
                # 2026-10-08) - built, as strftime can't drop the day's zero portably
                return u'{0} {1} {2}'.format(day, datetime.date(year, month, day).strftime('%B'), year)
        except (ValueError, IndexError):
            pass
        return dateStr

    def calculateAge(self, birthDateStr, deathDateStr=None):
        if not birthDateStr:
            return None
        try:
            parts = birthDateStr.split('-')
            if len(parts) != 3:
                return None
            birthDate = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))

            if deathDateStr:
                parts = deathDateStr.split('-')
                endDate = datetime.date(int(parts[0]), int(parts[1]), int(parts[2])) if len(parts) == 3 else datetime.date.today()
            else:
                endDate = datetime.date.today()

            age = endDate.year - birthDate.year
            if (endDate.month, endDate.day) < (birthDate.month, birthDate.day):
                age -= 1
            return age
        except (ValueError, IndexError):
            return None

    def groupFilmographyByGuid(self, items):
        """One entry per title (copies.sameTitleKey(): a film in two libraries or on two servers is
        one) and {key: its copies}. The one shown, and opened, is the one Search would open
        (copies.pickCopy()): part-watched, then the library highest in the sidebar, then the best
        quality - it was the highest bitrate's (the user, 2026-10-07)."""
        byKey, order = {}, []
        for item in items:
            key = copies.sameTitleKey(item)
            if key is None:
                order.append([item])
            elif key in byKey:
                byKey[key].append(item)
            else:
                byKey[key] = [item]
                order.append(byKey[key])
        if not any(len(group) > 1 for group in order):
            return [group[0] for group in order], byKey
        serverOrder = dict((server.uuid, i) for i, server in enumerate(getattr(self, 'servers', None) or []))
        positions = sidebar_model.entryPositions(sidebar_model.loadNavSettings())
        return [group[0] if len(group) == 1 else copies.pickCopy(group, serverOrder, positions) for group in order], byKey


class ActorWindow(PersonWindow):
    pass


class DirectorWindow(PersonWindow):
    pass
