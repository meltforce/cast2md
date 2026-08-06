# Decisions

Decisions taken about cast2md, with the reasoning that led to them. One section
per decision, newest first.

A decision belongs here once it has been made — including decisions to *not* do
something, which are the ones most likely to be re-derived from scratch
otherwise. Open work lives in [`ROADMAP.md`](ROADMAP.md); postmortems live in
[`INCIDENTS.md`](INCIDENTS.md).

Structure per entry: **decision**, **reasoning**, **trigger to re-open**, and a
**revisions** log when the decision has changed. A revised decision is edited in
place with the old form recorded under revisions — the entry is not duplicated.

---

## 2026-08-06 — the dependency virtualenv is a layer of its own, independent of the source

**Decided:** 2026-08-06

**Decision.** The Dockerfile has three stages. `deps` installs torch and the
rest of the dependencies into `/build/.venv` from `pyproject.toml` and `uv.lock`
alone; `project` adds the editable install and keeps only the resulting
`cast2md-*.dist-info`; the runtime stage copies the venv and that metadata as
two separate layers. `src/` is copied last and reaches the application through
`PYTHONPATH`, not through the venv.

**Reasoning.** The venv is ~1.45 GB of a 1.86 GB image, and it is one layer. If
its contents differ between builds, the deploy target re-pulls that gigabyte —
which is why a cast2md deploy took several times as long as a vimmary deploy on
the same day, for a change of about a kilobyte of Python.

Two separate causes made it differ, and both had to go.

`COPY src/ ./src/` sat above the installs, so any commit touching a Python file
invalidated the torch layer and it was rebuilt from scratch. Measured across
three consecutive builds: the venv layer digest was byte-identical between
`3657260` and `3322ac9` — the latter touched only CI config and documentation —
and changed at `ef45dc9`, which touched two files under `src/`.

Moving the copy down was necessary but not sufficient. `uv pip install -e .`
writes `uv_cache.json` into the dist-info, and that file carries a fingerprint
of the source tree, so the copied venv still changed on every source change even
with every install step cached. Verified by building twice with one appended
comment in between: the venv layer digest still differed, and a diff of the
metadata showed `uv_cache.json` and the `RECORD` entry covering it as the only
differences.

Splitting the metadata into its own `COPY` puts those bytes in a layer of their
own. Verified the same way afterwards: with only a comment changed, the venv
layer digest is identical and exactly two layers differ — the metadata and
`src/`, together about a megabyte.

The editable install's path hook is not carried into the runtime image. It
points at `/build/src`, which does not exist there; the modules have always been
found through `PYTHONPATH=/app/src` instead. Only the `dist-info` travels,
because `cast2md/__init__.py` resolves its version through
`importlib.metadata.version("cast2md")` and raises without it.

**Trigger to re-open.** uv stops fingerprinting the source into the dist-info,
or the project stops reading its own version from package metadata — either
would make the split unnecessary.

---

## 2026-08-06 — `ruff format` owns line length; E501 is not enforced separately

**Decided:** 2026-08-06

**Decision.** `ruff format` is applied to `src/` and `tests/` and checked in CI
with `ruff format --check`. `E501` is in `ignore` in `pyproject.toml`.

**Reasoning.** `ruff` had never run in CI, so 366 findings had accumulated. The
formatter run (47 of 80 files) cleared most of them and took E501 from 141 to
83. Every one of the 83 survivors was classified: 55 in string literals, 23 in
SQL and shell heredocs inside triple-quoted strings, 3 comments, 2 docstrings.
Not one was wrappable Python. Breaking them by hand would make the SQL less
readable and would change behaviour inside the RunPod pod startup scripts.

Enforcing both a formatter and a line-length lint means two authorities for one
question, and the lint can only ever fire on what the formatter deliberately
left alone.

**Alternative considered.** Keeping E501 and hand-wrapping the 83. Rejected on
the classification above. Also considered: skipping the formatter and only
ignoring E501 — rejected because it leaves the other 58 E501 findings that the
formatter does fix, and leaves the codebase without a formatting authority.

**Cost accepted.** The format run rewrites 47 files, so `git blame` on those
lines points at commit `6f3e133`. That commit is listed in
`.git-blame-ignore-revs`, which the forge reads automatically; a local checkout
needs `git config blame.ignoreRevsFile .git-blame-ignore-revs` once.

**Trigger to re-open.** A `ruff format` release whose output changes materially,
or a decision to adopt a different line length.

## 2026-08-06 — runs queue per ref instead of cancelling each other

**Decided:** 2026-08-06

**Decision.** `ci.yml` sets `concurrency` at workflow scope with
`group: <workflow>-<ref>` and `cancel-in-progress: false`.

**Reasoning.** Forgejo aborted running push runs of the same workflow and branch
on the next push, hardcoded, up to v12 (forgejo#5914: "can lead to partial and
broken deploys"). `concurrency` became configurable in v14; this instance is on
16.0.2 and nothing had been set.

The cost was paid the same day this repo's deploy checks were built: the deploy
of `730bdd7` ended as `cancelled` because the next commit landed four minutes
later. Production stayed on the previous image, and nothing was red — `cancelled`
is a third outcome next to `success` and `failure`, and `deploy-gate` cannot
report on a run killed before its jobs start. The gate was not bypassed; it never
ran.

**Workflow scope, not job scope.** `deploy-gate` compares the running build
against `github.sha`, so a second deploy starting while the first run's gate is
still polling would make that comparison fail on a correct deploy. Serialising
the whole run keeps it meaningful. The group carries the ref, so a pull request
never queues behind `main`.

**Cost accepted.** Two pushes in quick succession now take roughly twice as long
to reach production, because the second waits rather than replacing the first.
With one developer that is a few minutes; the alternative is a deploy that
disappears without a trace.

**Trigger to re-open.** Queueing becoming a bottleneck — at which point the
narrower fix is job-scoped concurrency on `build-deploy` plus folding the
revision check into the same job, so there is nothing left to race against.

## 2026-08-06 — a skipped deploy fails the pipeline

**Decided:** 2026-08-06

**Decision.** A `deploy-gate` job runs after `build-deploy` on every push to
`main` with `if: always()`, and exits 1 unless `needs.build-deploy.result` is
`success`. It then polls `/api/health` on the production host for up to 60s and
requires `"status":"healthy"`.

**Reasoning.** A `uses:` job whose `if:` evaluates false is reported as
`skipped`, and a skipped job does not fail the workflow. Between early June and
2026-08-01 that produced a green pipeline over a deploy that never ran, and the
condition was found by reading the file rather than by any signal. The gate
converts that silence into a red run.

**Verified on the first run** (`fc094bd`, 2026-08-06): all seven jobs green,
`deploy-gate` among them. An ordinary `runs-on: docker` job does reach
`cast2md.coydog-fence.ts.net` over the tailnet, so the health step needs no
Tailscale action of its own. That was the one assumption in the job that no
prior run had tested.

**Revision check added the same day.** The gate originally proved only that the
deploy left *a* serving instance. It now compares the commit as well, which
closes the remaining half of the same failure class: if the push left `:edge`
pointing at the old image, `docker compose pull` fetches that one, `up -d`
reports up-to-date, and health is green over a deploy that changed nothing.

The mechanism turned out to need no new plumbing. `build-push-deploy.yml` has
passed `VERSION=edge-<sha>` as a build arg since `v1`, and the `Dockerfile`
already wrote it to `org.opencontainers.image.version` — the commit was in the
image all along, readable only over the Docker socket. One `ENV` line makes it
readable to the process, `/api/health` reports it as `build`, and the gate
compares it against `github.sha` over HTTPS.

**Alternative considered.** Comparing image digests over SSH inside the shared
`build-push-deploy.yml`. Rejected: it would force cast2md off `v1`, and `v3`
copies the calling repo's `docker-compose.yml` to the target — here the dev
stack, with a hardcoded password and Postgres published to the host, over the
production `compose.yaml` that homelab owns. The bump is worth doing, with
`sync_compose: false`, but it is its own change and is on the roadmap.

`tests/test_health.py` pins the field name and the shell extraction, because a
rename on the Python side would otherwise only surface as a failing deploy.

**Alternative considered.** Removing the `if:` from `build-deploy` so it can
never be skipped. Rejected: it would then also run on pull requests, deploying
unreviewed code to production.

**Trigger to re-open.** The revision landing in the image, at which point the
health poll should compare it against `github.sha` instead of only checking
liveness.

## 2026-08-06 — infrastructure postmortems live in `homelab`, not here

**Decided:** 2026-08-06

**Decision.** `INCIDENTS.md` in this repo takes failures of the application:
the transcription pipeline, workers and nodes, the job queue, the database,
search and embeddings. Failures of CI, the deploy path, the container host or
the tailnet go to `homelab/INCIDENTS.md`, and this file carries a pointer rather
than a copy.

**Reasoning.** The split follows where the fix lands, not where the symptom
appeared. The deploy gate that skipped `build-deploy` for two months surfaced as
"cast2md is not updating", but the defect was in the `if:` condition pattern of a
`uses:` job and it affected every app repo calling `ci-workflows`. Written up
here it would have been one of several copies, each drifting, and none of them
where the next person looks — the fix was in the shared workflow.

**Alternative considered.** Duplicating infrastructure postmortems into every
affected app repo. Rejected: the number of copies equals the number of callers,
and a correction to one of them reaches none of the others.

**Trigger to re-open.** cast2md leaving the homelab — a deployment whose CI and
hosts are not covered by that repo would have nowhere else to put them.

## 2026-08-01 — releases go to ghcr.io and GitHub; Codeberg is dropped, Docker Hub keeps only the afterburner

**Decided:** 2026-08-01 (commits `4d84d4f`, `3a521ea`)

**Decision.** The `release` job publishes `ghcr.io/meltforce/cast2md`, PyPI and a
GitHub release. Codeberg references are removed. Docker Hub receives
`meltforce/cast2md-afterburner` only, built when
`deploy/afterburner/Dockerfile` changes on `main`. Dev builds never reach a
public registry.

**Reasoning.** Three public mirrors of the same artifact is three chances for a
consumer to pull a stale one, and Codeberg was carrying no traffic the other two
did not. Keeping the afterburner on Docker Hub is a constraint from the pod side,
not a preference: RunPod pulls it and ghcr.io adds an authentication step there
for no gain.

**Trigger to re-open.** ghcr.io rate limits becoming a problem for external
users, or RunPod gaining a reason to prefer ghcr.io.

## 2026-05-11 — production follows `:edge`, not a release tag

**Decided:** 2026-05-11 (established with the Forgejo migration, `aac6399`;
moved onto the shared workflow in `b0abaf7` on 2026-05-12)

**Decision.** The production server runs
`git.coydog-fence.ts.net/meltforce.net/cast2md:edge`. The `build-deploy` job
rebuilds `:edge` and redeploys on every push to `main`, so `main` reaches
production without a tag. Tagged releases publish to ghcr.io and PyPI for
external users and do not change what this server runs.

**Reasoning.** The server has one user. A tag between "merged to `main`" and
"running" would add a step whose only function is ceremony, and the release
stream exists for people who are not the operator.

**Consequence that has to be stated with it.** A failure in `build-deploy` is
invisible as long as the pipeline reports success — production simply stops
moving while `main` keeps advancing. That is not hypothetical; see the pointer
in [`INCIDENTS.md`](INCIDENTS.md).

**Trigger to re-open.** A second person depending on the production instance, or
a change whose rollback needs a named version to go back to.

## 2026-05-11 — Forgejo is canonical, GitHub is the public distribution channel

**Decided:** 2026-05-11 (`aac6399`, preceded by `8d238fc` on 2026-05-10)

**Decision.** `origin` is
`https://git.coydog-fence.ts.net/meltforce.net/cast2md.git`. GitHub
(`github.com/meltforce/cast2md`) receives commits and tags through a Forgejo
push-mirror and serves as a distribution channel only. CI is
`.forgejo/workflows/ci.yml`; there is no `.github/workflows/`.

**Reasoning.** The push-mirror runs `git push --mirror`, which prunes every ref
not present on Forgejo. Any branch created only on GitHub is deleted on the next
sync. Two consequences follow and are not optional:

- The `docs` job pushes `gh-pages` to **Forgejo** and lets the mirror carry it to
  GitHub, where Pages serves it. Pushing `gh-pages` straight to GitHub works once
  and is removed by the next sync.
- The `release` job waits for the tag to reach GitHub before creating the release,
  because the release must reference an existing tag. It polls `api.github.com`
  for 5 minutes and exits 1 otherwise — a stalled mirror therefore fails the
  release *after* ghcr.io and PyPI have already been published.

**Certain GitHub references stay on purpose** and must not be rewritten to
Forgejo:

| Location | Why GitHub |
|---|---|
| `services/pod_setup.py:100`, `deploy/afterburner/afterburner.py:706` | RunPod pods run Tailscale in userspace mode. Their outbound HTTP proxy has no CONNECT tunneling, and `git.coydog-fence.ts.net` answers on HTTPS only (port 80 refused), so `pip install git+https://git.coydog-fence.ts.net/...` cannot work from a pod. |
| `docs/installation/*.md`, `README.md` | The docs are published at cast2md.meltforce.org. Readers outside the tailnet cannot resolve the Forgejo host. |
| `pyproject.toml` (`Repository`, `Issues`), `mkdocs.yml` (`repo_url`), `Dockerfile` (`image.source`) | Public-facing metadata for PyPI, the docs site, and image provenance. |

This holds only because the mirror keeps GitHub current, including
`raw.githubusercontent.com/meltforce/cast2md/main/scripts/cast2md-node.sh`, which
`docs/installation/node.md` pipes into bash.

Internal installs use Forgejo: `deploy/install.sh` defaults to the Forgejo clone
URL and takes `CAST2MD_REPO` to override it for hosts outside the tailnet.

**Trigger to re-open.** The tailnet ceasing to be the primary working
environment, or a second contributor who cannot reach the Forgejo host.
