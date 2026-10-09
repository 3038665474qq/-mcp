"""Synthetic tests only: no real Yinxiang note is queried or exported."""
import base64
import hashlib
import html
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yinxiang_server as server


def fixture(body=None, title="测试笔记", count=1, filename="../../outside.png"):
    images = (b"synthetic PNG one", b"synthetic PNG two")
    hashes = [hashlib.md5(image, usedforsecurity=False).hexdigest() for image in images]
    if body is None:
        body = f'<div>第一段</div><en-media type="image/png" hash="{hashes[1]}"/><p>第二段</p><en-media type="image/png" hash="{hashes[0]}"/>'
    resources = "".join(
        '<resource><data encoding="base64">' + base64.b64encode(image).decode() +
        '</data><mime>image/png</mime><resource-attributes><file-name>' +
        html.escape(filename) + '</file-name></resource-attributes></resource>'
        for image in images
    )
    note = '<note><title>' + html.escape(title) + '</title><content><![CDATA[<en-note>' + body + '</en-note>]]></content><created>20260101T010203Z</created>' + resources + '</note>'
    return ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE en-export SYSTEM "https://xml.evernote.com/pub/evernote-export.dtd"><en-export>' + note * count + '</en-export>').encode(), hashes


class BridgeTests(unittest.TestCase):
    def setUp(self):
        server._cache.clear()

    def test_two_images_preserve_body_order_and_bytes(self):
        data, hashes = fixture()
        with patch.object(server, "_export_enex", return_value=data):
            result = server.find_notes("测试")
        self.assertNotIn("enml", result["matches"][0])
        self.assertNotIn("markdown", result["matches"][0])
        note_id = result["matches"][0]["note_id"]
        read = server.read_note(note_id)
        self.assertLess(read["markdown"].index(hashes[1]), read["markdown"].index(hashes[0]))
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "BASE_DIR", Path(directory)):
            prepared = server.prepare_article(note_id)
            rendered = Path(prepared["html_path"]).read_text(encoding="utf-8")
            self.assertLess(rendered.index(hashes[1]), rendered.index(hashes[0]))
            for resource in prepared["resources"]:
                path = Path(resource["path"])
                self.assertEqual(path.parent, Path(prepared["directory"]) / "resources")
                self.assertEqual(hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest(), resource["hash"])
                self.assertNotIn("..", path.name)
            self.assertFalse((Path(directory) / "outside.png").exists())
            Path(prepared["markdown_path"]).write_text("user edit", encoding="utf-8")
            server.prepare_article(note_id)
            self.assertEqual(Path(prepared["markdown_path"]).read_text(encoding="utf-8"), "user edit")

    def test_entities_blocked_in_export_and_inner_enml(self):
        malicious = b'<!DOCTYPE en-export [<!ENTITY x SYSTEM "file:///C:/secret.txt">]><en-export><note><title>&x;</title></note></en-export>'
        with self.assertRaisesRegex(server.BridgeError, "forbidden XML"):
            server._parse_enex(malicious, 10)
        inner = '<!DOCTYPE en-note [<!ENTITY x "secret">]><en-note>&x;</en-note>'
        exported = ('<en-export><note><title>x</title><content><![CDATA[' + inner + ']]></content></note></en-export>').encode()
        with self.assertRaisesRegex(server.BridgeError, "forbidden XML"):
            server._parse_enex(exported, 10)

    def test_standard_enml_named_entities_keep_escaped_markup_as_text(self):
        enml = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
                '<en-note><div>标题&nbsp;&copy; &lt;script&gt;alert(1)&lt;/script&gt; '
                '&amp;nbsp; &quot;quoted&quot; &apos;literal&apos;</div></en-note>')
        exported = ('<en-export><note><title>HTML entities</title><content><![CDATA['
                    + enml + ']]></content></note></en-export>').encode()
        note = server._parse_enex(exported, 1)[0]
        self.assertEqual(note.enml, enml)
        rendered, warnings = server._render_body(note)
        self.assertIn("标题\u00a0©", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&amp;nbsp;", rendered)
        self.assertFalse(warnings)
        with patch.object(server, "_export_enex", return_value=exported):
            result = server.find_notes("HTML entities")
        read = server.read_note(result["matches"][0]["note_id"])
        self.assertIn("©", read["markdown"])
        # CDATA is already literal text and must not be entity-normalized.
        literal = server._safe_enml('<en-note><![CDATA[&nbsp; &copy;]]></en-note>')
        self.assertEqual(literal.text, "&nbsp; &copy;")

    def test_named_entity_normalization_does_not_allow_custom_dtd_entities(self):
        for declaration, content in (
            ('<!ENTITY x "secret">', '&x;'),
            ('<!ENTITY nbsp SYSTEM "file:///C:/secret.txt">', '&nbsp;'),
            ('<!ENTITY copy "custom replacement">', '&copy;'),
        ):
            enml = '<!DOCTYPE en-note [' + declaration + ']><en-note>' + content + '</en-note>'
            with self.subTest(declaration=declaration), self.assertRaisesRegex(server.BridgeError, "forbidden XML"):
                server._safe_enml(enml)
        with self.assertRaisesRegex(server.BridgeError, "invalid"):
            server._safe_enml('<en-note>&unknownEntity;</en-note>')

    def test_empty_and_injected_query_never_calls_enscript(self):
        with patch.object(server, "_export_enex") as export:
            for title in ("", "  ", 'x" any:', "x\nany:", "*", "abc\\", "x\u0085", "x\u2028"):
                with self.subTest(title=title), self.assertRaises(server.BridgeError):
                    server.find_notes(title)
            for notebook in ('x" any:', "x\t"):
                with self.assertRaises(server.BridgeError):
                    server.find_notes("valid", notebook)
            for limit in (0, 21, True):
                with self.assertRaises(server.BridgeError):
                    server.find_notes("valid", limit=limit)
            export.assert_not_called()
        self.assertEqual(server._build_query("中文 标题", "笔记本"), 'notebook:"笔记本" intitle:"中文 标题"')

    def test_enscript_error_and_no_match(self):
        with patch.object(server, "_export_enex", side_effect=server.BridgeError("ENScript failed (exit 2)")):
            with self.assertRaisesRegex(server.BridgeError, "exit 2"):
                server.find_notes("test")
        for data in (b"", b"<en-export/>"):
            with patch.object(server, "_export_enex", return_value=data):
                with self.assertRaisesRegex(server.BridgeError, "No notes matched"):
                    server.find_notes("test")
        self.assertEqual(len(server._cache), 0)

    def test_limits_duplicates_and_expiration(self):
        data, _ = fixture(count=2)
        with patch.object(server, "_export_enex", return_value=data):
            with self.assertRaisesRegex(server.BridgeError, "exceeding limit"):
                server.find_notes("test", limit=1)
            matches = server.find_notes("test")
            self.assertTrue(matches["selection_required"])
            self.assertEqual(matches["count"], 2)
            ids = [entry["note_id"] for entry in matches["matches"]]
            self.assertNotEqual(*ids)
            for _ in range(10):
                server.find_notes("test")
        self.assertEqual(len(server._cache), 20)
        with self.assertRaisesRegex(server.BridgeError, "expired"):
            server.read_note(ids[0])
        for note_id in ("../../test", "C:\\secret", ""):
            with self.assertRaisesRegex(server.BridgeError, "Invalid note_id"):
                server.prepare_article(note_id)

    def test_active_html_is_removed_and_remote_images_not_loaded(self):
        body = ('<script>alert(1)</script><div onclick="bad()" style="background:url(https://x)">safe</div>'
                '<a href="javascript:bad()">bad</a><a href="https://example.com">good</a>'
                '<img src="https://example.com/tracker.png"/><svg><script>x</script></svg>')
        data, _ = fixture(body=body)
        note = server._parse_enex(data, 1)[0]
        rendered, warnings = server._render_body(note)
        self.assertNotIn("script", rendered)
        self.assertNotIn("onclick", rendered)
        self.assertNotIn("style=", rendered)
        self.assertNotIn("tracker.png", rendered)
        self.assertIn('href="https://example.com"', rendered)
        self.assertIn("safe", rendered)
        self.assertTrue(warnings)

    def test_status_does_not_read_notes(self):
        with patch.object(server, "_command") as command:
            result = server.status()
        command.assert_not_called()
        self.assertFalse(result["currently_selected_note_supported"])

    def test_command_failures_timeout_and_size_limit(self):
        class FakeProcess:
            def __init__(self, returncode):
                self.returncode = returncode
                self.killed = False

            def poll(self):
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

            def wait(self, timeout=None):
                return self.returncode

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            executable = folder / "ENScript.exe"
            executable.write_bytes(b"not executed")
            target = folder / "notes.enex"
            with patch.object(server, "ENSCRIPT", executable):
                process = FakeProcess(1)
                with patch.object(server.subprocess, "Popen", return_value=process):
                    with self.assertRaisesRegex(server.BridgeError, "No notes may match"):
                        server._command(["exportNotes"], folder, target)
                process = FakeProcess(3)
                with patch.object(server.subprocess, "Popen", return_value=process):
                    with self.assertRaisesRegex(server.BridgeError, "exit 3"):
                        server._command(["exportNotes"], folder, target)
                process = FakeProcess(None)
                with patch.object(server.subprocess, "Popen", return_value=process), patch.object(server, "COMMAND_TIMEOUT", -1):
                    with self.assertRaisesRegex(server.BridgeError, "timed out"):
                        server._command(["exportNotes"], folder, target)
                self.assertTrue(process.killed)
                process = FakeProcess(None)
                def oversized(*args, **kwargs):
                    kwargs["stdout"].write(b"x" * 17)
                    kwargs["stdout"].flush()
                    return process
                with patch.object(server.subprocess, "Popen", side_effect=oversized), patch.object(server, "MAX_EXPORT_BYTES", 16):
                    with self.assertRaisesRegex(server.BridgeError, "25 MB"):
                        server._command(["exportNotes"], folder, target)
                self.assertTrue(process.killed)

    def test_export_redirects_stdout_without_native_file_argument(self):
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "BASE_DIR", Path(directory)):
            with patch.object(server, "_command", return_value=data) as command:
                exported = server._export_enex('intitle:"优化指标"')
            args, folder, target = command.call_args.args
            self.assertEqual(args, ["exportNotes", "/q", 'intitle:"优化指标"', "/s", "personal"])
            self.assertNotIn("/f", args)
            self.assertEqual(target.parent, folder)
            self.assertEqual(target.name, "notes.enex")
            self.assertEqual(exported, data)

    def test_stdout_export_stays_separate_from_stderr(self):
        data, _ = fixture()
        class CompletedProcess:
            returncode = 0
            def poll(self):
                return self.returncode
        def output_export(*args, **kwargs):
            self.assertFalse(kwargs["shell"])
            self.assertIsNot(kwargs["stdout"], kwargs["stderr"])
            kwargs["stdout"].write(data)
            kwargs["stderr"].write(b"Diagnostic warning, not XML")
            return CompletedProcess()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            executable = folder / "ENScript.exe"
            executable.write_bytes(b"not executed")
            target = folder / "notes.enex"
            with patch.object(server, "ENSCRIPT", executable), patch.object(server.subprocess, "Popen", side_effect=output_export):
                exported = server._command(["exportNotes", "/q", 'intitle:"test"'], folder, target)
            self.assertEqual(exported, data)
            self.assertEqual(len(server._parse_enex(exported, 1)), 1)
            self.assertEqual((folder / "stderr.log").read_bytes(), b"Diagnostic warning, not XML")

    def test_failed_export_with_empty_body_is_never_accepted(self):
        class FailedProcess:
            returncode = 1
            def poll(self):
                return self.returncode
        def incomplete_export(*args, **kwargs):
            kwargs["stdout"].write(b'<en-export><note><title>matched</title><content/></note></en-export>')
            kwargs["stderr"].write(b"Can't write to standard output, error: No error")
            return FailedProcess()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            executable = folder / "ENScript.exe"
            executable.write_bytes(b"not executed")
            with patch.object(server, "ENSCRIPT", executable), patch.object(server.subprocess, "Popen", side_effect=incomplete_export):
                with self.assertRaisesRegex(server.BridgeError, "empty note content"):
                    server._command(["exportNotes", "/q", 'intitle:"matched"'], folder, folder / "notes.enex")


if __name__ == "__main__":
    unittest.main()
