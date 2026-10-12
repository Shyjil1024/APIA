"""Tests for GENERIC C++ detection in the off-model ingester (ingest.py).

Covers the DETECT-FIRST / ASK-ONLY-ON-GENUINE-DOUBT / ASK-SPECIFICALLY
principle, the persisted project-facts artifact + .index.json block, MSBuild
static detection, and the offline doctor/preflight. The confident GoogleTest
case runs against the committed samples/cppapp; the ambiguous CTest-only case
and the MSBuild case use small inline tmp_path fixtures so no C++ toolchain or
network is exercised. The doctor test may invoke a local `cmake --version`.
"""

import json
import os
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
CPPAPP = os.path.join(REPO_ROOT, "samples", "cppapp")


def _read(knowledge_dir, name):
    with open(os.path.join(knowledge_dir, name), "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Confident GoogleTest case (real sample)
# ---------------------------------------------------------------------------
def test_cppapp_detected_cmake_googletest_high_confidence(tmp_path):
    kd = str(tmp_path / "kd")
    result = ingest.ingest(CPPAPP, kd)
    assert result["stacks"] == ["cpp"]

    with open(os.path.join(kd, ingest.INDEX_FILE), "r", encoding="utf-8") as fh:
        index = json.load(fh)
    facts = index["project_facts"]["cpp"]
    assert facts["build_system"] == "cmake"
    assert "googletest" in facts["test_frameworks"]
    assert facts["confidence"] >= 0.75
    assert facts["needs_clarification"] is False
    assert facts["clarifying_question"] is None
    # Concrete evidence must be listed (file: marker).
    assert any("gtest/gtest.h" in e for e in facts["evidence"])


def test_cppapp_project_facts_artifact_emitted(tmp_path):
    kd = str(tmp_path / "kd")
    ingest.ingest(CPPAPP, kd)
    pf = _read(kd, "project-facts.md")
    assert "## C++" in pf
    assert "cmake" in pf
    assert "googletest" in pf
    assert "Evidence" in pf
    assert "gtest/gtest.h" in pf


def test_cppapp_stack_md_surfaces_detection(tmp_path):
    kd = str(tmp_path / "kd")
    ingest.ingest(CPPAPP, kd)
    stack_md = _read(kd, "stack.md")
    # Table row shape preserved.
    assert "| cpp |" in stack_md
    # Detection prose surfaced beneath the table.
    assert "C++ detection:" in stack_md
    assert "cmake" in stack_md


# ---------------------------------------------------------------------------
# Ambiguous CTest-only case (inline fixture) -> below threshold + question
# ---------------------------------------------------------------------------
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_ctest_only_is_ambiguous_and_composes_specific_question(tmp_path):
    repo = tmp_path / "ctestonly"
    # CMake with CTest wiring but NO gtest/catch/boost includes anywhere.
    _write(str(repo / "CMakeLists.txt"),
           "cmake_minimum_required(VERSION 3.14)\n"
           "project(ctestonly CXX)\n"
           "add_executable(runner src/main.cpp)\n"
           "enable_testing()\n"
           "add_test(NAME runner COMMAND runner)\n")
    _write(str(repo / "src" / "main.cpp"),
           "int main() { return 0; }\n")

    kd = str(tmp_path / "kd")
    result = ingest.ingest(str(repo), kd)
    assert "cpp" in result["stacks"]

    with open(os.path.join(kd, ingest.INDEX_FILE), "r", encoding="utf-8") as fh:
        index = json.load(fh)
    facts = index["project_facts"]["cpp"]
    assert facts["build_system"] == "cmake"
    assert facts["confidence"] < 0.75
    assert facts["needs_clarification"] is True
    q = facts["clarifying_question"]
    assert q, "a specific clarifying question must be recorded"
    # The question must name the CTest/standalone inference and ask the user to
    # name the framework or paste the exact test command.
    assert "CTest" in q
    assert "standalone" in q
    assert "paste the exact command" in q
    # And it must be surfaced in the artifact for the model/user to see.
    pf = _read(kd, "project-facts.md")
    assert "Clarifying question" in pf
    assert "CTest" in pf


# ---------------------------------------------------------------------------
# MSBuild static detection (inline fixture, never built)
# ---------------------------------------------------------------------------
def test_msbuild_detected_from_vcxproj_and_sln(tmp_path):
    repo = tmp_path / "msb"
    _write(str(repo / "App.sln"),
           "Microsoft Visual Studio Solution File, Format Version 12.00\n")
    _write(str(repo / "App" / "App.vcxproj"),
           "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
           "<Project xmlns=\"http://schemas.microsoft.com/developer/msbuild/2003\">\n"
           "</Project>\n")
    _write(str(repo / "App" / "main.cpp"), "int main(){return 0;}\n")

    kd = str(tmp_path / "kd")
    result = ingest.ingest(str(repo), kd)
    assert "cpp" in result["stacks"]
    with open(os.path.join(kd, ingest.INDEX_FILE), "r", encoding="utf-8") as fh:
        index = json.load(fh)
    assert index["project_facts"]["cpp"]["build_system"] == "msbuild"


def test_msbuild_vstest_markers_detected(tmp_path):
    repo = tmp_path / "msbtest"
    _write(str(repo / "Tests.vcxproj"),
           "<Project>Microsoft.VisualStudio.TestTools</Project>\n")
    _write(str(repo / "tests" / "UnitTest.cpp"),
           "[TestClass]\nclass X { [TestMethod] void M() {} };\n")
    kd = str(tmp_path / "kd")
    ingest.ingest(str(repo), kd)
    with open(os.path.join(kd, ingest.INDEX_FILE), "r", encoding="utf-8") as fh:
        index = json.load(fh)
    facts = index["project_facts"]["cpp"]
    assert facts["build_system"] == "msbuild"
    assert "vstest" in facts["test_frameworks"]


def test_infer_build_system_prefers_cmake_when_both_present():
    files = ["CMakeLists.txt", "App.sln", "src/main.cpp"]
    assert ingest.infer_build_system(files) == "cmake"
    assert ingest.infer_build_system(["App.vcxproj"]) == "msbuild"
    assert ingest.infer_build_system(["src/main.cpp"]) is None


# ---------------------------------------------------------------------------
# doctor / preflight
# ---------------------------------------------------------------------------
def test_doctor_reports_present_tools_and_flags_missing():
    # cmake/ctest are present in this sandbox; a bogus tool is deliberately
    # missing and must produce a clear warning.
    tools = [
        ("cmake", ["--version"], "CMake."),
        ("ctest", ["--version"], "CTest."),
        ("definitely-not-a-real-tool-xyz", ["--version"], "Deliberately missing."),
    ]
    report = ingest.doctor(tools=tools)
    by_name = {e["tool"]: e for e in report["tools"]}
    assert by_name["cmake"]["present"] is True
    assert by_name["cmake"]["version"]  # local `cmake --version` succeeded
    assert by_name["ctest"]["present"] is True
    missing = by_name["definitely-not-a-real-tool-xyz"]
    assert missing["present"] is False
    assert any("definitely-not-a-real-tool-xyz" in w for w in report["warnings"])


def test_doctor_flags_vs_developer_shell_for_msbuild():
    report = ingest.doctor(tools=[("msbuild", ["-version"], "MSBuild.")])
    # No msbuild in this (Linux) sandbox -> the VS Developer-shell warning fires.
    assert report["tools"][0]["present"] is False
    assert any("Developer Command Prompt" in w for w in report["warnings"])


def test_doctor_report_is_renderable_string():
    report = ingest.doctor(tools=[("cmake", ["--version"], "CMake.")])
    text = ingest.format_doctor_report(report)
    assert "APIA doctor" in text
    assert "cmake" in text
