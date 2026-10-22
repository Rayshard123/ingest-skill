# Ingest evaluation set

- `evals.json` contains three human-readable dry-run skill evaluations in the skill-creator schema.
- `quality_cases.jsonl` and `routing_cases.jsonl` remain broader intent/routing inventories.
- Deterministic executor, profile, path-boundary, recovery, zero-pollution, and anchor regressions live in `scripts/test_ingest_v1.py`.

Run deterministic tests only with a local non-synced `INGEST_V1_TEST_ROOT` and the profile venv. The tests create disposable fixture Vaults and never target the production Wiki.
