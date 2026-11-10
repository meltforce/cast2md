# cast2md Standards

Ground rules for working in this repo. Each rule carries a one-line **why** so it
can be revisited when the context that produced it changes.

This document is the policy layer. Mechanics live in [`CLAUDE.md`](CLAUDE.md) —
repo overview, gotchas, day-to-day commands. The git workflow is in `CLAUDE.md`
as well, because it sets aside a harness default and therefore has to be in
every session's context.

A rule belongs here when the answer to *would this have prevented a real
mistake?* is yes, evidenced by the mistake. A rule that cannot name one is a
preference, and preferences do not need a policy document.

## Repo documents

The axis is where a thing *is*, not what it is about.

| File | Holds |
|---|---|
| `ROADMAP.md` | Open work only. Status token `[open]`. |
| `DECISIONS.md` | Decisions taken, including decisions not to do something — those are the ones most likely to be re-derived from scratch otherwise. |
| `INCIDENTS.md` | Postmortems for things that broke. Newest first. Application failures only; CI, deploy and host failures go to `homelab/INCIDENTS.md`. |

**The movement rule.** When an item closes it is *removed* from `ROADMAP.md`,
and its reasoning moves to whichever document above holds that kind of thing.
Nothing is struck through. Status tokens are exactly `[open]`,
`[done YYYY-MM-DD]`, `[dropped YYYY-MM-DD]`; emoji never carry status.

**Before closing an item, read its entry for residual work, dates, or
triggers.** Each of those becomes its own `[open]` row before the entry leaves
the roadmap; otherwise the residual work is deleted together with the entry.

**No new top-level documents** unless the concern is genuinely orthogonal to the
ones above.

**Operational exceptions never live in documentation.** They belong in
configuration — a condition, the inventory. Documentation may reference them;
it may not replace them.

Every rule written here carries a one-line **why**, so it can be revisited when
the context that produced it changes.

`tools/check-docs.sh`, run without arguments, checks this document contract:
the status tokens in `ROADMAP.md`, and ISO dates and the decimal point in the
documents above and in this file. It detects; it does not prevent.

## Language

English is the only language used inside this repo. German prose describing
English identifiers forces a translation layer ("Rolle" in the text, `role` in
the YAML) and breaks keyword search between an explanation and the code it
explains.

Applies to documents, code comments, log and error strings, CLI output,
identifiers and commit messages. A verbatim quote of external output keeps its
original wording.

German *language data* is not prose and is unaffected: the stopword list in
`src/cast2md/db/tsquery.py` and the German example queries in
`src/cast2md/mcp/tools.py` are payload.

Number and date formats follow the English convention: `1.82 GB` (decimal
point), `217,226` (comma as thousands separator), `2026-08-04` (ISO 8601, never
the German `DD.MM.YYYY` form).

A German name or term stays as it is: a proper noun (`Nürnberg`), an identifier
that exists only in German (a vault named `Persönlich`), or a domain term with
no English equivalent. An existing passage is not rewritten for a single German
word; it is translated when the passage is edited anyway.

Conversation language is independent of this and follows the operator.

## Skills

A skill lives in exactly one home, decided by *what it touches*:
`.claude/skills/<name>/` in this repo when it depends on this project — its
scripts, its services, its MCP servers — and `~/.claude/skills/<name>/` when it
works anywhere. A `skills/` directory at the repo root is not a discovery
location; a skill placed there is invisible.

Before writing or editing a skill, read `.claude/skills/CLAUDE.md`. It holds
what a `description` has to carry and when a symlinked skill is justified.

## Claude settings

`.claude/settings.json` is committed; `.claude/settings.local.json` is not.
Settings that belong to this project live here rather than in a host-level
configuration role: they deploy with a `git pull` and appear in a diff. They
cover only sessions whose working directory is this repo.

**`deny` names what must never run, in any mode.** A call that matches no rule
is decided by the permission mode — Manual asks, auto applies a classifier,
`dontAsk` refuses — so an allow list says nothing about what is refused. Deny
rules are evaluated first and an allow rule cannot override them. A `deny` list
is not required for its own sake.

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
