# legacy_pyapp (legacy, no-tests sample)

A deliberately **untested** legacy Python module used to demonstrate the APIA
legacy path. There are no tests here on purpose.

In Phase 1, FEAT-004 uses this sample to prove the characterization-test flow:
APIA generates tests that pin the *current* behavior of `inventory.py` before
any change is proposed, so regressions become detectable even though the code
shipped without a test suite.
