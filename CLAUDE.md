# cast2md — podcast transcription service

Downloads podcast episodes via RSS and produces transcripts. Publisher and
Pocket Casts transcripts are fetched first; audio is transcribed only when
neither exists. A server with remote transcription nodes and optional RunPod
GPU pods. Subsystem notes load on demand from the nested `CLAUDE.md` files
under `src/cast2md/` and the `runpod-afterburner` skill.

## Gotchas

- **Three compose files.** `docker-compose.yml` is the dev stack (builds from
  the checkout, publishes Postgres on 5432). `compose.example.yml` is the
  production template. The file production runs is `compose.yaml` under
  `/opt/docker/stacks/cast2md/` on the server and is not in this repo; the
  server has no git checkout.
- **Never test against production.** Restarts disrupt workers, nodes and job
  state. Dev runs from the checkout on the Macs: `uv sync --extra dev`,
  `docker compose up -d postgres`, `.venv/bin/python -m cast2md serve`.
  Dev `DATABASE_URL` points at `localhost:5432`, production at `postgres:5432`.
- **Test the API through its URL**, not through `ssh … curl localhost`, which
  bypasses the reverse proxy.
- **`trash/` does not restore.** Deleting a feed moves files there and deletes
  the DB records; entries older than 30 days are removed on startup.
- **iTunes resolution runs before validation** in `api/feeds.py:create_feed()`.
  Reordering breaks Apple Podcasts URLs and no RSS-URL test fails.
- `DELETE /api/episodes/{id}/audio` refuses on an episode without a transcript.
- Re-transcription has no UI; `/api/queue/.../retranscribe` is the path.
- **Forgejo is canonical; GitHub is a push-mirror** (`git push --mirror`,
  force and prune). Nothing may originate on GitHub. The `docs` job pushes
  `gh-pages` to Forgejo and the mirror carries it to GitHub Pages; it writes
  `site/CNAME` at build time, and `docs/internal/` is out of the nav but public.
- **Release:** bump `version` in `pyproject.toml` to the tag first (PyPI
  rejects duplicates), then `git tag <version> && git push origin main
  <version>`. The `release` job waits 5 minutes for the tag on GitHub and fails
  after ghcr.io and PyPI are already published when the mirror stalls.
- **`build-deploy` has no `github.event_name` condition on purpose**: on a
  `uses:` job it is evaluated in the called workflow, where the event is
  `workflow_call`. The shared workflow lives in `meltforce.net/ci-workflows`.
- Production tracks `:edge`; CI redeploys. Hosting rules are in the homelab
  repo's `STANDARDS.md`.
- Run the `verify` skill before committing a change to code or CI.

## Git

`main` is the only long-lived branch: commit and push to `main` yourself once a
change is complete and verified, no PR. This overrides the harness default of
branching first. Stage paths explicitly, never `git add -A` — parallel sessions
share this checkout.

## Documents

Do not write documentation that restates the code. Reasoning goes into the
commit message, or into a comment next to the code when a later reader would
otherwise undo the change. Everything written into the repo is English.
