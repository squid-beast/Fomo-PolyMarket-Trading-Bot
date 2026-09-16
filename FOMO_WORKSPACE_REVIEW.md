# FOMO workspace and architecture review

Reviewed 15 September 2026. This review used source inspection, the published FOMO API documentation, isolated synthetic records, and a browser preview. No account credentials, existing databases, runtime state, or real orders were accessed.

## Current boundary

The application is a Solana research and paper-trading dashboard. Its current path is:

`DexScreener → safety checks → strategy scores → risk gate → entry approval → Jupiter`

The existing FOMO client is a separate research helper. It is not connected to the daemon's discovery loop, dashboard state, wallet, or executor. The current executor is Jupiter on Solana; changing a chain label does not add FOMO or BNB execution.

The user confirmed the analytics key came from [fomoapi.io/dashboard](https://fomoapi.io/dashboard). The provider describes itself as an independent, unofficial FOMO analytics service. Its [published API reference](https://fomoapi.io/docs) documents trader data, holdings, trade history, theses, alerts, and streams through data endpoints. It does not document an order-placement endpoint for a user's FOMO account. The UI therefore treats the key as analytics access and keeps account execution unavailable until a separate, verified executor is identified.

## What changed in this pass

- Added a FOMO-focused workspace with Overview, Market analysis, Copy trading, Trade history, and Rules & setup views.
- Separated FOMO analytics and copy-trading intent from the legacy DexScreener/Jupiter path.
- Added an explicit trading-unavailable state and disabled approvals when FOMO execution is not connected.
- Kept exit settings visible as configuration references: 25% take profit, 12% stop loss, 10% trailing stop after a 15% activation move, and 24-hour maximum hold. These are quoted-price triggers, not guaranteed net profit targets.
- Marked chart-based early exits as not implemented or validated. Pattern analysis cannot guarantee that a large loss will be detected before it happens.
- Added read-only rules and integration provenance. Runtime daemon overrides and execution mode remain explicitly unverified.
- Added searchable, date-filtered, mode-filtered, outcome-filtered, paginated local trade history with separate live, paper, and unknown summaries, expandable details, safe displayed-page CSV export, and valid Solscan links only for plausible signatures.
- Preserved proposal rows when a decision request fails. Approval errors now explain the unavailable executor.

## High-priority architecture work before real money

1. **Use confirmed fills.** The current daemon books a simulated quantity after a Jupiter result and passes estimated quantities into later exits. Store raw integer amounts, token decimals, actual fees, wallet identity, and confirmed receipts; support partial fills and partial exits.
2. **Separate persisted identities.** Saved state has no mode, chain, wallet, or account identity. A restart or paper/live switch can associate the wrong position with the current mode. Key state by account, chain, and mode, and refuse new entries when identity is ambiguous.
3. **Reconcile uncertain orders.** Persist order intent before submission and track submitted, confirmed, partial, failed, and unknown states. Resolve unknown transactions before retrying; an empty live signature cannot count as success.
4. **Restore risk history.** Closed trades, cooldowns, daily loss, and entry counts are not rebuilt with open positions after restart. Reconstruct risk state from durable records and require reconciliation after corrupt or incomplete state.
5. **Make exit monitoring honest.** The YAML file says 15 seconds, while `Service` defaults to the `MANAGE_INTERVAL_SEC` environment value of 60 seconds and skips missing prices. Publish actual cadence and quote age, record stale-price failures, and isolate management from blocking discovery work.
6. **Make FOMO research live-safe.** The research client cache is designed for repeatable analysis, not current execution. Add freshness, reconnect/backfill, duplicate handling, source-cap metadata, and durable provider-budget accounting before it becomes an always-on signal source.

## Forward path

Verify a supported FOMO account-execution integration separately from analytics access. Then add FOMO market analysis and selected-trader copying as two independently measurable signal sources. Both must provide provenance, delay, liquidity, fees, slippage, and out-of-sample edge to the existing risk gate. Forward-test restarts, reconnects, partial fills, late fills, stale prices, and mode mismatches before enabling a supervised live trial.
