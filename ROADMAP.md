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
| `[open]` | Move cast2md off `build-push-deploy.yml@v1` | `.forgejo/workflows/ci.yml` | | `v1` is from 2026-05-12; the other callers are on `v3`. Missing since then: authenticated Docker Hub login against the 429 rate limit (`v2`) and the compose sync (`v3`). **A bump needs `sync_compose: false`** — homelab owns the deployed `compose.yaml`, and `v3` would otherwise copy this repo's `docker-compose.yml`, the dev stack with password `dev` and port 5432 published, over it. `v2` also makes `DOCKERHUB_USERNAME`/`TOKEN` required secrets, which this repo does not pass today. |

## Code structure

Measured at `3bfb208` by the static structure analysis in
[`analysis/structure-report.md`](analysis/structure-report.md); the letters
match its section 4, where each row's numbers are reproducible by the command
that produced them.

A, B, C, D and E landed on 2026-08-07 and are removed from this table. A's and
E's reasoning is in [`DECISIONS.md`](DECISIONS.md) under that date; B, C and D
carry theirs in their commit messages, because none of them decided anything
that a later session would re-derive.

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | **F** — split `db/repository.py` | `db/repository.py` | | 3036 lines, 10 repository classes, CC sum 295, 51 commits — rank 1 of the hotspot ranking. The public surface is exactly those 10 class names across 24 importing files, so `db/repositories/<name>.py` plus a re-export module leaves every importer unchanged. `JobRepository` (1020) and `EpisodeRepository` (984) hold 2004 of the 3036 lines, and both stay above the p90 yardstick of 980 after the split — F removes the aggregation, not the two large classes. Two edits in the move are not mechanical: `tests/test_episode_status_query.py:43` and `tests/test_feed_status_counts.py:38` patch `cast2md.db.repository.execute` and must be repointed at the new module, and ruff needs an explicit `__all__` in both `db/repository.py` and `db/repositories/__init__.py` or F401 fires. The A1 trigger is met — that edge was removed on 2026-08-07. |
| `[open]` | **G** — move the `admin_status_page` aggregation out of `web/views.py` | `web/views.py`, `src/cast2md/templates/` | | 65 commits, the highest of any file; CC 28 in `admin_status_page` (231 lines). The measurement the row used to ask for is made: **47 of the 65 commits touch `views.py` and a template together, 72.3 %** — `base.html` 18, `status.html` 16, `feed_detail.html` 14, `search.html` 11, `episode_detail.html` 10, the rest below 8. The correlation is high, so the edit is the one the report names. `admin_status_page` holds no raw SQL (the report's "8 direct `get_db` calls" is a figure for the whole file); it opens one connection block and then derives — throughput, worker-slot assignment, three passes of orphan detection, server-vs-node state, display capping. Only the repository reads belong in a route. Its `episode_repo.get_by_id` in three loops is an N+1 that a batch method removes. |

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
