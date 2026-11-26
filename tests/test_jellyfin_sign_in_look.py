"""
JellyMod's two sign-in screens in Jellyfin's look (its Modern pages, 12.x), each using the whole screen with no panel:
Connect to Server (Host, Connect, Cancel) before a server is known, then Sign in to continue: the server's users as
tiles above Use Quick Connect, Manual Login and Switch server, stacked; User and Password with a Quick Connect code and
QR below them; the code and QR on their own. Jellyfin's logo is credited in LICENSE.txt.
"""

import os
import xml.etree.ElementTree as ET

from . import REPO_ROOT
from .base import KodiTestCase, make_engine

LOGO = 'script.plex/jellyfin/logo.png'
SILHOUETTE = 'script.plex/jellyfin/avatar.png'
PANEL = 'FF1B2433'  # the old card's and picker's panel colour


class SignInLookTest(KodiTestCase):
    def rendered(self, name):
        engine = make_engine(self.mktemp())
        engine.apply('modern-colored', lambda *args: None, templates=[name])
        with open(os.path.join(engine.target_dir, f'script-plex-{name}.xml'), encoding='utf-8') as fp:
            return ET.fromstring(fp.read())

    @staticmethod
    def textures(root):
        return [t for t in root.iter() if t.tag.startswith('texture')]

    @staticmethod
    def by_id(root):
        return {c.get('id'): c for c in root.iter('control') if c.get('id')}

    @staticmethod
    def group_showing(root, mode_condition):
        return next(
            g for g in root.iter('control') if g.get('type') == 'group' and g.findtext('visible') == mode_condition
        )

    def test_connect_to_server_fills_the_screen(self):
        screen = self.rendered('pin_login')
        labels = [c.findtext('label') or '' for c in screen.iter('control')]
        for prop in ('label.title', 'label.host', 'label.hint', 'found', 'error'):
            self.assertTrue(any(f'Window.Property({prop})' in label for label in labels), prop)
        controls = self.by_id(screen)
        self.assertTrue({'201', '203', '204'} <= set(controls))  # Host, Connect, Cancel
        connect, cancel = controls['203'], controls['204']
        self.assertEqual(connect.find('texturenofocus').get('colordiffuse'), 'FF3D36B2')  # Jellyfin's indigo
        self.assertEqual(connect.findtext('posx'), cancel.findtext('posx'))
        self.assertEqual(connect.findtext('width'), '1600')
        self.assertIn(LOGO, [t.text for t in self.textures(screen)])
        self.assertNotIn(PANEL, [t.get('colordiffuse') for t in self.textures(screen)])

    def test_sign_in_to_continue_shows_users_above_three_stacked_buttons(self):
        screen = self.rendered('user_select')
        users = self.group_showing(screen, 'String.IsEmpty(Window.Property(mode))')
        ids = [c.get('id') for c in users.iter('control') if c.get('id')]
        self.assertEqual(ids, ['101', '401', '402', '403'])
        buttons = [self.by_id(users)[i] for i in ('401', '402', '403')]
        self.assertEqual({b.findtext('posx') for b in buttons}, {'160'})
        self.assertEqual({b.findtext('width') for b in buttons}, {'1600'})
        tops = [b.findtext('posy') for b in buttons]
        self.assertEqual(len(set(tops)), 3)  # stacked, one under the other
        self.assertEqual(self.by_id(users)['101'].findtext('width'), '1920')  # the row spans the screen
        textures = [t.text or '' for t in self.textures(screen)]
        self.assertIn(LOGO, textures)
        self.assertIn(SILHOUETTE, textures)
        self.assertFalse([t for t in textures if 'protected' in t or 'admin' in t])
        self.assertNotIn(PANEL, [t.get('colordiffuse') for t in self.textures(screen)])

    def test_the_form_has_a_quick_connect_code_and_qr_below_it(self):
        screen = self.rendered('user_select')
        form = self.group_showing(screen, 'String.IsEqual(Window.Property(mode),form)')
        self.assertTrue({'201', '202', '203', '205'} <= set(self.by_id(form)))  # User, Password, Sign In, Cancel
        qr = [c for c in form.iter('control') if c.findtext('texture') == '$INFO[Window.Property(qr)]']
        code = [c for c in form.iter('control') if c.findtext('label') == '$INFO[Window.Property(pin)]']
        self.assertEqual((len(qr), len(code)), (1, 1))
        sign_in_top = self.by_id(form)['203'].findtext('posy')
        self.assertGreater(float(qr[0].findtext('posy')), float(sign_in_top))  # below the form

    def test_use_quick_connect_shows_the_code_and_qr(self):
        screen = self.rendered('user_select')
        view = self.group_showing(screen, 'String.IsEqual(Window.Property(mode),quickconnect)')
        self.assertIn('206', self.by_id(view))  # Cancel
        self.assertTrue([c for c in view.iter('control') if c.findtext('texture') == '$INFO[Window.Property(qr)]'])
        self.assertTrue([c for c in view.iter('control') if c.findtext('label') == '$INFO[Window.Property(pin)]'])

    def test_license_credits_jellyfins_logo(self):
        with open(os.path.join(REPO_ROOT, 'LICENSE.txt'), encoding='utf-8') as fp:
            text = fp.read()
        for needed in (
            'resources/skins/Main/media/script.plex/jellyfin/logo.png',
            'CC BY-SA 4.0',
            'https://github.com/jellyfin/jellyfin-ux',
        ):
            self.assertTrue(needed in text, f'LICENSE.txt does not mention {needed}')
