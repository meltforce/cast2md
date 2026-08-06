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
| `[open]` | Run `pytest` and `ruff` in CI | `.forgejo/workflows/ci.yml` | | The `build` job runs `uv build` only. Eight test files and both tools are configured in `pyproject.toml`, so nothing but the wiring is missing. Until then the test suite is a local-only control. |
| `[open]` | Decide whether the deploy gate needs a positive signal | `.forgejo/workflows/ci.yml` | After the CI test job lands | A skipped `build-deploy` still reports success. The 2026-08-01 incident was found by inspection, not by an alert; nothing currently distinguishes "deployed" from "silently skipped". |

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
