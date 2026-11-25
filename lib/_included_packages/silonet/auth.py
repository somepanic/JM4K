"""
Signing in to Silo. A TV shows a code that someone approves on a device where they are already signed in
(docs/architecture/device-login.md): start, poll every poll_after seconds, collect the tokens when approved. A
code nobody opened is replaced when it expires; Back cancels it. A password sign-in is the fallback.
"""

from .errors import SiloError, SiloRateLimited, SiloUnavailable


class DeviceCode:
    def __init__(self, data):
        self.deviceCode = data['device_code']
        self.userCode = data['user_code']
        self.verificationUri = data['verification_uri']
        self.verificationUriComplete = data.get('verification_uri_complete') or data['verification_uri']
        self.interval = data.get('interval') or 5
        self.expiresIn = data.get('expires_in')


class Tokens:
    # a password sign-in is approved at once and binds no profile: the profile picker follows
    status = 'approved'
    profileId = profileToken = None

    def __init__(self, data):
        self.accessToken = data.get('access_token')
        self.refreshToken = data.get('refresh_token')
        self.user = data.get('user') or {}


class PollResult(Tokens):
    def __init__(self, data):
        super().__init__(data.get('tokens') or {})
        self.status = data['status']
        self.opened = bool(data.get('opened'))
        self.pollAfter = data.get('poll_after')
        self.profileId = data.get('profile_id') or None
        self.profileToken = data.get('profile_token') or None


def formatUserCode(userCode):
    """Silo's eight digits as people read them: 4821 7730."""
    digits = userCode.replace('-', '').replace(' ', '')
    return digits[:4] + ' ' + digits[4:]


class DeviceLogin:
    def __init__(self, client):
        self.client = client

    def start(self, deviceName):
        data = self.client.request(
            'POST',
            '/api/v2/auth/device/start',
            profile=False,
            json={
                'client_purpose': 'device_login',
                'device_name': deviceName,
                'device_platform': self.client.device.platform,
            },
        )
        return DeviceCode(data)

    def poll(self, code):
        return PollResult(
            self.client.request(
                'POST', '/api/v2/auth/device/poll', profile=False, json={'device_code': code.deviceCode}
            )
        )

    def cancel(self, code):
        try:
            self.client.request(
                'POST', '/api/v2/auth/device/cancel', profile=False, json={'device_code': code.deviceCode}
            )
        except SiloError:
            pass  # best effort: an abandoned code expires by itself

    def waitForApproval(self, code, wait):
        """
        Poll until the request leaves 'pending'. wait(seconds) sleeps and answers False when the user gave up;
        the request is then canceled and None returned. A busy or briefly unreachable server is waited out.
        """
        delay = code.interval
        while True:
            if not wait(delay):
                self.cancel(code)
                return None
            try:
                result = self.poll(code)
            except (SiloRateLimited, SiloUnavailable) as e:
                delay = e.retryAfter or code.interval
                continue
            if result.status != 'pending':
                return result
            delay = result.pollAfter or code.interval


def signInWithCode(deviceLogin, deviceName, showCode, wait):
    """showCode(DeviceCode) for each code shown. The final PollResult, or None if the user gave up."""
    while True:
        code = deviceLogin.start(deviceName)
        showCode(code)
        result = deviceLogin.waitForApproval(code, wait)
        if result is None or result.status != 'expired' or result.opened:
            return result


def passwordLogin(client, username, password):
    """Tokens for a username and password; SiloAuthError when they are wrong. client holds no credentials."""
    return Tokens(
        client.request('POST', '/api/v2/auth/login', profile=False, json={'username': username, 'password': password})
    )
