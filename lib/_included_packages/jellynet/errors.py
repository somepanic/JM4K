"""Errors raised by jellynet."""


class JellyfinError(Exception):
    def __init__(self, status, message=''):
        super().__init__(f'{status} {message}'.strip())
        self.status = status
        self.message = message


class JellyfinAuthError(JellyfinError):
    """Authentication failed or the session token was revoked."""


class JellyfinUnavailable(JellyfinError):
    """The server could not be reached or could not answer yet; keep credentials and retry later."""

    def __init__(self, status, message='', retry_after=None):
        super().__init__(status, message)
        self.retry_after = retry_after


class JellyfinIncompatible(JellyfinError):
    """The server is not a supported Jellyfin server (10.11 through 12.x)."""
