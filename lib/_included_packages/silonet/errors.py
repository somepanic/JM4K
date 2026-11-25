"""Errors raised by silonet. `type` is the Problem Details identifier, the last path segment of Silo's type URI."""


class SiloError(Exception):
    def __init__(self, status, type_, detail=''):
        super().__init__(f'{status} {type_}')
        self.status = status
        self.type = type_
        self.detail = detail


class SiloAuthError(SiloError):
    """The login session is gone: the user has to sign in again."""


class SiloRateLimited(SiloError):
    def __init__(self, status, type_, detail='', retryAfter=None):
        super().__init__(status, type_, detail)
        self.retryAfter = retryAfter


class SiloUnavailable(SiloError):
    """The server could not be reached, or could not answer yet; keep the credentials and retry later."""

    def __init__(self, status, type_, detail='', retryAfter=None):
        super().__init__(status, type_, detail)
        self.retryAfter = retryAfter


class SiloIncompatible(SiloError):
    """The server speaks an API version SM4K does not support."""
