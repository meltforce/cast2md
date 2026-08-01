# Mobile Deploy with Claude

Deploy code changes to the production server from your phone using Claude.

## How it works

1. You make code changes with Claude (phone, tablet, anywhere)
2. Claude commits and pushes to `main`
3. Forgejo Actions (on bob-01) builds a Docker image from the current code
4. The workflow SSHs into the server via Tailscale and deploys it

The workflow pushes the image as `git.coydog-fence.ts.net/meltforce.net/cast2md:edge` on the in-tailnet Forgejo registry. cast2md is deployed via the homelab `docker-stacks` catalog (`configuration/docker-stacks/stacks/cast2md.yml`), so the production compose lives at `/opt/docker/stacks/cast2md/` and pins `image: git.coydog-fence.ts.net/meltforce.net/cast2md:edge`. A plain `docker compose pull && docker compose up -d` on the cast2md host picks up the new edge build over anonymous OCI (repo is public, no `docker login` needed). Tagged releases push to `ghcr.io/meltforce/cast2md:<version>` (public release stream — that's what `compose.example.yml` in the repo points at for fresh installs).

## Steps

1. Open Claude on your phone
2. Describe the code change you want
3. Ask Claude to commit and push
4. The deploy workflow triggers automatically on push to `main`
5. Check progress: Forgejo UI → repo → Actions tab

## Manual trigger

If the push didn't trigger a deploy (e.g., you only want to redeploy):

1. Forgejo UI → repo → Actions → ci → Run workflow
2. Or ask Claude: `run the deploy workflow manually`

## Rolling back

To go back to the last tagged release:

```bash
ssh root@cast2md.coydog-fence.ts.net "cd /opt/cast2md && \
    docker pull ghcr.io/meltforce/cast2md:latest && \
    sed -i 's|image:.*cast2md.*|image: ghcr.io/meltforce/cast2md:latest|' docker-compose.yml && \
    docker compose up -d cast2md"
```

Or from Claude: ask to run this command.

Rolling back swaps the production `image:` line in `docker-compose.yml` from the Forgejo edge tag to the ghcr.io release tag, then `compose up -d`. Forward again: swap the image line back to `git.coydog-fence.ts.net/meltforce.net/cast2md:edge` and pull.

## What's deployed?

```bash
ssh root@cast2md.coydog-fence.ts.net "docker inspect git.coydog-fence.ts.net/meltforce.net/cast2md:edge --format '{{index .Config.Labels \"org.opencontainers.image.version\"}}'"
```

- `edge-<sha>` = deployed from main (the SHA tells you which commit)
- `2026.01.1` (or similar) = tagged release

## Workflow file

`.forgejo/workflows/ci.yml` — `docker-edge` job builds the image and pushes to the Forgejo registry, `deploy-edge` job runs on a `host`-labeled runner (already in the tailnet) and SSHes to the production server.
