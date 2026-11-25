"""
Watch together via Jellyfin SyncPlay (Phase 9, J-D9): keeps Kodi's player in step with a
Jellyfin SyncPlay group using jellynet.syncplay's REST endpoints and /socket WebSocket.
"""

import functools
import math
import threading
import time

from jellynet import syncplay as jf_syncplay
from jellynet.errors import JellyfinError
from kodi_six import xbmc

from lib import util
from lib.i18n import T

ECHO = 1.5  # seconds after a remote command during which local player events are ignored
IN_STEP = 1.0  # seconds within which a command does not seek
MAX_HOLD = 15.0  # seconds at most held still or played up to the group
SEEK_LEAD, MAX_LEAD = 2.0, 12.0  # adaptive seek lead for keyframe-aligned players
SETTLE = 1.0  # seconds a seek takes to land
STEPS = 5  # convergence adjustments at most per command

STOP_FOR_EVERYONE, LEAVE_GROUP, KEEP_WATCHING = 0, 1, 2

# Active SyncPlay session (one at a time)
CURRENT = None


def _timer(delay, fn):
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()


class SyncPlayController:
    """Coordinates a Jellyfin SyncPlay group's state, queue, and Kodi player playback."""

    def __init__(self, group, player, client=None, link=None, later=_timer):
        group = dict(group or {})
        self.group_id = str(group.get('GroupId') or '')
        self.group_name = str(group.get('GroupName') or '')
        self.state = str(group.get('State') or 'Idle')
        self.participants = [str(p) for p in (group.get('Participants') or ()) if p]
        self.playlist = []
        self.playing_item_index = -1
        self.start_position_ticks = 0
        self.is_playing = False
        self.queue_updated_at = None
        self.queue_revision = 0
        self.version = 0
        self.ended = None
        self.title_source = None
        self._titles = {}

        self.player = player
        self.client = client
        self.link = link
        self.later = later
        self.clock = time.time
        self.sleep = time.sleep
        self.last_command = None
        self.last_target = None
        self._lead = SEEK_LEAD
        self._token = 0
        self._converging = False
        self._echo_until = 0.0
        self._stop = threading.Event()
        self.changed = threading.Event()

    def current_queue_item(self):
        if 0 <= self.playing_item_index < len(self.playlist):
            return self.playlist[self.playing_item_index]
        return None

    @property
    def selected_item_id(self):
        item = self.current_queue_item()
        return str(item['ItemId']) if item and item.get('ItemId') else None

    @property
    def selected_playlist_item_id(self):
        item = self.current_queue_item()
        return str(item['PlaylistItemId']) if item and item.get('PlaylistItemId') else ''

    def title_for(self, item_id):
        """Return a display title for item_id (fetched once via title_source and cached)."""
        if not item_id:
            return ''
        key = str(item_id)
        if key not in self._titles:
            try:
                self._titles[key] = self.title_source(key) if self.title_source else ''
            except JellyfinError:
                return ''
        return self._titles[key]

    def adopt_queue(self, item_ids, playing_position=0, start_seconds=0.0):
        """Adopt a locally submitted queue immediately before the server's PlayQueue broadcast arrives."""
        self.playlist = [{'ItemId': str(item_id), 'PlaylistItemId': ''} for item_id in item_ids]
        self.playing_item_index = int(playing_position) if self.playlist else -1
        self.start_position_ticks = jf_syncplay.seconds_to_ticks(start_seconds)
        self.is_playing = bool(self.playlist)
        self.state = 'Waiting' if self.playlist else 'Idle'
        self.queue_updated_at = None
        self.queue_revision += 1
        self.version += 1
        self.changed.set()

    def mark_stopped(self):
        """Mark the group idle after a local Stop-for-everyone action."""
        self.state = 'Idle'
        self.is_playing = False
        self.version += 1
        self.changed.set()

    def selection(self):
        """Return (item_id, playlist_item_id, start_seconds) while active with a queued item, else None."""
        item = self.current_queue_item()
        if item is None or self.state == 'Idle':
            return None
        item_id = str(item.get('ItemId') or '')
        if not item_id:
            return None
        playlist_item_id = str(item.get('PlaylistItemId') or '')
        position = jf_syncplay.ticks_to_seconds(self.start_position_ticks)
        if self.state == 'Playing' and self.is_playing and self.queue_updated_at is not None and self.link is not None:
            position += max(0.0, self.link.server_time() - self.queue_updated_at)
        return item_id, playlist_item_id, position

    def _position(self):
        position = self.player.position()
        return round(max(0.0, float(position)), 3) if math.isfinite(position) else 0.0

    def _group_item_playing(self):
        return self.selected_item_id is not None and self.player.playingKey() == self.selected_item_id

    def _echo(self):
        return self._converging or self.clock() < self._echo_until

    def _over(self):
        return self.ended is not None or self._stop.is_set()

    def stop(self):
        self._stop.set()
        self.changed.set()

    # --- WebSocket group updates ---

    def on_group_update(self, update):
        if not isinstance(update, dict):
            return
        kind = update.get('Type')
        data = update.get('Data')

        if kind == 'GroupJoined' and isinstance(data, dict):
            self.group_id = str(data.get('GroupId') or self.group_id)
            self.group_name = str(data.get('GroupName') or self.group_name)
            if data.get('State'):
                self.state = str(data['State'])
            if isinstance(data.get('Participants'), list):
                self.participants = [str(p) for p in data['Participants'] if p]
            self.version += 1
            self.changed.set()
        elif kind == 'UserJoined' and data:
            name = str(data)
            if name not in self.participants:
                self.participants.append(name)
            self.version += 1
            self.changed.set()
        elif kind == 'UserLeft' and data:
            name = str(data)
            self.participants = [p for p in self.participants if p != name]
            self.version += 1
            self.changed.set()
        elif kind == 'PlayQueue' and isinstance(data, dict):
            self._apply_play_queue(data)
        elif kind == 'StateUpdate' and isinstance(data, dict):
            new_state = str(data.get('State') or self.state)
            self.state = new_state
            if new_state == 'Playing':
                self.is_playing = True
            elif new_state in ('Idle', 'Paused'):
                self.is_playing = False
            self.version += 1
            self.changed.set()
        elif kind == 'GroupLeft':
            self._end('group_left', T(35352, 'Left the Watch together group'))
        elif kind == 'NotInGroup':
            self._end('not_in_group', T(35352, 'Left the Watch together group'))
        elif kind == 'GroupDoesNotExist':
            self._end('group_does_not_exist', T(35353, 'The Watch together group ended'))
        elif kind == 'LibraryAccessDenied':
            self._end('library_access_denied', T(35354, "You don't have access to this library"))

    def _apply_play_queue(self, data):
        prev_item_id = self.selected_item_id
        prev_pli = self.selected_playlist_item_id
        raw_playlist = data.get('Playlist')
        if isinstance(raw_playlist, list):
            self.playlist = [
                {
                    'ItemId': str(entry.get('ItemId') or ''),
                    'PlaylistItemId': str(entry.get('PlaylistItemId') or ''),
                }
                for entry in raw_playlist
                if isinstance(entry, dict) and entry.get('ItemId')
            ]
        raw_index = data.get('PlayingItemIndex')
        if raw_index is not None:
            self.playing_item_index = int(raw_index)
        elif not self.playlist:
            self.playing_item_index = -1
        if 'StartPositionTicks' in data and data.get('StartPositionTicks') is not None:
            self.start_position_ticks = int(data['StartPositionTicks'])
        if 'IsPlaying' in data:
            self.is_playing = bool(data['IsPlaying'])
        self.queue_updated_at = jf_syncplay.parse_iso(data.get('LastUpdate'))

        if self.current_queue_item() is None:
            self.state = 'Idle'
            self.is_playing = False
        elif self.state == 'Idle':
            self.state = 'Playing' if self.is_playing else 'Paused'

        new_item_id = self.selected_item_id
        new_pli = self.selected_playlist_item_id
        if new_item_id != prev_item_id or (prev_pli and new_pli != prev_pli):
            self.queue_revision += 1
        self.version += 1
        self.changed.set()

    # --- WebSocket transport commands ---

    def on_command(self, command):
        if self._over() or not isinstance(command, dict):
            return
        when = jf_syncplay.parse_iso(command.get('When'))
        delay = 0.0 if when is None or self.link is None else when - self.link.server_time()
        self.later(max(0.0, delay), lambda: self._apply_command(command))

    def _apply_command(self, command):
        if self._over():
            return
        action = command.get('Command')
        if action == 'Stop':
            self._token += 1
            self.state = 'Idle'
            self.is_playing = False
            self.last_command = command
            self.last_target = None
            self.version += 1
            self.changed.set()
            if self._group_item_playing():
                self._echo_until = self.clock() + ECHO
                self.player.stop()
            return

        if 'PositionTicks' in command and command.get('PositionTicks') is not None:
            self.start_position_ticks = int(command['PositionTicks'])
        if action == 'Unpause':
            self.state = 'Playing'
            self.is_playing = True
        elif action == 'Pause':
            self.state = 'Paused'
            self.is_playing = False
        self.version += 1
        self.changed.set()

        if not self._group_item_playing():
            self.last_command = command
            self.last_target = None
            return

        is_seek = action == 'Seek'
        if action == 'Unpause':
            playing = True
        elif action == 'Pause':
            playing = False
        elif is_seek:
            playing = self.state == 'Playing' and self.is_playing
        else:
            return

        raw_ticks = command.get('PositionTicks')
        base = self._position() if raw_ticks is None else jf_syncplay.ticks_to_seconds(raw_ticks)
        execute_at = jf_syncplay.parse_iso(command.get('When'))

        def group_position():
            if not playing or execute_at is None or self.link is None:
                return base
            return base + max(0.0, self.link.server_time() - execute_at)

        self._token += 1
        token = self._token
        self.last_command = command
        self.last_target = base if (is_seek and not playing) else group_position()
        self._converge(group_position, playing, is_seek, token)
        if token == self._token and self.state == 'Waiting':
            self.report_ready(playlist_item_id=command.get('PlaylistItemId'))
        util.LOG('SyncPlay: ran {0} at {1:.1f}', action, self.player.position())

    def _converge(self, group_position, playing, is_seek, token):
        """Converge Kodi's keyframe-snapping player onto group_position and match playing/paused state."""
        self._converging = True
        try:
            if is_seek and abs(self.player.position() - group_position()) > IN_STEP:
                self._seek_to(group_position(), playing)
            for _ in range(STEPS):
                if token != self._token:
                    return
                drift = self.player.position() - group_position()
                if abs(drift) <= IN_STEP:
                    break
                if playing and 0 < drift <= MAX_HOLD:
                    self.player.setPaused(True)
                    self._wait(drift, token)
                    self.player.setPaused(False)
                elif not playing and -MAX_HOLD <= drift < 0:
                    self._play_up_to(group_position(), token)
                else:
                    self._seek_to(group_position(), playing)
            if token == self._token and self.player.isPaused() == playing:
                self.player.setPaused(not playing)
        finally:
            if token == self._token:
                self._converging = False
                self._echo_until = self.clock() + ECHO

    def _seek_to(self, target, playing):
        aim = target + (self._lead if playing else 0.0)
        self.player.seek(aim)
        self.sleep(SETTLE)
        if playing:
            short = (target + SETTLE) - self.player.position()
            if short > IN_STEP:
                self._lead = min(MAX_LEAD, self._lead + short)

    def _play_up_to(self, target, token):
        self.player.setPaused(False)
        waited, limit = 0.0, (target - self.player.position()) + 3.0
        while self.player.position() < target - 0.1 and waited < limit and token == self._token:
            self.sleep(0.1)
            waited += 0.1
        self.player.setPaused(True)

    def _wait(self, seconds, token):
        waited = 0.0
        while waited < seconds and token == self._token:
            step = min(0.1, seconds - waited)
            self.sleep(step)
            waited += step

    def _now_iso(self):
        when = self.link.server_time() if self.link is not None else self.clock()
        return jf_syncplay.iso_now(when)

    def report_ready(self, playlist_item_id=None):
        """Send POST /SyncPlay/Ready for the currently playing item."""
        if self.client is None or self._over():
            return
        pli = str(playlist_item_id or self.selected_playlist_item_id or '')
        try:
            jf_syncplay.ready(self.client, self._now_iso(), self._position(), not self.player.isPaused(), pli)
        except JellyfinError as e:
            util.LOG('SyncPlay: Ready failed: {0}', e)

    def report_buffering(self, playlist_item_id=None):
        """Send POST /SyncPlay/Buffering for the currently playing item."""
        if self.client is None or self._over():
            return
        pli = str(playlist_item_id or self.selected_playlist_item_id or '')
        try:
            jf_syncplay.buffering(self.client, self._now_iso(), self._position(), not self.player.isPaused(), pli)
        except JellyfinError as e:
            util.LOG('SyncPlay: Buffering failed: {0}', e)

    # --- Local Kodi player callbacks ---

    def on_local_pause(self, paused):
        if self._over() or self._echo() or not self._group_item_playing() or self.client is None:
            return
        try:
            if paused:
                jf_syncplay.pause(self.client)
            else:
                jf_syncplay.unpause(self.client)
        except JellyfinError as e:
            util.LOG('SyncPlay: pause/unpause failed: {0}', e)

    def on_local_seek(self, seconds):
        if self._over() or self._echo() or not self._group_item_playing() or self.client is None:
            return
        try:
            jf_syncplay.seek(self.client, max(0.0, float(seconds)))
        except JellyfinError as e:
            util.LOG('SyncPlay: seek failed: {0}', e)

    def on_local_start(self):
        if self._over() or self._echo() or not self._group_item_playing() or self.client is None:
            return
        self.report_ready()

    def on_local_stop(self):
        if self._over() or self._echo() or not self._group_item_playing():
            return

    def _end(self, reason, text):
        self.ended = reason
        if self._group_item_playing():
            self._echo_until = self.clock() + ECHO
            self.player.stop()
        self.player.notify(text)
        self._stop.set()
        self.version += 1
        self.changed.set()


class KodiPlayer(xbmc.Player):
    """Kodi player adapter forwarding local transport events to a SyncPlayController."""

    def __init__(self, server):
        super().__init__()
        self.server = server
        self.controller = None

    def playingKey(self):
        from lib import player

        video = player.PLAYER.video if self.isPlayingVideo() else None
        if video is None or getattr(video, 'server', None) is not self.server:
            return None
        return str(video.ratingKey)

    def position(self):
        try:
            return self.getTime()
        except RuntimeError:
            return 0.0

    def isPaused(self):
        return bool(xbmc.getCondVisibility('Player.Paused'))

    def seek(self, seconds):
        self.seekTime(max(0.0, seconds))

    def setPaused(self, paused):
        if self.isPaused() != paused:
            self.pause()

    def notify(self, text):
        util.showNotification(text, header=T(35330, 'Watch together'))

    def onAVStarted(self):
        if self.controller:
            self.controller.on_local_start()

    def onPlayBackPaused(self):
        if self.controller:
            self.controller.on_local_pause(True)

    def onPlayBackResumed(self):
        if self.controller:
            self.controller.on_local_pause(False)

    def onPlayBackSeek(self, time, seekOffset):
        if self.controller:
            self.controller.on_local_seek(time / 1000.0)

    def onPlayBackStopped(self):
        if self.controller:
            self.controller.on_local_stop()


class SyncPlaySession:
    """Holds an active SyncPlay group: controller, Kodi player hooks, and WebSocket link."""

    def __init__(self, server, group, socket_factory=None):
        self.server = server
        self.player = KodiPlayer(server)
        self.controller = SyncPlayController(group, self.player, client=server.client)
        self.player.controller = self.controller
        kwargs = {'socket_factory': socket_factory} if socket_factory is not None else {}
        self.link = jf_syncplay.SyncPlaySocket(
            server.client,
            on_command=self.controller.on_command,
            on_group_update=self.controller.on_group_update,
            **kwargs,
        )
        self.controller.link = self.link
        self.controller.title_source = functools.partial(jf_syncplay.title_of, server.client)

    def begin(self):
        self.link.start()

    def leave(self):
        """Stop the controller and WebSocket, detach Kodi player hooks, and POST /SyncPlay/Leave."""
        global CURRENT
        self.controller.stop()
        self.link.stop()
        self.player.controller = None
        if self.controller.ended is None:
            try:
                jf_syncplay.leave_group(self.server.client)
            except JellyfinError as e:
                util.LOG('SyncPlay: leaving group failed: {0}', e)
        if CURRENT is self:
            CURRENT = None

    @property
    def active(self):
        return self.controller.ended is None and self.link.ended is None


def current(server=None):
    """Return the active SyncPlaySession (on server, if given), or None."""
    if CURRENT is None or not CURRENT.active:
        return None
    if server is not None and CURRENT.server is not server:
        return None
    return CURRENT


def leaveCurrent():
    """Leave any active SyncPlay group when Home closes for another session."""
    if CURRENT is not None:
        CURRENT.leave()


def _begin(server, group):
    global CURRENT
    if CURRENT is not None:
        CURRENT.leave()
    CURRENT = SyncPlaySession(server, group)
    CURRENT.begin()
    return CURRENT


def _default_group_name(server):
    username = getattr(getattr(server, 'client', None), 'username', None) or ''
    if username:
        return T(35356, "{0}'s group").format(username)
    return T(35330, 'Watch together')


def player_for(window):
    """Return play(video, seconds) launching videoplayer over window and blocking while it plays."""

    def play(video, seconds):
        from lib.windows import videoplayer

        video.set('viewOffset', int(seconds * 1000))
        window.processCommand(videoplayer.play(video=video, resume=seconds >= 1, context=window))

    return play


def start_with_item(video, play):
    """Create a SyncPlay group if needed, set video as the new queue, and run the group loop."""
    server = video.getServer() if hasattr(video, 'getServer') else video.server
    session = current(server)
    item_id = str(video.ratingKey)
    try:
        if session is None:
            group = jf_syncplay.create_group(server.client, _default_group_name(server))
            session = _begin(server, group)
        jf_syncplay.set_new_queue(server.client, [item_id])
        session.controller.adopt_queue([item_id], playing_position=0, start_seconds=0.0)
    except JellyfinError as e:
        util.LOG('SyncPlay: starting item failed: {0}', e)
        util.showNotification(T(35349, "Couldn't start Watch together"), header=T(35330, 'Watch together'))
        return
    run_group(session, play)


def _pick_group(heading, options):
    from lib.windows.dialog import showOptionsDialog

    return showOptionsDialog(heading, options, non_playback=True, menu=True)


def _ask_group_name(default_name):
    from kodi_six import xbmcgui

    return xbmcgui.Dialog().input(T(35332, 'Group name'), defaultt=default_name)


def open_syncplay(server, play, pick_group=None, ask_name=None):
    """Open the active SyncPlay group or list existing groups with an option to create one."""
    session = current(server)
    if session is not None:
        run_group(session, play)
        return

    try:
        groups = jf_syncplay.list_groups(server.client)
    except JellyfinError as e:
        util.LOG('SyncPlay: listing groups failed: {0}', e)
        util.showNotification(T(35351, "Couldn't load SyncPlay groups"), header=T(35330, 'Watch together'))
        return

    options = []
    groups_by_id = {}
    for entry in groups:
        gid = str(entry.get('GroupId') or '')
        if not gid:
            continue
        groups_by_id[gid] = entry
        name = str(entry.get('GroupName') or gid)
        count = len(entry.get('Participants') or ())
        subtitle = T(35346, '1 participant') if count == 1 else T(35347, '{0} participants').format(count)
        options.append((f'join:{gid}', (name, subtitle), False))
    options.append(('create', T(35331, 'Create group'), False))

    choice = (pick_group or _pick_group)(T(35330, 'Watch together'), options)
    if not choice:
        return
    if choice == 'create':
        name = (ask_name or _ask_group_name)(_default_group_name(server))
        if not name or not name.strip():
            return
        try:
            group = jf_syncplay.create_group(server.client, name.strip())
        except JellyfinError as e:
            util.LOG('SyncPlay: creating group failed: {0}', e)
            util.showNotification(T(35349, "Couldn't start Watch together"), header=T(35330, 'Watch together'))
            return
        session = _begin(server, group)
        run_group(session, play)
    elif isinstance(choice, str) and choice.startswith('join:'):
        group_id = choice.split(':', 1)[1]
        try:
            jf_syncplay.join_group(server.client, group_id)
        except JellyfinError as e:
            util.LOG('SyncPlay: joining group failed: {0}', e)
            util.showNotification(T(35350, "Couldn't join the group"), header=T(35330, 'Watch together'))
            return
        session = _begin(server, groups_by_id.get(group_id) or {'GroupId': group_id})
        run_group(session, play)


def _screen(session):
    from lib.windows import syncplaydialog

    return syncplaydialog.show(session)


def _ask_stopped():
    from lib.windows import optionsdialog

    choice = optionsdialog.show(
        T(35330, 'Watch together'),
        T(35348, 'You stopped watching.'),
        T(35334, 'Stop for everyone'),
        T(35333, 'Leave group'),
        T(35335, 'Keep watching'),
    )
    return KEEP_WATCHING if choice is None else choice


def run_group(session, play, screen=None, ask=None):
    """Main-thread SyncPlay loop: show SyncPlayDialog while idle, play queued items while active."""
    screen = screen or _screen
    ask = ask or _ask_stopped
    server = session.server
    while session.active:
        selection = session.controller.selection()
        if selection is None:
            how = screen(session)
            if how == 'leave':
                session.leave()
                return
            if how is None and session.controller.selection() is None:
                return
            continue
        item_id, _playlist_item_id, seconds = selection
        revision = session.controller.queue_revision
        video = server.getObject('/library/metadata/' + item_id)
        if video is None:
            session.controller.mark_stopped()
            continue
        play(video, seconds)
        if not session.active:
            return
        if session.controller.selection() is None or session.controller.queue_revision != revision:
            continue
        choice = ask()
        if choice == LEAVE_GROUP:
            session.leave()
            return
        if choice == STOP_FOR_EVERYONE:
            try:
                jf_syncplay.stop(server.client)
            except JellyfinError as e:
                util.LOG('SyncPlay: stopping for everyone failed: {0}', e)
            session.controller.mark_stopped()


def group_rows(controller):
    """Build SelectDialog rows for SyncPlayDialog from controller's current state."""
    from lib.windows.dialog import optionGroup

    selected = controller.selected_item_id
    active_item = selected is not None and controller.state != 'Idle'
    if not active_item:
        rows = [
            (
                'status',
                (T(35341, 'Nothing playing yet'), T(35342, 'Pick something with Watch together on its screen')),
                False,
            )
        ]
    else:
        title = controller.title_for(selected) or selected
        if controller.state == 'Paused':
            label = T(35344, 'Paused: {0}').format(title)
        elif controller.state == 'Waiting':
            label = T(35345, 'Waiting: {0}').format(title)
        else:
            label = T(35343, 'Playing: {0}').format(title)
        rows = [('status', (label, ''), False)]
        if controller.is_playing and controller.state == 'Playing':
            rows.append(('pause', T(35336, 'Pause'), False))
        else:
            rows.append(('unpause', T(35337, 'Play'), False))
        rows.append(('stop', T(35334, 'Stop for everyone'), False))
        if len(controller.playlist) > 1:
            if controller.playing_item_index + 1 < len(controller.playlist):
                rows.append(('next', T(35338, 'Next'), False))
            if controller.playing_item_index > 0:
                rows.append(('previous', T(35339, 'Previous'), False))

    rows.append(('leave', T(35333, 'Leave group'), False))
    rows.append(optionGroup(T(35340, 'Participants')))
    for participant in controller.participants:
        rows.append(('participant', (participant, ''), False))
    return rows


def choose(session, key):
    """Handle a SyncPlayDialog choice; return True to keep open and redraw, False to close."""
    controller = session.controller
    client = session.server.client
    try:
        if key == 'pause':
            jf_syncplay.pause(client)
        elif key == 'unpause':
            jf_syncplay.unpause(client)
        elif key == 'stop':
            jf_syncplay.stop(client)
            controller.mark_stopped()
        elif key == 'next':
            pli = controller.selected_playlist_item_id
            if pli:
                jf_syncplay.next_item(client, pli)
        elif key == 'previous':
            pli = controller.selected_playlist_item_id
            if pli:
                jf_syncplay.previous_item(client, pli)
        elif key == 'leave':
            return False
    except JellyfinError as e:
        util.LOG('SyncPlay: {0} failed: {1}', key, e)
    return True
