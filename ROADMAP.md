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
| `[open]` | Make a frozen `:edge` fail visibly rather than only in the run list | `.forgejo/workflows/ci.yml`, homelab monitoring | | `build-deploy` failed on every push from `04adeee` (2026-08-06) to `b32746f` (2026-08-07), and production stayed on `edge-d6510ed` for 16 commits without anything reporting it. `deploy-gate` did turn the pipeline red, so the state was visible in the run list and nowhere else. Postmortem: `homelab/INCIDENTS.md`, 2026-08-07. The open question is what should carry the signal — a notification on a failed `main` run, or a check that compares the `build` field of `/api/health` against the head of `main` on a schedule. |

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

Both rows were found on 2026-08-07 while testing production after the deploy
that ended the frozen `:edge`. Neither was introduced by that deploy: the files
involved are byte-identical to `d6510ed`, the commit production had been serving.

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | `GET /api/search/transcripts` returns 500 on every query that builds a tsquery | `api/search.py:145-152` | | `SegmentResult.published_at` is typed `str \| None`, and the value reaches it as a `datetime` straight from the row (`search/repository.py:271`), so pydantic raises `string_type`. Reproduced on production for `KI`, `künstliche Intelligenz`, `KI-Revolution` and a quoted phrase. A query consisting only of stopwords returns 200 with 0 results, because `build_flexible_tsquery` yields an empty string and the method returns before building any row. `GET /api/search/episodes` is unaffected and returns 174 hits for `KI`. The `/search` page is not affected either — `web/views.py:transcript_search_page` renders server-side from the repository and does not call this endpoint. Decide whether the field takes `datetime` or the repository serialises it; `SearchResult.published_at` carries the same annotation and the same mismatch. |
| `[open]` | `GET /api/queue/all` and `/api/queue/stuck` are unreachable | `api/queue.py:539`, `:946`, `:995` | | `@router.get("/{job_id}")` is declared at line 539, ahead of `/stuck` at 946 and `/all` at 995. FastAPI matches in declaration order, so both paths bind `job_id="stuck"` / `"all"` and fail with 422 `int_parsing`. The declaration order is the same at `d6510ed`. Nothing in `src/`, `docs/`, `scripts/` or `tools/` calls either path, and the queue page renders server-side through `web/views.py:admin_queue_page`, so no surface depends on them today. Moving both declarations above `/{job_id}` fixes it; the endpoints then need their first exercise, since neither has ever returned a response. |

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
