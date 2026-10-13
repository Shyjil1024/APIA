"""Deterministic artifact producer for the APIA Layer-1 proof (FEAT-004).

This small producer turns a fixed list of input rows into two deterministic
artifacts the self-correction loop grades against committed goldens:

  * ``report.json`` -- a JSON array of per-row records (one record per input
    row), produced by running the REAL ``calc.operations`` arithmetic. This is
    the golden-file / invariant / differential subject for the Python stack.
  * ``table.csv``   -- the same rows rendered as CSV, used to demonstrate the
    ``text`` compare mode (newline-insensitive) alongside the ``json`` mode.

The producer is PURE and OFFLINE: it imports only the stdlib and the sample's
own ``calc.operations`` and makes no network/LLM call. Determinism is the whole
point -- the same INPUT_ROWS always yield byte-identical artifacts, so a
committed golden copy is a stable oracle and the differential old-vs-new run can
prove that an unrelated change leaves an artifact byte-identical.

Run as a module from the sample root so ``calc`` resolves::

    python -m calc.report out/report.json out/table.csv

With no arguments it prints the JSON report to stdout (used as the
characterization capture command: the frozen stdout IS the recorded behavior).
"""

import csv
import io
import json
import os
import sys

from calc.operations import add, multiply, subtract

# Fixed, deterministic input. The row COUNT here is the invariant oracle
# (row_count_matches_input): the produced report must have exactly this many
# rows. Values are small integers so the arithmetic is exact and stable.
INPUT_ROWS = [
    {"a": 2, "b": 3},
    {"a": 4, "b": 5},
    {"a": 10, "b": 7},
]


def build_rows(input_rows=None):
    """Return the deterministic per-row report records.

    Each output record carries the inputs plus the results of the REAL
    ``calc.operations`` functions, so the artifact reflects actual code
    behavior (not a restatement of it). Pure and deterministic.
    """
    rows = INPUT_ROWS if input_rows is None else input_rows
    out = []
    for row in rows:
        a = row["a"]
        b = row["b"]
        out.append({
            "a": a,
            "b": b,
            "sum": add(a, b),
            "diff": subtract(a, b),
            "prod": multiply(a, b),
        })
    return out


def render_json(rows):
    """Render the rows as a stable JSON string (sorted keys, fixed separators)."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":")) + "\n"


def render_csv(rows):
    """Render the rows as a stable CSV string (fixed column order, LF endings)."""
    buf = io.StringIO()
    fieldnames = ["a", "b", "sum", "diff", "prod"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def write_artifacts(json_path, csv_path=None, input_rows=None):
    """Produce the artifact(s) on disk and return the rows that were written."""
    rows = build_rows(input_rows)
    os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
    with open(json_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(render_json(rows))
    if csv_path:
        os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(render_csv(rows))
    return rows


def main(argv=None):
    """CLI: write artifacts to the given paths, or print the JSON to stdout.

    With no path arguments the deterministic JSON report is printed to stdout so
    this doubles as the characterization capture command (the frozen stdout IS
    the recorded current behavior)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stdout.write(render_json(build_rows()))
        return 0
    json_path = argv[0]
    csv_path = argv[1] if len(argv) > 1 else None
    write_artifacts(json_path, csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
