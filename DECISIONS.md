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

The health poll is deliberately weaker than it looks and is documented as such
in the job: it proves the deploy left a serving instance, not that this commit
is the one serving. No image label or endpoint carries the revision — that is an
open roadmap item spanning this repo and `ci-workflows`.

**Verified on the first run** (`fc094bd`, 2026-08-06): all seven jobs green,
`deploy-gate` among them. An ordinary `runs-on: docker` job does reach
`cast2md.coydog-fence.ts.net` over the tailnet, so the health step needs no
Tailscale action of its own. That was the one assumption in the job that no
prior run had tested.

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
