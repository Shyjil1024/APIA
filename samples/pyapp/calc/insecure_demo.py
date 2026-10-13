"""APIA Reviewer FIXTURE module (intentional, detectable smells).

This file exists ONLY so the Phase-2 Reviewer's off-model scanners
(.apia/scripts/scan.py) produce REAL, asserted findings against samples/pyapp.
It is deliberately NOT imported by calc/__init__.py and NOT collected by the
project's pytest suite (pyproject.toml pins ``testpaths = ["tests"]``, and this
module lives under ``calc/``), so the existing green test baseline is untouched.

Each smell below is marked with the scanner + rule expected to flag it:
  * bandit B602 / B604  - subprocess with shell=True (command-injection risk)
  * bandit B105         - hardcoded password string
  * radon rank C+        - the intentionally over-complex function
DO NOT "fix" these; they are the reviewer's proof fixtures.
"""

import subprocess

# bandit B105: hardcoded password (intentional fixture smell).
API_PASSWORD = "s3cr3t-hardcoded-password"  # noqa


def run_shell(cmd):
    """Intentional bandit B602/B604 smell: subprocess with shell=True."""
    return subprocess.call(cmd, shell=True)


def over_complex(value):
    """Intentional radon high-complexity smell (deeply nested branches)."""
    total = 0
    if value > 0:
        if value > 1:
            if value > 2:
                if value > 3:
                    if value > 4:
                        if value > 5:
                            total += 6
                        else:
                            total += 5
                    else:
                        total += 4
                else:
                    total += 3
            else:
                total += 2
        else:
            total += 1
    elif value < 0:
        if value < -1:
            total -= 1
        else:
            total -= 2
    else:
        total = 0
    return total
