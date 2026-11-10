# Writing a skill in this repo

Where a skill lives is decided in the root `STANDARDS.md` § Skills. This file
covers what applies once the home is `.claude/skills/<name>/`.

A `description` carries the literal trigger phrases that should invoke the
skill, in the languages they are spoken in, plus the cases that should *not*
invoke it. The description is the only part loaded into every session, so it
does the whole job of routing.

An entry under `.claude/skills/` may be a symlink to a directory elsewhere on
disk — Claude Code follows it and reads `SKILL.md` from the target. That is
worth using when a skill genuinely has to live somewhere else; it is not worth
using to keep the source outside `.claude/`, because the link then has to be
recreated on every checkout.

`.gitignore` has to let this directory through: `.claude/*` followed by
`!.claude/skills/`. A skill that is not committed exists on one machine only.
