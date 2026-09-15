---
name: ship
description: Pre-deploy checklist — verify everything, confirm no secrets, and summarise what is about to change.
allowed-tools: Bash(python*) Bash(git*) Bash(docker*) Read
disable-model-invocation: true
---

Deploying money-handling code. Work through this and stop at the first failure.

1. Run `/verify` — all checks must pass
2. `git status` — working tree clean?
3. `git diff origin/main --stat` — what is actually changing?
4. Confirm nothing secret is tracked:
   ```bash
   git ls-files | grep -E '(^|/)\.env($|\.)|\.db$|\.pem$|\.key$'
   ```
   Must return nothing (`.env.example` aside).
5. Review the diff against `@.claude/rules/non-negotiables.md`. Flag explicitly if
   the change touches the risk engine, the edge gate, the cash floor, exit
   automation, or the ledger.
6. Confirm `.env.example` still ships `LIVE_TRADING=false`

Then summarise: what changes, what it affects at runtime, and what would have to be
true for it to be unsafe. **Do not run the deploy** — the GitHub workflow is manual
and requires typing `DEPLOY`.
