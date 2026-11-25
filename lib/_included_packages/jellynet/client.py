"""
The HTTP client every jellynet call goes through: Jellyfin's MediaBrowser Authorization header, per-user DeviceId
derivation, session-end handling on 401, and 503/network unavailability. Tokens and secrets never reach the log.
"""

import hashlib
from urllib.parse import quote

import requests
from kodi_six import xbmc

from .errors import JellyfinAuthError, JellyfinError, JellyfinUnavailable


def log(message):
    xbmc.log('jellynet: ' + message, xbmc.LOGDEBUG)


class ClientIdentity:
    """This Kodi installation as Jellyfin sees it."""

    def __init__(self, client, version, device, device_uuid):
        self.client = client
        self.version = version
        self.device = device
        self.device_uuid = device_uuid


def device_id_for(identity, username):
    """Stable DeviceId per device and lower-cased username so multiple users on one Kodi never share a token."""
    normalized = (username or '').strip().lower()
    if not normalized:
        return identity.device_uuid
    digest = hashlib.sha256(f'{identity.device_uuid}:{normalized}'.encode()).hexdigest()[:16]
    return f'{identity.device_uuid}-{digest}'


def _quote_header_value(value):
    return quote(str(value), safe=' -._~:/+=')


def authorization_header(identity, username=None, token=None):
    """Build Jellyfin's MediaBrowser Authorization header with quoted values; omit Token when unauthenticated."""
    parts = []
    if token:
        parts.append(f'Token="{_quote_header_value(token)}"')
    parts.append(f'Client="{_quote_header_value(identity.client)}"')
    parts.append(f'Version="{_quote_header_value(identity.version)}"')
    parts.append(f'DeviceId="{_quote_header_value(device_id_for(identity, username))}"')
    parts.append(f'Device="{_quote_header_value(identity.device)}"')
    return 'MediaBrowser ' + ', '.join(parts)


def _retry_after(response):
    value = response.headers.get('Retry-After', '').strip()
    return int(value) if value.isdigit() else None


def _error_message(response):
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        message = body.get('detail') or body.get('title') or body.get('Message')
        if message:
            return str(message)
    elif isinstance(body, str) and body.strip():
        return body.strip()
    header_message = (response.headers.get('Message') or '').strip()
    if header_message:
        return header_message
    text = (getattr(response, 'text', '') or '').strip()
    if text and not text.lstrip().startswith('<'):
        return text[:200]
    return f'HTTP {response.status_code}'


class JellyfinClient:
    def __init__(self, base_url, identity, token=None, username=None, session=None, timeout=(5, 30)):
        self.base_url = base_url.rstrip('/')
        self.identity = identity
        self.token = token
        self.username = username
        self.session = session or requests.Session()
        self.timeout = timeout
        self.onSessionEnded = None
        self._ended_token = None

    def __repr__(self):
        return f'<JellyfinClient authenticated={bool(self.token)}>'

    def auth_header(self):
        return authorization_header(self.identity, self.username, self.token)

    def _headers(self, extra=None):
        headers = {
            'Accept': 'application/json',
            'Authorization': self.auth_header(),
        }
        if extra:
            headers.update(extra)
        return headers

    def request(self, method, path, *, params=None, json=None, headers=None):
        """Send a Jellyfin API request and return decoded JSON (or None for 204/empty). Raises JellyfinError types."""
        clean_path = path if path.startswith('/') else '/' + path
        log(f'{method} {clean_path}')
        try:
            response = self.session.request(
                method,
                self.base_url + clean_path,
                params=params,
                json=json,
                headers=self._headers(headers),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            log(f'{method} {clean_path} failed: {type(e).__name__}')
            raise JellyfinUnavailable(0, type(e).__name__)
        return self._result(response, method, clean_path)

    def get(self, path, *, params=None, headers=None):
        return self.request('GET', path, params=params, headers=headers)

    def post(self, path, *, params=None, json=None, headers=None):
        return self.request('POST', path, params=params, json=json, headers=headers)

    def delete(self, path, *, params=None, headers=None):
        return self.request('DELETE', path, params=params, headers=headers)

    def close(self):
        self.session.close()

    def dropIdleConnections(self):
        pass  # requests' session opens new connections itself after a sleep

    def _result(self, response, method, path):
        status = response.status_code
        if status < 400:
            if status == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                raise JellyfinError(status, 'Invalid JSON response')

        log(f'{method} {path} answered {status}')
        message = _error_message(response)
        if status == 401:
            if self.token and self._ended_token != self.token:
                self._ended_token = self.token
                if self.onSessionEnded:
                    self.onSessionEnded()
            raise JellyfinAuthError(status, message)
        if status == 503:
            raise JellyfinUnavailable(status, message, retry_after=_retry_after(response))
        raise JellyfinError(status, message)
