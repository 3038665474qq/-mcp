import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import publish_html as launcher
import yinxiang_server as bridge


class HTMLLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'export'
        self.source.mkdir()
        self.base = self.root / 'bridge'
        self.base.mkdir()
        self.patches = [patch.object(launcher, 'BASE', self.base), patch.object(bridge, 'BASE_DIR', self.base)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def html(self, body):
        f = self.source / '优化指标.html'
        f.write_text('<html><head><meta charset="utf-8"><title>Evernote Export</title></head><body>' + body + '</body></html>', encoding='utf-8')
        return f

    def test_real_title_and_image_staging_stable_digest(self):
        import base64
        assets = self.source / '配图'
        assets.mkdir()
        data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        (assets / 'a.png').write_bytes(data)
        path = self.html('<p>第一段</p><img src="配图/a.png"><p>第二段</p>')
        key, article = launcher.stage_html(path)
        self.assertEqual(article['title'], '优化指标')
        self.assertEqual(len(article['resources']), 1)
        self.assertEqual(Path(article['resources'][0]['path']).read_bytes(), data)
        self.assertEqual(key, launcher.stage_html(path)[0])
        md = Path(article['markdown_path']).read_text(encoding='utf-8')
        self.assertLess(md.index('第一段'), md.index('!['))
        self.assertLess(md.index('!['), md.index('第二段'))

    def test_missing_external_and_traversal_images_stop(self):
        (self.root / 'secret.png').write_bytes(b'private')
        for src in ['missing.png', 'https://example.com/a.png', '../secret.png', 'file:///C:/a.png']:
            with self.subTest(src=src), self.assertRaises(ValueError):
                launcher.stage_html(self.html(f'<p>内容</p><img src="{src}">'))

    def test_multiple_notes_stop(self):
        with self.assertRaises(ValueError):
            launcher.stage_html(self.html('<a name="1"></a>第一篇<a name="2"></a>第二篇'))

    def test_active_content_does_not_silently_publish(self):
        with self.assertRaisesRegex(ValueError, '转换需要检查'):
            launcher.stage_html(self.html('<p>内容</p><script>alert(1)</script>'))

    def test_mode_authorization_is_distinct(self):
        self.assertIn('绝对不要点击正式发布', launcher.prompt_for('draft', {}, {}, None))
        self.assertIn('已授权', launcher.prompt_for('publish', {}, {}, None))

    def test_cli_routes_required_tool_approvals_to_review(self):
        command = launcher.codex_command('codex.exe', self.root / 'result.json', {})
        self.assertIn('--approve-for-me', command)
        self.assertNotIn('approval_policy="never"', command)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)

    def test_local_npm_cli_keeps_paths_as_separate_arguments(self):
        command = launcher.codex_command(['C:/Node path/node.exe', 'D:/中文 path/codex.js'],
                                         self.root / 'result.json', {'codex_model': 'example-model'})
        self.assertEqual(command[:5], ['C:/Node path/node.exe', 'D:/中文 path/codex.js',
                                      'exec', '--model', 'example-model'])

    def test_portable_cli_uses_current_installation_after_move(self):
        local_js = self.base / '.runtime/codex/node_modules/@openai/codex/bin/codex.js'
        local_js.parent.mkdir(parents=True)
        local_js.write_text('// fixture', encoding='utf-8')
        node = self.root / 'node.exe'
        node.write_bytes(b'fixture')
        with patch('publish_html.shutil.which', return_value=str(node)):
            self.assertEqual(launcher.find_codex(), [str(node), str(local_js)])

    def test_article_survives_child_file_access_denied(self):
        _, article = launcher.stage_html(self.html('<p>正文中文与符号 &amp; &lt;标记&gt;</p><pre>print("末尾")</pre>'))
        content = launcher.article_content(article)
        import json
        # Once captured, constructing and consuming the prompt needs no file read.
        with patch.object(Path, 'read_text', side_effect=PermissionError('Access is denied')):
            prompt = launcher.prompt_for('draft', article, {}, None, content)
            decoded = json.loads(prompt.split('article_content_json=', 1)[1])
        self.assertEqual(decoded, content)
        self.assertIn('正文中文与符号', decoded['markdown'])
        self.assertIn('末尾', decoded['markdown'])
        self.assertIn('不要用 PowerShell', prompt)


if __name__ == '__main__':
    unittest.main()
