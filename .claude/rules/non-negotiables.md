# Non-negotiables

These exist because removing them is the fastest path to losing real money. If a
request conflicts with one, say so and propose the alternative rather than
complying.

## 1. The risk engine is a hard gate

`ct/risk.py` runs **after** scoring and after any LLM opinion. Nothing upstream may
override it. If a strategy scores 99/100 and the risk engine says no, the answer is
no.

Do not add a bypass flag, an "override" parameter, or a code path that skips
`risk.evaluate()`.

## 2. The edge gate blocking everything is correct

```
no_measured_edge(signal unvalidated; friction 4.1%)
```

This fires because `expected_edge_pct` is `None` — no strategy has supplied a
*measured* edge. The research measured the existing signal at **negative** gross
expectancy.

**The wrong fix:** setting `require_edge_gate: false`, or passing a made-up
`expected_edge_pct`, or lowering `min_expected_edge_pct` until trades pass.

**The right fix:** produce a signal whose edge has been measured out-of-sample, and
pass that measured number. If the measurement says the edge is below friction, the
system is telling you the truth — the trade is not worth taking.

## 3. Exits are automatic, entries need approval

Opening risk is opt-in. Reducing risk never waits for a human. If a stop fires at
3am while the owner is asleep, waiting for a Telegram tap is how an account is
destroyed.

Never add an approval step to `Service.manage()`. Never remove one from
`Service.execute()`.

## 4. The cash floor is not a suggestion

`min_cash_reserve_pct` exists because the owner reached $0 cash twice, leaving no
ability to act on anything — including taking profit on a winning position.

## 5. The ledger is append-only

`ct/ledger.py` is hash-chained: each entry hashes the previous entry's hash. Any
`UPDATE` or `DELETE` breaks the chain and `audit.py verify` will report it.

If a record is wrong, append a correcting entry. Never edit history.

Numeric fields are coerced to `float` before hashing — an `int` written into a
`REAL` column returns as `float` and would break verification on clean data. Do not
remove `Ledger._num()`.

## 6. Failures must be recorded, especially exits

A failed exit is the **most important row in the database**: the system believes it
has closed a position it still holds. It is recorded with `position_still_open=True`.

Never swallow an execution exception without a ledger entry.

## 7. Confirmation is never assumed

`JupiterExecutor.confirm()` returns `False` when a send's confirmation does not
arrive. Do not change it to return `True` optimistically. An unconfirmed send is an
unknown position, and every later decision would rest on a false premise.

## 8. Live and paper are never mixed

Every ledger row and every fill carries its `mode`. Analysing paper fills as if they
were real money is the worst silent error this system can make.
