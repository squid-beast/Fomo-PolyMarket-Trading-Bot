---
name: verify
description: Full verification — scenario tests, engine self-checks, audit chain integrity, and a live scan. Run before any commit or deploy.
allowed-tools: Bash(python*) Bash(docker compose*) Read
---

Run every check and report a single pass/fail verdict.

1. `python -m pytest tests/ -q` — 59 scenario tests
2. `python simulate.py` — must print **25/25 checks passed**
3. `python audit.py verify` — must print **CHAIN INTACT**
4. `python scan.py` — a live scan; report the funnel counts

Then state, in this order:

- **VERDICT: PASS / FAIL**
- any failing test, with the assertion and file:line
- the scan funnel (`universe → safety → scored → proposed`)

A scan proposing **zero** entries is expected and correct while the edge gate is
active — do not report it as a failure. See `@.claude/rules/non-negotiables.md`.
