# cppapp (green baseline sample)

A tiny C++ library (`mathops`: `add` / `subtract` / `divide`) with an existing,
passing GoogleTest suite. It is the C++ counterpart of `samples/pyapp` and
serves as the APIA Phase-2 green baseline: an existing passing suite that acts
as the regression guard for the `ingest -> clarify -> modify -> test -> apply`
loop.

GoogleTest is pulled in via CMake **FetchContent** (pinned to `v1.14.0`), so the
sample is portable and self-contained: no system-wide GoogleTest install is
required (the first configure clones + builds GoogleTest, which takes a moment).

## Configure, build, and test

Run from the repository root:

```sh
cmake -S samples/cppapp -B samples/cppapp/build
cmake --build samples/cppapp/build
ctest --test-dir samples/cppapp/build --output-on-failure
```

All tests should report passed. The generated `build/` directory is gitignored
and must never be committed.

## Characterization-test path

The no-tests (legacy) characterization path is demonstrated by the sibling
sample `samples/legacy_cppapp`, which commits a GoogleTest characterization
suite that pins the current behavior of an initially untested library before a
new function is added. Both samples use the adapter's characterization template
`.github/skills/apia-test-authoring/templates/cpp_characterization_test.cpp.tmpl`
referenced from `.apia/adapters/cpp.yml`.
