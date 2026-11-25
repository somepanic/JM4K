"""
Signing in to Jellyfin: Quick Connect (initiate a code, poll /QuickConnect/Connect until approved,
exchange the secret at /Users/AuthenticateWithQuickConnect) and password sign-in (/Users/AuthenticateByName).
Secrets, passwords and access tokens never reach the log.
"""

from .client import JellyfinClient
from .errors import JellyfinError, JellyfinUnavailable

POLL_INTERVAL = 5  # seconds between Quick Connect polls, matching SM4K's sign-in poll interval


class QuickConnectCode:
    def __init__(self, code, secret, date_added=None, authenticated=False):
        self.code = code
        self.secret = secret
        self.date_added = date_added
        self.authenticated = bool(authenticated)

    def __repr__(self):
        return f'<QuickConnectCode code={self.code!r}>'


class AuthResult:
    def __init__(self, access_token, user_id, user_name, is_admin, server_id):
        self.access_token = access_token
        self.user_id = user_id
        self.user_name = user_name
        self.is_admin = bool(is_admin)
        self.server_id = server_id

    def __repr__(self):
        return f'<AuthResult user_id={self.user_id!r} server_id={self.server_id!r}>'


def _unauthenticated_client(client, username=None):
    """Unauthenticated JellyfinClient sharing client's session so auth 401s never end an active session."""
    return JellyfinClient(
        client.base_url,
        client.identity,
        token=None,
        username=username,
        session=client.session,
        timeout=client.timeout,
    )


def _auth_result(data):
    if not isinstance(data, dict) or not data.get('AccessToken'):
        raise JellyfinError(0, 'Missing AccessToken in authentication response')
    user = data.get('User') or {}
    session_info = data.get('SessionInfo') or {}
    policy = user.get('Policy') or {}
    user_id = user.get('Id') or session_info.get('UserId') or ''
    user_name = user.get('Name') or session_info.get('UserName') or ''
    server_id = data.get('ServerId') or user.get('ServerId') or ''
    return AuthResult(
        access_token=data['AccessToken'],
        user_id=user_id,
        user_name=user_name,
        is_admin=bool(policy.get('IsAdministrator')),
        server_id=server_id,
    )


def _secret_of(code):
    return code.secret if isinstance(code, QuickConnectCode) else code


class QuickConnect:
    def __init__(self, client, poll_interval=POLL_INTERVAL):
        self.client = client
        self.poll_interval = poll_interval

    def _request_client(self):
        return _unauthenticated_client(self.client, username=self.client.username)

    def initiate(self):
        data = self._request_client().post('/QuickConnect/Initiate')
        if not isinstance(data, dict) or not data.get('Code') or not data.get('Secret'):
            raise JellyfinError(0, 'Unexpected /QuickConnect/Initiate response')
        return QuickConnectCode(
            data['Code'],
            data['Secret'],
            date_added=data.get('DateAdded'),
            authenticated=bool(data.get('Authenticated')),
        )

    def check(self, code):
        data = self._request_client().get('/QuickConnect/Connect', params={'secret': _secret_of(code)})
        return bool(isinstance(data, dict) and data.get('Authenticated'))

    def finish(self, code):
        data = self._request_client().post('/Users/AuthenticateWithQuickConnect', json={'Secret': _secret_of(code)})
        return _auth_result(data)

    def poll(self, show_code, wait):
        """
        Initiate a Quick Connect code, show it via show_code(code), and poll until authenticated or
        wait(seconds) returns False (cancelled). An expired secret (HTTP 404) starts a new code; brief
        unavailability (503 / network error) is waited out.
        """
        while True:
            code = self.initiate()
            show_code(code)
            delay = self.poll_interval
            while True:
                if not wait(delay):
                    return None
                try:
                    authenticated = self.check(code)
                except JellyfinUnavailable as e:
                    delay = e.retry_after or self.poll_interval
                    continue
                except JellyfinError as e:
                    if e.status == 404:
                        break
                    raise
                if authenticated:
                    try:
                        return self.finish(code)
                    except JellyfinError as e:
                        if e.status == 404:
                            break
                        raise
                delay = self.poll_interval


def passwordLogin(client, username, password):
    """Sign in with a username and password; empty string signs in a user without a password."""
    data = _unauthenticated_client(client, username=username).post(
        '/Users/AuthenticateByName',
        json={'Username': username, 'Pw': password or ''},
    )
    return _auth_result(data)
