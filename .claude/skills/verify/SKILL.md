---
name: verify
description: Verify a change to cast2md before committing — test suite, linter, package build, document contract, and where applicable a live check against the running instance. Triggers — "verify", "prüf das", "läuft das durch", "kann ich das committen", "run the checks", "before committing". Also reach for it after editing CI, the repo documents, or anything under src/cast2md/. Not for a docs-only typo fix.
---

# Verify a cast2md change

Run the checks that match what the change touched. Report what passed, what
failed, and what was **not** run — a check that was skipped is not a check that
passed.

## Always

```bash
tools/check-docs.sh
```

The script takes no arguments and exits 2 when given one. It checks the
document contract: struck-through roadmap rows, closed items left on the
roadmap, unknown status tokens, non-ISO dates, decimal commas in figures. A
finding is fixed in the document. The language rule in `STANDARDS.md` has no
mechanical check.

## When the change touched `src/`, `tests/`, or `pyproject.toml`

```bash
uv sync --extra dev              # no .venv in a fresh checkout
docker compose up -d postgres    # 68 of 117 tests need a database
DATABASE_URL="postgresql://cast2md:dev@localhost:5432/cast2md" .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
uv build
```

All four must pass — CI runs the same set in the `test` job, so a failure here
is a failure there. The expected result is **117 passed**, `All checks passed!`
and `85 files already formatted`.

**Without Postgres, 68 tests error at fixture setup** with
`ValueError: DATABASE_URL environment variable is required` and 49 pass. That is
not a passing run. If you cannot start Postgres, report the numbers rather than
the word "passed".

**`ruff format --check` is part of the gate, not optional.** The formatter owns
line length here and E501 is in `ignore` — see `DECISIONS.md`, 2026-08-06. Run
`ruff format src tests` to fix, never re-enable E501 to work around it.

**On a Mac, `docker` may not be on `PATH`** even with Docker Desktop installed:
the binary lives in `/Applications/Docker.app/Contents/Resources/bin/`.

**A missing interpreter is not a test result.** If `.venv/bin/python` does not
exist, run `uv sync --extra dev` first; if that also fails, say the suite was
not exercised.

**A green local run is not a green pipeline.** CI runs `uv build` in the
`build` job and `ruff check`, `ruff format --check` and `pytest` in the `test`
job, on every push and pull request. `tools/check-docs.sh` runs in no CI job.
Say which of the two was observed when reporting a pass.

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

`build-deploy` calls `build-push-deploy.yml` in `meltforce.net/ci-workflows`, a
second repository, at the tag pinned in `ci.yml`. The current tag is recorded in
`homelab/STANDARDS.md` § *CI / Forgejo Actions*. A change that depends on a
change there is not verified until both are in place.

## When the change touched the production stack

```bash
curl -s https://cast2md.coydog-fence.ts.net/api/health
```

Test through the URL, never `ssh <host> "curl localhost:8000/..."` — the second
exercises a different path than users take and hides reverse-proxy faults.

The `build` field reads `edge-<sha>` and names the commit actually serving.
`git log --oneline -1` on `main` should match it; if it does not, `main` has
moved ahead of production. `deploy-gate` checks the same thing in CI, so a
mismatch here means either a run is still going or one failed.

**Never rename the `build` field or change its shape** without updating the
`sed` expression in `deploy-gate` and `tests/test_health.py`. The test exists to
make that coupling fail locally rather than at deploy time.

## Before a release

`version` in `pyproject.toml` must equal the tag about to be pushed. PyPI
rejects duplicate versions, and the failure lands after the image has already
been pushed to ghcr.io.

The `release` job then waits up to 5 minutes for the tag to reach GitHub through
the push-mirror. If the mirror is stalled, the release fails after ghcr.io and
PyPI have published.
