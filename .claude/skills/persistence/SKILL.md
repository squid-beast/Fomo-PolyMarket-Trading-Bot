---
name: persistence
description: Run the trader persistence test (gate G2) against the fomo API — does any trader have skill worth copying?
arguments: [traders]
allowed-tools: Bash(python*) Read Write
---

Gate **G2** from `docs/BUILD-PLAN.md`. This is the experiment the project turns on.

**Before spending any API credits**, confirm the test itself still works:

```bash
python validate_persistence.py
```

It must show ~5–10% false positives on pure luck and ≥90% detection at 3% skill.
If it does not, stop — the instrument is broken and its output would be worthless.

Then run the real thing (default 100 traders; budget is the whole free tier):

```bash
set -a; . ./.env; set +a          # nothing in Python loads .env; docker does it via env_file
python run_persistence.py --traders ${traders:-100} --calls-per-trader 10
```

Report against the **pre-agreed** pass conditions — do not renegotiate them:

- **G2 pass:** ≥2 of the 3 tests significant **and** skill SD ≥ 5%/trade
- **G3 pass:** ≥10 traders whose edge clears friction + their own price impact,
  trading pools deeper than $400K

If G2 fails, say so plainly. That is a real answer bought cheaply, and the project
should stop rather than proceed on a signal that does not exist. Do not soften it,
and do not suggest re-running with different parameters to get a better number.
