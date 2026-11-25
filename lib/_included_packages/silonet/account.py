"""
The signed-in Silo account in the shape Uno's screens expect of plexapp.ACCOUNT. Silo's household profiles are
its home users; a PIN-protected profile is unlocked with /api/v2/profiles/{id}/verify-pin, which hands back the
X-Profile-Token the client then sends. Silo has no paid tier, so every feature counts as unlocked.
"""

from plexnet import util as pnUtil

from .errors import SiloError


class SiloProfile:
    """One household profile, with the attributes Uno's user-select screen reads."""

    def __init__(self, data):
        self.id = data['id']
        self.title = data.get('name') or ''
        self.thumb = data.get('avatar_url') or ''
        self.isProtected = bool(data.get('has_pin'))
        self.isAdmin = bool(data.get('is_primary'))
        self.isChild = bool(data.get('is_child'))

    def __repr__(self):
        return f'<SiloProfile {self.id}>'


class SiloAccount:
    # Plex account subtitle preferences the screens read; Silo keeps these per profile (mapped in Phase 5)
    subtitlesForced = 0
    subtitlesSDH = 0
    subtitlesLanguage = ''

    def __init__(self, client, user=None):
        self.client = client
        client.onProfileLocked = self.requireVerification
        client.onSessionEnded = self.sessionEnded
        self.user = user if user is not None else {}  # shared: sign-in fills it in later
        self.homeUsers = []
        self.switchUser = False
        self.isOffline = False
        self.isAuthenticated = False  # lib/silo.py chooseProfile() decides once the profiles are known

    @property
    def ID(self):
        return self.client.credentials.profileId

    @property
    def authToken(self):
        return self.client.credentials.accessToken

    @property
    def isSignedIn(self):
        return bool(self.client.credentials.accessToken)

    @property
    def isAdmin(self):
        return self.user.get('role') == 'admin'

    @property
    def username(self):
        return self.user.get('username') or ''

    @property
    def title(self):
        """The name Home shows for who is signed in: the acting profile's, else the account's."""
        current = self.getHomeUser(self.ID)
        return current.title if current else self.username

    @property
    def thumb(self):
        current = self.getHomeUser(self.ID)
        return current.thumb if current else ''

    @property
    def isProtected(self):
        current = self.getHomeUser(self.ID)
        return bool(current and current.isProtected)

    def hasPlexPass(self):
        return True

    def updateHomeUsers(self, refreshSubscription=False):
        data = self.client.request('GET', '/api/v2/profiles', profile=False)
        self.homeUsers = [SiloProfile(p) for p in data.get('items') or []]

    def getHomeUser(self, userId):
        return next((u for u in self.homeUsers if u.id == userId), None)

    def safeUserThumb(self, userId, thumb=None):
        return thumb or ''

    def switchHomeUser(self, userId, pin=None, silent=False):
        """
        Act as another profile; True when it is now the acting profile. A PIN-protected one needs its PIN: a wrong
        one answers False, and after five wrong ones Silo locks the profile (SiloRateLimited, never retried).
        """
        self.switchUser = False
        target = self.getHomeUser(userId)
        profileToken = None
        if target is not None and target.isProtected:
            answer = self.client.request(
                'POST', f'/api/v2/profiles/{userId}/verify-pin', profile=False, json={'pin': pin or ''}
            )
            if not answer.get('valid'):
                return False
            profileToken = answer.get('profile_token')

        credentials = self.client.credentials
        credentials.profileId = userId
        credentials.profileToken = profileToken
        if credentials.onChange:
            credentials.onChange(credentials)
        self.switchUser = True
        self.isAuthenticated = True
        return True

    def sessionEnded(self):
        """
        Silo refused the session's refresh (signed out on Silo, the password changed, the account disabled): Home hears
        account:session_ended and closes, and start-up signs in again (lib/main.py, lib/silo.py init()).
        """
        if pnUtil.APP:
            pnUtil.APP.trigger('account:session_ended')

    def requireVerification(self):
        """
        The acting profile's PIN session ended (403 profile_verification_required, from any request): its token is
        forgotten, also in the stored session, and the first time Home hears it (account:verification_required) and
        closes to the profile picker, which asks for the PIN.
        """
        credentials = self.client.credentials
        credentials.profileToken = None
        if credentials.onChange:
            credentials.onChange(credentials)
        if self.isAuthenticated:
            self.isAuthenticated = False
            if pnUtil.APP:
                pnUtil.APP.trigger('account:verification_required')

    def signOut(self, expired=False):
        try:
            self.client.request('POST', '/api/v2/auth/logout', profile=False)
        except SiloError:
            pass  # the tokens are forgotten either way
        credentials = self.client.credentials
        credentials.accessToken = credentials.refreshToken = credentials.profileToken = None
        if credentials.onChange:
            credentials.onChange(credentials)
        self.homeUsers = []
        self.isAuthenticated = False

    def refreshAccount(self):
        self.updateHomeUsers()

    def cancelOfflineRetry(self):
        pass  # plex.tv's offline retry; Silo has no separate account service to wait for

    def resumeOfflineRetry(self):
        pass
