# Reading overnight run results

When you return to an unattended run, use two reports for two different views:

- `snodo plan status <name> --run-summary` is the latest run of one plan. It
  lists each planned task's outcome, halt type, attempts, duration, tokens,
  cost, and whether it was delivered, followed by totals. Add `--json` for
  machine-readable output. This flag is opt-in; ordinary plan status remains
  the plan progress view.
- `snodo task report --days N` summarizes activity across the project's
  rolling time window (30 days by default). Its run section includes first-pass
  rate, outcomes, halt types, cost, tokens, and duration. It also includes
  human-review and validator outcome sections. Add `--json` for machine-readable
  output.

For plan task states, job status, and delivery outcomes, see [Following a plan
run](following-a-plan-run.md). The command reference has the full options and
output notes for `snodo task report` (see [Command reference](command-reference.md)).

## Read the metrics

### Validator block rate

The report gives validator outcomes separately for each validator and phase
(`pre` or `post`). Counts include `pass`, `warn`, and `blocker`. Block rate is
the number of non-reused `blocker` verdicts divided by the total number of
non-reused verdicts for that validator in that phase. Reused verdicts are
displayed in the JSON data as `reused_count` and `reused_severity_counts`, but
are excluded from both the block-rate numerator and denominator. A validator
that has no non-reused verdicts has no block rate (`n/a` in text, `null` in
JSON).

### First-pass rate

First-pass rate is the fraction of runs with known first-pass status whose first
recorded coder attempt had outcome `resolved` (case-insensitive). Missing or
incomplete attempt history is unknown and excluded from the rate's denominator.
If no run has a known status, the report shows `n/a` (JSON `null`). This measures
first-attempt resolution, not whether a run eventually completed or its work was
delivered.

### Cost: unknown and estimated

Cost totals add up the recorded costs that are available. A run with no recorded
cost contributes to `unknown_cost_runs`; it is not treated as zero. In the text
report, the cost line shows the total and how many runs had no measured cost.
The JSON run statistics also include `cost_by_source_usd`, which separates
recorded amounts by their provenance, and `unknown_cost_runs`.

Provider-reported costs and catalog estimates are both numeric costs, but their
sources differ. An estimated amount is a cost estimate, not a provider-reported
charge. Keep the source label when interpreting or comparing totals. If cost
cannot be determined, it remains unknown rather than being presented as an
estimate.

## When a block rate looks wrong

Check the detail before changing a validator or its policy:

1. Compare the exact validator and phase. Pre- and post-validation verdicts are
   separate, so a high rate in one phase does not describe every invocation.
2. Read the verdict counts with the rate. The rate is based on non-reused
   verdicts; reused counts are separate and do not affect it. A small verdict
   count can make the rate move sharply.
3. Check the report window. `snodo task report --days N` uses the selected
   rolling window (30 days when omitted), so compare the same window before
   drawing conclusions.
4. Inspect the affected task's validation details and audit events. Confirm
   that the validator ID, phase, severity, and reuse status match what you
   expected. The `skipped_events` count in JSON indicates malformed validation
   events omitted from the aggregate.
5. If the recorded verdicts or their context are unclear, inspect the task
   report and run logs before deciding whether the validator behavior or the
   underlying work needs attention.
