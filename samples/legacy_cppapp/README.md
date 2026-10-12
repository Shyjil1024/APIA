# legacy_cppapp (legacy, originally no-tests sample)

A deliberately **originally untested** legacy C++ library (`inventory`) used to
demonstrate the APIA legacy / characterization-first path. It is the C++
counterpart of `samples/legacy_pyapp`: there were no tests here on purpose.

This sample proves the no-tests characterization flow end to end:

1. **Characterization suite** (`tests/test_inventory_characterization.cpp`) pins
   the *current* behavior of `inventory.cpp` (including quirks, e.g.
   `apply_discount` performs no validation so a negative percent raises the
   price) BEFORE any change, so regressions become detectable even though the
   code shipped without a test suite.
2. **Safe change**: a NEW function (`reorder_quantity`) is added without
   altering any existing function, covered by `tests/test_reorder.cpp`. The
   characterization suite stays green, proving the change was safe.

The committed suites follow the adapter's characterization template
`.github/skills/apia-test-authoring/templates/cpp_characterization_test.cpp.tmpl`
referenced from `.apia/adapters/cpp.yml`. GoogleTest is fetched via CMake
FetchContent (pinned `v1.14.0`), so the sample is portable and self-contained.

## Configure, build, and test

Run from the repository root:

```sh
cmake -S samples/legacy_cppapp -B samples/legacy_cppapp/build
cmake --build samples/legacy_cppapp/build
ctest --test-dir samples/legacy_cppapp/build --output-on-failure
```

All tests should report passed. The generated `build/` directory is gitignored
and must never be committed.
