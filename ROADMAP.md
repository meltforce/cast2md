# Roadmap

**This file contains open work only.** Every row carries the status token
`[open]`. Closed work is not struck through here — it is removed and lives in
[`DECISIONS.md`](DECISIONS.md) (decisions, with their reasoning) or
[`INCIDENTS.md`](INCIDENTS.md) (postmortems).

Columns: **Status** is always `[open]`. **Where** names the artifact the work
touches. **Trigger** carries the condition for items that are deliberately
deferred, and is empty for items that are simply pending. **Notes** carries the
reasoning.

Before closing an item, check its entry for residual work, dates or triggers —
each becomes its own `[open]` row before the entry is moved out.

## CI and verification

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Run `pytest` and `ruff` in CI | `.forgejo/workflows/ci.yml` | | The `build` job runs `uv build` only. The job needs a Postgres service container: without `DATABASE_URL`, 68 of 102 tests error at fixture setup and 34 pass. `ruff` needs the baseline below cleared first, or it fails every run. |
| `[open]` | Clear the 366 `ruff` findings | `src/`, `tests/` | | 196 are auto-fixable with `ruff check --fix`. This is pre-existing debt, not a regression — `ruff` has never run in CI. Until it is cleared, `ruff` cannot gate anything. |
| `[open]` | Decide whether the deploy gate needs a positive signal | `.forgejo/workflows/ci.yml` | After the CI test job lands | A skipped `build-deploy` still reports success. The 2026-08-01 incident was found by inspection, not by an alert; nothing currently distinguishes "deployed" from "silently skipped". |

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
