// APIA Reviewer FIXTURE source (intentional, detectable C++ smells).
//
// This file exists ONLY so the Phase-2 Reviewer's off-model scanners
// (.apia/scripts/scan.py -> cppcheck / clang-tidy) produce REAL, asserted
// findings against samples/cppapp. It is deliberately NOT referenced by
// CMakeLists.txt and therefore NOT compiled or linked into the mathops library,
// the report_main producer, or the GoogleTest `test_mathops` target, so the
// existing green GoogleTest baseline is completely untouched. cppcheck/clang-tidy
// analyze it as a standalone translation unit (they do not need it in the build).
//
// Smells below, with the scanner + check expected to flag each:
//   * cppcheck `uninitvar`          - returning an uninitialized variable
//   * cppcheck `memleak`            - allocated buffer never freed
//   * cppcheck performance checks   - needless by-value / passed-by-value smell
// DO NOT "fix" these; they are the reviewer's proof fixtures.

#include <cstring>

// Intentional cppcheck uninitvar smell: `x` is read without being assigned.
int uninitialized_read() {
    int x;
    return x;
}

// Intentional cppcheck memleak smell: `buf` is allocated and never freed.
int leaky(int n) {
    char* buf = new char[n];
    std::memset(buf, 0, n);
    return buf[0];
}

// Intentional perf smell: a large object taken by value (cppcheck passedByValue
// / performance category) instead of by const reference.
struct BigThing {
    char data[1024];
};

int by_value(BigThing thing) {
    return thing.data[0];
}
