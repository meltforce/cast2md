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

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | **A1** — move `RUNPOD_TRANSCRIPTION_MODELS` out of `config/settings.py` | `db/repository.py:2466`, `config/settings.py` | | The one `db → config` edge, and the whole of that cycle. Target is a module that imports nothing (`cast2md/constants.py` or `transcription/models.py`). The `config → db` direction stays and is then acyclic — it carries the database-backed settings that `api/settings.py` writes. Verify with `cycles.py`, expect the SCC to lose `config`. |
| `[open]` | **A2** — move `build_flexible_tsquery` below both `db/` and `search/` | `search/repository.py:78`, `db/repository.py:976` | | The one `db → search` edge. The function builds a tsquery string and holds no search state; `db/sql.py` is a candidate target because `search/repository.py:9` already imports from it. Leaves the five `search → db` edges as the only direction. Verify with `cycles.py`, expect `sccs: []`. |
| `[open]` | **B** — consolidate the 73-line clone of the word-level segment merge | `search/parser.py:95-167`, `transcription/formats.py:35-107` | | Exact copies, CC 17, and no test names either one (`grep -rn merge_word_level_segments tests/` is empty), so changing one copy alone produces no failing test. Keep the copy in `search/parser.py` and import it: `transcription → search` already exists as a package edge, the reverse does not. |
| `[open]` | **C** — delete the unused SQL dialect abstraction | `db/sql.py`, `db/config.py` | | 12 functions that `vulture` reports as unused across `src/`, `tests/`, `scripts/`, `tools/` and `deploy/`; `db/config.py:96` says `# SQL dialect helpers - PostgreSQL only`. From outside `db/` only three names are imported out of the two modules: `execute`, `get_database_config`, `get_db_config`. Grep the names across `templates/*.html` before deleting — `vulture` does not see Jinja2. |
| `[open]` | **D** — resolve `include_stuck`, a documented parameter with no implementation | `db/repository.py:1880-1895` | | Fifth parameter of `JobRepository.get_all_jobs`, documented at `:1895` as "If True and status is None, includes stuck indicator". The body never reads it and no caller passes it. Either implement the indicator or remove parameter and docstring line; `api/queue.py:1003` is the method's only consumer and decides which. |
| `[open]` | **E** — reconcile the repository rule with the 12 sites that break it | `docs/development/index.md`, `api/`, `mcp/tools.py`, `cli.py` | | The rule names `db/repository.py`, which excludes `search/repository.py` on wording though not in substance. 12 sites outside `db/` issue raw SQL; five of them are the same `SELECT segment_start, segment_end, text` and collapse into one repository method. Needs a `DECISIONS.md` entry either way — reword the rule so the grep in the report's § 2.2 checks it, move the sites behind repository methods, or both. |
| `[open]` | **F** — split `db/repository.py` | `db/repository.py` | A1 has landed | 3036 lines, 10 repository classes, CC sum 295, 51 commits — rank 1 of the hotspot ranking. The public surface is exactly those 10 class names across 24 importing files, so `db/repositories/<name>.py` plus a re-export module leaves every importer unchanged. `JobRepository` (1020) and `EpisodeRepository` (984) hold 2004 of the 3036 lines. Deferred until A1, which removes the file's only outgoing edge to `config/`. |
| `[open]` | **G** — establish why `web/views.py` changes so often, then decide the edit | `web/views.py`, `src/cast2md/templates/` | | 65 commits, the highest of any file; CC 28 in `admin_status_page` (231 lines), 8 direct `get_db` calls. The suspicion is that the churn follows the templates (`feed_detail.html` 47 commits, `base.html` 42, `status.html` 32), which is a measurement and not yet made: count how many of the 65 commits touch `views.py` and a template together. High correlation points at moving the `admin_status_page` aggregation into a repository or view model; low correlation means the split has to be decided on other grounds. |

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
