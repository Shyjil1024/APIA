"""Tests for the APIA off-model LOCAL sharing engine (share.py, Phase-6a).

Coverage (per FEAT-002), all REAL (deterministic, off-model, no LLM/network):
  (a) knowledge-pack export->import ROUND-TRIP: imported artifacts + project-facts
      match the source exactly (byte-identical);
  (b) SAFE-MERGE: a locally-corrected artifact is NOT silently clobbered by import
      (reported as a conflict/skipped, on-disk content unchanged), and force=True
      DOES override;
  (c) version-field handling: a known schema_version imports; an unknown/newer
      version is REPORTED, not applied;
  (d) profile apply + PRECEDENCE: profile values fill ABSENT keys, LOCAL override
      wins where present;
  (e) PLUGIN discovery/load for an adapter AND a scanner (and a shape), asserting
      the relevant engine (ingest/scan/scaffold/verify) actually discovers+uses it;
  (f) a MALFORMED-plugin graceful-failure case (captured in errors, loader does
      not raise, engines still work);
  (g) CAPABILITY-MANIFEST content: python=complete, cpp=complete, csharp/groovy=
      stub, scanners, supported shapes, loaded plugins, versions present;
  (h) the no-LLM/no-network static guard for share.py mirroring the existing
      tripwire lists.

Both Python and C++ contexts are exercised (manifest completeness, a C++ shape
plugin). The pack round-trip ingests the committed samples/pyapp fixture into a
tmp knowledge dir so the committed knowledge base is never touched.
"""

import json
import os
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import share  # noqa: E402
import ingest  # noqa: E402
import scan  # noqa: E402
import scaffold  # noqa: E402
import verify  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
PYAPP = os.path.join(REPO_ROOT, "samples", "pyapp")


# --- helpers -----------------------------------------------------------------

def _ingest_sample(tmp_path):
    """Ingest the committed pyapp sample into a fresh tmp knowledge dir."""
    kd = str(tmp_path / "src_knowledge")
    result = ingest.ingest(PYAPP, kd)
    assert result["reprocessed"] is True
    return kd


# --- (a) knowledge-pack export -> import ROUND-TRIP --------------------------

def test_pack_round_trip_byte_identical(tmp_path):
    src_kd = _ingest_sample(tmp_path)
    out = str(tmp_path / "pack.json")
    pack = share.export_pack(knowledge_dir=src_kd, out_path=out)

    # Versioned pack document with the expected shape.
    assert pack["schema_version"] == share.PACK_SCHEMA_VERSION
    assert "generated_at" in pack and "source_repo" in pack
    # Every committed artifact that exists on disk is packed (incl. project-facts).
    for name in ingest.ARTIFACTS:
        assert name in pack["artifacts"], "artifact missing from pack: {}".format(name)
        assert pack["artifacts"][name]["content"]
        assert pack["artifacts"][name]["hash"]
    assert "project-facts.md" in pack["artifacts"]
    assert os.path.isfile(out)

    # Import into a FRESH (empty) target dir and assert byte-identical artifacts.
    dst_kd = str(tmp_path / "dst_knowledge")
    result = share.import_pack(out, knowledge_dir=dst_kd)
    assert result["unknown_version"] is False
    assert not result["conflicts"]
    assert not result["skipped_local"]
    for name in ingest.ARTIFACTS:
        with open(os.path.join(src_kd, name), encoding="utf-8") as fh:
            src = fh.read()
        with open(os.path.join(dst_kd, name), encoding="utf-8") as fh:
            dst = fh.read()
        assert src == dst, "round-trip mismatch for {}".format(name)
        assert name in result["imported"]
    # The index is carried over too.
    assert os.path.isfile(os.path.join(dst_kd, ingest.INDEX_FILE))


def test_export_only_packs_existing_artifacts(tmp_path):
    kd = str(tmp_path / "partial")
    os.makedirs(kd)
    with open(os.path.join(kd, "stack.md"), "w", encoding="utf-8") as fh:
        fh.write("# just one artifact\n")
    pack = share.export_pack(knowledge_dir=kd)
    assert list(pack["artifacts"].keys()) == ["stack.md"]


# --- (b) SAFE-MERGE: no silent clobber; force=True overrides -----------------

def test_import_preserves_local_correction(tmp_path):
    src_kd = _ingest_sample(tmp_path)
    pack = share.export_pack(knowledge_dir=src_kd)

    # Pre-seed the target with a LOCAL correction of one artifact.
    dst_kd = str(tmp_path / "dst")
    os.makedirs(dst_kd)
    corrected = "# LOCAL CORRECTION -- do not clobber\n"
    with open(os.path.join(dst_kd, "architecture.md"), "w", encoding="utf-8") as fh:
        fh.write(corrected)

    result = share.import_pack(pack, knowledge_dir=dst_kd)
    # The divergent local artifact is kept + reported, never silently clobbered.
    assert "architecture.md" in result["skipped_local"]
    conflict_names = [c["artifact"] for c in result["conflicts"]]
    assert "architecture.md" in conflict_names
    with open(os.path.join(dst_kd, "architecture.md"), encoding="utf-8") as fh:
        assert fh.read() == corrected  # unchanged on disk
    # Other artifacts still imported.
    assert "stack.md" in result["imported"]
    assert "architecture.md" not in result["imported"]


def test_import_force_overrides_local(tmp_path):
    src_kd = _ingest_sample(tmp_path)
    pack = share.export_pack(knowledge_dir=src_kd)

    dst_kd = str(tmp_path / "dst")
    os.makedirs(dst_kd)
    with open(os.path.join(dst_kd, "architecture.md"), "w", encoding="utf-8") as fh:
        fh.write("# LOCAL CORRECTION\n")

    result = share.import_pack(pack, knowledge_dir=dst_kd, options={"force": True})
    assert "architecture.md" in result["forced"]
    assert "architecture.md" in result["imported"]
    assert not result["conflicts"]
    # On disk it now matches the pack content.
    with open(os.path.join(dst_kd, "architecture.md"), encoding="utf-8") as fh:
        assert fh.read() == pack["artifacts"]["architecture.md"]["content"]


def test_import_identical_is_not_a_conflict(tmp_path):
    src_kd = _ingest_sample(tmp_path)
    pack = share.export_pack(knowledge_dir=src_kd)
    # Import twice into the same dir: the second import sees identical content and
    # must treat it as imported, never a conflict.
    dst_kd = str(tmp_path / "dst")
    share.import_pack(pack, knowledge_dir=dst_kd)
    result = share.import_pack(pack, knowledge_dir=dst_kd)
    assert not result["conflicts"]
    assert not result["skipped_local"]
    assert "architecture.md" in result["imported"]


# --- (c) version-field handling ----------------------------------------------

def test_known_version_imports(tmp_path):
    src_kd = _ingest_sample(tmp_path)
    pack = share.export_pack(knowledge_dir=src_kd)
    assert pack["schema_version"] == share.PACK_SCHEMA_VERSION
    result = share.import_pack(pack, knowledge_dir=str(tmp_path / "dst"))
    assert result["unknown_version"] is False
    assert result["imported"]


def test_unknown_version_reported_not_applied(tmp_path):
    src_kd = _ingest_sample(tmp_path)
    pack = share.export_pack(knowledge_dir=src_kd)
    pack["schema_version"] = "999"  # a newer/unknown layout
    dst_kd = str(tmp_path / "dst")
    result = share.import_pack(pack, knowledge_dir=dst_kd)
    assert result["unknown_version"] is True
    assert result["imported"] == []
    # Nothing was written.
    assert not os.path.isdir(dst_kd) or not os.listdir(dst_kd)


# --- (c2) EXIT-CODE CONTRACT (finding 1): preserved correction == success ----

def test_cli_import_exit_zero_even_with_preserved_correction(tmp_path, capsys):
    """A kept local correction is a SUCCESS: the --import-pack CLI exits 0.

    The safe no-clobber merge working (a divergent local artifact preserved and
    reported as a conflict) is NOT a failure; the preserved correction is
    surfaced in the --json report, not via the exit code.
    """
    src_kd = _ingest_sample(tmp_path)
    out = str(tmp_path / "pack.json")
    share.export_pack(knowledge_dir=src_kd, out_path=out)

    dst_kd = str(tmp_path / "dst")
    os.makedirs(dst_kd)
    with open(os.path.join(dst_kd, "architecture.md"), "w", encoding="utf-8") as fh:
        fh.write("# LOCAL CORRECTION -- keep me\n")

    rc = share.main(["--import-pack", "--pack", out,
                     "--knowledge-dir", dst_kd, "--json"])
    assert rc == 0  # preserved local correction is a success
    payload = json.loads(capsys.readouterr().out)
    # The kept correction is surfaced in the report, not via the exit code.
    assert "architecture.md" in payload["skipped_local"]
    assert any(c["artifact"] == "architecture.md" for c in payload["conflicts"])


def test_cli_import_exit_nonzero_only_on_unknown_version(tmp_path, capsys):
    """An unknown/newer pack version (nothing applied) is the ONLY non-zero exit."""
    src_kd = _ingest_sample(tmp_path)
    pack = share.export_pack(knowledge_dir=src_kd)
    pack["schema_version"] = "999"
    out = str(tmp_path / "future.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(pack, fh)
    dst_kd = str(tmp_path / "dst")
    rc = share.main(["--import-pack", "--pack", out,
                     "--knowledge-dir", dst_kd, "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["unknown_version"] is True

    # A clean import of a KNOWN version exits 0.
    good = str(tmp_path / "pack.json")
    share.export_pack(knowledge_dir=src_kd, out_path=good)
    rc2 = share.main(["--import-pack", "--pack", good,
                      "--knowledge-dir", str(tmp_path / "fresh"), "--json"])
    capsys.readouterr()
    assert rc2 == 0


# --- (c3) INDEX GATING (finding 2): all-conflict import leaves index intact --

def test_all_conflict_import_does_not_overwrite_local_index(tmp_path):
    """When EVERY artifact is a preserved local correction, the on-disk index is
    left untouched -- it must never describe pack content that was not applied.
    """
    src_kd = _ingest_sample(tmp_path)
    pack = share.export_pack(knowledge_dir=src_kd)
    assert pack["index"]  # the sample ingest produced a non-empty index

    # Seed the target so EVERY packed artifact diverges locally (all-conflict),
    # plus a DIVERGENT local .index.json that must survive the import.
    dst_kd = str(tmp_path / "dst")
    os.makedirs(dst_kd)
    for name in pack["artifacts"]:
        with open(os.path.join(dst_kd, name), "w", encoding="utf-8") as fh:
            fh.write("# LOCAL CORRECTION of {}\n".format(name))
    local_index = {"local/only.py": "deadbeefdeadbeef"}
    index_path = os.path.join(dst_kd, ingest.INDEX_FILE)
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(local_index, fh, sort_keys=True)

    result = share.import_pack(pack, knowledge_dir=dst_kd)
    # Nothing applied: every artifact a conflict, no import, index not written.
    assert result["imported"] == []
    assert result["index_written"] is False
    assert sorted(result["skipped_local"]) == sorted(pack["artifacts"].keys())
    # The divergent local index on disk is UNTOUCHED (not clobbered by the pack).
    with open(index_path, encoding="utf-8") as fh:
        assert json.load(fh) == local_index


def test_index_written_when_at_least_one_artifact_applied(tmp_path):
    """A partial import that applies at least one artifact DOES write the index."""
    src_kd = _ingest_sample(tmp_path)
    pack = share.export_pack(knowledge_dir=src_kd)
    assert pack["index"]

    # One divergent local correction; the rest are missing -> get written.
    dst_kd = str(tmp_path / "dst")
    os.makedirs(dst_kd)
    with open(os.path.join(dst_kd, "architecture.md"), "w", encoding="utf-8") as fh:
        fh.write("# LOCAL CORRECTION\n")

    result = share.import_pack(pack, knowledge_dir=dst_kd)
    assert result["imported"]  # at least one artifact applied
    assert "architecture.md" in result["skipped_local"]
    assert result["index_written"] is True
    assert os.path.isfile(os.path.join(dst_kd, ingest.INDEX_FILE))


# --- (d) profile apply + PRECEDENCE (local wins) -----------------------------

def test_apply_profile_precedence_local_wins():
    profile = {
        "confidence_threshold": 0.8,
        "token_budget": {"share": 2000, "ingest": 1},
        "review": {"thresholds": {"fail_on_severity": "medium",
                                  "max_findings": {"security": 0, "quality": 25}}},
        "new_from_profile": "yes",
    }
    local = {
        "confidence_threshold": 0.6,            # local present -> local wins
        "token_budget": {"ingest": 8000},       # local ingest wins; share filled in
        "review": {"thresholds": {"fail_on_severity": "high"}},  # local wins; quality filled
    }
    eff, provenance = share.apply_profile(profile, local)

    # Local overrides win where present.
    assert eff["confidence_threshold"] == 0.6
    assert eff["token_budget"]["ingest"] == 8000
    assert eff["review"]["thresholds"]["fail_on_severity"] == "high"
    # Profile fills absent keys.
    assert eff["token_budget"]["share"] == 2000
    assert eff["review"]["thresholds"]["max_findings"]["quality"] == 25
    assert eff["new_from_profile"] == "yes"
    # Provenance records the source of leaf keys.
    assert provenance["confidence_threshold"] == "local"
    assert provenance["token_budget.ingest"] == "local"
    assert provenance["token_budget.share"] == "profile"
    assert provenance["new_from_profile"] == "profile"


def test_example_profile_loads_and_applies():
    profile_path = os.path.join(REPO_ROOT, ".apia", "profiles", "example-team.yml")
    assert os.path.isfile(profile_path)
    profile = share.load_profile(profile_path)
    assert profile  # non-empty
    # The shipped config.yml sets fail_on_severity: high; the profile proposes
    # medium. LOCAL (config.yml) must win.
    eff, provenance = share.effective_config(REPO_ROOT, profile_path)
    assert eff["review"]["thresholds"]["fail_on_severity"] == "high"
    assert provenance["review.thresholds.fail_on_severity"] == "local"
    # The profile's quality cap is absent locally -> filled in from the profile.
    assert eff["review"]["thresholds"]["max_findings"]["quality"] == 25
    assert provenance["review.thresholds.max_findings.quality"] == "profile"


def test_empty_profile_is_noop():
    local = {"confidence_threshold": 0.75}
    eff, provenance = share.apply_profile({}, local)
    assert eff == local
    assert all(v == "local" for v in provenance.values())


# --- (e) PLUGIN discovery/load + engine wiring -------------------------------

def _write_plugin_repo(tmp_path):
    """Create a tmp repo with a LOCAL plugin set (adapter + scanner + shape)."""
    repo = tmp_path / "plugrepo"
    (repo / ".apia" / "plugins" / "adapters").mkdir(parents=True)
    (repo / ".apia" / "plugins" / "scanners").mkdir(parents=True)
    (repo / ".apia" / "plugins" / "shapes").mkdir(parents=True)
    # Adapter plugin: a `rust` stack.
    (repo / ".apia" / "plugins" / "adapters" / "rust.yml").write_text(
        "name: rust\nlanguage: Rust\ntest_command: cargo test\n"
        "extensions:\n  - .rs\nmanifests:\n  - Cargo.toml\n"
        "frameworks:\n  - cargo\n", encoding="utf-8")
    # Scanner plugin: a quality scanner for the python stack (reuses flake8 fmt).
    (repo / ".apia" / "plugins" / "scanners" / "myquality.yml").write_text(
        "stack: python\ncategory: quality\nscanner:\n"
        "  tool: myquality\n  command: python -m flake8 .\n  format: flake8\n",
        encoding="utf-8")
    # Shape plugin.
    (repo / ".apia" / "plugins" / "shapes" / "myshape.yml").write_text(
        "shape: rust_cli\nstack: rust\n", encoding="utf-8")
    return str(repo)


def test_discover_and_load_plugins(tmp_path):
    repo = _write_plugin_repo(tmp_path)
    catalog = share.load_plugins(repo)
    assert any(a["name"] == "rust" for a in catalog["adapters"])
    assert any(s["name"] == "myquality" and s["stack"] == "python"
               for s in catalog["scanners"])
    assert any(sh["shape"] == "rust_cli" for sh in catalog["shapes"])
    assert catalog["errors"] == []


def test_scaffold_includes_plugin_shape(tmp_path):
    repo = _write_plugin_repo(tmp_path)
    shapes = scaffold.resolve_supported_shapes(repo)
    assert "rust_cli" in shapes
    # The built-in shapes are still present (additive, not replacing).
    assert "python_cli" in shapes


def test_scan_adapter_resolution_finds_plugin_adapter(tmp_path):
    repo = _write_plugin_repo(tmp_path)
    # The plugin provides the `rust` stack which config.yml does not declare.
    path = scan._adapter_path_for_stack("rust", repo)
    assert path is not None
    assert path.endswith("rust.yml")


def test_verify_load_adapter_resolves_plugin_by_name(tmp_path):
    repo = _write_plugin_repo(tmp_path)
    # verify.load_adapter accepts a bare stack name resolved via the plugin loader.
    adapter = verify.load_adapter("rust", repo_path=repo)
    assert adapter.get("name") == "rust"
    assert adapter.get("test_command") == "cargo test"


def test_ingest_detection_surfaces_plugin_stack(tmp_path):
    repo = _write_plugin_repo(tmp_path)
    # Add real evidence for the plugin stack: a Rust source + manifest.
    (tmp_path / "plugrepo" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plugrepo" / "src" / "main.rs").write_text(
        "fn main() {}\n", encoding="utf-8")
    (tmp_path / "plugrepo" / "Cargo.toml").write_text(
        "[package]\nname = \"x\"\n", encoding="utf-8")
    files = ingest.walk_repo(repo)
    detected = ingest.detect_stacks(repo, files)
    assert "rust" in detected
    assert detected["rust"].get("plugin") is True


def test_scan_runs_plugin_scanner_on_python(tmp_path):
    repo = _write_plugin_repo(tmp_path)
    # Give the plugin repo a real Python source so the python-stack scan (and its
    # plugin-provided quality scanner) runs on the repo that owns the plugin.
    with open(os.path.join(repo, "mod.py"), "w", encoding="utf-8") as fh:
        fh.write("import os\n\n\ndef f():\n    return 1\n")
    adapter = scan.load_adapter(scan.default_adapter_path())
    report = scan.scan(repo, adapter, {"category": "quality"})
    # The plugin scanner `myquality` must be folded into the enumeration and
    # actually run (its per-scanner meta is present).
    tools = {m["tool"] for m in report["scanners"]}
    assert "myquality" in tools


# --- (f) MALFORMED plugin graceful failure -----------------------------------

def test_malformed_plugin_captured_not_raised(tmp_path):
    repo = tmp_path / "badrepo"
    (repo / ".apia" / "plugins" / "adapters").mkdir(parents=True)
    (repo / ".apia" / "plugins" / "scanners").mkdir(parents=True)
    (repo / ".apia" / "plugins" / "shapes").mkdir(parents=True)
    # Adapter with no name (invalid); scanner missing scanner mapping; shape with
    # no shape id; plus one VALID adapter so the good one still loads.
    (repo / ".apia" / "plugins" / "adapters" / "bad.yml").write_text(
        "language: Nothing\n", encoding="utf-8")
    (repo / ".apia" / "plugins" / "adapters" / "good.yml").write_text(
        "name: good\nextensions:\n  - .gd\n", encoding="utf-8")
    (repo / ".apia" / "plugins" / "scanners" / "bad.yml").write_text(
        "stack: python\ncategory: quality\n", encoding="utf-8")
    (repo / ".apia" / "plugins" / "shapes" / "bad.yml").write_text(
        "stack: python\n", encoding="utf-8")
    (repo / ".apia" / "plugins" / "adapters" / "broken.yml").write_text(
        "name: [unclosed\n", encoding="utf-8")  # invalid YAML

    catalog = share.load_plugins(str(repo))  # must NOT raise
    # The one valid adapter loaded; the malformed ones are captured as errors.
    assert any(a["name"] == "good" for a in catalog["adapters"])
    assert len(catalog["errors"]) >= 3
    kinds = {e["kind"] for e in catalog["errors"]}
    assert "adapter" in kinds and "scanner" in kinds and "shape" in kinds

    # Engines still work with a malformed plugin present (no crash).
    shapes = scaffold.resolve_supported_shapes(str(repo))
    assert "python_cli" in shapes


# --- (g) CAPABILITY MANIFEST content -----------------------------------------

def test_manifest_reports_completeness_and_versions():
    manifest = share.build_manifest(REPO_ROOT)
    # Versions present.
    assert manifest["apia_version"] == share.APIA_VERSION
    assert manifest["schema_version"] == share.MANIFEST_SCHEMA_VERSION
    status = {a["name"]: a["status"] for a in manifest["adapters"]}
    assert status["python"] == "complete"
    assert status["cpp"] == "complete"
    assert status["csharp"] == "stub"
    assert status["groovy"] == "stub"
    # Scanners union reports presence.
    assert "bandit" in manifest["scanners"]
    assert "cppcheck" in manifest["scanners"]  # declared even if tool absent
    # Supported shapes include the built-ins AND the shipped example shape plugin.
    assert "python_cli" in manifest["supported_shapes"]
    assert "cpp_console" in manifest["supported_shapes"]
    assert "cpp_plugin" in manifest["supported_shapes"]  # example shape plugin
    # Loaded plugins include the shipped example adapter + scanner + shape.
    plugin_adapter_names = {a["name"] for a in manifest["plugins"]["adapters"]}
    assert "lua" in plugin_adapter_names
    plugin_scanner_names = {s["name"] for s in manifest["plugins"]["scanners"]}
    assert "radon-mi" in plugin_scanner_names
    plugin_shape_ids = {s["shape"] for s in manifest["plugins"]["shapes"]}
    assert "cpp_plugin" in plugin_shape_ids
    assert manifest["plugins"]["errors"] == []


def test_manifest_json_serializable_and_formatted():
    manifest = share.build_manifest(REPO_ROOT)
    # Must be JSON-serializable (the --json CLI path).
    text = json.dumps(manifest, sort_keys=True)
    assert "supported_shapes" in text
    human = share.format_manifest(manifest)
    assert "APIA capability manifest" in human
    assert "python" in human and "complete" in human


def test_manifest_cpp_stack_present_and_complete():
    """C++ context: the cpp adapter is complete and its scanners are enumerated."""
    manifest = share.build_manifest(REPO_ROOT)
    cpp = next(a for a in manifest["adapters"] if a["name"] == "cpp")
    assert cpp["status"] == "complete"
    cpp_tools = {s["tool"] for s in cpp["scanners"]}
    assert "cppcheck" in cpp_tools
    assert "clang-tidy" in cpp_tools


# --- (h) static no-network / no-LLM guard ------------------------------------

def test_share_makes_no_network_or_llm_calls():
    """Static guard: share.py must not import network/LLM SDKs or model endpoints.

    A TRIPWIRE mirroring test_scan.test_scan_makes_no_network_or_llm_calls / the
    other engines' guards. The real guarantee is structural: share.py is
    stdlib + optional PyYAML only and only reads/writes LOCAL files already
    committed to (or destined for) the git repo (see the module docstring's HARD
    CONSTRAINT).
    """
    src_path = os.path.join(SCRIPTS_DIR, "share.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    forbidden = [
        "import requests",
        "import openai",
        "import anthropic",
        "import httpx",
        "urllib.request",
        "http.client",
        "socket.socket",
        "api.openai.com",
        "chat/completions",
    ]
    for token in forbidden:
        assert token not in src, "share.py must not reference '{}'".format(token)
