"""
The sign-in card and the user picker in Jellyfin's look (its Modern login page, 12.x): a slate backdrop under a top bar
with Jellyfin's logo, "Sign in to continue", one panel; the card's password form with Quick Connect's code and QR beside
it at once; user tiles without locks or PIN pad, and a Manual Login tile. Jellyfin's logo is credited in LICENSE.txt.
"""

import os
import xml.etree.ElementTree as ET
from unittest import mock

from jellynet.server_info import LoginOptions
from plexnet import plexapp

from lib.windows import signin, userselect

from . import REPO_ROOT
from .base import KodiTestCase, make_engine
from .jellynet_fakes import FakeSession
from .test_jellynet_account import PUBLIC_USERS, make_account

LOGO = 'script.plex/jellyfin/logo.png'
SILHOUETTE = 'script.plex/jellyfin/avatar.png'


class SignInLookTest(KodiTestCase):
    def rendered(self, name):
        engine = make_engine(self.mktemp())
        engine.apply('modern-colored', lambda *args: None, templates=[name])
        with open(os.path.join(engine.target_dir, f'script-plex-{name}.xml'), encoding='utf-8') as fp:
            return ET.fromstring(fp.read())

    @staticmethod
    def textures(root):
        return [t.text or '' for t in root.iter() if t.tag.startswith('texture')]

    def test_card_reads_like_jellyfins_sign_in(self):
        props = {}
        window = signin.PinLoginWindow.__new__(signin.PinLoginWindow)
        window.setProperty = props.__setitem__
        window.setFocusId = mock.Mock()
        window.setOptions(LoginOptions(True, name='TheRubys'), 'http://jf.lan:8096', 'alice')

        self.assertEqual(props['label.heading'], 'Sign in to continue')
        self.assertEqual(props['label.phone'], 'Quick Connect')
        card = self.rendered('pin_login')
        self.assertIn(LOGO, self.textures(card))
        labels = [c.findtext('label') or '' for c in card.iter('control')]
        self.assertTrue(any('Window.Property(label.heading)' in label for label in labels))
        self.assertTrue(any('Window.Property(server.name)' in label for label in labels))

    def test_quick_connect_shows_beside_the_form_at_once(self):
        card = self.rendered('pin_login')
        controls = {c.get('id'): c for c in card.iter('control') if c.get('id')}
        self.assertTrue({'201', '202', '203', '204'} <= set(controls))  # username, password, Sign In, Change
        qr = [c for c in card.iter('control') if (c.findtext('texture') or '') == '$INFO[Window.Property(qr)]']
        self.assertEqual(len(qr), 1)
        groups = [g for g in card.iter('control') if g.get('type') == 'group' and qr[0] in list(g.iter('control'))]
        conditions = {g.findtext('visible') for g in groups} - {None}
        # shown whenever Jellyfin offers Quick Connect: no button to press first
        self.assertEqual(conditions, {'!String.IsEmpty(Window.Property(quickconnect.enabled))'})

    def test_card_for_another_user_has_no_server_change(self):
        card = self.rendered('pin_login')
        change = [c for c in card.iter('control') if c.get('id') == '204']
        server_row = [c for c in card.iter('control') if 'Window.Property(label.server)' in (c.findtext('label') or '')]
        for control in change + server_row:
            self.assertEqual(control.findtext('visible'), '!String.IsEmpty(Window.Property(change.enabled))')
        self.assertEqual((len(change), len(server_row)), (1, 1))

    def test_user_picker_reads_like_jellyfins(self):
        acct = make_account(FakeSession(PUBLIC_USERS))
        acct.updateHomeUsers()
        window = userselect.UserSelectWindow.__new__(userselect.UserSelectWindow)
        window.userList = mock.Mock()
        window.setProperty = mock.Mock()
        window.setFocusId = mock.Mock()
        with mock.patch.object(plexapp, 'ACCOUNT', acct):
            window.start(with_busy=False)
        items = window.userList.addItems.call_args[0][0]
        self.assertEqual(items[3].getLabel(), 'Manual Login')

        picker = self.rendered('user_select')
        textures = self.textures(picker)
        self.assertIn(LOGO, textures)
        self.assertIn(SILHOUETTE, textures)
        self.assertFalse([t for t in textures if 'protected' in t or 'admin' in t])  # no locks or admin badges
        ids = {c.get('id') for c in picker.iter('control')}
        self.assertIn('101', ids)
        self.assertFalse(ids & {str(n) for n in range(201, 212)})  # no PIN pad
        labels = [c.findtext('label') or '' for c in picker.iter('control')]
        self.assertTrue(any('35309]' in label for label in labels))  # "Sign in to continue"

    def test_license_credits_jellyfins_logo(self):
        with open(os.path.join(REPO_ROOT, 'LICENSE.txt'), encoding='utf-8') as fp:
            text = fp.read()
        for needed in (
            'resources/skins/Main/media/script.plex/jellyfin/logo.png',
            'CC BY-SA 4.0',
            'https://github.com/jellyfin/jellyfin-ux',
        ):
            self.assertTrue(needed in text, f'LICENSE.txt does not mention {needed}')
