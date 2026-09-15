# copytrader — project instructions

Autonomous Solana trading system. Discovery → safety filter → strategies → risk
engine → Telegram approval → Jupiter execution, with an append-only audit ledger.

**Read `@.claude/rules/non-negotiables.md` before changing anything under `ct/risk.py`,
`ct/safety.py`, `app/executor.py`, or `ct/ledger.py`.**

## Read this first

This system currently **refuses to trade**, on purpose. The edge gate blocks every
entry with `no_measured_edge(...)`. That is **not a bug**. It is the correct output
of research in `docs/FINDINGS.md`, which measured the entry signal as having
negative expectancy *before costs*.

If asked to "make it trade", do **not** remove or weaken the edge gate, the cash
floor, or the risk engine. The correct path is to find a signal with measured edge
and supply `expected_edge_pct` — see `@.claude/rules/non-negotiables.md`.

## Commands

```bash
python -m pytest tests/ -q        # 59 scenario tests
python simulate.py                # 25 engine self-checks — must print 25/25
python scan.py                    # one live scan against real DexScreener data
python audit.py verify            # audit chain integrity
python audit.py failures          # every recorded failure
python dashboard.py paper.db      # regenerate the static dashboard.html
python web/server.py              # control panel API + UI -> http://127.0.0.1:8787
cd web/ui && npm install          # first time only
cd web/ui && npm run build        # build the React UI into web/static
cd web/ui && npm run dev          # UI dev server on :5173, proxies /api to :8787
docker compose up -d --build      # run the daemon + UI
```

Both test suites must pass before any commit. There is a pre-commit hook that
enforces this.

## CI

`.github/workflows/ci.yml` runs five jobs on every push and PR: `secrets-scan`
(gitleaks), `guard` (repo hygiene), `test` (pytest + `simulate.py` + a 5,000-entry
ledger tamper test), `ui` (tsc + vite build), and `build` (Docker image, non-root,
imports clean, UI present).

The `guard` job pins claims to the code. Each check exists because the thing it
checks for has already shipped broken once:

- no `.env`, key material, `*.db`, `data/`, `web/static/` or `node_modules/` tracked
- no trailing comments in `.gitignore` — git treats the whole line as the pattern
- every protected path verifiably matched by `git check-ignore`
- `.env.example` ships `LIVE_TRADING=false`
- `web/server.py` never calls the O(n) `Ledger.verify()` — use `verify_from()`
- the README's gate count equals `grep -c 'r.append(' ct/safety.py`

If a doc claims a number, add a guard that derives it from the code. A stale
number in a README is how a reader ends up trusting the wrong thing.

Deployment is `.github/workflows/deploy.yml` — `workflow_dispatch` only, requires
typing `DEPLOY`, backs up the ledger first, verifies the chain and that the UI
answers `/api/state`, and rolls back on any failure.

## Architecture

| Path | Role |
|---|---|
| `ct/costs.py` | Friction model. Every simulated fill goes through it. |
| `ct/safety.py` | Binary reject gates, run before any scoring. |
| `ct/strategies/` | Scorers, 0–100 with written rationale. |
| `ct/risk.py` | **Hard gate.** Sizing, caps, cash floor, edge gate. |
| `ct/portfolio.py` | Paper fills, positions, exits, stats. |
| `ct/ledger.py` | Append-only hash-chained audit trail. |
| `app/service.py` | The 24/7 daemon. |
| `app/executor.py` | Jupiter swaps + live-quote impact gate. |
| `research/` | Price-path collection, the exit-rule study, one-off analyses. |
| `web/server.py` | FastAPI + SSE. **Records decisions; never executes.** |
| `web/ui/` | React + TypeScript source (Vite). |
| `web/static/` | Build output. Gitignored — rebuilt by npm or Docker. |
| `docs/` | Internal strategy/research notes. **Gitignored — not in the repo.** |

Config lives in `config.yaml` (and `config-small.yaml` for a $100 account).
**No threshold is hardcoded anywhere else** — keep it that way.

## Settled by evidence — do not redo

- **Momentum/trending-token entries have no edge.** Fitted on real price paths:
  −$1.55/trade out of sample, signal worse than random, negative gross before costs.
  Do not rebuild this strategy or re-tune its exits. See `docs/FINDINGS.md`.
- **Friction is ~4% round trip and U-shaped.** $2 trades cost 7.9%; $200 in a deep
  pool costs 3.68%. Small accounts sit at the worst end.
- **Copy trading only works in deep pools.** The leader's own price impact is your
  entry handicap: 28.6% in a $50K pool, 1.6% in a $1.2M pool.
- **Open question:** does any fomo trader show persistent, copyable skill? Untested.
  `run_persistence.py` answers it. This is the highest-value work available.

## Style

- Every cost parameter carries `measured: true|false`. They are currently all
  `false` — assumptions, not measurements. Do not present them as measured.
- New gates get a test in `tests/` in the same change.
- Comments explain *why*, especially where a value was chosen or a failure mode
  is being defended against.
- Never widen a threshold to make a test pass. Fix the code or change the test
  deliberately, and say which.

## Never

- Commit `.env`, `*.db`, `*.pem`, `*.key`, or anything under `data/`.
- `UPDATE` or `DELETE` a row in the `ledger` table. It is append-only; corrections
  are new entries.
- Put the trading key anywhere except `.env` on the VPS.
- Make exits require approval. Entries are opt-in; exits must never wait for a human.
- Let `web/` execute a trade. It writes a decision row; the daemon re-runs
  `risk.evaluate()` before signing. Never call the executor from the web process.
- Bind the UI to anything but `127.0.0.1`. It has no auth by design — reach it
  over an SSH tunnel, never by opening a port.
- Call `Ledger.verify()` on a hot path. It is O(n) with a SHA256 per row — 116ms
  at 20k rows and climbing. Use `verify_from(seq, hash)` with a checkpoint for
  anything that runs repeatedly; full `verify()` is for audits.
