# Mobile Deploy with Claude

Deploy code changes to the production server from your phone using Claude.

## How it works

1. You make code changes with Claude (phone, tablet, anywhere)
2. Claude commits and pushes to `main`
3. GitHub Actions builds a Docker image from the current code
4. The workflow SSHs into the server via Tailscale and deploys it

The workflow pushes the image as `meltforce/cast2md:edge` on Docker Hub, then retags it as `latest` on the server. This keeps Docker Hub's `latest` tag clean for tagged releases while the server runs whatever you last deployed.

## Steps

1. Open Claude on your phone
2. Describe the code change you want
3. Ask Claude to commit and push
4. The deploy workflow triggers automatically on push to `main`
5. Check progress: **GitHub Actions > Deploy** (or ask Claude to check with `gh run list`)

## Manual trigger

If the push didn't trigger a deploy (e.g., you only want to redeploy):

1. Go to GitHub > Actions > Deploy > Run workflow
2. Or ask Claude: `run the deploy workflow manually`

## Rolling back

To go back to the last tagged release:

```bash
ssh root@cast2md "docker pull meltforce/cast2md:latest && cd /opt/cast2md && docker compose up -d cast2md"
```

Or from Claude: ask to run this command.

Since edge builds retag `latest` locally on the server, a rollback needs to pull `latest` from Docker Hub again (which is the last tagged release).

## What's deployed?

```bash
ssh root@cast2md "docker inspect meltforce/cast2md:latest --format '{{index .Config.Labels \"org.opencontainers.image.version\"}}'"
```

- `edge-<sha>` = deployed from main (the SHA tells you which commit)
- `2026.01.1` (or similar) = tagged release

## Workflow file

`.github/workflows/deploy.yml` — builds the image, connects to Tailscale, deploys via SSH.
