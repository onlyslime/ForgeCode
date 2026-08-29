# ForgeCode v0.0.9 acceptance report

Date: 2026-08-28 (Asia/Shanghai). This report records a fresh local gate and
bounded offline feature evidence; runtime files remain ignored.

## Gate

| Command | Exit | Result |
|---|---:|---|
| `uv sync` | 0 | lockfile environment resolved |
| `uv run python -m compileall -q src tests` | 0 | source/tests compiled |
| `uv run pytest -rs` | 0 | **340 passed, 8 skipped, 2 warnings** in 210.97s (fresh release gate) |
| `uv run pytest tests/test_v009_features.py -q` | 0 | 5 focused feature tests |

The eight skips are Windows symlink/junction permission conditions (the tests
explicitly report unavailable link creation); the two warnings are pytest
collection warnings for imported helper classes. No assertion was weakened.

## Feature evidence

The focused suite proves automatic compaction emits an append-only event with
reason and fingerprint while preserving a bounded summary and tool context;
durable trajectory scoring rejects a model-only “tests pass” claim; path
completion is stable and advisory; named profiles are listed without secret
values; and clone/import/tree metadata uses fresh run ids with `replay=false`.

The existing v0.0.8 fresh DemoProvider Plan -> Act -> failing test -> patch ->
passing test -> review -> undo evidence remains covered by the 335-test
baseline and was not reimplemented with a second loop.

## Delivery risk

`https://github.com/onlyslime/ForgeCode.git` is still private. The assessment
requires a public repository, so publication remains an explicit owner action;
no visibility change or token operation was performed by this run.
Fresh offline E2E run `3b365f8b387842849605a281c7465927` completed with exit 0,
90 parseable JSONL lines, zero malformed envelopes and zero stderr bytes.
`eval latest --jsonl` returned status `completed`, score `1.0`, real
verification and audit complete. Review export/verify (`77bc89a669164bbf9ff97491ae90a15e`)
both exited 0. Approved undo exited 0; repeated undo exited 3 with a
preserved `transaction_conflict`.

The final sync, compile, full regression, doctor, CLI smoke and package-build
checks all exited 0; the full regression duration was 210.97s.
