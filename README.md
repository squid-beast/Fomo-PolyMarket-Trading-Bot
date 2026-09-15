# copytrader

An autonomous Solana trading system built to answer one question honestly:
**does a strategy have an edge that survives its own costs?**

It is a forward-testing instrument first and a trading bot second. Every
simulated fill is priced through an explicit friction model, every decision is
written to a tamper-evident audit log, and the risk engine will refuse to trade
rather than act on a signal whose edge has not been measured.

> **It currently refuses to place entries.** The edge gate blocks them with
> `no_measured_edge(...)`. That is the designed behaviour, not a fault — no
> signal has yet demonstrated an edge that clears friction.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt pytest

python -m pytest tests/ -q     # 59 scenario tests
python simulate.py             # 25 engine self-checks
python scan.py                 # one live scan against real DexScreener data
```

Run the service and control panel:

```bash
cd web/ui && npm install && npm run build && cd ../..
cp .env.example .env           # then fill in, leaving LIVE_TRADING=false
docker compose up -d --build   # daemon + UI on 127.0.0.1:8787
```

Everything tunable lives in `config.yaml` (`config-small.yaml` for a small
account). No threshold is hardcoded anywhere else.

---

## How it works

```
DexScreener  →  SAFETY FILTER  →  STRATEGIES  →  RISK ENGINE  →  FILL
  ~290 live      21 hard gates     3 scorers     hard gate      cost-priced
                                                      ↓
                                            Telegram / web approval
```

Exits are processed **before** entries each cycle, so capital and risk slots are
freed before new candidates compete for them.

| Layer | Behaviour |
|---|---|
| **Safety filter** | Binary rejects before any scoring: liquidity band, pair age, wash-trading signature, FDV/liquidity ratio, transaction count, flow imbalance, ticker collision. A token scoring 99/100 that fails one gate is still rejected. |
| **Strategies** | Independent scorers, 0–100, each with written rationale. Entry needs a composite above threshold *and* agreement between strategies. |
| **Risk engine** | The hard gate. Position size, exposure caps, concurrency, cooldowns, daily-loss breaker, cash floor, and an edge gate that refuses any trade whose expected move cannot clear round-trip friction. Nothing upstream can override it. |
| **Exits** | Stop, trailing stop, take-profit, max-hold, liquidity collapse — **fully automatic**. |
| **Audit ledger** | Append-only and hash-chained. Any later edit or deletion breaks the chain at that exact record. |

### Entries ask. Exits don't.

Opening risk is opt-in: a proposal goes to Telegram or the web UI and executes
only on approval, expiring if ignored. Reducing risk never waits for a human —
a stop firing at 3am cannot depend on someone being awake.

---

## Control panel

React + TypeScript, served by the same FastAPI process that exposes the API.

Updates are **pushed, not polled**. The server watches SQLite's `data_version`
(≈3µs per check) and streams a Server-Sent Event only when the daemon actually
wrote something — roughly **90ms from write to render**, against 5s for a timer.

The UI records an approve/reject *decision*; it never executes. The daemon picks
the decision up and re-runs the risk engine before anything is signed.

```bash
cd web/ui && npm run dev       # :5173 with HMR, proxies /api to :8787
```

---

## Auditing

```bash
python audit.py verify              # chain integrity
python audit.py onchain             # do live signatures exist on Solana?
python audit.py failures            # every failure, newest first
python audit.py open                # positions rebuilt from the ledger alone
python audit.py export trades.csv   # FIFO cost-basis export
```

`open` reconstructs positions independently of the daemon's own state file — if
the two disagree, something is wrong. A logged live trade with no matching
on-chain transaction means the records and reality have parted ways.

---

## What it deliberately will not do

- **Trade on an unmeasured signal.** The edge gate blocks it, and disabling that
  gate is treated as a defect, not a configuration choice.
- **Wait for a human to exit a position.**
- **Let the web layer sign anything.**
- **Present assumed costs as measured ones.** Every cost parameter carries a
  `measured: true|false` flag. They are currently all `false`.
- **Bind the UI to a public interface.** It has no auth by design; reach it over
  an SSH tunnel.

---

## Layout

```
ct/            engine — costs, safety filter, strategies, risk, portfolio, ledger
app/           daemon — service loop, Jupiter executor, notifier, wallet sync
web/server.py  FastAPI + SSE. Records decisions; never executes.
web/ui/        React + TypeScript (Vite)
tests/         59 scenario tests — execution failures, rugs, restarts, risk gates
research/      price-path collection and strategy studies
deploy/        VPS bootstrap, verified backups, health checks
```

Research notes, findings and infrastructure docs live in `docs/`, which is not
tracked in this repository.

---

## Development

`CLAUDE.md` and `.claude/rules/non-negotiables.md` carry the invariants that keep
this system safe, and CI enforces several of them. A pre-commit hook blocks any
commit that stages a secret or has failing tests.

Contributions that weaken the risk engine, the edge gate, the cash floor, exit
automation, or the append-only ledger will be rejected — those constraints are
the point of the project.
