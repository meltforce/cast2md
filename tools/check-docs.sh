#!/usr/bin/env bash
# Guards the document contract from CLAUDE.md § "Repo documents", and — when the
# language-en module is selected — the language rule from § Language.
#
# Run from the repo root.
#
#   check-docs.sh              document contract
#   check-docs.sh --language   repo-wide language sweep (needs the language-en module)
#   check-docs.sh --all        both
#
# Which checks apply is read from .claude/project-standards.json, so this script
# is copied between projects unchanged. Project-specific exceptions go in
# tools/check-docs.allow, not in here.
#
# Exit codes are three-valued on purpose, because "found nothing" and "could not
# look" are different answers and a caller must be able to tell them apart:
#
#   0   the checks ran and found nothing
#   1   the checks ran and found something
#   2   the checks could not run — bad usage, or the stamp cannot be read
#
# This detects. It does not prevent — for prevention, call it from
# .githooks/pre-commit.
set -euo pipefail
fail=0

mode="${1:-documents}"
case "$mode" in
  documents|--documents) mode=documents ;;
  --language) mode=language ;;
  --all) mode=all ;;
  *) echo "usage: check-docs.sh [--language|--all]" >&2; exit 2 ;;
esac

STAMP=".claude/project-standards.json"

# The module selection is read once, and three outcomes are kept apart:
#
#   no stamp          this project was never aligned. Checks that need a module
#                     are skipped; project-audit is what reports the absence.
#   readable stamp    its "modules" list decides which checks apply.
#   unreadable stamp  an error. Not an empty selection.
#
# The last distinction is why this is not inline in has_module. Reading an
# unreadable stamp as "no modules selected" makes every module-gated check skip
# itself while the script still exits 0 — a green check that verified nothing,
# and nothing else in the pipeline would report it.
MODULES=""
if [ -f "$STAMP" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "check-docs: $STAMP exists but python3 is missing — cannot determine which checks apply" >&2
    exit 2
  fi
  if ! MODULES=$(python3 - "$STAMP" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    data = json.load(fh)
mods = data.get("modules", [])
if not isinstance(mods, list):
    raise TypeError('"modules" is not a list')
print("\n".join(str(m) for m in mods))
PY
  ); then
    echo "check-docs: $STAMP cannot be parsed — repair the stamp or remove it" >&2
    exit 2
  fi
else
  echo "check-docs: no $STAMP — running only the checks that need no module selection" >&2
fi

# has_module <id> — true when the module is selected for this project.
has_module() {
  printf '%s\n' "$MODULES" | grep -qxF -- "$1"
}

# The documents that exist depend on the selection. A missing file is not a
# failure here — this checks contracts, not completeness; project-audit checks
# completeness.
DOCS=(ROADMAP.md DECISIONS.md)
has_module incidents && DOCS+=(INCIDENTS.md)
has_module policy && DOCS+=(STANDARDS.md)

check_documents() {
  if [ -f ROADMAP.md ]; then
    if grep -q '~~' ROADMAP.md; then
      echo "ROADMAP.md: struck-through rows found — move them out instead"
      fail=1
    fi

    if grep -qE '\[(done|dropped)' ROADMAP.md; then
      echo "ROADMAP.md: closed items do not belong here"
      fail=1
    fi

    if grep -oE '^\| *\[[a-z]+' ROADMAP.md | grep -vqE '\[open'; then
      echo "ROADMAP.md: unknown status token"
      fail=1
    fi
  fi

  for f in "${DOCS[@]}"; do
    [ -f "$f" ] || continue

    # Non-ISO dates. The surrounding character classes keep the match out of
    # dotted number runs — version strings (v12.13.2) and IPv4 addresses
    # (46.225.64.29) are not dates and must not be reformatted.
    if grep -qE '(^|[^0-9.])[0-9]{2}\.[0-9]{2}\.(20[0-9]{2})?([^0-9]|$)' "$f"; then
      echo "$f: non-ISO date found — use YYYY-MM-DD"
      fail=1
    fi

    if has_module language-en; then
      # Decimal comma in a measurement.
      if grep -qE '[0-9],[0-9]+ ?(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB|k|M|G|T)\b' "$f"; then
        echo "$f: decimal comma in a figure — use a decimal point"
        fail=1
      fi
    fi
  done
}

# ── Repo-wide language sweep ────────────────────────────────────────────────
#
# Two passes over every *.md outside .archive/ plus the common code file types.
# Pass 1 matches German function words; pass 2 matches any word carrying an
# umlaut or ß, which catches the German noun phrases a function-word list misses
# by construction.
#
# Neither pass proves a file is English — it shows only that the file carries
# none of the markers. Review stays the actual control for newly added files.

ALLOWFILE="tools/check-docs.allow"

FUNCTION_WORDS='(^|[[:space:]("])(aber|auch|aus|bei|beim|bereits|bleibt|braucht|damit|dann|dass|dem|den|der|des|deshalb|die|diese[nmrs]?|durch|eine[nmrs]?|erst|falls|fehlt|für|geht|gegen|hier|immer|ist|jetzt|kann|kein|keine[nmrs]?|können|läuft|liegt|macht|man|mit|muss|müssen|nicht|noch|nur|oder|ohne|schon|sich|siehe|sind|soll|sollen|sondern|sonst|steht|über|und|unter|vom|von|weil|weiter|wenn|werden|wie|wieder|wird|wurde|zum|zur|Änderung|Beispiel|Datei|Grund|Hinweis|Pfad|Rolle|Zeile|Zweck)([[:space:].,;:!?")]|$)'
UMLAUT_WORDS='[A-Za-zÄÖÜäöüß]*[äöüß][A-Za-zÄÖÜäöüß]*'

# A hit is suppressed when its path matches an allowlist entry and its content
# contains that entry's substring. Format per line: <path>%%<substring>.
#
# When a pass fires on a verbatim German quote, or on an English word the
# heuristic cannot tell apart, add that specific line to the allowlist — do NOT
# weaken the pattern, or the guard stops catching the case it exists for.
is_allowed() {
  local file="$1" content="$2" entry path sub
  [ -f "$ALLOWFILE" ] || return 1
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    case "$entry" in \#*) continue ;; esac
    path="${entry%%%%*}"
    sub="${entry#*%%}"
    [[ "$file" == "$path" ]] || continue
    [[ "$content" == *"$sub"* ]] && return 0
  done < "$ALLOWFILE"
  return 1
}

sweep() {
  local label="$1" pattern="$2"
  local hit file rest lineno content

  # The umlaut class is a multibyte bracket expression. Pin a UTF-8 locale so
  # the pattern behaves the same on a CI host as on a workstation; fall back
  # silently when the locale is unavailable.
  local LC_ALL
  if locale -a 2>/dev/null | grep -qx 'C.UTF-8'; then
    LC_ALL=C.UTF-8
  elif locale -a 2>/dev/null | grep -qix 'en_US.UTF-8'; then
    LC_ALL=en_US.UTF-8
  fi
  export LC_ALL

  while IFS= read -r hit; do
    file="${hit%%:*}"; rest="${hit#*:}"
    lineno="${rest%%:*}"; content="${rest#*:}"
    file="${file#./}"
    is_allowed "$file" "$content" && continue
    echo "$file:$lineno: $label — repo artifacts are English (CLAUDE.md § Language)"
    fail=1
  done < <(
    # This script and its allowlist are excluded from the sweep: they carry the
    # patterns by construction, so every entry would match itself. German text
    # added to them is therefore not caught here — review it directly.
    #
    # µ and ° appear legitimately in sensor and metric strings.
    # Dependency trees fetched by a tool are not repo artifacts — without the
    # prune, one `uv pip install` buries the real findings.
    find . \
      \( -path ./.git -o -path ./.archive \
         -o -path ./tools/check-docs.sh -o -path ./tools/check-docs.allow \
         -o -name node_modules -o -name vendor \
         -o -name .venv -o -name venv -o -name site-packages \
         -o -name ansible_collections \) -prune -o \
      \( -name '*.md' -o -name '*.sh' -o -name '*.py' -o -name '*.go' \
         -o -name '*.ts' -o -name '*.js' -o -name '*.rs' \
         -o -name '*.yml' -o -name '*.yaml' -o -name '*.j2' \
         -o -name 'Dockerfile*' -o -name '*.json' \) -type f -print0 \
    | xargs -0 grep -InE "$pattern" 2>/dev/null \
    | grep -vE 'µ|°' || true
  )
}

check_language() {
  if ! has_module language-en; then
    # Under --all the sweep is one of two checks and skipping it is a correct
    # outcome for a project without the module. Under --language it is the only
    # thing asked for, so reporting success would say the repo passed a sweep
    # that never ran.
    if [ "$mode" = language ]; then
      echo "check-docs: --language was requested, but the language-en module is not selected for this project" >&2
      exit 2
    fi
    echo "language sweep skipped — the language-en module is not selected for this project"
    return 0
  fi
  sweep "German function word" "$FUNCTION_WORDS"
  sweep "German word (umlaut/ss)" "$UMLAUT_WORDS"
}

case "$mode" in
  documents) check_documents ;;
  language)  check_language ;;
  all)       check_documents; check_language ;;
esac

exit $fail
