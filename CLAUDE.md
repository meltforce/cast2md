# cast2md - Project Knowledge

## Where the rest is documented

This file holds what applies everywhere. Subsystem knowledge loads on demand:

- `src/cast2md/transcription/CLAUDE.md` - transcript sources, provider priority, adding providers
- `src/cast2md/templates/CLAUDE.md` - web UI workflow, episode status buttons, status polling, tooltip convention
- `src/cast2md/search/CLAUDE.md` - hybrid search, embeddings, segment merging, pgvector
- `.claude/skills/runpod-afterburner/SKILL.md` - RunPod GPU workers, Tailscale userspace networking, auto-termination, GPU compatibility

## Repository and CI

Forgejo is canonical: `origin` is `https://git.coydog-fence.ts.net/meltforce.net/cast2md.git`.
GitHub (`github.com/meltforce/cast2md`) receives commits and tags through a
Forgejo push-mirror and serves as the public distribution channel only.

- CI is `.forgejo/workflows/ci.yml` (Forgejo Actions). There is no `.github/workflows/`.
- The push-mirror runs `git push --mirror`, which prunes every ref not present on
  Forgejo. A branch created only on GitHub is deleted on the next sync -- this is
  why the `docs` job pushes `gh-pages` to Forgejo rather than to GitHub.
- `build-deploy` calls the shared workflow
  `meltforce.net/ci-workflows/.forgejo/workflows/build-push-deploy.yml@v1`,
  a second Forgejo repository that has to be kept in step with this one.

### GitHub references that must stay

Some code fetches from GitHub on purpose. Do not rewrite these to Forgejo.

| Location | Why GitHub |
|---|---|
| `services/pod_setup.py:100`, `deploy/afterburner/afterburner.py:706` | RunPod pods run Tailscale in userspace mode. Their outbound HTTP proxy has no CONNECT tunneling, and `git.coydog-fence.ts.net` answers on HTTPS only (port 80 refused), so `pip install git+https://git.coydog-fence.ts.net/...` cannot work from a pod. |
| `docs/installation/*.md`, `README.md` | The docs are published at cast2md.meltforce.org. Readers outside the tailnet cannot resolve the Forgejo host. |
| `pyproject.toml` (`Repository`, `Issues`), `mkdocs.yml` (`repo_url`), `Dockerfile` (`image.source`) | Public-facing metadata for PyPI, the docs site, and image provenance. |

This works because the push-mirror keeps GitHub current, including
`raw.githubusercontent.com/meltforce/cast2md/main/scripts/cast2md-node.sh`,
which `docs/installation/node.md` pipes into bash.

Internal installs use Forgejo: `deploy/install.sh` defaults to the Forgejo clone
URL and takes `CAST2MD_REPO` to override it for hosts outside the tailnet.

| Job | Trigger | Output |
|---|---|---|
| `build` | every push and PR | `uv build` validation, no artifact |
| `build-deploy` | push to `main` | `:edge` image in the Forgejo registry, deploy to `cast2md.coydog-fence.ts.net` |
| `build-afterburner` | `deploy/afterburner/Dockerfile` changed on `main`, or `workflow_dispatch` | `docker.io/meltforce/cast2md-afterburner` |
| `release` | tag matching `[0-9]+.[0-9]+*` | `ghcr.io/meltforce/cast2md`, PyPI, GitHub release |
| `docs` | push to `main` or `workflow_dispatch` | `gh-pages` branch on Forgejo |

## Deployment

The production server runs on `cast2md` (Tailscale hostname) via Docker Compose. The server has no git repo.

- Compose file and `.env`: `/opt/docker/stacks/cast2md/` (`compose.yaml`, not `docker-compose.yml`)
- Data bind-mount: `/opt/cast2md/data/` -- this path holds data only, no configuration
- App image: `git.coydog-fence.ts.net/meltforce.net/cast2md:edge`, overridable via `APP_IMAGE`

**Production tracks `:edge`, not a release tag.** The `build-deploy` job rebuilds
`:edge` and redeploys on every push to `main`, so `main` reaches production
without a tag. Tagged releases publish to ghcr.io and PyPI for external users
and do not change what this server runs.

Manual redeploy (CI normally does this):
```bash
ssh root@cast2md "cd /opt/docker/stacks/cast2md && docker compose pull cast2md && docker compose up -d cast2md"
```

**Docker Hub** now receives only `meltforce/cast2md-afterburner`, built when
`deploy/afterburner/Dockerfile` changes on `main`. The application image moved to
`ghcr.io/meltforce/cast2md` for releases and to the Forgejo registry for `:edge`.
Never push dev builds to a public registry -- other users could pull an undefined state.

**Releasing a new version:**
1. Bump `version` in `pyproject.toml` to match the new tag (e.g., `"2026.08.1"`)
2. Commit the version bump
3. `git tag 2026.08.1 && git push origin main 2026.08.1`
4. The `release` job pushes `ghcr.io/meltforce/cast2md:<tag>` and `:latest`, publishes to PyPI, and creates the GitHub release

**Important:** The `pyproject.toml` version must match the git tag. PyPI rejects duplicate versions, so forgetting to bump it will fail the release.

**Important:** The `release` job depends on the push-mirror. Its "Wait for tag to
propagate to GitHub" step polls `api.github.com` for 5 minutes and exits 1 if the
tag has not arrived, because the GitHub release must reference an existing tag.
A stalled mirror therefore fails the release after ghcr.io and PyPI have already
been published. Requires `secrets.GH_PAT`.

**Important:** Always test on the dev machine first. Never use the production server for testing -- repeated restarts disrupt workers, nodes, and job state.

## Architecture

- **Production**: Runs entirely via Docker Compose (app + PostgreSQL)
- **Node workers**: Remote transcription nodes connect to the server
- **Local workers**: Download workers and one local transcription worker run in the app container
- **Database**: PostgreSQL with pgvector, runs in Docker (`docker compose up -d`)

### Production Stack

Both PostgreSQL and the cast2md app run as Docker containers:

```bash
# Start/restart the full stack
ssh root@cast2md "cd /opt/docker/stacks/cast2md && docker compose up -d"

# View logs
ssh root@cast2md "cd /opt/docker/stacks/cast2md && docker compose logs -f cast2md"

# Check status
ssh root@cast2md "docker compose -f /opt/docker/stacks/cast2md/compose.yaml ps"
```

Configuration is in `/opt/docker/stacks/cast2md/.env` (not checked into git). The Docker Compose file reads env vars from `.env` and passes them to the containers. `DB_IMAGE` and `APP_IMAGE` have defaults in `compose.yaml` and are only set in `.env` to pin a specific image.

**Important:** The `.env` file contains secrets (RUNPOD_API_KEY, database credentials). Never commit it. The running image is `git.coydog-fence.ts.net/meltforce.net/cast2md:edge`, built by the `build-deploy` job.

## Development (Dev Machine)

The dev machine (`jesus`) runs a test instance from the git checkout for fast iteration on migrations, API changes, UI, and worker logic.

- Status UI: https://<your-tailnet>/status
- API docs: https://<your-tailnet>/docs

### Setup

PostgreSQL runs via Docker Compose (same as production):
```bash
cd ~/projects/cast2md
docker compose up -d postgres
```

The app runs from the local git checkout in a virtualenv:
```bash
.venv/bin/python -m cast2md serve --host 0.0.0.0 --port 8000
```

### Configuration

Dev config is in `.env` (local, not committed):
```
DATABASE_URL=postgresql://cast2md:dev@localhost:5432/cast2md
STORAGE_PATH=./data/podcasts
TEMP_DOWNLOAD_PATH=./data/temp
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

Key difference from production: `DATABASE_URL` points to `localhost:5432` (Docker postgres exposes the port to the host), while in Docker Compose production, the app uses `postgres:5432` (Docker internal DNS).

### Workflow

1. Make code changes
2. Start dev server: `.venv/bin/python -m cast2md serve --host 0.0.0.0 --port 8000`
3. Test at `http://localhost:8000`
4. Stop with Ctrl+C when done

No systemd service -- run on demand. Reinstall after dependency changes:
```bash
.venv/bin/python -m pip install -e .
```

## Documentation

Public docs are at [cast2md.meltforce.org](https://cast2md.meltforce.org), built with **Zensical** (MkDocs Material successor). This matches `site_url` in `mkdocs.yml`; the older `meltforce.org/cast2md` redirects there.

- Source: `docs/` directory + `mkdocs.yml`
- CI: the `docs` job in `.forgejo/workflows/ci.yml`, on every push to `main` (no path filter) or `workflow_dispatch`
- Publishing: the job builds `site/`, then force-pushes it as branch `gh-pages` to
  **Forgejo**; the push-mirror carries that branch to GitHub, where Pages serves it
  from the branch. Pushing `gh-pages` straight to GitHub would break on the next
  mirror sync (see "Repository and CI").
- Requires `secrets.FJ_PUSH_TOKEN`. The push step runs without `set -x` because the
  push URL embeds the token.
- Local preview: `pip install zensical && zensical serve`

Two things the docs tree does not show on its own:
- There is no `docs/CNAME` in the repo -- the `docs` job writes `cast2md.meltforce.org`
  into `site/CNAME` at build time, alongside `site/.nojekyll`
- `docs/internal/` is kept out of the nav but stays publicly reachable by URL

## Testing

### API Testing

Always test the server API directly via the URL, not by SSH + localhost:
```bash
# Good - direct API call
curl https://<your-tailnet>/api/health

# Bad - unnecessary SSH
ssh root@<server> "curl localhost:8000/api/health"
```

## Transcription

### Backends

The system supports two transcription backends:

| Backend | Use Case | Languages | Speed |
|---------|----------|-----------|-------|
| **Whisper** | Local/server transcription | 99+ languages | Varies by model |
| **Parakeet** | RunPod GPU pods (default) | 25 EU languages | Very fast |

The backend is controlled by `TRANSCRIPTION_BACKEND` environment variable (`whisper` or `parakeet`).

### Model Tracking

Episodes track which model was used via `transcript_model` column (e.g., `parakeet-tdt-0.6b-v3`, `large-v3-turbo`). This is visible on the episode detail page.

### Re-transcription

Re-transcription is script-only -- the UI for it was removed, but the endpoints
under `/api/queue/.../retranscribe` remain and are the supported path.

## iTunes URL Support

Feeds can be added via Apple Podcasts URLs. The system automatically resolves them to RSS feed URLs.

1. `feed/itunes.py:resolve_feed_url()` detects Apple Podcasts URLs
2. Extracts iTunes ID from URL pattern `podcasts.apple.com/.*/id(\d+)`
3. Calls iTunes Lookup API to get RSS feed URL
4. Stores `itunes_id` on the feed for reference

`api/feeds.py:create_feed()` calls `resolve_feed_url()` before validation, so an
Apple URL never reaches the feed parser.

## Audio Management

Episodes with external transcripts don't need audio files. The audio can be deleted to save space:

- `DELETE /api/episodes/{id}/audio` - Deletes audio file, keeps `audio_url` for re-download
- Only allowed if episode has a transcript
- Episode detail page shows "Delete Audio" / "Download Audio" buttons accordingly

## Feed Deletion and Trash

When a feed is deleted, files are moved to trash instead of being permanently deleted:

### How It Works

1. User clicks "Delete Feed" on feed detail page
2. Confirmation dialog requires typing "delete"
3. `DELETE /api/feeds/{id}` moves files to trash, then deletes DB records
4. Server auto-cleans trash entries older than 30 days on startup

### Trash Structure

```
{storage_path}/trash/{feed_slug}_{feed_id}_{timestamp}/
├── audio/
│   └── {feed_id}/
│       └── *.mp3
└── transcripts/
    └── {feed_id}/
        └── *.json
```

### Limitations

- DB records are deleted immediately (no restore from trash)
- Only files are preserved in trash
- Manual restore requires re-adding feed and copying files back
