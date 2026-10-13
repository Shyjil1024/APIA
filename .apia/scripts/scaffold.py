#!/usr/bin/env python3
"""APIA off-model greenfield-generation support engine (Phase-3).

This script is the DETERMINISTIC half of APIA's greenfield PROJECT GENERATION.
It is the scaffolding counterpart to scan.py (the Reviewer engine) and verify.py
(the test harness): it does ONLY mechanical, reproducible work and never
reasons, never writes feature logic, and never invents correctness.

MODEL vs OFF-MODEL SPLIT (the same discipline as scan.py / verify.py). The LIVE
Copilot session does the CREATIVE half -- turning a user's app prompt into an
approved structured spec and WRITING the actual feature code. This script does
the MECHANICAL half:

  (a) detect_shape(spec)      - map an APPROVED structured spec to a fixed
                                project SHAPE (python_cli, python_library,
                                python_web_api, cpp_console, cpp_library -- fully
                                verifiable; gui, embedded, driver -- recognized
                                and HONESTLY degraded to partial verification).
  (b) emit_skeleton(spec,...) - write the idiomatic base layout + build files for
                                the detected shape ONLY (never feature logic),
                                deterministically and idempotently.
  (c) verify_generated(...)   - drive build -> run/smoke -> unit tests through
                                verify.py's EXISTING machinery and NORMALIZE the
                                result into the shared counts shape. The run/
                                smoke step is a REAL execution of the shape's
                                runnable entrypoint/binary (the adapter's
                                generate.templates.<shape>.run_command, e.g.
                                python -m <name> or ./build/<name>), DISTINCT
                                from the unit-test phase. FULL for the verifiable
                                shapes, HONEST-PARTIAL (never green) for gui/
                                embedded/driver.
  (d) generation_gate(...)    - the gate decision, by CALLING
                                scan.apply_thresholds with the SAME
                                review.thresholds from .apia/config.yml.
  (e) decide_generation(...)  - the iterate/stop/ask decision, by REUSING
                                evalloop.resolve_max_iterations (the hard cap)
                                and evalloop.propose_acceptance_criteria /
                                evalloop.acceptance_criteria_confirmed (the
                                anti-circularity gate).

HARD CONSTRAINT (identical to ingest.py / scan.py / verify.py; see
docs/ARCHITECTURE.md sections 1-3): this module performs ZERO network and ZERO
LLM/model calls. It NEVER imports any HTTP client, networking SDK, or model SDK
(no requests / urllib networking / openai / anthropic / httpx / low-level HTTP /
raw sockets), and it NEVER reaches a remote endpoint. Model inference always
runs on the user's existing Copilot session. This engine spawns NO subprocess of
its own: the only build/test subprocesses are launched through verify.py's
EXISTING single ``_run()`` / ``run_build_phase`` chokepoint (we do not add a
second subprocess chokepoint or a second output parser). Copilot writes the
feature code live; scaffold.py only detects shape, emits skeleton/build
templates, normalizes verification, and decides the gate.

    # assert: no network/LLM calls anywhere in this module -- no subprocess is
    # spawned here; build/test subprocesses go through verify.py's _run().

Usage:
    python3 .apia/scripts/scaffold.py --detect-shape SPEC [--json]
    python3 .apia/scripts/scaffold.py --emit SPEC --dest DIR [--json]
    python3 .apia/scripts/scaffold.py --verify DIR --shape SHAPE [--json]
    python3 .apia/scripts/scaffold.py --gate FINDINGS.json [--json]
    python3 .apia/scripts/scaffold.py --doctor [--json]

Exit 0 on gate pass / full verification, non-zero otherwise (mirrors scan.py /
verify.py).
"""

# NOTE: standard-library only (plus the optional PyYAML import guarded below for
# reading spec/config/adapter YAML). There is deliberately NO networking/LLM
# import here -- see the module docstring's HARD CONSTRAINT.
import argparse
import json
import os
import sys

# scan.py / verify.py / evalloop.py / ingest.py are the SHARED off-model
# building blocks we COMPOSE (never reimplement): scan.apply_thresholds is the
# single gate; verify.run_build_phase + verify.verify + the result parsers are
# the single build/test chokepoint; evalloop supplies the iterate cap and the
# anti-circularity acceptance gate; ingest supplies the shared doctor preflight.
# The scripts dir is this file's directory; make sure it is importable
# regardless of the caller's cwd (mirrors scan.py / verify.py). These are LOCAL
# stdlib-only modules that perform no network/LLM calls (enforced by their own
# guard tests + the one for this module).
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import scan      # noqa: E402  (apply_thresholds + config/threshold resolution)
import verify    # noqa: E402  (run_build_phase, verify(), result parsers)
import evalloop  # noqa: E402  (resolve_max_iterations + acceptance gate)

try:
    import ingest  # noqa: E402  (reused for the shared doctor() preflight)
except ImportError:
    ingest = None


# ---------------------------------------------------------------------------
# Supported shapes vocabulary.
# ---------------------------------------------------------------------------
#
# The FIVE fully-verifiable shapes can be deterministically built + run + tested
# end-to-end. The THREE honest-partial shapes are recognized but inherently
# cannot be auto-verified (no headless display / no target hardware), so they
# are emitted + compiled + unit-tested and reported partial -- NEVER green.
FULLY_VERIFIABLE_SHAPES = (
    "python_cli",
    "python_library",
    "python_web_api",
    "cpp_console",
    "cpp_library",
)
HONEST_PARTIAL_SHAPES = (
    "gui",
    "embedded",
    "driver",
)
# Built-in default used when config.yml does not (yet) declare
# generate.supported_shapes. FEAT-003 adds that config key; scaffold.py reads it
# WHEN PRESENT and falls back to this default so these tests/behaviour never
# depend on FEAT-003 having run.
SUPPORTED_SHAPES = FULLY_VERIFIABLE_SHAPES + HONEST_PARTIAL_SHAPES

# Which stack each shape builds against (selects the verify.py adapter).
_SHAPE_STACK = {
    "python_cli": "python",
    "python_library": "python",
    "python_web_api": "python",
    "cpp_console": "cpp",
    "cpp_library": "cpp",
    # Honest-partial shapes carry the stack from the spec; default to python.
}


def _package_config_path():
    apia_dir = os.path.dirname(_SCRIPTS_DIR)
    return os.path.join(apia_dir, "config.yml")


def _load_config(repo_path=None):
    """Return the merged config dict (REPO-FIRST, package fallback), or {}.

    Resolution mirrors scan._config_stacks / evalloop._load_config exactly: the
    target repo's own ``.apia/config.yml`` wins, then the APIA package config is
    the fallback. Returns the FIRST config file that parses to a dict.
    """
    try:
        import yaml
    except ImportError:
        return {}
    candidates = []
    if repo_path:
        candidates.append(os.path.join(repo_path, ".apia", "config.yml"))
    candidates.append(_package_config_path())
    for cfg in candidates:
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def resolve_supported_shapes(repo_path=None):
    """Return the allowed shapes list (config generate.supported_shapes first).

    REPO-FIRST config resolution (same pattern as scan._config_stacks /
    evalloop._load_config). Reads ``generate.supported_shapes`` from config.yml
    WHEN PRESENT (that key is added in FEAT-003), falling back to the built-in
    SUPPORTED_SHAPES so this module works before FEAT-003 ships. Always returns a
    non-empty tuple.
    """
    cfg = _load_config(repo_path)
    generate = cfg.get("generate") if isinstance(cfg, dict) else None
    if isinstance(generate, dict):
        shapes = generate.get("supported_shapes")
        if isinstance(shapes, list) and shapes:
            return tuple(str(s) for s in shapes)
    return tuple(SUPPORTED_SHAPES)


# ---------------------------------------------------------------------------
# (a) SHAPE DETECTION.
# ---------------------------------------------------------------------------


def _spec_signals(spec):
    """Collect the lowercased signal strings a spec exposes, for keyword probing.

    Flattens the spec's free-text-ish fields (summary/description/prompt,
    entrypoint, framework, declared signals list, keywords) into one lowercased
    blob plus the explicit structured keys. Pure string inspection; nothing is
    opened or executed.
    """
    parts = []
    for key in ("summary", "description", "prompt", "entrypoint", "framework",
                "kind", "target"):
        val = spec.get(key)
        if isinstance(val, str):
            parts.append(val)
    for key in ("signals", "keywords"):
        val = spec.get(key)
        if isinstance(val, (list, tuple)):
            parts.extend(str(v) for v in val)
    return " ".join(parts).lower()


# Keyword probes per shape. Ordered most-specific first so a web-API spec is not
# swallowed by the generic CLI probe, etc.
_PY_WEB_MARKERS = ("flask", "fastapi", "django", "web api", "web-api", "webapi",
                   "rest api", "http server", "endpoint", "wsgi", "asgi")
_PY_CLI_MARKERS = ("cli", "command line", "command-line", "console tool",
                   "argparse", "entrypoint", "__main__", "terminal")
_PY_LIB_MARKERS = ("library", "package", "sdk", "importable", "reusable module")
_CPP_CONSOLE_MARKERS = ("console", "cli", "command line", "command-line",
                        "executable", "main.cpp", "stdout program")
_CPP_LIB_MARKERS = ("library", "static library", "shared library", "header",
                    "reusable", ".a", ".so", "lib")
_GUI_MARKERS = ("gui", "desktop", "qt", "gtk", "wxwidgets", "tkinter", "window",
                "widget", "pyqt", "pyside", "electron", "swing")
_EMBEDDED_MARKERS = ("embedded", "firmware", "microcontroller", "arduino",
                     "stm32", "esp32", "rtos", "bare metal", "bare-metal", "mcu")
_DRIVER_MARKERS = ("driver", "kernel module", "kernel-module", "device driver",
                   "ioctl", "kmod", "ko module")


def _any(blob, markers):
    return any(m in blob for m in markers)


def detect_shape(spec, repo_path=None):
    """Map an APPROVED structured spec to one of the SUPPORTED_SHAPES.

    ``spec`` is the approved structured spec dict (the LIVE clarify stage, not
    this script, produced it). Recognized keys:
      * ``shape``     - an explicit shape the spec already declares (wins).
      * ``stack``     - "python" | "cpp" (steers the ambiguous default).
      * ``kind``/``target`` - a coarse hint ("cli", "library", "web_api",
        "console", "gui", "embedded", "driver").
      * ``summary``/``description``/``prompt`` + ``signals``/``keywords`` -
        free-text-ish fields probed for the markers above.

    Returns a structured result (never raises on an odd spec)::

        {shape, stack, verifiable: bool, confidence: "high"|"low",
         reasons: [...], supported: bool}

    This NEVER guesses silently: when the signals are ambiguous it still returns
    a best-effort shape but marks ``confidence: "low"`` and records why in
    ``reasons`` so the LIVE clarify gate (the model's job, not this script's) can
    ask the user. The clarify decision itself is NOT made here.
    """
    spec = spec or {}
    supported = resolve_supported_shapes(repo_path)
    reasons = []
    blob = _spec_signals(spec)
    stack = str(spec.get("stack") or "").lower().strip()
    kind = str(spec.get("kind") or spec.get("target") or "").lower().strip()

    # (0) An explicitly-declared shape wins outright (the clarify stage settled
    # it); we still validate it against the supported list.
    explicit = str(spec.get("shape") or "").lower().strip()
    if explicit:
        shape = explicit
        reasons.append("spec declared shape {!r} explicitly".format(explicit))
        return _finalize_shape(shape, stack, reasons, supported, confidence="high")

    # (1) Honest-partial shapes are checked FIRST: a GUI/embedded/driver intent
    # must not be mis-detected as a plain CLI/console just because it also
    # mentions a command line.
    if _any(blob, _GUI_MARKERS) or kind == "gui":
        reasons.append("GUI/desktop toolkit markers present")
        return _finalize_shape("gui", stack or "python", reasons, supported,
                               confidence="high")
    if _any(blob, _EMBEDDED_MARKERS) or kind == "embedded":
        reasons.append("embedded/firmware markers present")
        return _finalize_shape("embedded", stack or "cpp", reasons, supported,
                               confidence="high")
    if _any(blob, _DRIVER_MARKERS) or kind == "driver":
        reasons.append("device-driver/kernel markers present")
        return _finalize_shape("driver", stack or "cpp", reasons, supported,
                               confidence="high")

    # (2) Coarse `kind`/`target` hints map directly when the stack is known.
    kind_map = {
        ("python", "web_api"): "python_web_api",
        ("python", "webapi"): "python_web_api",
        ("python", "cli"): "python_cli",
        ("python", "library"): "python_library",
        ("cpp", "console"): "cpp_console",
        ("cpp", "cli"): "cpp_console",
        ("cpp", "library"): "cpp_library",
    }
    if (stack, kind) in kind_map:
        shape = kind_map[(stack, kind)]
        reasons.append("stack={!r} + kind={!r}".format(stack, kind))
        return _finalize_shape(shape, stack, reasons, supported, confidence="high")

    # (3) Keyword probing, stack-aware.
    if stack == "cpp":
        if _any(blob, _CPP_LIB_MARKERS) and not _any(blob, _CPP_CONSOLE_MARKERS):
            reasons.append("C++ library markers")
            return _finalize_shape("cpp_library", "cpp", reasons, supported,
                                   confidence="high")
        if _any(blob, _CPP_CONSOLE_MARKERS):
            reasons.append("C++ console/executable markers")
            return _finalize_shape("cpp_console", "cpp", reasons, supported,
                                   confidence="high")
        reasons.append("C++ stack but no clear console/library markers -> "
                       "defaulting to cpp_console (LOW confidence)")
        return _finalize_shape("cpp_console", "cpp", reasons, supported,
                               confidence="low")

    if stack == "python" or not stack:
        if _any(blob, _PY_WEB_MARKERS):
            reasons.append("Python web-framework markers")
            return _finalize_shape("python_web_api", "python", reasons, supported,
                                   confidence="high")
        if _any(blob, _PY_LIB_MARKERS) and not _any(blob, _PY_CLI_MARKERS):
            reasons.append("Python library/package markers")
            return _finalize_shape("python_library", "python", reasons, supported,
                                   confidence="high")
        if _any(blob, _PY_CLI_MARKERS):
            reasons.append("Python CLI/console markers")
            return _finalize_shape("python_cli", "python", reasons, supported,
                                   confidence="high")
        # Ambiguous: no stack, no markers. Best-effort python_cli, LOW
        # confidence, surfaced for the clarify gate.
        reasons.append("no stack and no shape markers detected -> best-effort "
                       "python_cli (LOW confidence; clarify recommended)")
        return _finalize_shape("python_cli", "python", reasons, supported,
                               confidence="low")

    # Unknown stack with no markers: honest low-confidence default.
    reasons.append("unrecognized stack {!r} with no markers (LOW "
                   "confidence)".format(stack))
    return _finalize_shape("python_cli", stack or "python", reasons, supported,
                           confidence="low")


def _finalize_shape(shape, stack, reasons, supported, confidence):
    """Assemble the detect_shape result dict and classify verifiability."""
    verifiable = shape in FULLY_VERIFIABLE_SHAPES
    is_supported = shape in supported
    if not is_supported:
        reasons = list(reasons) + [
            "shape {!r} is not in the supported list {}".format(
                shape, list(supported))
        ]
    if not verifiable and shape in HONEST_PARTIAL_SHAPES:
        reasons = list(reasons) + [
            "shape {!r} is HONEST-PARTIAL: it cannot be fully auto-verified "
            "(no headless display / target hardware); it will be emitted, "
            "compiled and unit-tested, and reported partial -- never "
            "green".format(shape)
        ]
    if not stack:
        stack = _SHAPE_STACK.get(shape, "python")
    return {
        "shape": shape,
        "stack": stack or _SHAPE_STACK.get(shape, "python"),
        "verifiable": verifiable,
        "confidence": confidence,
        "supported": is_supported,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# (b) SKELETON EMISSION.
# ---------------------------------------------------------------------------


def _project_name(spec, default="app"):
    """Return a safe, deterministic project/package name from the spec."""
    raw = str(spec.get("name") or spec.get("project") or default).strip()
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in raw)
    safe = safe.strip("_").lower() or default
    if safe[0].isdigit():
        safe = "p_" + safe
    return safe


def _write(path, text):
    """Write ``text`` to ``path`` (creating parent dirs). Deterministic + idempotent."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _adapter_fragment(adapter_path, shape):
    """Return the per-shape template fragment from an adapter, or {}.

    FEAT-003 adds a ``generate.templates`` section to the adapters. When present
    (and PyYAML is available), this returns the fragment for ``shape``; otherwise
    an empty dict so emission falls back to the built-in templates. Reading the
    adapter here is OPTIONAL and never required.
    """
    if not adapter_path or not os.path.isfile(adapter_path):
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with open(adapter_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, ValueError):
        return {}
    generate = data.get("generate") if isinstance(data, dict) else None
    if isinstance(generate, dict):
        templates = generate.get("templates")
        if isinstance(templates, dict) and isinstance(templates.get(shape), dict):
            return templates[shape]
    return {}


def _emit_python_cli(spec, dest, name):
    files = []
    files.append(_mk(dest, "pyproject.toml", _PYPROJECT.format(
        name=name, desc="APIA-generated Python CLI skeleton.")))
    files.append(_mk(dest, os.path.join(name, "__init__.py"),
                     '"""{} package (APIA-generated skeleton)."""\n'.format(name)))
    files.append(_mk(dest, os.path.join(name, "__main__.py"), _PY_CLI_MAIN.format(
        name=name)))
    files.append(_mk(dest, os.path.join("tests", "test_smoke.py"),
                     _PY_CLI_TEST.format(name=name)))
    return files


def _emit_python_library(spec, dest, name):
    files = []
    files.append(_mk(dest, "pyproject.toml", _PYPROJECT.format(
        name=name, desc="APIA-generated Python library skeleton.")))
    files.append(_mk(dest, os.path.join(name, "__init__.py"), _PY_LIB_INIT.format(
        name=name)))
    files.append(_mk(dest, os.path.join("tests", "test_smoke.py"),
                     _PY_LIB_TEST.format(name=name)))
    return files


def _emit_python_web_api(spec, dest, name):
    files = []
    files.append(_mk(dest, "pyproject.toml", _PYPROJECT.format(
        name=name, desc="APIA-generated Python web-API skeleton.")))
    files.append(_mk(dest, os.path.join(name, "__init__.py"),
                     '"""{} web-API package (APIA-generated skeleton)."""\n'.format(name)))
    files.append(_mk(dest, os.path.join(name, "app.py"), _PY_WEB_APP.format(
        name=name)))
    files.append(_mk(dest, os.path.join("tests", "test_smoke.py"),
                     _PY_WEB_TEST.format(name=name)))
    return files


def _emit_cpp_console(spec, dest, name):
    header = name + "_lib.h"
    files = []
    files.append(_mk(dest, "CMakeLists.txt", _CMAKE_CONSOLE.format(name=name)))
    files.append(_mk(dest, os.path.join("src", "main.cpp"),
                     _CPP_MAIN.format(header=header)))
    files.append(_mk(dest, os.path.join("src", name + "_lib.cpp"),
                     _CPP_LIB_SRC.format(header=header)))
    files.append(_mk(dest, os.path.join("include", header), _CPP_LIB_HDR))
    files.append(_mk(dest, os.path.join("tests", "test_smoke.cpp"),
                     _CPP_TEST.format(header=header)))
    return files


def _emit_cpp_library(spec, dest, name):
    header = name + "_lib.h"
    files = []
    files.append(_mk(dest, "CMakeLists.txt", _CMAKE_LIBRARY.format(name=name)))
    files.append(_mk(dest, os.path.join("src", name + "_lib.cpp"),
                     _CPP_LIB_SRC.format(header=header)))
    files.append(_mk(dest, os.path.join("include", header), _CPP_LIB_HDR))
    files.append(_mk(dest, os.path.join("tests", "test_smoke.cpp"),
                     _CPP_TEST.format(header=header)))
    return files


def _emit_partial(spec, dest, name, shape, stack):
    """Emit a MINIMAL skeleton + a clear partial marker for a non-verifiable shape.

    gui/embedded/driver get a minimal buildable skeleton for the underlying
    stack plus a plainly-worded PARTIAL.md so the user is never shown a faked
    green. We do NOT emit a full feature layout for these.
    """
    files = []
    if stack == "cpp":
        header = name + "_lib.h"
        files.append(_mk(dest, "CMakeLists.txt", _CMAKE_LIBRARY.format(name=name)))
        files.append(_mk(dest, os.path.join("src", name + "_lib.cpp"),
                         _CPP_LIB_SRC.format(header=header)))
        files.append(_mk(dest, os.path.join("include", header), _CPP_LIB_HDR))
        files.append(_mk(dest, os.path.join("tests", "test_smoke.cpp"),
                         _CPP_TEST.format(header=header)))
    else:
        files.append(_mk(dest, "pyproject.toml", _PYPROJECT.format(
            name=name, desc="APIA-generated {} skeleton (partial).".format(shape))))
        files.append(_mk(dest, os.path.join(name, "__init__.py"), _PY_LIB_INIT.format(
            name=name)))
        files.append(_mk(dest, os.path.join("tests", "test_smoke.py"),
                         _PY_LIB_TEST.format(name=name)))
    files.append(_mk(dest, "PARTIAL.md", _PARTIAL_MD.format(shape=shape)))
    return files


_EMITTERS = {
    "python_cli": _emit_python_cli,
    "python_library": _emit_python_library,
    "python_web_api": _emit_python_web_api,
    "cpp_console": _emit_cpp_console,
    "cpp_library": _emit_cpp_library,
}


def _mk(dest, relpath, text):
    """Write a file under dest and return its repo-relative path (for the manifest)."""
    full = os.path.join(dest, relpath)
    _write(full, text)
    return relpath.replace("\\", "/")


def emit_skeleton(spec, dest, repo_path=None, adapter_path=None):
    """Emit the idiomatic base layout + build files for the spec's shape ONLY.

    Writes the skeleton (NOT feature logic) for the detected shape to ``dest``:
    build file (pyproject.toml / CMakeLists.txt), package/source layout, an
    entrypoint/stub, and a tests/ dir with a smoke test. Emission is
    DETERMINISTIC and IDEMPOTENT (re-emitting overwrites to the same bytes).
    Per-shape adapter template fragments (added in FEAT-003) are used WHEN
    PRESENT; otherwise the built-in templates are used.

    For an HONEST-PARTIAL shape (gui/embedded/driver) only a minimal skeleton +
    a PARTIAL.md marker is emitted (never a full feature layout, never a faked
    green).

    Returns::

        {shape, stack, dest, verifiable, partial, files: [...], detection: {...}}
    """
    spec = spec or {}
    detection = detect_shape(spec, repo_path=repo_path)
    shape = detection["shape"]
    stack = detection["stack"]
    name = _project_name(spec)
    os.makedirs(dest, exist_ok=True)

    # Adapter fragments are optional (FEAT-003). We read them so the hook exists,
    # but the built-in templates below are always a complete fallback.
    _adapter_fragment(adapter_path, shape)  # presence hook; result unused when absent

    emitter = _EMITTERS.get(shape)
    if emitter is not None:
        files = emitter(spec, dest, name)
        partial = False
    else:
        files = _emit_partial(spec, dest, name, shape, stack)
        partial = True

    return {
        "shape": shape,
        "stack": stack,
        "dest": os.path.abspath(dest),
        "verifiable": detection["verifiable"],
        "partial": partial,
        "files": sorted(files),
        "detection": detection,
    }


# ---------------------------------------------------------------------------
# (c) VERIFICATION NORMALIZATION.
# ---------------------------------------------------------------------------


def _adapter_for_stack(stack):
    """Resolve the verify.py adapter path for a stack ('python' | 'cpp')."""
    apia_dir = os.path.dirname(_SCRIPTS_DIR)
    return os.path.join(apia_dir, "adapters", "{}.yml".format(stack))


_EMPTY_COUNTS = {"passed": 0, "failed": 0, "error": 0, "skipped": 0,
                 "no_tests": False}


def _counts_from_phase(phase):
    """Return a copy of a verify phase's counts in the shared shape."""
    counts = dict(_EMPTY_COUNTS)
    if phase and isinstance(phase.get("counts"), dict):
        counts.update({k: phase["counts"].get(k, counts[k]) for k in counts})
    return counts


def _project_name_from_project(project_path, shape):
    """Infer the generated project's name from its on-disk layout.

    The adapter run_command uses a ``{name}`` placeholder (the package name for
    Python ``python -m {name}`` / imports, the CMake target for C++
    ``./build/{name}``). We recover it from the emitted layout so the run step
    does not depend on the original spec:

      * C++: the ``project(<name> ...)`` declaration in CMakeLists.txt (the same
        token used for the console ``add_executable`` target and the binary name
        under build/).
      * Python: the single importable package directory (a dir holding
        ``__init__.py`` that is not ``tests``).

    Returns a best-effort name, or ``"app"`` (the emit default) if nothing is
    recognizable, so a run_command still formats rather than crashing.
    """
    stack = _SHAPE_STACK.get(shape)
    is_cpp = stack == "cpp" or os.path.isfile(
        os.path.join(project_path, "CMakeLists.txt"))
    if is_cpp:
        cmake = os.path.join(project_path, "CMakeLists.txt")
        if os.path.isfile(cmake):
            try:
                with open(cmake, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                text = ""
            import re
            m = re.search(r"project\(\s*([A-Za-z0-9_]+)", text)
            if m:
                return m.group(1)
    else:
        try:
            entries = sorted(os.listdir(project_path))
        except OSError:
            entries = []
        for entry in entries:
            if entry == "tests":
                continue
            pkg_init = os.path.join(project_path, entry, "__init__.py")
            if os.path.isfile(pkg_init):
                return entry
    return "app"


def _resolve_run_command(shape, adapter_path, name):
    """Return the shape's smoke/run command with ``{name}`` substituted, or None.

    The source of truth for what "run" means per shape is the adapter's
    ``generate.templates.<shape>.run_command`` (FEAT-003): e.g. python_cli
    ``python -m {name}``, python_web_api a health() import, cpp_console
    ``./build/{name}``. A shape whose idiomatic run IS the test suite (a library)
    may still declare a run_command (e.g. cpp_library's ``ctest ...``); that is
    honest -- the run reflects whatever the adapter says the shape's run means.

    Returns None when the adapter has no fragment for the shape or the fragment
    declares no run_command, so a shape lacking one falls back to the current
    (test-level) behavior WITHOUT crashing on a missing adapter fragment.
    """
    fragment = _adapter_fragment(adapter_path, shape)
    if not isinstance(fragment, dict):
        return None
    raw = fragment.get("run_command")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return raw.format(name=name)
    except (KeyError, IndexError, ValueError):
        # A malformed template placeholder must never crash verification.
        return None


def _run_smoke_step(run_command, project_path):
    """Execute a shape's run_command via verify.py's EXISTING _run() chokepoint.

    This adds NO new subprocess chokepoint: it reuses verify._split_command()
    (the same interpreter-rewrite the test phases use, so ``python`` maps to the
    harness's own interpreter / .venv) and verify._run() (the harness's single
    subprocess entry point), so scaffold.py stays stdlib+PyYAML-only and keeps
    its no-subprocess-of-its-own / no-network discipline.

    Returns the ``run`` field dict: {ran, passed, command, returncode,
    stdout_tail, stderr_tail}. ``passed`` is True iff the entrypoint/binary
    exited 0 (a non-zero exit must never be reported as a passing run).
    """
    argv = verify._split_command(run_command)
    returncode, stdout, stderr = verify._run(argv, cwd=project_path)
    return {
        "ran": True,
        "passed": returncode == 0,
        "command": " ".join(argv),
        "returncode": returncode,
        "stdout_tail": verify._tail(stdout),
        "stderr_tail": verify._tail(stderr),
    }


def verify_generated(project_path, shape, adapter_path=None, repo_path=None):
    """Drive build -> run/smoke -> unit tests for a generated project; NORMALIZE.

    Reuses verify.py's EXISTING machinery (verify.verify, which internally uses
    run_build_phase + the result parsers + the single ``_run()`` chokepoint) --
    this module adds NO second subprocess chokepoint and NO second parser.

    The RUN/SMOKE step is a REAL execution of the shape's runnable entrypoint/
    binary, not an alias of the unit-test phase: for a shape that declares a
    ``generate.templates.<shape>.run_command`` in the adapter (e.g. python_cli
    ``python -m {name}``, cpp_console ``./build/{name}``, python_web_api a
    health() import, cpp_library ``ctest ...``) scaffold resolves that command,
    substitutes the project name, and runs it through verify.py's existing
    ``_run()`` chokepoint from the project directory (for C++ AFTER the build
    phase, so the binary exists). The ``run`` field reflects THAT execution
    (ran/passed/returncode + a short stdout/stderr tail) and is DISTINCT from the
    ``tests`` field (which stays the unit-test phase result). A run that exits
    non-zero makes a fully-verifiable result NOT ok -- a run is never faked green.
    A shape with no run_command (or a missing adapter fragment) falls back to the
    prior test-level ``run`` signal without crashing.

    For a FULLY-verifiable shape (python_cli/library/web_api, cpp_console/
    library): the generated project must BUILD (C++), RUN its entrypoint/binary,
    run its unit tests, and be all-green. ``partial`` is False; ``verifiable`` is
    True; ``ok`` = build ok (if any) AND tests green AND run passed (when the
    shape declares a run_command).

    For an HONEST-PARTIAL shape (gui/embedded/driver): only what CAN run runs
    (compile/build + any unit tests). ``partial`` is set True and
    ``unverified_aspects`` lists -- in plain language -- what still needs a human
    check. partial is NEVER reported as green.

    Returns the SHARED counts shape plus the generation-specific fields::

        {passed, failed, error, skipped, no_tests,   # <- shared counts shape
         shape, verifiable, partial, build, run, tests,
         unverified_aspects: [...], ok: bool, summary: {...}}
    """
    stack = _SHAPE_STACK.get(shape)
    if stack is None:
        # Honest-partial shapes carry their stack via the project layout: a
        # CMakeLists.txt means cpp, else python.
        stack = "cpp" if os.path.isfile(
            os.path.join(project_path, "CMakeLists.txt")) else "python"
    adapter_path = adapter_path or _adapter_for_stack(stack)
    verifiable = shape in FULLY_VERIFIABLE_SHAPES

    summary = verify.verify(project_path, adapter_path=adapter_path)

    phases = {p.get("phase"): p for p in summary.get("phases", [])}
    build_phase = phases.get("build")
    # The baseline phase runs the generated project's unit tests (the smoke
    # test). regression repeats them; both are the "tests" signal here.
    test_phase = phases.get("baseline") or phases.get("regression")

    build_ok = build_phase["passed"] if build_phase else None
    tests_ok = test_phase["passed"] if test_phase else None

    # The counts shape is taken from the test phase (the unit-test signal); a
    # pure build-only project (no test phase) carries zeroed counts.
    counts = _counts_from_phase(test_phase)

    # REAL run/smoke step: execute the shape's runnable entrypoint/binary (per
    # the adapter's run_command) through verify.py's EXISTING _run() chokepoint,
    # DISTINCT from the unit-test phase above. For C++ this runs AFTER the build
    # phase so the binary exists; if the build failed we do not run a stale/
    # absent binary. A shape with no run_command (or a missing fragment) falls
    # back to the prior test-level run signal (run == tests) without crashing.
    name = _project_name_from_project(project_path, shape)
    run_command = _resolve_run_command(shape, adapter_path, name)
    build_blocks_run = build_phase is not None and not build_ok
    if run_command and not build_blocks_run:
        run_field = _run_smoke_step(run_command, project_path)
    else:
        # Fallback: no runnable entrypoint declared (or build failed) -> report
        # the run honestly from the test-phase signal rather than inventing one.
        run_field = {"ran": test_phase is not None, "passed": tests_ok}
    run_ok = run_field.get("passed")

    result = dict(counts)  # start with {passed,failed,error,skipped,no_tests}
    result.update({
        "shape": shape,
        "verifiable": verifiable,
        "build": {"ran": build_phase is not None, "passed": build_ok},
        "run": run_field,
        "tests": {"ran": test_phase is not None, "passed": tests_ok,
                  "counts": counts},
        "summary": summary,
    })

    unverified = []
    if verifiable:
        # FULLY verifiable: every executed phase must be green AND real tests
        # ran AND -- when the shape has a runnable entrypoint/binary -- the real
        # run exited 0. ok = build ok (if any) AND tests green AND run passed.
        result["partial"] = False
        all_green = bool(summary.get("ok")) and not counts["no_tests"]
        if run_command:
            all_green = all_green and bool(run_ok)
        result["ok"] = all_green
    else:
        # HONEST-PARTIAL: compile/build + unit tests are what we CAN check; the
        # runtime behavior cannot be auto-verified. NEVER green.
        result["partial"] = True
        result["ok"] = False
        if shape == "gui":
            unverified.append(
                "GUI behavior: windows, widgets and user interaction cannot be "
                "auto-verified without a human at a display; verify the UI "
                "manually.")
        elif shape == "embedded":
            unverified.append(
                "On-device behavior: this cannot be auto-verified without the "
                "target hardware/emulator; flash and test on the device.")
        elif shape == "driver":
            unverified.append(
                "Driver load/runtime behavior: cannot be auto-verified without "
                "the target kernel/privileges; load and test manually.")
        else:
            unverified.append(
                "This shape cannot be fully auto-verified; manual verification "
                "is required.")
        if build_phase is not None and not build_ok:
            unverified.append("the build/compile step did NOT pass.")
        if test_phase is not None and not tests_ok:
            unverified.append("the unit tests did NOT pass.")
    result["unverified_aspects"] = unverified
    return result


# ---------------------------------------------------------------------------
# (d) GENERATION GATE.
# ---------------------------------------------------------------------------


def generation_gate(findings, thresholds=None, repo_path=None):
    """Decide the generation gate by CALLING scan.apply_thresholds.

    This does NOT reimplement any threshold logic. When ``thresholds`` is not
    given, it resolves the SAME ``review.thresholds`` block from .apia/config.yml
    via scan.py's EXISTING loader (REPO-FIRST, package fallback), exactly like
    the standalone Reviewer gate. Returns scan's gate block UNCHANGED
    (passed / fail_on_severity / failing_severities / failing_counts / reasons).
    """
    if thresholds is None:
        review_config = scan.load_review_config(repo_path)
        thresholds = scan.resolve_thresholds(review_config)
    return scan.apply_thresholds(findings or [], thresholds)


# ---------------------------------------------------------------------------
# (e) ITERATE + ANTI-CIRCULARITY.
# ---------------------------------------------------------------------------


def decide_generation(spec, verify_result, findings, acceptance=None,
                      repo_path=None, state=None):
    """Decide green/iterate/stop/ask for a generation increment.

    Thin composition over the EXISTING primitives (nothing is reinvented here):
      * evalloop.resolve_max_iterations        - the HARD iterate cap;
      * evalloop.propose_acceptance_criteria /
        evalloop.acceptance_criteria_confirmed - the anti-circularity gate;
      * generation_gate -> scan.apply_thresholds - the severity gate.

    ``state`` is an evalloop.LoopState threaded by the caller so the iteration
    counter persists across attempts (identical contract to evalloop.evaluate).
    The actual code FIX between attempts is a LIVE Copilot action, never done by
    this script.

    Decision order:
      1. ANTI-CIRCULARITY: if ``acceptance`` describes NEW behavior that is not
         yet human-confirmed (and the config gate is on), return
         ``{status: 'ask', reason: 'acceptance_criteria_unconfirmed'}`` -- the
         loop must not grade a change against unconfirmed new-behavior criteria.
      2. GATE: if the reviewer findings breach the thresholds, this is a failure
         to feed back -> iterate (or stop at the cap).
      3. VERIFY: if verification is not green (a fully-verifiable shape that is
         not all-green, OR an honest-partial shape), feed back -> iterate (or
         stop at the cap). An honest-partial shape can NEVER be 'green'.
      4. GREEN: gate passed AND verification green -> stop, success.

    Returns a JSON-serializable decision dict analogous to evalloop.evaluate::

        {status: 'green'|'iterate'|'stop'|'ask', reason, iteration,
         max_iterations, remaining_iterations, gate, verify, acceptance?, ask?}
    """
    spec = spec or {}
    repo_path = os.path.abspath(repo_path) if repo_path else None
    if state is None:
        state = evalloop.LoopState()

    cap = evalloop.resolve_max_iterations(repo_path)

    # (1) ANTI-CIRCULARITY GATE (reuse evalloop's predicate, not a copy). The
    # caller may pass an explicit ``acceptance`` (a propose_acceptance_criteria
    # result, possibly confirmed) or let us propose from the spec.
    gate_on = evalloop.resolve_acceptance_criteria_gate(repo_path)
    if acceptance is None:
        acceptance = evalloop.propose_acceptance_criteria(spec)
    if gate_on and acceptance and not evalloop.acceptance_criteria_confirmed(acceptance):
        return {
            "status": "ask",
            "reason": "acceptance_criteria_unconfirmed",
            "iteration": state.iteration,
            "max_iterations": cap,
            "remaining_iterations": max(cap - state.iteration, 0),
            "acceptance": acceptance,
            "ask": (
                "These acceptance criteria describe NEW behavior the generated "
                "code does not yet exhibit. Confirm them before APIA grades the "
                "generation against them (anti-circularity gate): {}".format(
                    [c.get("statement") for c in acceptance.get("criteria") or []]
                )
            ),
        }

    # Spend one iteration (identical to evalloop.evaluate's counter discipline).
    state.iteration += 1

    gate = generation_gate(findings, repo_path=repo_path)
    verify_result = verify_result or {}
    verify_green = bool(verify_result.get("ok")) and not verify_result.get("partial")

    # (4) GREEN: both the gate and verification pass.
    if gate["passed"] and verify_green:
        return {
            "status": "green",
            "reason": "gate_passed_and_verified",
            "iteration": state.iteration,
            "max_iterations": cap,
            "remaining_iterations": max(cap - state.iteration, 0),
            "gate": gate,
            "verify": verify_result,
            "acceptance": acceptance,
            "state": state.to_dict(),
        }

    # Not green: assemble the failure reasons to feed back to the live model.
    reasons = []
    if not gate["passed"]:
        reasons.extend(gate.get("reasons") or ["reviewer thresholds breached"])
    if not verify_green:
        if verify_result.get("partial"):
            reasons.append("verification is HONEST-PARTIAL (never green for this "
                           "shape); manual checks remain: {}".format(
                               verify_result.get("unverified_aspects") or []))
        else:
            reasons.append("verification did not pass (build/run/tests not all "
                           "green)")

    # (2+3) RED at the HARD cap -> STOP; never silently ship a broken increment.
    if state.iteration >= cap:
        return {
            "status": "stop",
            "reason": "max_iterations_reached",
            "iteration": state.iteration,
            "max_iterations": cap,
            "remaining_iterations": 0,
            "gate": gate,
            "verify": verify_result,
            "failure_reasons": reasons,
            "ask": (
                "Reached the hard max_iterations cap ({}) without a green, "
                "gate-passing generation. The loop will NOT ship a broken "
                "increment. How would you like to proceed -- adjust the spec, "
                "relax a constraint, or stop for manual review?".format(cap)
            ),
            "state": state.to_dict(),
        }

    # RED with budget remaining -> iterate: hand the failure back to the live model.
    return {
        "status": "iterate",
        "reason": "gate_or_verify_failed",
        "iteration": state.iteration,
        "max_iterations": cap,
        "remaining_iterations": max(cap - state.iteration, 0),
        "gate": gate,
        "verify": verify_result,
        "failure_reasons": reasons,
        "state": state.to_dict(),
    }


# ---------------------------------------------------------------------------
# Built-in skeleton templates (deterministic; feature logic is NOT emitted).
# ---------------------------------------------------------------------------

_PYPROJECT = '''[project]
name = "{name}"
version = "0.1.0"
description = "{desc}"
requires-python = ">=3.9"
dependencies = []

[project.optional-dependencies]
test = ["pytest"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
'''

_PY_CLI_MAIN = '''"""Entrypoint for the {name} CLI (APIA-generated skeleton).

Feature logic is written live by Copilot; this is only the runnable stub so the
skeleton builds, runs and is testable from the first increment.
"""


def main(argv=None):
    """Return the CLI result. Replace the body with real feature logic."""
    return "{name}: ready"


if __name__ == "__main__":  # pragma: no cover - exercised via -m in real use
    print(main())
'''

_PY_CLI_TEST = '''from {name}.__main__ import main


def test_main_runs():
    # Smoke test: the generated entrypoint is importable and returns a result.
    assert main() == "{name}: ready"
'''

_PY_LIB_INIT = '''"""{name} library (APIA-generated skeleton).

Feature logic is written live by Copilot; this is the importable stub so the
package builds and is testable from the first increment.
"""


def version():
    """Return the package version string. Replace with real API."""
    return "0.1.0"
'''

_PY_LIB_TEST = '''from {name} import version


def test_version():
    # Smoke test: the generated package is importable and exposes its API stub.
    assert version() == "0.1.0"
'''

_PY_WEB_APP = '''"""{name} web-API app (APIA-generated skeleton).

A single smoke endpoint stub so the skeleton is testable without a running
server. Feature logic (routes, models) is written live by Copilot. The handler
is framework-agnostic so the smoke test needs no web dependency.
"""


def health():
    """Smoke endpoint: return the service health payload."""
    return {{"status": "ok", "service": "{name}"}}
'''

_PY_WEB_TEST = '''from {name}.app import health


def test_health():
    # Smoke test: the health endpoint stub returns an ok payload.
    assert health()["status"] == "ok"
'''

_CMAKE_CONSOLE = '''# APIA-generated C++ console skeleton (CMake + GoogleTest via FetchContent).
#
# Mirrors samples/cppapp: a library target holds the logic, a console executable
# links it, and GoogleTest is fetched so the skeleton is self-contained. Feature
# logic is written live by Copilot; this is the buildable+testable stub.
cmake_minimum_required(VERSION 3.14)
project({name} CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library({name}_lib src/{name}_lib.cpp)
target_include_directories({name}_lib PUBLIC include)

add_executable({name} src/main.cpp)
target_link_libraries({name} PRIVATE {name}_lib)

include(FetchContent)
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG v1.14.0
)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

enable_testing()
add_executable(test_smoke tests/test_smoke.cpp)
target_link_libraries(test_smoke PRIVATE {name}_lib GTest::gtest_main)
include(GoogleTest)
gtest_discover_tests(test_smoke)
'''

_CMAKE_LIBRARY = '''# APIA-generated C++ library skeleton (CMake + GoogleTest via FetchContent).
#
# A library target + public headers + a GoogleTest suite. Feature logic is
# written live by Copilot; this is the buildable+testable stub.
cmake_minimum_required(VERSION 3.14)
project({name} CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library({name}_lib src/{name}_lib.cpp)
target_include_directories({name}_lib PUBLIC include)

include(FetchContent)
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG v1.14.0
)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

enable_testing()
add_executable(test_smoke tests/test_smoke.cpp)
target_link_libraries(test_smoke PRIVATE {name}_lib GTest::gtest_main)
include(GoogleTest)
gtest_discover_tests(test_smoke)
'''

_CPP_LIB_HDR = '''// APIA-generated skeleton header. Replace the stub with real API.
#pragma once

namespace app {
// Smoke function so the skeleton links and is testable from the first increment.
int answer();
}  // namespace app
'''

_CPP_LIB_SRC = '''// APIA-generated skeleton source. Feature logic is written live by Copilot.
#include "{header}"

namespace app {{
// Smoke stub so the skeleton links and tests from the first increment.
int answer() {{ return 42; }}
}}  // namespace app
'''

_CPP_MAIN = '''// APIA-generated C++ console entrypoint (skeleton).
#include <iostream>
#include "{header}"

int main() {{
  // Smoke: run the stub and print its result so the console app is runnable.
  std::cout << "ready: " << app::answer() << std::endl;
  return 0;
}}
'''

_CPP_TEST = '''// APIA-generated GoogleTest smoke suite (skeleton).
#include <gtest/gtest.h>
#include "{header}"

TEST(SmokeTest, AnswerIsStable) {{
  // Smoke test: the generated library symbol links and returns its stub value.
  EXPECT_EQ(app::answer(), 42);
}}
'''

_PARTIAL_MD = '''# Partial verification notice ({shape})

APIA generated this project as an HONEST-PARTIAL shape: `{shape}` cannot be
fully auto-verified in a headless environment (no display / no target hardware /
no privileged kernel). APIA emitted the skeleton, a build check and unit tests,
but it will NEVER report a faked green for this shape.

What APIA verified: the skeleton builds/compiles and its unit tests run.

What needs YOUR manual check: the runtime behavior specific to this shape
(window/UI interaction, on-device execution, or driver load), which no automated
step in this environment can prove.
'''


# The C++ stubs always use the ``app`` namespace and include their project's own
# header (``{header}`` substituted at emit time) so a single built-in template
# set compiles regardless of project name; the public header is emitted under
# <name>_lib.h and the library target's PUBLIC include dir makes it resolvable.


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _load_spec(path):
    """Load a spec file (JSON or YAML) into a dict. Pure local file read."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    # Try JSON first (a strict subset), then YAML when available.
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml
    except ImportError:
        raise SystemExit("spec is not valid JSON and PyYAML is unavailable to "
                         "read YAML: {}".format(path))
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SystemExit("spec did not parse to a mapping: {}".format(path))
    return data


def _load_findings(path):
    """Load a findings JSON list (or a scan report dict with 'findings')."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "findings" in data:
        return data["findings"]
    if isinstance(data, list):
        return data
    raise SystemExit("findings file must be a JSON list or a scan report with a "
                     "'findings' key: {}".format(path))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "APIA off-model greenfield-generation support engine (shape "
            "detection, skeleton emission, verification normalization, and the "
            "generation gate; no LLM/network calls)."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--detect-shape", dest="detect_spec", metavar="SPEC",
                       help="Read a spec JSON/YAML and print the detected shape.")
    group.add_argument("--emit", dest="emit_spec", metavar="SPEC",
                       help="Emit the skeleton for a spec to --dest.")
    group.add_argument("--verify", dest="verify_dir", metavar="DIR",
                       help="Run verify_generated on a generated project dir.")
    group.add_argument("--gate", dest="gate_findings", metavar="FINDINGS",
                       help="Print the gate decision from a findings JSON.")
    group.add_argument("--doctor", action="store_true",
                       help="Run the shared offline toolchain preflight and exit.")
    parser.add_argument("--dest", default=None,
                        help="Destination directory for --emit.")
    parser.add_argument("--shape", default=None,
                        help="Shape for --verify (required with --verify).")
    parser.add_argument("--adapter", default=None,
                        help="Override the stack adapter path.")
    parser.add_argument("--repo", default=None,
                        help="Repo root for REPO-FIRST config resolution.")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    if args.doctor:
        if ingest is None:
            parser.error("doctor preflight unavailable: could not import ingest.py")
        report = ingest.doctor(repo_path=(
            args.repo if args.repo and os.path.isdir(args.repo) else None))
        print(ingest.format_doctor_report(report), end="")
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    if args.detect_spec:
        spec = _load_spec(args.detect_spec)
        result = detect_shape(spec, repo_path=args.repo)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("shape: {} (stack={}, verifiable={}, confidence={})".format(
                result["shape"], result["stack"], result["verifiable"],
                result["confidence"]))
            for r in result["reasons"]:
                print("  - {}".format(r))
        return 0

    if args.emit_spec:
        if not args.dest:
            parser.error("--emit requires --dest DIR")
        spec = _load_spec(args.emit_spec)
        result = emit_skeleton(spec, args.dest, repo_path=args.repo,
                               adapter_path=args.adapter)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("emitted {} skeleton ({}) to {}".format(
                result["shape"], "partial" if result["partial"] else "full",
                result["dest"]))
            for f in result["files"]:
                print("  + {}".format(f))
        return 0

    if args.verify_dir:
        if not args.shape:
            parser.error("--verify requires --shape SHAPE")
        result = verify_generated(args.verify_dir, args.shape,
                                  adapter_path=args.adapter, repo_path=args.repo)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
        else:
            state = "GREEN" if result["ok"] else (
                "PARTIAL" if result["partial"] else "FAILED")
            print("verify {} => {} (passed={} failed={})".format(
                result["shape"], state, result["passed"], result["failed"]))
            for u in result["unverified_aspects"]:
                print("  ! {}".format(u))
        return 0 if result["ok"] else 1

    if args.gate_findings:
        findings = _load_findings(args.gate_findings)
        gate = generation_gate(findings, repo_path=args.repo)
        if args.json:
            print(json.dumps(gate, indent=2, sort_keys=True))
        else:
            print("gate: {}".format("PASSED" if gate["passed"] else "FAILED"))
            for r in gate["reasons"]:
                print("  - {}".format(r))
        return 0 if gate["passed"] else 1

    parser.error("no action selected")


if __name__ == "__main__":
    sys.exit(main())
