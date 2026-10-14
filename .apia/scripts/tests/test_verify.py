#!/usr/bin/env python3
"""Direct unit tests for verify.resolve_adapter_path / verify.load_adapter.

Phase-6a gave ``verify.load_adapter`` an optional ``repo_path`` and a new
``resolve_adapter_path`` so a bare stack NAME can resolve to a LOCAL adapter
plugin (``.apia/plugins/adapters/<name>.yml``). That plugin path is exercised
indirectly through test_share.py; this file covers it DIRECTLY, together with
the UNCHANGED behavior that matters for the standard verify flow:

  * a real file PATH is returned/loaded as-is (plugin resolution never interferes
    with the normal ``--adapter PATH`` path);
  * the python DEFAULT-fallback adapter is returned when PyYAML is absent or the
    path does not resolve, so the harness still runs ``python -m pytest``;
  * resolve_adapter_path returns a bare name UNCHANGED when no plugin provides it
    (so the python-default fallback downstream is preserved) and never raises.

These are pure LOCAL assertions: no network, no LLM, no subprocess.
"""
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import verify  # noqa: E402

PYTHON_ADAPTER = verify.default_adapter_path()


def _write_plugin_adapter(tmp_path, name="rust"):
    """Create a tmp repo shipping a LOCAL adapter plugin for ``name``."""
    repo = tmp_path / "plugrepo"
    (repo / ".apia" / "plugins" / "adapters").mkdir(parents=True)
    (repo / ".apia" / "plugins" / "adapters" / "{}.yml".format(name)).write_text(
        "name: {}\nlanguage: Rust\ntest_command: cargo test\n"
        "result_format: ctest\n".format(name),
        encoding="utf-8")
    return str(repo)


# --- resolve_adapter_path: UNCHANGED file-path / name-fallback behavior ------

def test_resolve_adapter_path_returns_existing_file_as_is():
    # An existing file path is returned verbatim; plugin resolution is skipped.
    assert verify.resolve_adapter_path(PYTHON_ADAPTER) == PYTHON_ADAPTER


def test_resolve_adapter_path_unknown_name_returned_unchanged():
    # A bare stack name with no matching plugin is returned UNCHANGED, so the
    # downstream python-default fallback in load_adapter still fires.
    assert verify.resolve_adapter_path("python") == "python"


def test_resolve_adapter_path_empty_is_noop():
    assert verify.resolve_adapter_path(None) is None
    assert verify.resolve_adapter_path("") == ""


# --- resolve_adapter_path: NEW bare-stack-name plugin resolution -------------

def test_resolve_adapter_path_resolves_plugin_by_name(tmp_path):
    repo = _write_plugin_adapter(tmp_path, "rust")
    resolved = verify.resolve_adapter_path("rust", repo_path=repo)
    assert resolved is not None
    assert resolved.endswith(os.path.join("adapters", "rust.yml"))
    assert os.path.isfile(resolved)


# --- load_adapter: file path, plugin-by-name, and default fallback -----------

def test_load_adapter_from_file_path():
    adapter = verify.load_adapter(PYTHON_ADAPTER)
    assert adapter.get("name") == "python"
    assert "pytest" in (adapter.get("test_command") or "")


def test_load_adapter_resolves_plugin_by_name(tmp_path):
    repo = _write_plugin_adapter(tmp_path, "rust")
    adapter = verify.load_adapter("rust", repo_path=repo)
    assert adapter.get("name") == "rust"
    assert adapter.get("test_command") == "cargo test"


def test_load_adapter_falls_back_to_python_default_for_unknown(tmp_path):
    # A bare name with no plugin and no file -> the minimal python default so the
    # harness still runs the standard pytest commands (behavior unchanged).
    adapter = verify.load_adapter("does-not-exist", repo_path=str(tmp_path))
    assert adapter.get("name") == "python"
    assert adapter.get("test_command") == "python -m pytest"


def test_resolve_adapter_path_never_raises_on_bad_repo():
    # A nonexistent repo path must not raise (plugin lookup is best-effort).
    assert verify.resolve_adapter_path("rust", repo_path="/no/such/repo") == "rust"
