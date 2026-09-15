#!/usr/bin/env bash
# PreToolUse(Bash). Two guards before a commit reaches the repo:
#   1. no secrets staged
#   2. both test suites pass
#
# Fail-open on hook malfunction (jq missing, unreadable stdin) so a broken hook
# never bricks the repo — but fail-CLOSED on an actual secret or a real test
# failure, which are the cases that matter.
set -uo pipefail

IN=$(cat 2>/dev/null || echo '{}')
command -v jq >/dev/null 2>&1 || exit 0
CMD=$(printf '%s' "$IN" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -n "$CMD" ] || exit 0

case "$CMD" in *"git commit"*) ;; *) exit 0 ;; esac

deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",
    permissionDecision:"deny", permissionDecisionReason:$r}}'
  exit 0
}

# 1. secrets
STAGED=$(git diff --cached --name-only 2>/dev/null || true)
# Catch .env AND every .env.* variant (.env.production is where a live key
# would actually sit), but allow .env.example which is meant to be committed.
BAD=$(printf '%s\n' "$STAGED" \
  | grep -E '(^|/)\.env($|\.)|\.db$|\.pem$|\.key$|\.p12$|(^|/)data/|(^|/)paths/' \
  | grep -v -E '(^|/)\.env\.example$' || true)
[ -n "$BAD" ] && deny "Blocked: secret/data files staged:
$BAD
Remove them with: git restore --staged <file>"

# 2. tests
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" || exit 0

# Prefer the project venv. A bare `python` may be a system interpreter with no
# pytest, and "No module named pytest" reported as "tests failing" would be a
# confusing lie that blocks legitimate commits.
PY_BIN=""
for cand in "./.venv/bin/python" "${VIRTUAL_ENV:-}/bin/python" "$(command -v python3)" "$(command -v python)"; do
  [ -x "$cand" ] || continue
  if "$cand" -c "import pytest" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
done

if [ -z "$PY_BIN" ]; then
  deny "Cannot verify this commit: no interpreter with pytest was found.
Tried ./.venv/bin/python, \$VIRTUAL_ENV, python3, python.

This is an environment problem, not a test failure. Fix it with:
  python -m venv .venv && source .venv/bin/activate
  python -m pip install -r requirements.txt pytest

Commits are blocked until the suite can actually run."
fi

if ! "$PY_BIN" -m pytest tests/ -q >/tmp/ct_pytest.log 2>&1; then
  deny "Blocked: scenario tests failing.
$(tail -12 /tmp/ct_pytest.log)"
fi
if ! "$PY_BIN" simulate.py >/tmp/ct_sim.log 2>&1; then
  deny "Blocked: engine self-checks failing.
$(tail -12 /tmp/ct_sim.log)"
fi
exit 0
