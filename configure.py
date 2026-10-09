"""Register this installation in Codex without changing unrelated settings."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime
import json
import ntpath
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

import tomlkit

ROOT = Path(__file__).resolve().parent
CHROME_SCRIPT = "node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js"
CHROME_FLAGS = [
    "--autoConnect", "--no-usage-statistics", "--no-performance-crux",
    "--no-category-network", "--no-category-performance",
    "--no-category-emulation", "--no-category-memory",
]


def chrome_args(root: Path) -> list[str]:
    return [str(root / CHROME_SCRIPT), *CHROME_FLAGS, "--workspace", str(root / "data")]


# Retained for callers of the original installer.
CHROME_ARGS = chrome_args(ROOT)


def server_configs(html_only: bool = False, *, root: Path | None = None,
                   node: str | None = None) -> dict:
    root = Path(root or ROOT).resolve()
    node = node or os.environ.get("NOTE_BRIDGE_NODE") or shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is missing. Run the initialization BAT first.")
    node_path = Path(node).expanduser()
    if not node_path.is_file():
        found = shutil.which(node)
        if not found:
            raise RuntimeError("NOTE_BRIDGE_NODE or the Node.js executable does not exist.")
        node_path = Path(found)
    servers = {}
    if not html_only:
        python = root / ".venv/Scripts/python.exe"
        if not python.is_file():
            raise RuntimeError("The local Python environment is missing. Run initialization first.")
        servers["yinxiang_local"] = {
            "command": str(python), "args": [str(root / "yinxiang_server.py")],
            "cwd": str(root), "startup_timeout_sec": 30, "tool_timeout_sec": 90,
            "enabled": True, "env": {"PYTHONUTF8": "1"},
        }
    servers["chrome_current"] = {
        "command": str(node_path.resolve()), "args": chrome_args(root), "cwd": str(root),
        "startup_timeout_sec": 30, "tool_timeout_sec": 90, "enabled": True,
    }
    return servers


def _normalized_path(value: object) -> str:
    return ntpath.normcase(ntpath.normpath(value)) if isinstance(value, str) else ""


def _managed_entry(name: str, value: object) -> bool:
    """Recognize only the exact connection layout produced by this bridge."""
    if not isinstance(value, dict):
        return False
    cwd, command, args = value.get("cwd"), value.get("command"), value.get("args")
    if not isinstance(cwd, str) or not ntpath.isabs(cwd) or not isinstance(args, list):
        return False
    if name == "yinxiang_local":
        return (_normalized_path(command) == _normalized_path(ntpath.join(cwd, ".venv/Scripts/python.exe"))
                and len(args) == 1
                and _normalized_path(args[0]) == _normalized_path(ntpath.join(cwd, "yinxiang_server.py")))
    if name == "chrome_current":
        return (isinstance(command, str)
                and ntpath.basename(command).lower() in {"node", "node.exe"}
                and len(args) == len(CHROME_FLAGS) + 3
                and _normalized_path(args[0]) == _normalized_path(ntpath.join(cwd, CHROME_SCRIPT))
                and args[1:-2] == CHROME_FLAGS
                and args[-2] == "--workspace"
                and _normalized_path(args[-1]) == _normalized_path(ntpath.join(cwd, "data")))
    return False


def _read_optional(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


@contextmanager
def _config_lock(config_root: Path):
    """Serialize this installer's runs; detect other editors before replacement."""
    lock = config_root / "config.note-bridge.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"Another initialization is using {lock}. Wait for it to finish; "
                           "if its process has ended, remove this stale lock and retry.") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def _atomic_write(path: Path, content: bytes, *, expected: bytes | None = None,
                  check_original: bool = False) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.note-bridge-", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if check_original and _read_optional(path) != expected:
            raise RuntimeError("Codex config changed concurrently; rerun initialization.")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def configure(*, root: Path | None = None, config_root: Path | None = None,
              html_only: bool = False, replace_managed: bool = False,
              node: str | None = None) -> dict:
    """Register servers, returning a small result suitable for CLI or tests."""
    root = Path(root or ROOT).resolve()
    servers = server_configs(html_only, root=root, node=node)
    if not (root / CHROME_SCRIPT).is_file():
        raise RuntimeError("Chrome MCP is not installed. Run the initialization BAT first.")
    if not html_only and not (root / "yinxiang_server.py").is_file():
        raise RuntimeError("The local Yinxiang MCP server is missing.")
    config_root = Path(config_root or os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    config_root.mkdir(parents=True, exist_ok=True)
    config = config_root / "config.toml"
    backup = None
    changed = False
    with _config_lock(config_root):
        original_bytes = _read_optional(config)
        original = original_bytes.decode("utf-8-sig") if original_bytes is not None else ""
        doc = tomlkit.parse(original)
        existing = doc.setdefault("mcp_servers", tomlkit.table())
        if not isinstance(existing, dict):
            raise RuntimeError("Codex mcp_servers must be a table; refusing to change the file.")
        for name, value in servers.items():
            if name not in existing:
                existing[name] = value
                continue
            old = existing[name]
            if not isinstance(old, dict):
                raise RuntimeError(f"Existing server {name} is not a table; refusing to overwrite it.")
            connection_fields = ("command", "args", "cwd")
            if all(old.get(key) == value[key] for key in connection_fields):
                continue
            if not replace_managed or not _managed_entry(name, old):
                raise RuntimeError(f"Existing server {name} differs; refusing to overwrite it. "
                                   "Use --replace-managed only to relocate a bridge-created entry.")
            # Keep enabled state, timeouts, per-tool approvals and other user settings.
            for key in connection_fields:
                old[key] = value[key]
        rendered = tomlkit.dumps(doc)
        tomlkit.parse(rendered)
        if rendered != original:
            if original_bytes is not None:
                backup_root = root / "setup-backups"
                backup_root.mkdir(parents=True, exist_ok=True)
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                backup = backup_root / f"config-{stamp}-{uuid.uuid4().hex[:8]}.toml"
                with backup.open("xb") as stream:
                    stream.write(original_bytes)
            _atomic_write(config, rendered.encode("utf-8"), expected=original_bytes, check_original=True)
            changed = True
    (root / "data").mkdir(exist_ok=True)
    portable = {"mcpServers": {name: {"command": value["command"], "args": value["args"],
                                      **({"env": value["env"]} if "env" in value else {})}
                               for name, value in servers.items()}}
    _atomic_write(root / "mcp-config.json", json.dumps(portable, ensure_ascii=False, indent=2).encode("utf-8"))
    return {"registered": list(servers), "config": str(config), "changed": changed,
            "backup": str(backup) if backup else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html-only", action="store_true", help="Register Chrome only; no Yinxiang desktop needed")
    parser.add_argument("--replace-managed", action="store_true", help="Relocate entries created by this bridge; keep other settings")
    args = parser.parse_args()
    try:
        result = configure(html_only=args.html_only, replace_managed=args.replace_managed)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Initialization failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
