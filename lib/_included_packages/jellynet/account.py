"""
The signed-in Jellyfin account in the shape Uno's screens expect of plexapp.ACCOUNT.
Each Jellyfin user on this Kodi keeps their own access token and per-user DeviceId, so signing in a
second user never revokes the first user's token, and signing out forgets only the active user.
"""

from urllib.parse import quote

from plexnet import util as pnUtil

from . import auth
from .errors import JellyfinAuthError, JellyfinError


def _user_thumb(base_url, user_id, primary_image_tag):
    if not base_url or not user_id or not primary_image_tag:
        return ''
    return f'{base_url.rstrip("/")}/Users/{quote(str(user_id), safe="")}/Images/Primary?tag={quote(str(primary_image_tag), safe="")}'


class JellyfinUser:
    """One Jellyfin user, with the attributes Uno's user-select screen and sidebar read."""

    def __init__(self, data, base_url=''):
        self.id = str(data['Id'])
        self.username = data.get('Name') or ''
        self.title = self.username
        policy = data.get('Policy') or {}
        self.isProtected = bool(data.get('HasPassword'))
        self.isAdmin = bool(policy.get('IsAdministrator'))
        self.primaryImageTag = data.get('PrimaryImageTag') or ''
        self.thumb = _user_thumb(base_url, self.id, self.primaryImageTag)

    @classmethod
    def from_session(cls, session_data, base_url=''):
        return cls(
            {
                'Id': session_data['user_id'],
                'Name': session_data.get('username') or '',
                'HasPassword': session_data.get('has_password', True),
                'Policy': {'IsAdministrator': bool(session_data.get('is_admin'))},
                'PrimaryImageTag': session_data.get('primary_image_tag') or '',
            },
            base_url=base_url,
        )

    def __repr__(self):
        return f'<JellyfinUser {self.id}>'


class JellyfinAccount:
    # Plex account subtitle preferences read by Uno's player/screens
    subtitlesForced = 0
    subtitlesSDH = 0
    subtitlesLanguage = ''

    def __init__(self, client, sessions=None, active_user_id=None, on_change=None):
        self.client = client
        self.sessions = {str(uid): dict(data) for uid, data in (sessions or {}).items() if isinstance(data, dict)}
        self.on_change = on_change
        self._public_users = []
        self.homeUsers = []
        self.switchUser = False
        self.isOffline = False
        self.isAuthenticated = False
        self._active_user_id = str(active_user_id) if active_user_id else None
        self._session_ended_sent = False
        client.onSessionEnded = self.sessionEnded
        self._sync_client()
        self._rebuild_home_users()

    def _active_session(self):
        return self.sessions.get(self._active_user_id) if self._active_user_id else None

    def _sync_client(self):
        session = self._active_session()
        if session and session.get('access_token'):
            self.client.token = session['access_token']
            self.client.username = session.get('username') or None
            self.client._ended_token = None
            self._session_ended_sent = False
        else:
            self.client.token = None
            self.client.username = None

    def _notify_change(self):
        if self.on_change:
            self.on_change(self.sessions, self._active_user_id)

    def _rebuild_home_users(self):
        merged = list(self._public_users)
        seen = {u.id for u in merged}
        for uid, data in self.sessions.items():
            if uid not in seen and data.get('user_id'):
                merged.append(JellyfinUser.from_session(data, base_url=self.client.base_url))
                seen.add(uid)
        self.homeUsers = merged

    @property
    def ID(self):
        return self._active_user_id

    @property
    def authToken(self):
        return self.client.token

    @property
    def isSignedIn(self):
        return bool(self.client.token)

    @property
    def isAdmin(self):
        current = self.getHomeUser(self._active_user_id) if self._active_user_id else None
        if current is not None:
            return current.isAdmin
        session = self._active_session()
        return bool(session and session.get('is_admin'))

    @property
    def username(self):
        session = self._active_session()
        if session and session.get('username'):
            return session['username']
        return self.client.username or ''

    @property
    def title(self):
        current = self.getHomeUser(self._active_user_id) if self._active_user_id else None
        return current.title if current else self.username

    @property
    def thumb(self):
        current = self.getHomeUser(self._active_user_id) if self._active_user_id else None
        if current is not None:
            return current.thumb
        session = self._active_session()
        if session:
            return _user_thumb(self.client.base_url, self._active_user_id, session.get('primary_image_tag'))
        return ''

    @property
    def isProtected(self):
        current = self.getHomeUser(self._active_user_id) if self._active_user_id else None
        if current is not None:
            return current.isProtected
        session = self._active_session()
        return bool(session and session.get('has_password'))

    def hasPlexPass(self):
        return True  # as SiloMod: Uno's Plex Pass gate is marker skipping, which Jellyfin's media segments support

    def hasSession(self, user_id):
        return bool(user_id and self.sessions.get(str(user_id), {}).get('access_token'))

    def recordAuth(self, result, has_password=None, primary_image_tag=''):
        """Store a newly authenticated user's session while keeping every other user's token intact."""
        uid = str(result.user_id)
        existing = self.sessions.get(uid) or {}
        self.sessions[uid] = {
            'user_id': uid,
            'username': result.user_name or existing.get('username') or '',
            'access_token': result.access_token,
            'is_admin': bool(result.is_admin),
            'has_password': existing.get('has_password', True) if has_password is None else bool(has_password),
            'primary_image_tag': primary_image_tag or existing.get('primary_image_tag') or '',
        }
        self._active_user_id = uid
        self._sync_client()
        self._rebuild_home_users()
        self._notify_change()

    def verifyCurrentUser(self):
        """Confirm the active session via authenticated GET /Users/Me and refresh user metadata."""
        data = self.client.get('/Users/Me')
        if not isinstance(data, dict) or not data.get('Id'):
            return None
        uid = str(data['Id'])
        session = self.sessions.get(uid)
        if session is not None:
            policy = data.get('Policy') or {}
            session['username'] = data.get('Name') or session.get('username') or ''
            session['is_admin'] = bool(policy.get('IsAdministrator'))
            session['has_password'] = bool(data.get('HasPassword'))
            session['primary_image_tag'] = data.get('PrimaryImageTag') or ''
            self._active_user_id = uid
            self._sync_client()
            self._rebuild_home_users()
            self._notify_change()
        return JellyfinUser(data, base_url=self.client.base_url)

    def updateHomeUsers(self, refreshSubscription=False):
        data = self.client.get('/Users/Public') or []
        public_users = []
        changed = False
        for entry in data:
            if not isinstance(entry, dict) or not entry.get('Id'):
                continue
            user = JellyfinUser(entry, base_url=self.client.base_url)
            public_users.append(user)
            stored = self.sessions.get(user.id)
            if stored is not None:
                updated = {
                    'username': user.username,
                    'is_admin': user.isAdmin,
                    'has_password': user.isProtected,
                    'primary_image_tag': user.primaryImageTag,
                }
                if any(stored.get(k) != v for k, v in updated.items()):
                    stored.update(updated)
                    changed = True
        self._public_users = public_users
        self._rebuild_home_users()
        if changed:
            self._notify_change()

    def getHomeUser(self, user_id):
        if user_id is None:
            return None
        uid = str(user_id)
        return next((u for u in self.homeUsers if u.id == uid), None)

    def safeUserThumb(self, user_id, thumb=None):
        return thumb or ''

    def switchHomeUser(self, user_id, password=None, username=None, silent=False):
        """
        Switch to another Jellyfin user:
        - Stored token for user_id -> switch directly using that user's token and DeviceId.
        - Otherwise authenticate via /Users/AuthenticateByName (empty password when HasPassword is False,
          provided password for password-protected or hidden users).
        Returns True on success, False on wrong/missing password. Keeps every other user's stored token.
        """
        self.switchUser = False
        uid = str(user_id) if user_id else None
        if uid and self.hasSession(uid):
            self._active_user_id = uid
            self._sync_client()
            self._notify_change()
            self.switchUser = True
            self.isAuthenticated = True
            return True

        target = self.getHomeUser(uid) if uid else None
        target_name = (username or (target.username if target else '')).strip()
        if not target_name:
            return False
        if password is None and target is not None and target.isProtected:
            return False

        try:
            result = auth.passwordLogin(self.client, target_name, password or '')
        except JellyfinAuthError:
            return False

        has_pw = bool(target.isProtected) if target is not None else bool(password)
        image_tag = target.primaryImageTag if target is not None else ''
        self.recordAuth(result, has_password=has_pw, primary_image_tag=image_tag)
        self.switchUser = True
        self.isAuthenticated = True
        return True

    def sessionEnded(self):
        """
        Jellyfin refused the active token with HTTP 401 (device deleted, password changed, user disabled):
        drop only the active user's stored token and notify Home once so start-up signs in again.
        """
        if self._active_user_id and self._active_user_id in self.sessions:
            self.sessions.pop(self._active_user_id, None)
            self._notify_change()
        self.client.token = None
        self.isAuthenticated = False
        self._rebuild_home_users()
        if not self._session_ended_sent:
            self._session_ended_sent = True
            if pnUtil.APP:
                pnUtil.APP.trigger('account:session_ended')

    def requireVerification(self):
        if self.isAuthenticated:
            self.isAuthenticated = False
            if pnUtil.APP:
                pnUtil.APP.trigger('account:verification_required')

    def signOut(self, expired=False):
        """Revoke the active user's session on Jellyfin and forget only that user's token locally."""
        if self.client.token:
            try:
                self.client.post('/Sessions/Logout')
            except JellyfinError:
                pass
        if self._active_user_id:
            self.sessions.pop(self._active_user_id, None)
        self._active_user_id = None
        self._sync_client()
        self.isAuthenticated = False
        self._rebuild_home_users()
        self._notify_change()

    def cancelOfflineRetry(self):
        pass

    def resumeOfflineRetry(self):
        pass
