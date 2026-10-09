"""Tests only touch temporary installations and temporary Codex homes."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tomlkit

import configure


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Portable Bridge 中文"
        self.home = self.base / "codex-home"
        self.node = self.base / "node.exe"
        self.node.write_bytes(b"")
        self.create_install(self.root)

    def create_install(self, root):
        for name in (configure.CHROME_SCRIPT, ".venv/Scripts/python.exe", "yinxiang_server.py"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")

    def run_configure(self, **kwargs):
        return configure.configure(root=self.root, config_root=self.home, node=str(self.node), **kwargs)

    def read_config(self):
        return tomlkit.parse((self.home / "config.toml").read_text(encoding="utf-8"))

    def write_config(self, doc):
        self.home.mkdir(exist_ok=True)
        (self.home / "config.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")

    def test_html_only_does_not_require_yinxiang_or_local_python(self):
        (self.root / "yinxiang_server.py").unlink()
        (self.root / ".venv/Scripts/python.exe").unlink()
        result = self.run_configure(html_only=True)
        self.assertEqual(result["registered"], ["chrome_current"])
        self.assertNotIn("yinxiang_local", self.read_config()["mcp_servers"])

    def test_default_retains_both_servers_and_node_env_override(self):
        with patch.dict(os.environ, {"NOTE_BRIDGE_NODE": str(self.node)}):
            servers = configure.server_configs(root=self.root)
        self.assertEqual(set(servers), {"yinxiang_local", "chrome_current"})
        self.assertEqual(servers["chrome_current"]["command"], str(self.node.resolve()))

    def test_idempotent_preserves_comments_and_backups_private_to_install(self):
        original = '# Keep my settings\nmodel = "custom-model"\n'
        self.home.mkdir()
        (self.home / "config.toml").write_text(original, encoding="utf-8")
        result = self.run_configure(html_only=True)
        self.assertEqual(Path(result["backup"]).parent, self.root / "setup-backups")
        self.assertEqual(Path(result["backup"]).read_text(encoding="utf-8"), original)
        self.assertEqual(self.read_config()["model"], "custom-model")
        before = (self.home / "config.toml").read_bytes()
        result = self.run_configure(html_only=True)
        self.assertFalse(result["changed"])
        self.assertEqual((self.home / "config.toml").read_bytes(), before)
        self.assertIn(b"# Keep my settings", before)

    def test_relocate_managed_preserves_approvals_and_enabled_state(self):
        self.run_configure()
        doc = self.read_config()
        old = doc["mcp_servers"]["chrome_current"]
        old["enabled"] = False
        old["tool_timeout_sec"] = 120
        old["tools"] = {"take_snapshot": {"approval_policy": "prompt"}}
        doc["mcp_servers"]["unrelated"] = {"command": "custom", "args": ["abc"]}
        self.write_config(doc)
        moved = self.base / "Another Computer"
        self.create_install(moved)
        configure.configure(root=moved, config_root=self.home, node=str(self.node), replace_managed=True)
        current = self.read_config()["mcp_servers"]
        self.assertEqual(current["chrome_current"]["cwd"], str(moved.resolve()))
        self.assertFalse(current["chrome_current"]["enabled"])
        self.assertEqual(current["chrome_current"]["tool_timeout_sec"], 120)
        self.assertEqual(current["chrome_current"]["tools"]["take_snapshot"]["approval_policy"], "prompt")
        self.assertEqual(current["yinxiang_local"]["args"], [str(moved / "yinxiang_server.py")])
        self.assertEqual(current["unrelated"]["command"], "custom")

    def test_html_only_keeps_existing_yinxiang_registration(self):
        self.run_configure()
        prior = self.read_config()["mcp_servers"]["yinxiang_local"].unwrap()
        self.run_configure(html_only=True)
        self.assertEqual(self.read_config()["mcp_servers"]["yinxiang_local"].unwrap(), prior)

    def test_unrelated_collision_refused_even_with_replace_flag(self):
        self.write_config({"mcp_servers": {"chrome_current": {"command": str(self.node),
                           "args": ["unrelated.js"], "cwd": str(self.root)}}})
        before = (self.home / "config.toml").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
            self.run_configure(html_only=True, replace_managed=True)
        self.assertEqual((self.home / "config.toml").read_bytes(), before)

    def test_relocation_requires_explicit_replace_flag(self):
        self.run_configure()
        moved = self.base / "Moved"
        self.create_install(moved)
        with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
            configure.configure(root=moved, config_root=self.home, node=str(self.node))

    def test_concurrent_edit_is_not_overwritten(self):
        self.home.mkdir()
        config = self.home / "config.toml"
        config.write_text('model = "first"\n', encoding="utf-8")
        original_atomic = configure._atomic_write
        def interleaved(path, content, **kwargs):
            if path == config:
                path.write_text('model = "concurrent"\n', encoding="utf-8")
            return original_atomic(path, content, **kwargs)
        with patch.object(configure, "_atomic_write", side_effect=interleaved):
            with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
                self.run_configure(html_only=True)
        self.assertEqual(self.read_config()["model"], "concurrent")
        self.assertFalse((self.home / "config.note-bridge.lock").exists())
        self.assertEqual(list(self.home.glob("*.tmp")), [])

    def test_existing_initializer_lock_is_respected(self):
        self.home.mkdir()
        lock = self.home / "config.note-bridge.lock"
        lock.write_text("another-process", encoding="ascii")
        with self.assertRaisesRegex(RuntimeError, "Another initialization"):
            self.run_configure(html_only=True)
        self.assertEqual(lock.read_text(), "another-process")
        self.assertFalse((self.home / "config.toml").exists())


if __name__ == "__main__":
    unittest.main()
