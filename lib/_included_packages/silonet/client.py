"""
The HTTP client every silonet call goes through: Silo's auth and device headers, Problem Details errors, the
one token refresh Silo asks for (docs/auth-api.md, "Access tokens after a role change"), and backing off on 429
and 503. Tokens never reach the log.
"""

import time

import requests
from kodi_six import xbmc

from .errors import SiloAuthError, SiloError, SiloRateLimited, SiloUnavailable

REFRESH_PATH = '/api/v2/auth/refresh'
CLIENT_NAME = 'SiloMod'


def log(message):
    xbmc.log('silonet: ' + message, xbmc.LOGDEBUG)


class DeviceIdentity:
    def __init__(self, deviceId, name, platform, clientVersion):
        self.deviceId = deviceId
        self.name = name
        self.platform = platform
        self.clientVersion = clientVersion

    def headers(self):
        return {
            'X-Silo-Device-Id': self.deviceId,
            'X-Silo-Device-Name': self.name,
            'X-Silo-Device-Platform': self.platform,
            'X-Silo-Client': CLIENT_NAME,
            'X-Silo-Client-Version': self.clientVersion,
        }


class Credentials:
    """The login session's tokens and the acting profile. onChange(credentials) runs when a refresh rotates them."""

    def __init__(self, accessToken=None, refreshToken=None, profileId=None, profileToken=None, onChange=None):
        self.accessToken = accessToken
        self.refreshToken = refreshToken
        self.profileId = profileId
        self.profileToken = profileToken
        self.onChange = onChange

    def rotate(self, accessToken, refreshToken):
        self.accessToken = accessToken
        self.refreshToken = refreshToken
        if self.onChange:
            self.onChange(self)

    def __repr__(self):
        return f'<Credentials profile={self.profileId!r}>'


def retryAfter(response):
    value = response.headers.get('Retry-After', '')
    return int(value) if value.strip().isdigit() else None


def problemOf(response):
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    name = (body.get('type') or '').rstrip('/').rsplit('/', 1)[-1] or f'http_{response.status_code}'
    return name, body.get('detail') or ''


class SiloClient:
    def __init__(self, baseUrl, device, credentials, session=None, timeout=(5, 30)):
        self.baseUrl = baseUrl.rstrip('/')
        self.device = device
        self.credentials = credentials
        self.session = session or requests.Session()
        self.timeout = timeout
        self._coolUntil = {}  # (method, path) -> monotonic time: Silo rate-limits per budget, not per client
        self.onProfileLocked = None  # called on 403 profile_verification_required: the profile's PIN session ended
        self.onSessionEnded = None  # called when Silo refuses the session's refresh: signed out there, for one

    def request(self, method, path, params=None, json=None, profile=True, headers=None):
        """The decoded JSON answer, or None for an empty one. Raises a silonet.errors type for every failure. headers:
        more request headers (a watch-together room's X-Room-Token)."""
        response = self._send(method, path, params, json, profile, extra=headers)
        if response.status_code == 401:
            name, detail = problemOf(response)
            if not self.credentials.refreshToken:
                raise SiloAuthError(401, name, detail)
            try:
                self._refresh()
            except SiloAuthError:
                # the session itself refused, not just this token: signed out on Silo, the password changed, the
                # account disabled
                if self.onSessionEnded:
                    self.onSessionEnded()
                raise
            response = self._send(method, path, params, json, profile, extra=headers)
            if response.status_code == 401:
                raise SiloAuthError(401, *problemOf(response))
        return self._result(response, method, path)

    def _refresh(self):
        response = self._send(
            'POST', REFRESH_PATH, None, {'refresh_token': self.credentials.refreshToken}, False, authorize=False
        )
        if response.status_code == 401:
            raise SiloAuthError(401, *problemOf(response))
        tokens = self._result(response, 'POST', REFRESH_PATH)
        self.credentials.rotate(tokens['access_token'], tokens.get('refresh_token') or self.credentials.refreshToken)

    def _headers(self, profile, authorize):
        headers = {'Accept': 'application/json'}
        headers.update(self.device.headers())
        if authorize and self.credentials.accessToken:
            headers['Authorization'] = 'Bearer ' + self.credentials.accessToken
        if profile and self.credentials.profileId:
            headers['X-Profile-Id'] = self.credentials.profileId
            if self.credentials.profileToken:
                headers['X-Profile-Token'] = self.credentials.profileToken
        return headers

    def _send(self, method, path, params, json, profile, authorize=True, extra=None):
        remaining = self._coolUntil.get((method, path), 0) - time.monotonic()
        if remaining > 0:
            raise SiloRateLimited(429, 'rate_limit_exceeded', 'cooling down', int(remaining + 0.999))

        log(f'{method} {path}')
        try:
            return self.session.request(
                method,
                self.baseUrl + path,
                params=params,
                json=json,
                headers=dict(self._headers(profile, authorize), **(extra or {})),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            log(f'{method} {path} failed: {type(e).__name__}')
            raise SiloUnavailable(0, 'unreachable', type(e).__name__)

    def _result(self, response, method, path):
        status = response.status_code
        if status < 400:
            if status == 204 or not response.content:
                return None
            return response.json()

        name, detail = problemOf(response)
        log(f'answered {status} {name}')
        if status == 429:
            wait = retryAfter(response) or 1
            self._coolUntil[(method, path)] = time.monotonic() + wait
            raise SiloRateLimited(status, name, detail, wait)
        if status == 503:
            raise SiloUnavailable(status, name, detail, retryAfter(response))
        if status == 401:
            raise SiloAuthError(status, name, detail)
        if status == 403 and name == 'profile_verification_required' and self.onProfileLocked:
            self.onProfileLocked()
        raise SiloError(status, name, detail)
