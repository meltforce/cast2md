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
| `[open]` | Give vimmary the same deploy-freshness heartbeat | `vimmary`, `configuration/uptimekuma` | | cast2md got one on 2026-08-07 (`deploy-freshness` in `.forgejo/workflows/ci.yml`, monitor `cast2md - deploy freshness`). vimmary calls the same shared deploy workflow, sits on the same runner and had its own deploy outage on 2026-08-06, so it carries the same exposure and none of the signal. The pieces to copy: a `schedule` trigger, the job, a push monitor in the Uptime Kuma spec, a setec target, and the resulting URL as a repo secret. |

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

## API

Both faults found on 2026-08-07 were fixed the same day and are removed from
this table; their reasoning is in the commit messages. Neither came from that
day's deploy — the files were byte-identical to `d6510ed`, the commit
production had been serving. A third instance of the route-order fault,
`DELETE /api/nodes/stale`, was found by the general test written for the first
two and fixed with them.

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Exercise the endpoints that had never returned a response | `api/queue.py`, `api/nodes.py` | | `GET /api/queue/all`, `GET /api/queue/stuck` and `DELETE /api/nodes/stale` were unreachable from the day they were added until 2026-08-07, so their bodies have only ever run against a local instance with one feed and one job. `/api/queue/all` in particular carries the branch for `status="stuck"` and the N+1 over `episode_repo.get_by_id` per job, neither of which has seen a realistic result set. Check them against production data before anything is built on them. |

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
