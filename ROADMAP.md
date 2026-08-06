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

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
