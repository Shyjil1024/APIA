# apia-eval benchmark corpus

This directory holds the APIA effectiveness-measurement corpus: seven declared,
offline, deterministic scenarios (under `scenarios/<id>/scenario.yml`) that
exercise the deterministic engines (scan / evaldata / ingest) against fixed
ground truth, plus the `baseline/` snapshot the harness writes. The corpus only
measures the off-model engines' behaviour against known inputs; it makes **no**
claim about AI-generation quality. (Placeholder — fully written in FEAT-004.)
