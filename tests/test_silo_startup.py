"""
lib/silo.py init(): SM4K's start-up. With no server it asks for one, with no session it signs in with a code,
and then it hands Uno's screens a SiloAccount and a SiloServerManager through plexapp, the way plex.init() handed
them Plex's.
"""

import json
from collections import deque
from unittest import mock

from kodienv import ENV
from plexnet import plexapp
from plexnet import util as pnUtil
from silonet import auth
from silonet.discovery import Found

from lib import silo

from .base import KodiTestCase, fixture
from .silonet_fakes import FakeResponse, FakeSession, problem

PROFILES = FakeResponse(200, {'items': [{'id': 'p1', 'name': 'Sam', 'has_pin': False, 'is_primary': True}]})
STORED = {'base_url': 'http://silo.lan:8090', 'server_id': 'srv-1', 'name': 'Silo'}


def approved(access='A1'):
    return auth.PollResult(
        {
            'status': 'approved',
            'profile_id': 'p1',
            'profile_token': '',
            'tokens': {'access_token': access, 'refresh_token': 'R1', 'user': {'role': 'admin'}},
        }
    )


def fullLogin():
    """A code sign-in approved for the whole account: Silo's poll answer then binds no profile ("profile_id: empty
    for a full login", /api/v2/openapi.json)."""
    return auth.PollResult(
        {
            'status': 'approved',
            'profile_id': '',
            'profile_token': '',
            'tokens': {'access_token': 'A1', 'refresh_token': 'R1', 'user': {'role': 'admin'}},
        }
    )


def profiles(*items):
    """/api/v2/profiles answering these (id, has_pin) profiles."""
    return FakeResponse(200, {'items': [{'id': pid, 'name': pid, 'has_pin': pin} for pid, pin in items]})


def asksForProfile(account):
    """lib/main.py _main()'s test for showing the profile picker before Home."""
    return not account.isOffline and not account.isAuthenticated and (len(account.homeUsers) > 1 or account.isProtected)


STORED_SESSION = {'server_id': 'srv-1', 'access_token': 'A1', 'refresh_token': 'R1', 'profile_id': 'p1'}


def probeAnswers():
    return [
        FakeResponse(200, json.loads(fixture('silonet', 'system_info.json'))),
        FakeResponse(200, {'server_id': 'srv-1'}),
        FakeResponse(200, json.loads(fixture('silonet', 'theme_branding.json'))),
    ]


class SiloStartupTest(KodiTestCase):
    def setUp(self):
        super().setUp()
        self.patches = [
            mock.patch.object(plexapp, 'ACCOUNT', None),
            mock.patch.object(plexapp, 'SERVERMANAGER', None),
            mock.patch.object(pnUtil, 'ACCOUNT', None, create=True),
            mock.patch.object(pnUtil, 'SERVERMANAGER', None, create=True),
            mock.patch.object(silo.discovery, 'find', side_effect=lambda: self.found),
        ]
        self.found = []
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_first_run_asks_for_server_then_code(self):
        ENV.dialog_answers = deque(['silo.lan:8090'])
        session = FakeSession(*probeAnswers(), PROFILES)

        with mock.patch.object(silo, 'signIn', return_value=approved()) as signIn:
            self.assertTrue(silo.init(session=session))

        signIn.assert_called_once()
        self.assertEqual([s.serverId for s in silo.storedServers()], ['srv-1'])
        self.assertEqual(plexapp.ACCOUNT.ID, 'p1')
        self.assertTrue(plexapp.ACCOUNT.isAdmin)
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-1')
        [stored] = json.loads(ENV.settings[silo.CREDENTIALS_SETTING])  # one session per server
        self.assertEqual((stored['server_id'], stored['access_token'], stored['profile_id']), ('srv-1', 'A1', 'p1'))

    def test_change_moves_to_the_new_address(self):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.dialog_answers = deque(['silo.lan:8091'])
        session = FakeSession(*probeAnswers(), PROFILES)  # the same server (srv-1) at its new address

        with mock.patch.object(silo, 'signIn', side_effect=[silo.CHANGE, approved()]) as signIn:
            self.assertTrue(silo.init(session=session))

        address = [c for c in ENV.dialog_calls if c[0] == 'input'][0][1][1]
        self.assertEqual(address, 'http://silo.lan:8090')  # pre-filled with the stored one
        self.assertEqual([c[0][1] for c in signIn.call_args_list], ['http://silo.lan:8090', 'http://silo.lan:8091'])
        self.assertEqual([s.baseUrl for s in silo.storedServers()], ['http://silo.lan:8091'])

    def test_change_given_up_shows_the_card_again(self):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.dialog_answers = deque([''])

        with mock.patch.object(silo, 'signIn', side_effect=[silo.CHANGE, None]) as signIn:
            self.assertFalse(silo.init(session=FakeSession()))

        self.assertEqual([c[0][1] for c in signIn.call_args_list], ['http://silo.lan:8090'] * 2)

    def test_signed_in_single_profile_goes_home(self):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps(
            {'server_id': 'srv-1', 'access_token': 'A1', 'refresh_token': 'R1', 'profile_id': 'p1'}
        )
        session = FakeSession(PROFILES)

        with mock.patch.object(silo, 'signIn') as signIn:
            self.assertTrue(silo.init(session=session))

        signIn.assert_not_called()
        self.assertEqual(session.calls[0]['headers']['Authorization'], 'Bearer A1')
        self.assertEqual([u.title for u in plexapp.ACCOUNT.homeUsers], ['Sam'])
        self.assertTrue(plexapp.ACCOUNT.isAuthenticated)

    def test_ended_session_signs_in_again(self):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps(
            {'server_id': 'srv-1', 'access_token': 'OLD', 'refresh_token': 'R0', 'profile_id': 'p1'}
        )
        session = FakeSession(problem(401, 'invalid_token'), problem(401, 'session_expired'), PROFILES)

        with mock.patch.object(silo, 'signIn', return_value=approved('NEW')):
            self.assertTrue(silo.init(session=session))

        self.assertEqual(session.calls[-1]['headers']['Authorization'], 'Bearer NEW')

    def test_credentials_of_another_server_are_ignored(self):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps({'server_id': 'other', 'access_token': 'X'})

        with mock.patch.object(silo, 'signIn', return_value=approved()) as signIn:
            silo.init(session=FakeSession(PROFILES))

        signIn.assert_called_once()

    def test_no_server_entered_ends_start_up(self):
        ENV.dialog_answers = deque([''])

        self.assertFalse(silo.init(session=FakeSession()))

    def test_unreachable_server_shows_the_card_with_change(self):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps(STORED_SESSION)
        import requests

        with mock.patch.object(silo, 'signIn', return_value=None) as signIn:
            self.assertFalse(silo.init(session=FakeSession(requests.ConnectionError('down'))))

        self.assertEqual(signIn.call_args[0][1:], ('http://silo.lan:8090',))
        self.assertEqual(signIn.call_args[1], {'reachable': False})

    def test_a_moved_server_is_found_again(self):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps(STORED_SESSION)
        self.found = [Found('Silo', 'http://silo.lan:8091', 'srv-1')]
        import requests

        session = FakeSession(requests.ConnectionError('down'), *probeAnswers(), PROFILES)
        with mock.patch.object(silo, 'signIn') as signIn:
            self.assertTrue(silo.init(session=session))

        signIn.assert_not_called()  # the stored session goes on at the new address
        self.assertEqual([s.baseUrl for s in silo.storedServers()], ['http://silo.lan:8091'])
        self.assertEqual(session.calls[-1]['url'], 'http://silo.lan:8091/api/v2/profiles')

    def test_another_server_claiming_the_id_is_not_taken(self):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps(STORED_SESSION)
        self.found = [Found('Silo', 'http://silo.lan:8091', 'srv-1')]
        answers = probeAnswers()
        answers[1] = FakeResponse(200, {'server_id': 'srv-9'})  # its identity says otherwise
        import requests

        with mock.patch.object(silo, 'signIn', return_value=None) as signIn:
            self.assertFalse(silo.init(session=FakeSession(requests.ConnectionError('down'), *answers)))

        self.assertEqual(signIn.call_args[1], {'reachable': False})
        self.assertEqual([s.baseUrl for s in silo.storedServers()], ['http://silo.lan:8090'])

    def signInWith(self, result, *answers):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        with mock.patch.object(silo, 'signIn', return_value=result):
            self.assertTrue(silo.init(session=FakeSession(*answers)))
        return plexapp.ACCOUNT

    def startWith(self, stored, *answers):
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED])
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps(stored)
        with mock.patch.object(silo, 'signIn') as signIn:
            self.assertTrue(silo.init(session=FakeSession(*answers)))
        signIn.assert_not_called()
        return plexapp.ACCOUNT

    def test_code_sign_in_with_several_profiles_asks_which(self):
        acct = self.signInWith(fullLogin(), profiles(('p1', False), ('p2', False)))

        self.assertIsNone(acct.ID)
        self.assertTrue(asksForProfile(acct))

    def test_code_sign_in_with_one_profile_without_a_pin_goes_home_as_it(self):
        acct = self.signInWith(fullLogin(), profiles(('p1', False)))

        self.assertEqual(acct.ID, 'p1')
        self.assertFalse(asksForProfile(acct))
        self.assertEqual(json.loads(ENV.settings[silo.CREDENTIALS_SETTING])[0]['profile_id'], 'p1')

    def test_code_sign_in_with_one_protected_profile_asks_for_its_pin(self):
        acct = self.signInWith(fullLogin(), profiles(('p1', True)))

        self.assertEqual(acct.ID, 'p1')
        self.assertTrue(asksForProfile(acct))

    def test_code_sign_in_bound_to_a_profile_goes_home_as_it(self):
        acct = self.signInWith(approved(), profiles(('p1', False), ('p2', False)))

        self.assertEqual(acct.ID, 'p1')
        self.assertFalse(asksForProfile(acct))

    def test_next_start_with_several_profiles_asks_again(self):
        acct = self.startWith(STORED_SESSION, profiles(('p1', False), ('p2', False)))

        self.assertEqual(acct.ID, 'p1')
        self.assertTrue(asksForProfile(acct))

    def test_automatically_sign_in_keeps_the_stored_profile(self):
        ENV.settings['auto_signin'] = 'true'

        acct = self.startWith(STORED_SESSION, profiles(('p1', False), ('p2', True)))

        self.assertEqual(acct.ID, 'p1')
        self.assertFalse(asksForProfile(acct))

    def test_a_stored_profile_that_is_gone_asks_again(self):
        acct = self.startWith(dict(STORED_SESSION, profile_id='p9'), profiles(('p1', False), ('p2', False)))

        self.assertIsNone(acct.ID)
        self.assertTrue(asksForProfile(acct))


class SiloServerSwitchTest(KodiTestCase):
    """Start-up after another server was picked (lib/silo.py pickServer()): each server keeps its own session, and
    giving up on the new one's sign-in opens the server the user came from."""

    def setUp(self):
        super().setUp()
        for p in (
            mock.patch.object(plexapp, 'ACCOUNT', None),
            mock.patch.object(plexapp, 'SERVERMANAGER', None),
            mock.patch.object(pnUtil, 'ACCOUNT', None, create=True),
            mock.patch.object(pnUtil, 'SERVERMANAGER', None, create=True),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_each_server_keeps_its_own_session(self):
        silo.saveCredentials('srv-1', silo.Credentials('A1', 'R1', 'p1'), {'username': 'sam'})
        silo.saveCredentials('srv-2', silo.Credentials('B1', 'R2', 'p9'), {'username': 'kim'})

        first, firstUser = silo.loadCredentials('srv-1')
        second, _ = silo.loadCredentials('srv-2')

        self.assertEqual((first.accessToken, first.profileId, firstUser['username']), ('A1', 'p1', 'sam'))
        self.assertEqual((second.accessToken, second.profileId), ('B1', 'p9'))

    def test_a_session_stored_before_there_were_several_is_still_read(self):
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps(STORED_SESSION)

        self.assertEqual(silo.loadCredentials('srv-1')[0].accessToken, 'A1')

    def test_back_on_the_new_servers_card_returns_to_the_previous_one(self):
        other = {'base_url': 'http://b.lan:8090', 'server_id': 'srv-2', 'name': 'Silo B'}
        ENV.settings[silo.SERVERS_SETTING] = json.dumps([STORED, other])  # srv-2 picked: stored last
        ENV.settings[silo.CREDENTIALS_SETTING] = json.dumps(STORED_SESSION)  # signed in to srv-1 only

        with mock.patch.object(silo, 'signIn', return_value=None) as signIn:  # Back on srv-2's card
            self.assertTrue(silo.initAfterSwitch('srv-1', session=FakeSession(PROFILES)))

        self.assertEqual(signIn.call_args[0][1], 'http://b.lan:8090')
        self.assertEqual(plexapp.SERVERMANAGER.selectedServer.uuid, 'srv-1')
        self.assertEqual(silo.storedServers()[-1].serverId, 'srv-1')
