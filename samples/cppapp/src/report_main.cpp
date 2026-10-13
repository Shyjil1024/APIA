// Deterministic artifact producer for the APIA Layer-1 proof (FEAT-004).
//
// The C++ counterpart of samples/pyapp/calc/report.py: it runs the REAL
// `mathops` arithmetic over a fixed, deterministic set of input rows and emits
// a JSON report. The self-correction loop grades freshly produced output of
// this binary against a committed golden copy (samples/cppapp/tests/golden/).
//
// Determinism is the whole point: the same inputs always yield byte-identical
// output, so a committed golden is a stable oracle and the differential run can
// prove an unrelated change leaves the artifact byte-identical. Performs NO
// network or LLM call; it only computes and prints.
//
// Usage:
//   report_main              -> print the JSON report to stdout
//   report_main <out.json>   -> also write the JSON report to <out.json>
//
// With no argument the JSON goes to stdout, so this doubles as the
// characterization capture command (the frozen stdout IS the recorded
// behavior).

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

#include "mathops.h"

namespace {

// Fixed deterministic input rows. The COUNT is the invariant oracle
// (row_count_matches_input): the produced report must have exactly this many
// rows. Mirrors samples/pyapp/calc/report.py INPUT_ROWS.
struct Row {
    int a;
    int b;
};

const Row kInputRows[] = {
    {2, 3},
    {4, 5},
    {10, 7},
};

// Build the deterministic JSON report string. Keys are emitted in a fixed,
// SORTED order (a, b, diff, prod, sum) with no incidental whitespace so the
// output is byte-stable and matches the Python producer's canonical form.
std::string BuildReport() {
    std::ostringstream out;
    out << "[";
    bool first = true;
    for (const Row& row : kInputRows) {
        if (!first) {
            out << ",";
        }
        first = false;
        out << "{"
            << "\"a\":" << row.a << ","
            << "\"b\":" << row.b << ","
            << "\"diff\":" << mathops::subtract(row.a, row.b) << ","
            << "\"prod\":" << mathops::multiply(row.a, row.b) << ","
            << "\"sum\":" << mathops::add(row.a, row.b)
            << "}";
    }
    out << "]\n";
    return out.str();
}

}  // namespace

int main(int argc, char** argv) {
    const std::string report = BuildReport();
    if (argc > 1) {
        std::ofstream fh(argv[1], std::ios::binary);
        fh << report;
    }
    std::fputs(report.c_str(), stdout);
    return 0;
}
