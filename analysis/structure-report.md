# Structure analysis

Static analysis at module level, `HEAD` = `3bfb208`, 2026-08-06. Subject are
import relations, module sizes, complexity and change pressure in
`src/cast2md/` — 72 Python files, 23,674 lines. Bugs inside individual
functions are not the subject.

Measurement and interpretation are kept apart: numbers carry the command and
the path that reproduces them, conclusions are marked **assumption**. Section 4
draws the actionable items from them; sections 1 to 3 state only what is the
case.

---

## 1. Intended architecture

No document in this repo describes a layering as such. There are four
statements with architectural content plus a directory layout; everything
beyond that is derived from directory names and marked as an assumption below.

| Statement | Source |
|---|---|
| "All database operations go through repository classes in `db/repository.py`. Direct SQL queries are concentrated there." | `docs/development/index.md`, *Key Patterns → Database Access* |
| Directory layout with a role per package (`api/` = REST endpoints, `web/` = HTML routes, `db/` = data access, `config/` = Pydantic settings, `worker/` = background threads, …) | `docs/development/index.md`, *Project Structure* |
| Registration order in `transcription/providers/__init__.py` is the priority order; new providers subclass `TranscriptProvider` | `src/cast2md/transcription/CLAUDE.md` |
| Distributed transcription is pull-based: nodes poll the server, the server never reaches a node | `docs/distributed/architecture.md`, *Design Principles* |

The resulting intended direction per layer, one sentence each:

1. **`config/`** — supplies settings to every other package and depends on none (*assumption*, derived from the role "Pydantic settings model").
2. **`db/`** — encapsulates data access, is used by the domain packages and uses none of them (*documented*, as far as the rule above fixes the direction "into `db/`").
3. **Domain packages** (`feed/`, `download/`, `transcription/`, `search/`, `storage/`, `notifications/`, `clients/`, `export/`) — use `db/` and `config/`, not each other in a cycle (*assumption*).
4. **Orchestration** (`worker/`, `distributed/`, `services/`, `scheduler.py`) — uses the domain packages (*assumption*).
5. **Edges** (`api/`, `web/`, `mcp/`, `cli.py`, `node/`) — use everything below and are used by nothing (*assumption*).

The rule in row 1 of the table is the only one that can be violated without
first postulating the layering. Violations of 3 to 5 are reported below as
violations of an *assumed* rule.

---

## 2. Cycles and layer violations

### 2.1 Cycles in the import graph

At package **and** file level the same single strongly connected component
appears, with three nodes and two elementary cycles. Command: `imports.py`
(AST) → `cycles.py` (Tarjan + DFS), both in *Appendix A*.

**Cycle 1 — `config` ⇄ `db`, 3 edges**

| Direction | Site | Imported name |
|---|---|---|
| `config → db` | `src/cast2md/config/settings.py:211` | `cast2md.db.connection.get_db` |
| `config → db` | `src/cast2md/config/settings.py:212` | `cast2md.db.repository.SettingsRepository` |
| `db → config` | `src/cast2md/db/repository.py:2466` | `cast2md.config.settings.RUNPOD_TRANSCRIPTION_MODELS` |

Rule violated: layer 1 of the intended architecture (*assumption*) — `config/`
should depend on no package. Both imports in `settings.py` sit inside
`_apply_db_overrides` (`config/settings.py:192`, CC 11), that is inside a
function body rather than at module level.

**Cycle 2 — `db` ⇄ `search`, 6 edges**

| Direction | Site | Imported name |
|---|---|---|
| `db → search` | `src/cast2md/db/repository.py:976` | `cast2md.search.repository.build_flexible_tsquery` |
| `search → db` | `src/cast2md/search/repository.py:9` | `cast2md.db.sql.execute` |
| `search → db` | `src/cast2md/search/repository.py:488` | `cast2md.db.repository.EpisodeRepository` |
| `search → db` | `src/cast2md/search/repository.py:602` | `cast2md.db.repository.EpisodeRepository` |
| `search → db` | `src/cast2md/search/repository.py:760` | `cast2md.db.connection.is_pgvector_available` |
| `search → db` | `src/cast2md/search/repository.py:761` | `cast2md.db.repository.EpisodeRepository` |

Rule violated: layer 2/3 (*assumption*) — `db/` should use no domain package.
Only `search/repository.py:9` is at module level; the other five sit inside
function bodies.

*Assumption:* eight of the nine edges are function-level imports, which is the
standard way to work around an import cycle. The cycle is therefore resolved at
runtime but not at the level of module dependency.

Self-loops: none. Cycles across four or more packages: none (DFS to length 5
across the SCC).

### 2.2 Violation of the documented repository rule

The rule from `docs/development/index.md` ("All database operations go through
repository classes in `db/repository.py`") is violated at 28 sites in 7 files
outside `db/`, measured by raw SQL keywords.

Command:
```bash
grep -rInE '(SELECT |INSERT INTO |UPDATE .* SET |DELETE FROM )' \
  src/cast2md --include='*.py' | grep -v '^src/cast2md/db/' \
  | awk -F: '{print $1}' | sort | uniq -c | sort -rn
```

| File | Hits |
|---|---|
| `src/cast2md/search/repository.py` | 16 |
| `src/cast2md/cli.py` | 4 |
| `src/cast2md/mcp/tools.py` | 3 |
| `src/cast2md/api/episodes.py` | 2 |
| `src/cast2md/api/system.py` | 1 |
| `src/cast2md/api/settings.py` | 1 |
| `src/cast2md/api/runpod.py` | 1 |

Sites in the edge layer, which under layer 5 (*assumption*) should not reach
the database at all:

- `src/cast2md/cli.py:553`, `:562`, `:674`, `:682` — `SELECT id, transcript_path FROM episode`
- `src/cast2md/mcp/tools.py:193`, `:668`, `:681` — `SELECT segment_start, segment_end, text`
- `src/cast2md/api/episodes.py:449`, `:461` — the same segment query
- `src/cast2md/api/settings.py:384` — `cursor.execute("DELETE FROM whisper_models")`
- `src/cast2md/api/runpod.py:512` — `cursor.execute("DELETE FROM runpod_models")`

*Assumption:* `search/repository.py` is not covered by the rule as worded (it
names `db/repository.py`) but satisfies it in substance, being a repository
class itself. The 12 hits in `cli.py`, `mcp/` and `api/` are not. The segment
query `SELECT segment_start, segment_end, text` appears verbatim at three sites
across two packages.

### 2.3 Edges against the assumed layering

Out of the package graph (Appendix B), five edges run against the direction
from step 1:

| Edge | Weight | Rule violated |
|---|---|---|
| `config → db` | 2 | layer 1 (*assumption*) |
| `db → config` | 1 | layer 2 (*assumption*) |
| `db → search` | 1 | layer 2 against layer 3 (*assumption*) |
| `download → feed` | 1 | two domain packages (*assumption*, layer 3) |
| `transcription → search` | 1 | two domain packages (*assumption*, layer 3) |

The remaining 79 of 84 package edges run in the intended direction. `api/`,
`web/`, `mcp/`, `cli.py` and `node/` are imported by no other package — layer 5
holds, with one exception: `cast2md/main.py` imports `api`, `web`, `mcp`,
`worker` and `services`, which matches its role as the application root.

### 2.4 Duplicates across package boundaries

Command: `npx -y jscpd src/cast2md --min-lines 15 --min-tokens 70 --format python`
→ 16 exact clones, 333 lines (1.41%) across 53 files.

The largest clone crosses a package boundary:

| Lines | Site A | Site B |
|---|---|---|
| 73 | `src/cast2md/search/parser.py:95-167` (`merge_word_level_segments`, CC 17) | `src/cast2md/transcription/formats.py:35-107` (`_merge_word_level_segments`) |

The other 15 clones sit inside a single file each, 6 of them in `api/nodes.py`,
4 in `worker/manager.py`, 3 in `search/repository.py`. Full list in Appendix F.

---

## 3. Hotspot ranking

Ranked by the product of a file's total cyclomatic complexity (`radon cc -j`,
summed over all functions and methods) and the number of commits touching it.
Both factors are measured; the product is a chosen weighting and therefore a
convention, not a measurement.

The yardsticks behind "large" and "complex" here:

- **Lines:** median across the 72 files = 205, p75 = 369, p90 = 980, maximum = 3036. "Large" below means "above p90", that is above 980 lines — 8 files.
- **Complexity:** grade C on radon's own scale, that is CC ≥ 11. The threshold comes from radon, not from this report.
- **Function length:** above 80 lines, threshold taken from the analysis brief.
- **Commits:** the full repo history. The 18-month window from the brief covers it completely — the first commit is `37d3130` of 2026-01-19, 471 commits in 6.5 months.

| # | Path | Lines | CC sum | CC max | Commits | Product | Suspicion (*assumption*) |
|---|---|---|---|---|---|---|---|
| 1 | `src/cast2md/db/repository.py` | 3036 | 295 | 15 | 51 | 15045 | Ten repository classes in one file, two of them above 980 lines (`JobRepository` 1020, `EpisodeRepository` 984); every schema extension lands here, which produces the second-highest change pressure in the repo. |
| 2 | `src/cast2md/web/views.py` | 1079 | 135 | 28 | 65 | 8775 | Highest change pressure of all files, CC max 28 in `admin_status_page` (231 lines); the HTML routes carry aggregation logic that no domain package holds. |
| 3 | `src/cast2md/services/runpod_service.py` | 1442 | 230 | 27 | 37 | 8510 | One class `RunPodService` spanning 1358 lines with CC sum 230; pod lifecycle, GPU selection and cleanup sit side by side without separation. |
| 4 | `src/cast2md/api/queue.py` | 1429 | 186 | 19 | 24 | 4464 | Largest file under `api/`, 31 direct `get_db` calls; the queue endpoints contain evaluation logic instead of delegating it. |
| 5 | `src/cast2md/node/worker.py` | 1009 | 141 | 14 | 21 | 2961 | `TranscriberNodeWorker` with 962 lines in one class; polling, termination check and upload in one object. |
| 6 | `src/cast2md/worker/manager.py` | 864 | 102 | 12 | 29 | 2958 | `WorkerManager` 814 lines, four clones inside the file (Appendix F); likely one job-type skeleton copied per type. |
| 7 | `src/cast2md/transcription/service.py` | 902 | 117 | 16 | 20 | 2340 | `TranscriptionService` 631 lines with two backends (Whisper, Parakeet) and one chunking variant each; a 22-line clone between them. |
| 8 | `src/cast2md/search/repository.py` | 980 | 97 | 28 | 21 | 2037 | `hybrid_search` at 248 lines and CC 28 is the longest function in the repo; also the endpoint of both cycle directions from 2.1. |
| 9 | `src/cast2md/api/nodes.py` | 983 | 119 | 21 | 16 | 1904 | Six clones inside the file, more than any other; `node_heartbeat` at CC 21. |
| 10 | `src/cast2md/cli.py` | 1009 | 97 | 12 | 14 | 1358 | Four raw SQL queries (2.2) at only 14 commits — low change pressure, but a rule violation. |

Files at the lower end, large without change pressure:
`transcription/formats.py` (487 lines, 8 commits), `export/formats.py` (241
lines, 5 commits), `clients/pocketcasts.py` (250 lines, 7 commits).
*Assumption:* stable format parsers whose size follows from the format rather
than from growth.

Cross-check on the ranking: `config/settings.py` has the fourth-highest commit
count at 39, but sits at rank 13 by product because its CC sum is 26. The
module changes often because settings are added, not because it is complex.

---

## 4. Next steps

Ordered by the ratio of measured effect to the size of the edit. Each item
names the sites it touches and the number it is derived from. Items A to C are
mechanical and independent of each other; D onwards need a design decision
first.

### A. Break both import cycles — 2 edges to move

Both cycles from 2.1 come apart by relocating one name each; the remaining
directions are then one-way.

**A1 — `db → config` (1 edge).** `db/repository.py:2466` reads
`RUNPOD_TRANSCRIPTION_MODELS` from `config/settings.py`. Moving that constant
into a module that imports nothing (a new `cast2md/constants.py`, or
`transcription/models.py`) removes the edge. The `config → db` direction stays
and is then acyclic. That direction carries a deliberate feature:
`_apply_db_overrides` reads settings out of the database, which is what
`api/settings.py` writes to.

**A2 — `db → search` (1 edge).** `db/repository.py:976` calls
`build_flexible_tsquery` (`search/repository.py:78`, CC 13). The function
builds a tsquery string and holds no search state. Moving it into a module
below both — for instance `db/sql.py`, which `search/repository.py:9` already
imports — removes the edge and leaves the five `search → db` edges as the only
direction.

Verification after the change: rerun `cycles.py`, expect `sccs: []`.

### B. Consolidate the 73-line clone — 1 function to delete

`search/parser.py:95-167` and `transcription/formats.py:35-107` are exact
copies at 73 lines, `merge_word_level_segments` and `_merge_word_level_segments`
(both 95 lines including signature and docstring, CC 17 for the former).

`transcription → search` already exists as a package edge (weight 1, Appendix
B), so keeping the copy in `search/parser.py` and importing it from
`transcription/formats.py` adds no new edge. The reverse direction would add
one. This decides which of the two survives.

Cost of not doing it: the function has CC 17, so a correction to the merge
logic has to be applied twice. No test covers both copies — `tests/` holds 11
files and 1926 lines against 23,674 lines of source, and neither copy is named
in them — so a change to one copy alone produces no failing test.

### C. Delete the unused dialect abstraction — 12 functions

`db/config.py:96` carries the comment `# SQL dialect helpers - PostgreSQL only`,
and `vulture` reports the 12 names below as unused across `src/`, `tests/`,
`scripts/`, `tools/` and `deploy/`:

```
db/config.py:41,85,97,106,118,127   get_postgres_dsn, reload_db_config,
                                    get_placeholder, get_placeholder_num,
                                    get_current_timestamp_sql, get_autoincrement_type
db/sql.py:6,27,36,48,57,121         ph, now_sql, bool_val, returning_clause,
                                    upsert_sql, adapt_params
```

From outside `db/` exactly three names are imported out of these two modules:
`execute` (`db/sql.py`, used at `search/repository.py:9`),
`get_database_config` and `get_db_config` (`db/config.py`). Appendix I.

Confirm before deleting: `vulture` does not see Jinja2 templates, so grep each
name across `src/cast2md/templates/*.html` as well. These are Python-only
helpers, so a template hit would be surprising and is worth knowing about.

### D. Check `include_stuck` at `db/repository.py:1886`

`vulture` reports this at 100% confidence as an unused variable. Checked: it is
the fifth parameter of `JobRepository.get_all_jobs`
(`db/repository.py:1880-1887`), declared `include_stuck: bool = False` and
documented at `:1895` as "If True and status is None, includes stuck
indicator". `grep -rn 'include_stuck' src/ tests/` returns only those two lines
— the body never reads it and no caller passes it.

So the flag is documented behaviour that does not exist. Two ways to close it:
implement the stuck indicator, or remove the parameter and the docstring line.
Which one is right depends on whether `api/queue.py:1003` (`get_all_jobs`, CC
13) was meant to use it; that endpoint is the only consumer of the method.

The two other 100% hits (`cli.py:897`, `main.py:109`, both named `frame`) are
signal-handler parameters required by the `signal` module signature. They
belong in `tools/check-docs.allow`'s equivalent for vulture, or in a
`# noqa`-style whitelist file, so the 100% tier stays empty and therefore
useful as a gate.

### E. Enforce the repository rule at the 12 edge-layer sites, or amend it

The rule in `docs/development/index.md` currently describes 12 sites that do
not follow it (2.2). Two ways out, and the choice is a decision for
`DECISIONS.md` either way:

**E1 — move the sites behind repository methods.** Grouped by the method each
would need:

| Sites | Proposed method |
|---|---|
| `api/episodes.py:449`, `:461`, `mcp/tools.py:193`, `:668`, `:681` | one segment reader on `TranscriptSearchRepository` — five copies of `SELECT segment_start, segment_end, text` collapse into one |
| `cli.py:553`, `:562`, `:674`, `:682` | one iterator over `(id, transcript_path)` on `EpisodeRepository` |
| `api/settings.py:384` | `WhisperModelRepository.delete_all()` |
| `api/runpod.py:512` | `RunPodModelRepository.delete_all()` |
| `api/system.py:127` (`SELECT 1`) | a connectivity probe, not a data operation. `db/connection.py` has no health helper today (checked), so either add one or exempt this site in the rule's wording |

The segment query alone accounts for 5 of the 12 sites, so E1 is mostly one
method.

**E2 — narrow the rule to what holds.** The sentence names `db/repository.py`,
which excludes `search/repository.py` on wording but not in substance. A
reworded rule would say: data access goes through repository classes under
`db/` and `search/`, and endpoint modules issue no SQL. That version is
enforceable by the grep in 2.2 and would fail today on 12 sites, which is the
point of writing it that way.

E1 and E2 are not exclusive — E2 makes the rule checkable, E1 makes the code
pass it.

### F. Split `db/repository.py` — mechanical, 10 names to preserve

3036 lines, 10 repository classes, 51 commits, CC sum 295 (rank 1 in section
3). The public surface is exactly 10 class names pulled by 24 files
(Appendix I), so a split into `db/repositories/<name>.py` with
`db/repository.py` kept as a re-export module leaves all 24 importers
unchanged.

Sizes that decide the cut (Appendix D): `JobRepository` 1020,
`EpisodeRepository` 984, `TranscriberNodeRepository` 275, `FeedRepository` 151,
`PodRunRepository` 126, `PodSetupStateRepository` 111, `WhisperModelRepository`
111, `RunPodModelRepository` 82. Two classes hold 2004 of the 3036 lines, so
extracting only those two takes the file to roughly a third of its size.

Do this after A, because A1 removes the file's only outgoing edge to `config/`
and a split would otherwise have to decide where that import goes.

### G. `web/views.py` — highest change pressure, 65 commits

CC 28 in `admin_status_page` (231 lines), CC 17 in `feed_detail` (118 lines),
CC 16 in `render_transcript_html` (84 lines), CC 15 in `episode_detail`. Eight
direct `get_db` calls.

*Assumption:* the change pressure follows the templates rather than the routes
— `templates/feed_detail.html` has 47 commits, `base.html` 42, `status.html`
32, and those numbers move together with the view functions that feed them.
What to do about it depends on whether that assumption holds, so the first step
is a measurement, not an edit: check how many of the 65 commits touch
`web/views.py` *and* a template in the same commit.

If the correlation is high, the aggregation for `admin_status_page` moving into
a repository or a view-model function is the edit that reduces both files. If
it is low, `views.py` is changing for its own reasons and the split has to be
decided on other grounds.

### H. What not to touch

Named explicitly because size alone would put them on a list:
`transcription/formats.py` (487 lines, 8 commits), `export/formats.py` (241
lines, 5 commits), `clients/pocketcasts.py` (250 lines, 7 commits). Their size
follows from the formats they parse, and 5 to 8 commits over the full history
is the bottom of the change-pressure distribution. Item B touches
`transcription/formats.py` for the clone and for nothing else.

The remaining 15 clones from 2.4 all sit inside a single file at 16 to 23 lines
each. At that size the extraction cost is comparable to the duplication cost,
so they are recorded in Appendix F and not proposed as work.

### Sequence

A → B → C → D are independent of each other and of any design decision. E needs
a `DECISIONS.md` entry. F depends on A1. G starts with a measurement, not an
edit.

None of these are on `ROADMAP.md`. Whether they become `[open]` rows there is a
separate call — this file is a snapshot at `3bfb208`, and `ROADMAP.md` is the
document that carries open work over time.

---

## 5. Raw data

### Appendix A — tools and scripts used

| Measurement | Command |
|---|---|
| Import graph, surface | `.venv/bin/python <scratch>/imports.py` (own AST script, source under A.1) |
| Cycles | `.venv/bin/python <scratch>/cycles.py <imports.json> pkg_edges` and `file_edges` (Tarjan SCC + DFS to length 5) |
| Function and class lengths | `.venv/bin/python <scratch>/lengths.py` (AST, `end_lineno - lineno + 1`) |
| Complexity | `uvx radon cc -n c -s src/`, JSON variant `uvx radon cc -j src/` |
| Change pressure | `git log --format= --name-only --since='18 months ago' \| sort \| uniq -c \| sort -rn \| head -30` |
| Dead code | `uvx vulture src/ tests/ scripts/ tools/ deploy/ --min-confidence 60` and `--min-confidence 100` |
| Clones | `npx -y jscpd src/cast2md --min-lines 15 --min-tokens 70 --format python` |
| Module sizes | `find src -name '*.py' \| xargs wc -l \| sort -rn` |
| Raw SQL | `grep -rInE '(SELECT \|INSERT INTO \|UPDATE .* SET \|DELETE FROM )' src/cast2md --include='*.py' \| grep -v '^src/cast2md/db/'` |

The scripts live in the session scratchpad and are not part of the repo.
`imports.py` resolves relative imports via `node.level`, maps dotted paths onto
packages against modules with an `is_dir()` check, and counts every `import`
statement as one edge.

**Not measurable:** none. All nine required measurements are present. One
limitation on item 8: `vulture` does not see Jinja2 access from
`templates/*.html`, which is why attributes of `db/models.py` appear as
candidates that the templates may well use. The numbers below are therefore an
upper bound.

### Appendix B — import graph between top-level packages, with edge weight

84 edges. `<root>/x.py` denotes a module directly under `src/cast2md/`.

```
 62  api -> db                          2  config -> db
 57  mcp -> db                          2  feed -> clients
 32  <root>/cli.py -> db                2  <root>/main.py -> <root>/scheduler.py
 28  services -> db                     2  <root>/main.py -> web
 15  transcription -> db                2  <root>/main.py -> search
 11  <root>/cli.py -> node              2  <root>/main.py -> storage
 10  api -> config                      2  mcp -> feed
 10  worker -> db                       2  node -> transcription
  9  <root>/main.py -> api              2  transcription -> clients
  9  <root>/main.py -> db               2  transcription -> storage
  9  web -> db                          2  worker -> storage
  8  feed -> db                         2  worker -> distributed
  6  api -> search                      1  <root>/__main__.py -> <root>/cli.py
  6  <root>/cli.py -> config            1  api -> download
  6  <root>/scheduler.py -> db          1  api -> transcription
  5  api -> storage                     1  api -> clients
  5  api -> services                    1  <root>/cli.py -> <root>/main.py
  5  download -> db                     1  config -> <root>
  5  node -> search                     1  db -> search
  5  search -> db                       1  db -> config
  5  worker -> search                   1  distributed -> services
  4  distributed -> db                  1  download -> config
  4  web -> search                      1  download -> feed
  4  web -> config                      1  feed -> config
  4  worker -> transcription            1  <root>/main.py -> <root>
  3  api -> feed                        1  <root>/main.py -> config
  3  <root>/cli.py -> search            1  <root>/main.py -> mcp
  3  download -> storage                1  <root>/main.py -> worker
  3  mcp -> search                      1  <root>/main.py -> services
  3  node -> config                     1  mcp -> config
  3  services -> config                 1  node -> storage
  3  transcription -> config            1  notifications -> config
  3  worker -> notifications            1  <root>/scheduler.py -> feed
  2  api -> export                      1  <root>/scheduler.py -> services
  2  api -> worker                      1  <root>/scheduler.py -> config
  2  api -> distributed                 1  storage -> config
  2  api -> notifications               1  transcription -> search
  2  <root>/cli.py -> feed              1  web -> worker
  2  <root>/cli.py -> download          1  web -> <root>
  2  <root>/cli.py -> transcription     1  web -> services
  2  <root>/cli.py -> mcp               1  worker -> config
  2  clients -> config                  1  worker -> download
```

Fan-in (number of importing modules) and fan-out (number of internal modules
imported), file level:

| Fan-in | Module | | Fan-out | Module |
|---:|---|---|---:|---|
| 25 | `cast2md.db.connection` | | 22 | `cast2md.main` |
| 24 | `cast2md.db.repository` | | 15 | `cast2md.cli` |
| 23 | `cast2md.config.settings` | | 13 | `cast2md.worker.manager` |
| 22 | `cast2md.db.models` | | 9 | `cast2md.web.views` |
| 12 | `cast2md.search.repository` | | 8 | `cast2md.api.queue` |
| 8 | `cast2md.storage.filesystem` | | 8 | `cast2md.api.feeds` |
| 7 | `cast2md.search.embeddings` | | 7 | `cast2md.mcp.tools` |
| 6 | `cast2md.services.runpod_service` | | 7 | `cast2md.mcp.resources` |
| 5 | `cast2md.node.config` | | 7 | `cast2md.api.nodes` |
| 5 | `cast2md.feed.discovery` | | 7 | `cast2md.transcription.service` |

### Appendix C — module sizes, top 15

```
3036  src/cast2md/db/repository.py
1442  src/cast2md/services/runpod_service.py
1429  src/cast2md/api/queue.py
1079  src/cast2md/web/views.py
1009  src/cast2md/node/worker.py
1009  src/cast2md/cli.py
 983  src/cast2md/api/nodes.py
 980  src/cast2md/search/repository.py
 953  src/cast2md/mcp/tools.py
 902  src/cast2md/transcription/service.py
 864  src/cast2md/worker/manager.py
 518  src/cast2md/api/runpod.py
 487  src/cast2md/transcription/formats.py
 486  src/cast2md/api/episodes.py
 420  src/cast2md/api/settings.py
```

Distribution across all 72 files: total 23,674, median 205, p75 369, p90 980,
maximum 3036. 12 files above 500 lines.

Templates for comparison (not part of the Python measurement): 11 files, 5530
lines, largest `base.html` at 1176. Tests: 11 files, 1926 lines — test to
source ratio 1 : 12.3.

### Appendix D — functions and classes above 80 lines

54 units. Lines = `end_lineno - lineno + 1`.

```
1358  class RunPodService                        services/runpod_service.py:73
1020  class JobRepository                        db/repository.py:1162
 984  class EpisodeRepository                    db/repository.py:176
 962  class TranscriberNodeWorker                node/worker.py:48
 814  class WorkerManager                        worker/manager.py:39
 772  class TranscriptSearchRepository           search/repository.py:209
 631  class TranscriptionService                 transcription/service.py:137
 283  class RemoteTranscriptionCoordinator       distributed/coordinator.py:15
 275  class TranscriberNodeRepository            db/repository.py:2484
 248  def   hybrid_search                        search/repository.py:733
 231  def   admin_status_page                    web/views.py:514
 193  def   create_app                           node/server.py:37
 192  class PocketCastsClient                    clients/pocketcasts.py:49
 166  def   _process_transcript_download_job     worker/manager.py:462
 164  class PocketCastsProvider                  transcription/providers/pocketcasts.py:118
 157  def   search                               mcp/tools.py:115
 153  def   _get_configurable_settings           api/settings.py:21
 151  class FeedRepository                       db/repository.py:23
 135  def   admin_runpod_page                    web/views.py:846
 129  def   discover_new_episodes                feed/discovery.py:251
 126  class PodRunRepository                     db/repository.py:2761
 122  class ItunesClient                         clients/itunes.py:24
 121  def   _create_pod                          services/runpod_service.py:899
 118  def   feed_detail                          web/views.py:303
 118  def   get_transcript                       mcp/tools.py:605
 113  def   search                               search/repository.py:265
 111  class PodSetupStateRepository              db/repository.py:2926
 111  class WhisperModelRepository               db/repository.py:2265
 107  class Podcast20Provider                    transcription/providers/podcast20.py:23
 107  def   setup_pod                            services/pod_setup.py:66
 102  def   _transcribe_mlx_chunked              transcription/service.py:505
 100  def   reset_running_jobs                   db/repository.py:1625
  97  class Settings                             config/settings.py:17
  96  def   transcript_search_page               web/views.py:984
  95  def   _transcribe_faster_whisper_chunked   transcription/service.py:340
  95  def   _merge_word_level_segments           transcription/formats.py:35
  95  def   merge_word_level_segments            search/parser.py:95
  95  def   get_feed                             mcp/resources.py:63
  95  def   search_by_feed                       db/repository.py:789
  91  def   transcribe_episode                   transcription/service.py:812
  91  def   fetch                                transcription/providers/podcast20.py:39
  89  def   get_status                           mcp/resources.py:281
  89  def   cmd_backfill_embeddings              cli.py:645
  88  def   _discover_pocketcasts_transcripts    feed/discovery.py:80
  87  class ParsedTranscript                     export/formats.py:19
  86  def   search_episodes_fts                  db/repository.py:955
  86  def   cmd_reindex_transcripts              cli.py:530
  84  def   render_transcript_html               web/views.py:97
  84  def   cmd_restore                          cli.py:410
  83  def   fetch                                transcription/providers/pocketcasts.py:141
  83  def   _vector_search                       search/repository.py:649
  82  class RunPodModelRepository                db/repository.py:2400
  81  def   _create_and_setup_pod                services/runpod_service.py:762
  81  def   get_queue_status                     api/queue.py:147
```

### Appendix E — cyclomatic complexity from grade C

`uvx radon cc -n c -s src/`. Grade D starts at CC 21, grade C at CC 11. No
grade E or F.

**Grade D (5 units):**

```
D (28)  admin_status_page                         web/views.py:514
D (28)  TranscriptSearchRepository.hybrid_search  search/repository.py:733
D (27)  RunPodService._create_pod                 services/runpod_service.py:899
D (21)  search                                    mcp/tools.py:115
D (21)  node_heartbeat                            api/nodes.py:232
```

**Grade C (38 units):**

```
C (20)  extract_transcript_url               feed/parser.py:114
C (19)  _find_matching_feed                  mcp/tools.py:26
C (19)  get_feed                             mcp/resources.py:63
C (19)  _discover_pocketcasts_transcripts    feed/discovery.py:80
C (19)  batch_queue_by_range                 api/queue.py:831
C (17)  feed_detail                          web/views.py:303
C (17)  merge_word_level_segments            search/parser.py:95
C (17)  discover_new_episodes                feed/discovery.py:251
C (16)  render_transcript_html               web/views.py:97
C (16)  get_episode                          mcp/resources.py:161
C (16)  parse_feed                           feed/parser.py:243
C (15)  episode_detail                       web/views.py:424
C (15)  JobRepository.reset_running_jobs     db/repository.py:1625
C (14)  extract_categories                   feed/parser.py:207
C (14)  Episode                              db/models.py:124
C (14)  TranscriberNodeWorker._poll_loop     node/worker.py:396
C (14)  ParsedTranscript                     export/formats.py:19
C (14)  RunPodService._cleanup_unreachable_pods  services/runpod_service.py:318
C (13)  get_transcript                       mcp/tools.py:605
C (13)  build_flexible_tsquery               search/repository.py:78
C (13)  Episode.from_row                     db/models.py:154
C (13)  get_all_jobs                         api/queue.py:1003
C (13)  update_settings                      api/settings.py:238
C (13)  TranscriberNodeWorker._check_should_terminate  node/worker.py:321
C (13)  ParsedTranscript.from_markdown       export/formats.py:28
C (12)  Podcast20Provider.fetch              transcription/providers/podcast20.py:39
C (12)  RemoteTranscriptionCoordinator._check_nodes  distributed/coordinator.py:138
C (12)  export_feed_transcripts              api/feeds.py:297
C (12)  report_setup_progress                api/runpod.py:200
C (12)  WorkerManager._process_transcript_download_job  worker/manager.py:462
C (12)  RunPodService.can_create_pod         services/runpod_service.py:434
C (12)  RunPodService._fetch_gpus_from_api   services/runpod_service.py:601
C (11)  _apply_db_overrides                  config/settings.py:192
C (11)  admin_runpod_page                    web/views.py:846
```

### Appendix F — clones

`npx -y jscpd src/cast2md --min-lines 15 --min-tokens 70 --format python`:
16 exact clones, 333 lines = 1.41% across 53 files.

```
 73 L  search/parser.py:95-167           ==  transcription/formats.py:35-107
 23 L  worker/manager.py:503-525         ==  worker/manager.py:715-737
 22 L  transcription/service.py:366-387  ==  transcription/service.py:547-568
 21 L  api/nodes.py:434-454              ==  api/nodes.py:731-751
 21 L  api/nodes.py:485-505              ==  api/nodes.py:777-797
 20 L  search/repository.py:679-698      ==  search/repository.py:705-724
 19 L  worker/manager.py:345-363         ==  worker/manager.py:472-490
 18 L  api/nodes.py:490-507              ==  api/nodes.py:782-799
 18 L  search/repository.py:302-319      ==  search/repository.py:331-348
 17 L  api/nodes.py:485-501              ==  api/nodes.py:589-605
 17 L  worker/manager.py:345-361         ==  worker/manager.py:391-407
 16 L  api/nodes.py:382-397              ==  api/nodes.py:682-697
 16 L  api/nodes.py:554-569              ==  api/nodes.py:645-660
 16 L  api/queue.py:1064-1079            ==  api/queue.py:1118-1133
 16 L  search/repository.py:359-374      ==  search/repository.py:418-432
 16 L  worker/manager.py:349-364         ==  worker/manager.py:476-491
```

### Appendix G — change pressure

`git log --format= --name-only --since='18 months ago' | sort | uniq -c | sort -rn | head -30`.
The window covers the entire history (first commit `37d3130`, 2026-01-19; 471
commits).

```
65  src/cast2md/web/views.py                 20  src/cast2md/transcription/service.py
51  src/cast2md/db/repository.py             20  src/cast2md/templates/episode_detail.html
47  src/cast2md/templates/feed_detail.html   19  src/cast2md/templates/search.html
42  src/cast2md/templates/base.html          19  src/cast2md/templates/runpod.html
39  src/cast2md/config/settings.py           18  src/cast2md/api/settings.py
37  src/cast2md/services/runpod_service.py   18  deploy/afterburner/afterburner.py
34  CLAUDE.md                                17  src/cast2md/db/models.py
32  src/cast2md/templates/status.html        17  scripts/cast2md-node.sh
29  src/cast2md/worker/manager.py            16  src/cast2md/api/nodes.py
27  pyproject.toml                           16  .forgejo/workflows/ci.yml
24  src/cast2md/api/queue.py                 15  src/cast2md/templates/settings.html
23  README.md                                15  src/cast2md/db/schema.py
22  src/cast2md/db/migrations.py             14  src/cast2md/cli.py
21  src/cast2md/search/repository.py         13  src/cast2md/api/runpod.py
21  src/cast2md/node/worker.py
21  src/cast2md/main.py
```

### Appendix H — dead code candidates

Candidates, explicitly not findings. `vulture` evaluates static name references
only and knows neither Jinja2 templates nor dynamic resolution.

`uvx vulture src/ tests/ scripts/ tools/ deploy/ --min-confidence 100` — 3 hits,
all certain:

```
src/cast2md/cli.py:897             unused variable 'frame'
src/cast2md/db/repository.py:1886  unused variable 'include_stuck'
src/cast2md/main.py:109            unused variable 'frame'
```

At `--min-confidence 60`, including `tests/`, `scripts/`, `tools/` and
`deploy/`: 204 hits in `src/`. Of those, 116 fall on decorated functions
(FastAPI routes, Click commands, MCP tools) that `vulture` cannot recognise as
used by construction — 95 candidates remain after subtracting them.

One coherent block among the 95, given in full because it forms a pattern:

```
src/cast2md/db/config.py:41   unused method   'get_postgres_dsn'
src/cast2md/db/config.py:85   unused function 'reload_db_config'
src/cast2md/db/config.py:97   unused function 'get_placeholder'
src/cast2md/db/config.py:106  unused function 'get_placeholder_num'
src/cast2md/db/config.py:118  unused function 'get_current_timestamp_sql'
src/cast2md/db/config.py:127  unused function 'get_autoincrement_type'
src/cast2md/db/sql.py:6       unused function 'ph'
src/cast2md/db/sql.py:27      unused function 'now_sql'
src/cast2md/db/sql.py:36      unused function 'bool_val'
src/cast2md/db/sql.py:48      unused function 'returning_clause'
src/cast2md/db/sql.py:57      unused function 'upsert_sql'
src/cast2md/db/sql.py:121     unused function 'adapt_params'
```

`src/cast2md/db/config.py:96` carries the comment
`# SQL dialect helpers - PostgreSQL only`. From outside `db/`, exactly one name
is pulled out of `db/sql.py`: `execute`, at `search/repository.py:9`. Out of
`db/config.py`: `get_database_config` and `get_db_config`.

*Assumption:* the twelve names are the remainder of a dialect abstraction for a
second database backend that does not exist.

Further candidates, grouped by package (excerpt of the 95, reproducible in full
via the command above):

```
db/repository.py:260,440,577,1214,1546,1571,1937,1958,2225,2558,2859,3005
      get_by_guid, update_transcript_path, get_status_counts_for_feed,
      get_next_job, get_by_episode, mark_running, get_failed_jobs,
      retry_failed_job, set_many, get_online, mark_orphaned_as_ended, cleanup_old
search/repository.py:250,451,550   remove_episode, reindex_all, remove_episode_embeddings
search/embeddings.py:113,140       embedding_to_floats, get_model_name
db/migrations.py:182,197           column_exists, table_exists
feed/itunes.py:34                  is_itunes_url
download/downloader.py:109         download_file
notifications/ntfy.py:104          notify_download_complete
scheduler.py:201                   get_scheduler_status
mcp/server.py:52                   unused import 'resources', 'tools' (90%)
```

The two imports at `mcp/server.py:52` are reported at 90% but most likely serve
registration through decorator side effects.

### Appendix I — public surface per module

Number of *distinct* names imported from outside the module itself, and the
number of importing files. Only `from … import X` forms; `import cast2md.x`
without a name reference does not count.

| Module | distinct names | importing files |
|---|---:|---:|
| `cast2md.storage.filesystem` | 11 | 8 |
| `cast2md.db.repository` | 10 | 24 |
| `cast2md.db.models` | 8 | 22 |
| `cast2md.db.connection` | 7 | 25 |
| `cast2md.config.settings` | 6 | 23 |
| `cast2md.transcription.service` | 5 | 5 |
| `cast2md.search.embeddings` | 5 | 7 |
| `cast2md.notifications.ntfy` | 5 | 2 |
| `cast2md.node.config` | 5 | 5 |
| `cast2md.services.runpod_service` | 4 | 6 |
| `cast2md.mcp.server` | 4 | 5 |
| `cast2md.search.parser` | 4 | 4 |
| `cast2md.transcription.preprocessing` | 4 | 1 |
| `cast2md.clients.pocketcasts` | 3 | 3 |
| `cast2md.transcription.providers.base` | 3 | 3 |
| `cast2md.feed.discovery` | 2 | 5 |
| `cast2md.search.repository` | 2 | 12 |
| `cast2md.clients.itunes` | 2 | 3 |
| `cast2md.distributed.coordinator` | 2 | 3 |

The names in detail for the four most-pulled modules:

```
db.repository      EpisodeRepository, FeedRepository, JobRepository,
                   PodRunRepository, PodSetupStateRepository, PodSetupStateRow,
                   RunPodModelRepository, SettingsRepository,
                   TranscriberNodeRepository, WhisperModelRepository
db.models          Episode, EpisodeStatus, Feed, Job, JobStatus, JobType,
                   NodeStatus, TranscriberNode
config.settings    NODE_SPECIFIC_SETTINGS, RUNPOD_TRANSCRIPTION_MODELS,
                   Settings, get_setting_source, get_settings, reload_settings
db.sql             execute
```

The maximum is 11 distinct names (`storage.filesystem`). No module reaches a
count at which one could speak of "no interface" — for that the name count
would have to be on the order of the module size. `db/repository.py` serves its
24 importers through exactly 10 class names at 3036 lines, which is a narrow
surface over a large body.

### A.1 — source of `imports.py` (core)

```python
def resolve(node, path):
    out = []
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.startswith("cast2md"):
                out.append((a.name, None))
    elif isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        if node.level:                       # resolve relative imports
            parts = list(path.relative_to(ROOT).parts[:-1])
            up = node.level - 1
            base = parts[: len(parts) - up] if up else parts
            mod = ".".join(["cast2md", *base] + ([mod] if mod else []))
        if mod.startswith("cast2md"):
            for a in node.names:
                out.append((mod, a.name))
    return out
```

Package mapping: `cast2md.X.…` → package `X` when `src/cast2md/X` is a
directory, otherwise `<root>/X.py`. Edges inside the same package are dropped
at package level and counted at file level.
