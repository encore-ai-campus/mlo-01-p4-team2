import re
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.template import Context, Engine
from django.test import RequestFactory, SimpleTestCase, override_settings

from config import settings as project_settings
from .rendering import render_document, template_engine


class MapRenderingTests(SimpleTestCase):
    def map_request(self):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(
            pk=1,
            is_authenticated=True,
            is_staff=True,
            campus_profile=SimpleNamespace(nickname="encore"),
        )
        return request

    def test_development_template_edit_is_visible_in_the_same_engine(self):
        if not project_settings.DEBUG:
            self.skipTest("Production templates intentionally use a cache.")
        with TemporaryDirectory() as directory:
            template = Path(directory) / "index.html"
            template.write_text("old map markup", encoding="utf-8")
            (Path(directory) / 'dist').mkdir()
            template = Path(directory) / 'dist/index.html'
            template.write_text('old map markup', encoding='utf-8')
            engine = template_engine(Path(directory), debug=True)
            self.assertEqual(
                engine.get_template("index.html").render(Context()), "old map markup"
            )
            template.write_text("new map markup", encoding="utf-8")
            self.assertEqual(
                engine.get_template("index.html").render(Context()), "new map markup"
            )

    def test_authenticated_map_updates_script_revision_after_a_live_edit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            import shutil
            for folder in ('accounts/templates', 'learning/templates'):
                shutil.copytree(project_settings.BASE_DIR / folder, root / folder)
            shutil.copy2(project_settings.BASE_DIR / 'dist/index.html', root / 'dist/index.html')
            script = root / "dist" / "campus.js"
            script.write_text("const version = 1;", encoding="utf-8")
            data = {'template': 'index.html', 'context': {'nickname': 'encore',
                    'user': {'is_authenticated': True, 'is_staff': True},
                    'view_config': {'canZoom': True}, 'presence_config': {},
                    'session_policy_config': {}, 'csrf_token': 'test-token'}}
            before, _ = render_document(data, root)
            script.write_text("const version = 2;", encoding="utf-8")
            after, _ = render_document(data, root)
            pattern = r'src="campus\.js\?v=([0-9a-f]+)"'
            before_revision = re.search(pattern, before)
            after_revision = re.search(pattern, after)
            self.assertIsNotNone(before_revision)
            self.assertIsNotNone(after_revision)
            self.assertNotEqual(before_revision.group(1), after_revision.group(1))
            self.assertIn('id="boothEmbeddedSection"', after)
            self.assertIn('"canZoom": true', after)

    def test_pinned_theme_revision_changes_when_its_loader_changes(self):
        import shutil
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'dist').mkdir()
            for folder in ('accounts/templates', 'learning/templates'):
                shutil.copytree(project_settings.BASE_DIR / folder, root / folder)
            shutil.copy2(project_settings.BASE_DIR / 'dist/index.html', root / 'dist/index.html')
            script = root / 'dist/pepero-theme.js'
            context = {'user': {'is_authenticated': True, 'is_staff': False}, 'nickname': 'viewer',
                       'view_config': {}, 'presence_config': {}, 'session_policy_config': {}, 'csrf_token': 'token'}
            script.write_text('old image loader')
            before, _ = render_document({'template': 'index.html', 'context': context}, root)
            script.write_text('new image loader with pairing garden')
            after, _ = render_document({'template': 'index.html', 'context': context}, root)
            pattern = r'src="pepero-theme\.js\?v=([^"]+)"'
            self.assertNotEqual(re.search(pattern, before).group(1), re.search(pattern, after).group(1))

    def test_ad_modules_load_only_when_the_backend_enables_ads(self):
        for enabled in (False, True):
            context = {'user': {'is_authenticated': True, 'is_staff': False}, 'nickname': 'viewer',
                       'view_config': {}, 'presence_config': {}, 'session_policy_config': {},
                       'csrf_token': 'token', 'ads_enabled': enabled}
            html, _ = render_document({'template': 'index.html', 'context': context}, project_settings.BASE_DIR)
            self.assertEqual('src="campus-ads.js?' in html, enabled)
            self.assertEqual('src="ad-visibility.js?' in html, enabled)
            self.assertEqual('src="roof-slideshow.js?' in html, not enabled)
            # 광고를 켜도 첫 광고 전 옥상 사진을 쓰므로 사진 목록은 늘 싣는다(AB-1).
            self.assertIn('src="roof-slideshow-config.js?', html)

    def test_backend_cannot_choose_an_arbitrary_template(self):
        with self.assertRaises(ValueError):
            render_document({'template': '../secrets', 'context': {}}, project_settings.BASE_DIR)
