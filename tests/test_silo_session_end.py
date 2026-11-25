"""
A session Silo ends while SiloMod is open - signed out from Silo's web, the password changed, the account disabled:
the access token's refresh is refused (401). The client says so (onSessionEnded), the account signals
account:session_ended, and Home closes so that start-up asks to sign in again (lib/main.py, silo.init()).
"""

from unittest import mock

from kodienv import ENV

ENV.abort_requested = True
from plexnet import util as pnUtil
from silonet.client import Credentials, SiloClient
from silonet.errors import SiloAuthError

from lib.windows import library

from .base import KodiTestCase
from .silonet_fakes import FakeSession, problem
from .test_silonet_account import DEVICE, account


class SessionEndTest(KodiTestCase):
    def test_a_refused_refresh_ends_the_session(self):
        session = FakeSession(problem(401, 'invalid_token'), problem(401, 'session_expired'))
        client = SiloClient(
            'http://silo.lan:8090', DEVICE, Credentials(accessToken='A1', refreshToken='R1'), session=session
        )
        client.onSessionEnded = mock.Mock()

        with self.assertRaises(SiloAuthError):
            client.request('GET', '/api/v2/home')

        client.onSessionEnded.assert_called_once_with()

    def test_a_wrong_password_ends_no_session(self):
        client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(), session=FakeSession(problem(401, 'invalid')))
        client.onSessionEnded = mock.Mock()

        with self.assertRaises(SiloAuthError):
            client.request('POST', '/api/v2/auth/login', json={'username': 'sam', 'password': 'bad'}, profile=False)

        client.onSessionEnded.assert_not_called()

    def test_the_account_tells_the_add_on(self):
        acct = account(FakeSession(problem(401, 'invalid_token'), problem(401, 'session_expired')))

        with mock.patch.object(pnUtil, 'APP') as app, self.assertRaises(SiloAuthError):
            acct.updateHomeUsers()

        app.trigger.assert_called_once_with('account:session_ended')

    def test_home_closes_for_sign_in(self):
        window = library.LibraryWindow.__new__(library.LibraryWindow)
        window._shuttingDown = False
        window._closeSessionWithOption = mock.Mock()

        window.onSessionEnded()

        window._closeSessionWithOption.assert_called_once_with('session_ended')
