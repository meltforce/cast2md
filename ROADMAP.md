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
| `[open]` | Put the commit revision into the image and check it after deploy | `Dockerfile`, `ci-workflows` | | `deploy-gate` proves the deploy job ran and left a healthy instance; it cannot prove *this* commit is serving. The `Dockerfile` sets `org.opencontainers.image.version` from a build arg but no `.revision`, and no endpoint exposes one. Needs the shared `build-push-deploy.yml` to pass the SHA, so it spans two repos. |
| `[open]` | Watch the first `deploy-gate` run for tailnet reachability | `.forgejo/workflows/ci.yml` | Next push to `main` | The health check assumes the runner reaches `cast2md.coydog-fence.ts.net`. `build-deploy` reaches the same host over Tailscale SSH, so it should hold — but it is untested from an ordinary `runs-on: docker` job. If it fails on DNS rather than on health, the step needs the Tailscale action rather than removal. |

## Documentation

| Status | Item | Where | Trigger | Notes |
|---|---|---|---|---|
| `[open]` | Add the screenshot the README reserves a placeholder for | `README.md` | | `<!-- Screenshot placeholder -->` has been in place since the repo was public. |
