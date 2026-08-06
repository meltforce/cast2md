# Incidents

Postmortems for things that broke. One section per incident, newest first.

Add an entry after fixing something that was not obvious — the kind of failure
where the useful question six months later is "have I seen this before?". Skip
routine config changes, dependency bumps, and one-line typos.

Structure per entry: **symptoms** (what was visible), **root cause** (concrete:
component, version, why), **fix** (what changed), **lesson** (one line,
actionable).

**Scope.** This file takes failures of the application: transcription pipeline,
workers and nodes, the job queue, the database, search and embeddings. Failures
of CI, the deploy path, the container host or the tailnet go to
`homelab/INCIDENTS.md` — see [`DECISIONS.md`](DECISIONS.md), 2026-08-06.

## Elsewhere

| Date | What | Where |
|---|---|---|
| 2026-08-01 | `build-deploy` and the image build were skipped for two months while the pipeline reported success; `:edge` was frozen at 2026-05-11. The `if:` on a `uses:` job is evaluated in the *called* workflow, where the event is `workflow_call`, so `github.event_name == 'push'` was never true. Fixed in `c7116cf`. | `homelab/INCIDENTS.md` § 2026-08-01 (evening) — App repo CI built and deployed nothing since June |

---

*No application incidents recorded yet. The first entry replaces this line.*
