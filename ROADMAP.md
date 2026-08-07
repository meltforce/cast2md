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

All seven items A to G landed on 2026-08-07 and are removed from this table.
A's and E's reasoning is in [`DECISIONS.md`](DECISIONS.md) under that date; B,
C, D, F and G carry theirs in their commit messages, because none of them
decided anything that a later session would re-derive. The rows below are the
residual work those items exposed, each recorded before its entry left.

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | The transcript-fetch card never reports an orphaned job | `web/status_view.py:_build_transcript_fetch_card` | | Found while moving the aggregation in **G**. The route added every running transcript-download job to the assigned set and then collected the jobs *not* in that set, so the list is empty by construction and the page has never shown one. The download and transcription cards do the same detection correctly, and the difference is that they assign against worker slots first. Preserved as-is by G, because fixing it changes what the page shows. Decide what an orphan means for a card that has no per-slot display, then implement it — the template already renders `orphaned` and `orphaned_total`. |
| `[open]` | Split `EpisodeRepository` and `JobRepository` by concern | `db/repositories/episode.py`, `db/repositories/job.py` | | Residual work from **F**, which moved the two classes into their own modules without making them smaller: `episode.py` is 1015 lines and `job.py` 1024, both above the p90 yardstick of 980 from the structure analysis. `JobRepository` mixes claiming and lifecycle with statistics (`get_completed_jobs_stats`, `get_audio_minutes_processed`, `count_stuck_jobs`); `EpisodeRepository` mixes CRUD with the FTS entry points `search_episodes_fts`, `search_episodes_fts_full` and `search_by_feed`. Unlike F this is not a mechanical move — it changes which class a caller names, so it needs the split lines drawn first. |
| `[open]` | Decide whether the remaining `web/views.py` routes are worth splitting | `web/views.py` | | Residual work from **G**, which took `admin_status_page` out and left the file at 853 lines. Next by complexity are `feed_detail` (CC 17), `render_transcript_html` (CC 16) and `episode_detail` (CC 15). Re-run G's co-change measurement once the file has settled: if the 72.3 % template correlation holds without `admin_status_page` in the count, the same treatment applies; if it drops, the churn was concentrated in the status page and the rest can stay. |
| `[open]` | Add tests for `web/status_view.py` | `tests/` | | `build_status_context` was split out of the route specifically so it can be exercised without Postgres — it takes a `StatusData` and a queue-status dict and returns the template context. No test does so yet. G was verified by comparing the context against the pre-change function over five worker-status variants, which is a one-off check rather than a regression guard. |

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
