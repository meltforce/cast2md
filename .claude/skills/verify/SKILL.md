---
name: verify
description: Verify a change to cast2md before committing — test suite, linter, package build, document contract, language sweep, and where applicable a live check against the running instance. Triggers — "verify", "prüf das", "läuft das durch", "kann ich das committen", "run the checks", "before committing". Also reach for it after editing CI, the repo documents, or anything under src/cast2md/. Not for a docs-only typo fix.
---

# Verify a cast2md change

Run the checks that match what the change touched. Report what passed, what
failed, and what was **not** run — a check that was skipped is not a check that
passed.

## Always

```bash
tools/check-docs.sh --all
```

Two passes: the document contract (struck-through roadmap rows, closed items
left on the roadmap, unknown status tokens, non-ISO dates, decimal commas in
figures) and a repo-wide German-language sweep.

The sweep reports a file and line. When a hit is a legitimate verbatim quote or
German language *data* rather than prose, add the specific line to
`tools/check-docs.allow` in the form `<path>%%<substring>`. Do not weaken the
pattern in `check-docs.sh` — that stops the guard catching the case it exists
for.

Known-allowed today: the German stopword list in `src/cast2md/search/repository.py`
and the German example query in `src/cast2md/mcp/tools.py`.

## When the change touched `src/`, `tests/`, or `pyproject.toml`

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
uv build
```

`pytest` reads `testpaths = ["tests"]` from `pyproject.toml`. `ruff` is
configured for line length 100 and rule sets `E,F,I,N,W,UP`.

**Only the dev machine (`jesus`) carries a `.venv` in the checkout.** On any
other machine these commands fail with "no such file or directory" and that is
not a test result. Either run them on `jesus`, or state in the report that the
suite was not exercised — never let a missing interpreter read as a pass.

**None of these run in CI.** The `build` job runs `uv build` only, so `pytest`
and `ruff` are local-only controls until the open roadmap item lands. Say so
when reporting a pass — a green local run is not a green pipeline.

## When the change touched the transcription pipeline or workers

Start the dev stack and exercise the path end to end. Do not test against
production; repeated restarts disrupt workers, nodes and job state.

```bash
docker compose up -d postgres
.venv/bin/python -m cast2md serve --host 0.0.0.0 --port 8000
```

Then, from another shell:

```bash
curl -s http://localhost:8000/api/health
```

Add a feed with a known Podcasting 2.0 transcript and confirm the episode
reaches `completed` without an audio download, then one without an external
transcript and confirm it drops through to `awaiting_transcript` or
`needs_audio`. The status transitions are in
`src/cast2md/templates/CLAUDE.md`.

## When the change touched `.forgejo/workflows/ci.yml`

The condition on a `uses:` job is evaluated in the **called** workflow, where
`github.event_name` is `workflow_call`. A `github.event_name == 'push'`
comparison there is never true, the job is skipped, and the caller still reports
success. Check any `if:` you added or moved against that, and verify the run in
Forgejo actually executed the job rather than trusting the green mark.

`build-deploy` calls `meltforce.net/ci-workflows/...@v1`, a second repository. A
change that depends on a change there is not verified until both are in place.

## When the change touched the production stack

```bash
curl -s https://cast2md.coydog-fence.ts.net/api/health
```

Test through the URL, never `ssh <host> "curl localhost:8000/..."` — the second
exercises a different path than users take and hides reverse-proxy faults.

## Before a release

`version` in `pyproject.toml` must equal the tag about to be pushed. PyPI
rejects duplicate versions, and the failure lands after the image has already
been pushed to ghcr.io.

The `release` job then waits up to 5 minutes for the tag to reach GitHub through
the push-mirror. If the mirror is stalled, the release fails after ghcr.io and
PyPI have published.
