# cast2md — podcast transcription service

Downloads podcast episodes via RSS and produces transcripts. Publisher-provided
transcripts (Podcasting 2.0) and Pocket Casts transcripts are fetched first;
audio is downloaded and transcribed locally only when neither exists. Runs as a
server with remote transcription nodes and optional RunPod GPU pods.

Subsystem knowledge loads on demand:

- `src/cast2md/transcription/CLAUDE.md` — transcript sources, provider priority, adding providers
- `src/cast2md/templates/CLAUDE.md` — web UI workflow, episode status buttons, status polling, tooltip convention
- `src/cast2md/search/CLAUDE.md` — hybrid search, embeddings, segment merging, pgvector
- `.claude/skills/runpod-afterburner/SKILL.md` — RunPod GPU workers, Tailscale userspace networking, auto-termination, GPU compatibility

## Gotchas

**Three compose files, three purposes.** `docker-compose.yml` is the dev stack:
`build: .`, `POSTGRES_PASSWORD: dev` hardcoded, port 5432 published to the host.
`compose.example.yml` is the production template: `ghcr.io` image,
`${POSTGRES_PASSWORD:?Required}`, Postgres on an internal network only.
`compose.yaml` is the file that actually runs in production, on the server under
`/opt/docker/stacks/cast2md/`, and is not in this repo. A `docker compose up -d`
in the checkout starts the dev stack — never treat its output as a production
rehearsal.

**Never test against the production server.** Repeated restarts disrupt workers,
nodes and job state. The dev machine (`jesus`) runs a test instance from the
checkout for exactly this.

**Test the API through its URL, not over SSH.** `curl https://<host>/api/health`,
not `ssh root@<host> "curl localhost:8000/api/health"` — the second exercises a
different path than the one users take and hides reverse-proxy faults.

**`trash/` does not restore.** Deleting a feed moves audio and transcripts to
`{storage_path}/trash/{feed_slug}_{feed_id}_{timestamp}/` and deletes the DB
records immediately. Only files survive, and entries older than 30 days are
removed on startup. Restoring means re-adding the feed and copying files back by
hand.

**iTunes resolution runs before validation.** `api/feeds.py:create_feed()` calls
`feed/itunes.py:resolve_feed_url()` first, so an Apple Podcasts URL never reaches
the feed parser. Reordering those two breaks Apple URLs without failing any test
that uses an RSS URL.

**Audio deletion needs a transcript.** `DELETE /api/episodes/{id}/audio` keeps
`audio_url` for re-download and refuses on episodes that have no transcript.

**`pyproject.toml`'s version must match the git tag before tagging.** PyPI
rejects duplicate versions, so a forgotten bump fails the release after the
image has already been pushed.

**The release depends on the push-mirror.** The `release` job polls
`api.github.com` for 5 minutes for the tag and exits 1 if it has not arrived,
because the GitHub release must reference an existing tag. A stalled mirror
fails the release *after* ghcr.io and PyPI have been published. Requires
`secrets.GH_PAT`.

## Repository and CI

Forgejo is canonical: `origin` is
`https://git.coydog-fence.ts.net/meltforce.net/cast2md.git`. GitHub receives
commits and tags through a push-mirror and is a distribution channel only. CI is
`.forgejo/workflows/ci.yml`; there is no `.github/workflows/`.

Why it is arranged this way, which GitHub references must not be rewritten to
Forgejo, and what the `git push --mirror` pruning implies for branches: see
[`DECISIONS.md`](DECISIONS.md), 2026-05-11.

`build-deploy` calls the shared workflow
`meltforce.net/ci-workflows/.forgejo/workflows/build-push-deploy.yml@v1`, a
second Forgejo repository that has to be kept in step with this one.

| Job | Trigger | Output |
|---|---|---|
| `build` | every push and PR | `uv build` validation, no artifact |
| `build-deploy` | push to `main` | `:edge` image in the Forgejo registry, deploy to `cast2md.coydog-fence.ts.net` |
| `build-afterburner` | `deploy/afterburner/Dockerfile` changed on `main`, or `workflow_dispatch` | `docker.io/meltforce/cast2md-afterburner` |
| `release` | tag matching `[0-9]+.[0-9]+*` | `ghcr.io/meltforce/cast2md`, PyPI, GitHub release |
| `docs` | push to `main` or `workflow_dispatch` | `gh-pages` branch on Forgejo |

The `if:` condition on `build-deploy` deliberately omits a `github.event_name`
comparison. On a `uses:` job the condition is evaluated in the *called* workflow,
where the event is `workflow_call` — see the pointer in
[`INCIDENTS.md`](INCIDENTS.md).

## Deployment

Production runs on `cast2md` (Tailscale hostname) via Docker Compose. The server
has no git checkout.

- Compose file and `.env`: `/opt/docker/stacks/cast2md/` (`compose.yaml`)
- Data bind-mount: `/opt/cast2md/data/` — data only, no configuration
- App image: `git.coydog-fence.ts.net/meltforce.net/cast2md:edge`, overridable via `APP_IMAGE`
- `.env` holds secrets (`RUNPOD_API_KEY`, database credentials) and is not in git

Production tracks `:edge`, not a release tag — every push to `main` rebuilds and
redeploys. Reasoning and its consequence: [`DECISIONS.md`](DECISIONS.md),
2026-05-11.

```bash
# manual redeploy (CI normally does this)
ssh root@cast2md "cd /opt/docker/stacks/cast2md && docker compose pull cast2md && docker compose up -d cast2md"

# logs, status
ssh root@cast2md "cd /opt/docker/stacks/cast2md && docker compose logs -f cast2md"
ssh root@cast2md "docker compose -f /opt/docker/stacks/cast2md/compose.yaml ps"
```

Releasing: bump `version` in `pyproject.toml` to the new tag, commit, then
`git tag <version> && git push origin main <version>`.

## Development

The dev machine runs Postgres from `docker-compose.yml` and the app from the
checkout:

```bash
docker compose up -d postgres
.venv/bin/python -m cast2md serve --host 0.0.0.0 --port 8000
```

Dev `.env` differs from production in one thing that matters: `DATABASE_URL`
points at `localhost:5432`, because the dev Postgres publishes its port to the
host, while in production the app reaches `postgres:5432` over Docker's internal
DNS. The remaining keys are in `.env.example`.

After a dependency change: `.venv/bin/python -m pip install -e .`

## Transcription

| Backend | Use case | Languages | Speed |
|---|---|---|---|
| Whisper | local and server transcription | 99+ | varies by model |
| Parakeet | RunPod GPU pods (default there) | 25 EU languages | very fast |

Selected by `TRANSCRIPTION_BACKEND` (`whisper` or `parakeet`). Episodes record
which model produced them in `transcript_model` (e.g. `parakeet-tdt-0.6b-v3`,
`large-v3-turbo`), shown on the episode detail page.

Re-transcription is script-only — the UI was removed, the endpoints under
`/api/queue/.../retranscribe` remain and are the supported path.

## Documentation

Public docs are at [cast2md.meltforce.org](https://cast2md.meltforce.org), built
with **Zensical** (MkDocs Material successor), matching `site_url` in
`mkdocs.yml`. The older `meltforce.org/cast2md` redirects there.

- Source: `docs/` plus `mkdocs.yml`; local preview `pip install zensical && zensical serve`
- CI: the `docs` job, on every push to `main` (no path filter) or `workflow_dispatch`
- The job builds `site/`, then force-pushes it as branch `gh-pages` to **Forgejo**.
  The mirror carries it to GitHub, where Pages serves it. Pushing `gh-pages`
  straight to GitHub is removed by the next mirror sync.
- Requires `secrets.FJ_PUSH_TOKEN`. The push step runs without `set -x` because
  the push URL embeds the token.

Two things the docs tree does not show on its own: there is no `docs/CNAME` —
the job writes `cast2md.meltforce.org` into `site/CNAME` at build time alongside
`site/.nojekyll`; and `docs/internal/` is kept out of the nav but stays publicly
reachable by URL.

## Standards

The policy layer is [`STANDARDS.md`](STANDARDS.md): the document contract and
movement rule, the language rule, the git workflow, skill placement, and the
settings conventions. It is a separate file because policy and mechanics answer
different questions and change at different rates.

The three documents it governs:

| File | Holds |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | Open work only. Status token `[open]`. |
| [`DECISIONS.md`](DECISIONS.md) | Decisions taken, with their reasoning. |
| [`INCIDENTS.md`](INCIDENTS.md) | Application postmortems. CI, deploy and host failures go to `homelab/INCIDENTS.md`. |

## Verification

Run the `verify` skill (`.claude/skills/verify/`) before committing anything
that touches code, CI or the repo documents. The instructions are there rather
than here because they are needed rarely and are long.
