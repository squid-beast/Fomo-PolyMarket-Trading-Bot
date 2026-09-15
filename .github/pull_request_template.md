## What this changes, and why

<!-- One or two sentences. Link the issue, or the research note that justifies it. -->

## Before you request review

- [ ] `python -m pytest tests/ -q` passes
- [ ] `python simulate.py` prints **25/25 checks passed**
- [ ] `python audit.py verify` reports **CHAIN INTACT**
- [ ] No threshold was widened to make a test pass. If a threshold moved, it is named below with its justification.
- [ ] Any new gate ships with a test in `tests/` in this same change.
- [ ] Exits still happen without a human: nothing here adds approval to `Service.manage()` or removes it from `Service.execute()`.

## Thresholds moved

<!-- Write "none", or: file, old value -> new value, and the measurement or argument
     that justifies it. "It made CI green" is not a justification. -->

none

## Money-path acknowledgement

<!--
REQUIRED when this PR touches ct/risk.py, ct/safety.py, ct/ledger.py,
app/executor.py, app/service.py or config*.yaml.

CI reads the line below straight out of this description. It fails if the line
is missing, or if the angle-bracket placeholders are still in it. Replace BOTH
placeholders with real text. If this PR touches none of those files, delete this
whole section.
-->

MONEY-PATH ACK: I have read .claude/rules/non-negotiables.md. This change touches non-negotiable #<number>, and does not weaken it because <one sentence saying why>.
