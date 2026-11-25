"""
lib/silo.py signIn(): the sign-in card (Uno's PIN window): the server's name and address, a username and password, and
Silo's sign-in code and link, until a password sign-in works or someone approves the code.
"""

import os
from collections import deque
from unittest import mock

from kodienv import ENV
from silonet import auth
from silonet.client import Credentials, DeviceIdentity, SiloClient

from lib import silo
from lib.windows import signin

from .base import KodiTestCase
from .silonet_fakes import FakeResponse, FakeSession, problem
from .test_silonet_auth import TOKENS, polled, started

DEVICE = DeviceIdentity('dev1', 'Living room', 'kodi', '0.4.4')
# what the card asks first (silonet.server_info.loginOptions): branding and auth providers
OPTIONS = (
    FakeResponse(200, {'server_name': 'Silo', 'login_subtitle': 'Sign in with an existing account.'}),
    FakeResponse(200, {'password_login': True}),
)
NO_PASSWORDS = (OPTIONS[0], FakeResponse(200, {'password_login': False}))


def pinWindow():
    window = signin.PinLoginWindow.__new__(signin.PinLoginWindow)
    window._closing = False
    window._winID = None
    window.windowId = 13001
    window.abort = False
    window.closed = False
    window.setFocusId = lambda controlID: setattr(window, 'focused', controlID)

    def doClose():
        window.closed = True

    window.doClose = doClose
    return window


def typed(window, username, password):
    """Once the card shows its code, the user types a username and password and presses Sign in."""
    setCode = window.setCode

    def showCode(code):
        setCode(code)
        ENV.dialog_answers.extend([username, password])
        for control in (window.USERNAME_ID, window.PASSWORD_ID, window.SIGN_IN_ID):
            window.onClick(control)

    window.setCode = showCode
    return window


class SiloSignInTest(KodiTestCase):
    def signIn(self, session, window):
        client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(), session=session)
        with mock.patch.object(signin.PinLoginWindow, 'create', return_value=window):
            return silo.signIn(client, 'http://silo.lan:8090')

    def test_qr_image_is_a_png_in_the_add_ons_profile_folder(self):
        # not Kodi's temp folder: on the user's box Kodi wouldn't load any image from there ("Direct texture file loading
        # failed", a known-good PNG too); from the add-on's own folders it does
        path = signin.qrImage('http://silo.lan:8090/4821-7730?code=4821-7730')

        self.assertEqual(os.path.dirname(path), os.path.join(silo.util.PROFILE, 'qr'))
        with open(path, 'rb') as f:
            self.assertEqual(f.read(8), b'\x89PNG\r\n\x1a\n')

    def test_a_new_code_replaces_the_old_qr_image(self):
        first = signin.qrImage('http://silo.lan:8090/4821-7730?code=4821-7730')

        second = signin.qrImage('http://silo.lan:8090/1111-2222?code=1111-2222')

        # a new name each time: Kodi caches an image by its path
        self.assertNotEqual(first, second)
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.exists(second))

    def test_the_card_shows_the_codes_qr(self):
        window = pinWindow()
        code = auth.DeviceCode(started().json())

        window.setCode(code)

        self.assertEqual(ENV.window_props[13001].get('qr'), signin.qrImage(code.verificationUriComplete))

    def test_code_window_shows_spaced_code_and_link(self):
        window = pinWindow()

        window.setCode(auth.DeviceCode(started().json()))

        props = ENV.window_props[13001]
        self.assertEqual(props.get('pin'), '4821 7730')
        self.assertIn('http://silo.lan:8090/activate', props.get('instructions'))

    def test_approved_sign_in_returns_the_session(self):
        window = pinWindow()

        result = self.signIn(FakeSession(*OPTIONS, started(), polled('approved', tokens=TOKENS)), window)

        self.assertEqual(result.status, 'approved')
        self.assertEqual(result.accessToken, 'A1')
        self.assertEqual(ENV.window_props[13001].get('pin'), '4821 7730')
        self.assertTrue(window.closed)

    def test_back_returns_none_and_cancels(self):
        window = pinWindow()
        window.abort = True
        session = FakeSession(*OPTIONS, started(), FakeResponse(200, {'status': 'canceled'}))

        self.assertIsNone(self.signIn(session, window))

        self.assertTrue(session.calls[-1]['url'].endswith('/api/v2/auth/device/cancel'))
        self.assertTrue(window.closed)

    def test_refused_sign_in_tells_the_user(self):
        with mock.patch.object(silo.util, 'messageDialog') as dialog:
            result = self.signIn(FakeSession(*OPTIONS, started(), polled('denied')), pinWindow())

        self.assertIsNone(result)
        dialog.assert_called_once()
        self.assertIn('refused', dialog.call_args[0][1])

    def test_the_card_shows_the_server_and_the_last_username(self):
        ENV.settings['silo_username'] = 'sam'
        window = pinWindow()
        window.abort = True

        self.signIn(FakeSession(*OPTIONS, started(), FakeResponse(200, {'status': 'canceled'})), window)

        props = ENV.window_props[13001]
        self.assertEqual(
            (props.get('server.name'), props.get('subtitle'), props.get('address'), props.get('username')),
            ('Silo', 'Sign in with an existing account.', 'http://silo.lan:8090', 'sam'),
        )
        self.assertEqual(props.get('password.enabled'), '1')
        self.assertEqual(
            (props.get('label.password.heading'), props.get('label.phone')),
            ('Sign in with your password', 'Use your phone instead'),
        )

    def test_typing_the_password_shows_dots(self):
        window = pinWindow()
        ENV.dialog_answers = deque(['pw'])

        window.onClick(window.PASSWORD_ID)

        self.assertEqual(window.password, 'pw')
        self.assertEqual(ENV.window_props[13001].get('password'), '\N{BULLET}\N{BULLET}')

    def test_password_sign_in_returns_the_session(self):
        window = typed(pinWindow(), 'sam', 'pw')
        session = FakeSession(*OPTIONS, started(), FakeResponse(200, TOKENS), FakeResponse(200, {'status': 'canceled'}))

        result = self.signIn(session, window)

        self.assertEqual((result.status, result.accessToken, result.profileId), ('approved', 'A1', None))
        login = session.calls[3]
        self.assertTrue(login['url'].endswith('/api/v2/auth/login'))
        self.assertEqual(login['json'], {'username': 'sam', 'password': 'pw'})
        # the phone code it no longer needs is withdrawn
        self.assertTrue(session.calls[-1]['url'].endswith('/api/v2/auth/device/cancel'))
        self.assertEqual(ENV.settings.get('silo_username'), 'sam')
        self.assertNotIn('pw', ENV.settings.values())
        self.assertTrue(window.closed)

    def test_a_wrong_password_keeps_the_card_open(self):
        window = typed(pinWindow(), 'sam', 'bad')
        session = FakeSession(
            *OPTIONS, started(), problem(401, 'invalid_credentials'), polled('approved', tokens=TOKENS)
        )

        result = self.signIn(session, window)

        # the card stayed open: the code was still approved on a phone afterwards
        self.assertEqual(result.status, 'approved')
        props = ENV.window_props[13001]
        self.assertIn('Wrong username or password', props.get('error'))
        self.assertEqual((window.username, window.password, props.get('password')), ('sam', '', ''))
        self.assertNotIn('silo_username', ENV.settings)

    def test_change_asks_for_another_server(self):
        window = pinWindow()
        setCode = window.setCode

        def showCode(code):
            setCode(code)
            window.onClick(window.CHANGE_ID)

        window.setCode = showCode
        session = FakeSession(*OPTIONS, started(), FakeResponse(200, {'status': 'canceled'}))

        self.assertIs(self.signIn(session, window), silo.CHANGE)
        self.assertTrue(session.calls[-1]['url'].endswith('/api/v2/auth/device/cancel'))
        self.assertTrue(window.closed)

    def test_an_unreachable_server_says_so_with_change_focused(self):
        window = pinWindow()
        window.abort = True
        session = FakeSession()
        client = SiloClient('http://silo.lan:8090', DEVICE, Credentials(), session=session)

        with mock.patch.object(signin.PinLoginWindow, 'create', return_value=window):
            self.assertIsNone(silo.signIn(client, 'http://silo.lan:8090', reachable=False))

        props = ENV.window_props[13001]
        self.assertIn('Could not reach a Silo server at http://silo.lan:8090', props.get('error'))
        self.assertEqual((window.focused, props.get('password.enabled')), (window.CHANGE_ID, ''))
        self.assertEqual(session.calls, [])  # nothing asked of a server that isn't there
        self.assertTrue(window.closed)

    def test_typing_again_clears_the_last_error(self):
        window = pinWindow()
        window.showError('Wrong username or password.')
        ENV.dialog_answers = deque(['sam'])

        window.onClick(window.USERNAME_ID)

        self.assertEqual(ENV.window_props[13001].get('error'), '')

    def test_signing_in_again_clears_the_last_error(self):
        window = pinWindow()
        window.showError('Wrong username or password.')

        window.onClick(window.SIGN_IN_ID)

        self.assertEqual(ENV.window_props[13001].get('error'), '')

    def test_a_server_without_password_sign_in_focuses_change(self):
        window = pinWindow()
        window.abort = True

        self.signIn(FakeSession(*NO_PASSWORDS, started(), FakeResponse(200, {'status': 'canceled'})), window)

        # the username field it would start on is hidden: Change is the card's one button then
        self.assertEqual((window.focused, ENV.window_props[13001].get('password.enabled')), (window.CHANGE_ID, ''))

    def test_too_many_attempts_says_so(self):
        window = typed(pinWindow(), 'sam', 'pw')
        session = FakeSession(
            *OPTIONS, started(), problem(429, 'rate_limit_exceeded', headers={'Retry-After': '30'}), polled('denied')
        )

        with mock.patch.object(silo.util, 'messageDialog'):
            self.signIn(session, window)

        self.assertIn('Too many sign-in attempts', ENV.window_props[13001].get('error'))
