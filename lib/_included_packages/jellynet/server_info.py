"""
Telling whether an address is a Jellyfin server JM4K can talk to, before anyone signs in:
GET /System/Info/Public answers without credentials, as do /QuickConnect/Enabled and /Branding/Configuration
for the sign-in card.
"""

import re

from .client import ClientIdentity, JellyfinClient
from .errors import JellyfinError, JellyfinIncompatible, JellyfinUnavailable

MIN_VERSION = (10, 11)
MAX_MAJOR = 12
_VERSION_RE = re.compile(r'^(\d+)\.(\d+)')
_PROBE_IDENTITY = ClientIdentity('JellyMod', '0.1.0', 'Kodi', 'probe')


class ServerInfo:
    def __init__(self, server_id, name, version):
        self.server_id = server_id
        self.name = name
        self.version = version


class LoginOptions:
    def __init__(self, quick_connect, disclaimer='', name=None, password=True):
        self.quick_connect = bool(quick_connect)
        self.disclaimer = disclaimer or ''
        self.name = name
        self.password = bool(password)

    @property
    def subtitle(self):
        return self.disclaimer


def _parse_version(version):
    if not isinstance(version, str):
        return None
    match = _VERSION_RE.match(version.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def probe(base_url, session=None, identity=None):
    """Probe a Jellyfin server and return ServerInfo. Raises JellyfinUnavailable or JellyfinIncompatible."""
    client = JellyfinClient(base_url, identity or _PROBE_IDENTITY, session=session)
    try:
        info = client.get('/System/Info/Public')
        if not isinstance(info, dict):
            raise JellyfinIncompatible(0, 'Unexpected /System/Info/Public response')
        if info.get('StartupWizardCompleted') is False:
            raise JellyfinUnavailable(503, 'Server setup is not finished yet.')
        server_id = info.get('Id')
        version = info.get('Version')
        if not isinstance(server_id, str) or not server_id or not isinstance(version, str) or not version:
            raise JellyfinIncompatible(0, 'Missing server Id or Version in /System/Info/Public')
        parsed = _parse_version(version)
        if parsed is None:
            raise JellyfinIncompatible(0, f'Unrecognized Jellyfin version: {version}')
        if parsed < MIN_VERSION:
            raise JellyfinIncompatible(0, f'Jellyfin {version} is not supported; Jellyfin 10.11 or newer is required.')
        if parsed[0] > MAX_MAJOR:
            raise JellyfinIncompatible(0, f'Jellyfin {version} is not supported (supported: 10.11 through 12.x).')
        raw_name = info.get('ServerName')
        name = raw_name if isinstance(raw_name, str) and raw_name else None
        return ServerInfo(server_id, name, version)
    except (JellyfinUnavailable, JellyfinIncompatible):
        raise
    except (JellyfinError, ValueError, KeyError, TypeError) as e:
        raise JellyfinIncompatible(0, type(e).__name__)
    finally:
        if session is None:
            client.close()


def loginOptions(client, name=None):
    """Sign-in options from /QuickConnect/Enabled and /Branding/Configuration (LoginDisclaimer)."""
    try:
        qc_answer = client.get('/QuickConnect/Enabled')
    except (JellyfinError, ValueError):
        qc_answer = False
    quick_connect = qc_answer is True

    try:
        branding = client.get('/Branding/Configuration')
    except (JellyfinError, ValueError):
        branding = {}
    if not isinstance(branding, dict):
        branding = {}
    raw_disclaimer = branding.get('LoginDisclaimer')
    disclaimer = raw_disclaimer.strip() if isinstance(raw_disclaimer, str) else ''
    return LoginOptions(quick_connect, disclaimer, name=name)
