# cast2md Standards

Ground rules for working in this repo. Each rule carries a one-line **why** so it
can be revisited when the context that produced it changes.

This document is the policy layer. Mechanics live in [`CLAUDE.md`](CLAUDE.md) —
repo overview, gotchas, day-to-day commands.

A rule belongs here when the answer to *would this have prevented a real
mistake?* is yes, evidenced by the mistake. A rule that cannot name one is a
preference, and preferences do not need a policy document.

## Repo documents

These documents carry state over time. The axis is where a thing *is*, not what
it is about.

| File | Holds |
|---|---|
| `ROADMAP.md` | Open work only. Status token `[open]`. |
| `DECISIONS.md` | Decisions taken, including decisions not to do something — those are the ones most likely to be re-derived from scratch otherwise. |
| `INCIDENTS.md` | Postmortems for things that broke. Newest first. Application failures only; CI, deploy and host failures go to `homelab/INCIDENTS.md`. |

**The movement rule.** When an item closes it is *removed* from `ROADMAP.md`,
and its reasoning moves to whichever document above holds that kind of thing.
Nothing is struck through — a struck-through row is a row that should have been
moved. Status tokens are exactly `[open]`, `[done YYYY-MM-DD]`,
`[dropped YYYY-MM-DD]`; emoji never carry status. *Why:* a closed item that stays
on the roadmap as a struck-through row is a row nobody trusts, and its residual
work leaves with it.

**Before closing an item, read its entry for residual work, dates, or
triggers.** Each of those becomes its own `[open]` row before the entry leaves
the roadmap. This is the step that gets skipped, and skipping it is how
finished-looking work quietly loses its tail.

**No new top-level documents** unless the concern is genuinely orthogonal to the
ones above. Everything else lives inside a project directory.

**Operational exceptions never live in documentation.** Excluding a host from a
run, skipping a check for one case — those belong in configuration, in a
condition, in the inventory. Documentation may reference them; it may not
replace them.

## Language

English is the only language used inside this repo. *Why:* German prose
describing English identifiers forces a translation layer ("Rolle" in the text,
`role` in the YAML) and breaks keyword search between an explanation and the code
it explains. The drift is documented: commit messages ran German from 2026-05 to
2026-08-06, one of them (`8874f4a`) with ASCII substitution for an umlaut, and
`.forgejo/workflows/ci.yml` carried a German comment.

Applies to every document, every code comment and docstring in every language
present, log messages, error strings, user-facing CLI output, identifiers
(variables, functions, roles, tags, unit names, secret paths), and commit
messages.

Number and date formats follow the English convention: `1.82 GB` (decimal
point), `217,226` (comma as thousands separator), `2026-08-04` (ISO 8601, never
the German `DD.MM.YYYY` form).

The only exception is a verbatim quote of external output — an upstream error
message, vendor documentation — which keeps its original wording. German
*language data* is not prose and is unaffected: the stopword list in
`src/cast2md/search/repository.py` and the German example queries in
`src/cast2md/mcp/tools.py` are
payload, and both are listed in `tools/check-docs.allow`.

Conversation language is independent of this and follows the operator.

`tools/check-docs.sh --all` checks both this rule and the document contract. It
detects; it does not prevent.

## Git workflow

Single developer. **`main` is the only long-lived branch** — commit straight to
`main`, never open a PR for this repo. This overrides the harness default of
branching before committing. *Why:* branch-and-PR with one developer is overhead
with no reviewer at the other end.

**Commit and push autonomously** once a coherent change is complete and
verified. No approval needed per commit.

**Stage explicitly, never `git add -A`.** Name the paths you touched. *Why:*
parallel sessions run in this same checkout, and a blanket add sweeps up their
work in progress and commits it under your message.

### Parallel sessions

Isolate concurrent sessions with worktrees:

```bash
claude --worktree <name>          # .claude/worktrees/<name>, branch worktree-<name>
```

A worktree branch is **ephemeral plumbing, not a feature branch**. Never push it
as a branch, never open a PR from it. Land the work on `main`:

```bash
git fetch origin
git rebase origin/main
git push origin HEAD:main          # a rejection means another session landed first
```

Rebase and push again rather than forcing.

Do **not** start background sessions for work that edits this repo — a
background session commits, pushes its own branch and opens a draft PR without
asking, and is hard-wired never to push to `main`. Background sessions are fine
for read-only investigation.

**If a harness rule conflicts with this, this file wins.** `main` *is* the review
surface here and `git revert` is the undo. Say plainly which rule you are
setting aside, then land the work. Do not stop at "the commit is ready, please
push it yourself" — that hands back a half-finished task.

## Skills

A skill lives in exactly one home, decided by *what it touches* — not by where
you were when you wrote it. *Why:* without the rule a skill lands wherever the
session happened to be, and a skill that depends on this project's environment
breaks once it is invoked from elsewhere.

| Home | For |
|---|---|
| `.claude/skills/<name>/` in this repo | Skills that depend on this project: its scripts, its services, its MCP servers. Committed here; `.gitignore` opens this path and keeps the rest of `.claude/` untracked. |
| `~/.claude/skills/<name>/` | Skills that work anywhere and carry no project dependency. Delivered by the `claude-standards` Ansible role, not by hand. |

Decide the home before writing. If the skill would fail outside this project, it
belongs here.

A `description` carries the literal trigger phrases that should invoke the
skill, in the languages they are spoken in, plus the cases that should *not*
invoke it. *Why:* the description is the only part loaded into every session, so
it does the whole job of routing.

This repo places project skills directly in `.claude/skills/`, not in a `skills/`
source directory — see [`DECISIONS.md`](DECISIONS.md), 2026-08-06.

## Claude settings

`.claude/settings.json` is committed; `.claude/settings.local.json` is not.
Project scope belongs in the repo, session and machine state does not.

**`allow` and `deny` are both required.** *Why:* an allow list on its own
describes what is permitted and says nothing about what is refused, which reads
as a complete policy and is not one.

Settings that belong to this project live here rather than in a host-level
configuration role: they deploy with a `git pull`, need no privileged run, and
appear in a diff. The cost is that they cover only sessions whose working
directory is this repo — anything spawned from elsewhere does not get them.

## When in doubt

- **A finding that is really an infrastructure failure.** If the fix lands in
  `ci-workflows`, in an Ansible role, or on the container host, the postmortem
  goes to `homelab/INCIDENTS.md` and this repo gets a pointer row.
- **A change that touches both the standard and the project.** Two commits. The
  alignment commit carries no project work, and the project commit carries no
  alignment.
- **Something that only reproduces in production.** Reproduce it on the dev
  machine first anyway; if it genuinely cannot be reproduced there, say so in
  the postmortem rather than restarting production to find out.
