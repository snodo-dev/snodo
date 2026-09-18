# Spec: `snodo models --benchmark` — one fixed prompt, timed

## Why
Model throughput has only ever been inferred from `--stats`, which aggregates
job telemetry: mean tokens/second across whatever prompts happened to run. That
figure moves with prompt size, tool-call count and how much the judge read, so
two models are never compared on the same work. Observed on a real project: the
coder was switched between providers twice on the strength of such aggregates,
and the comparison was never like-for-like.

A benchmark sends one fixed prompt and times the result. That answers the
question the aggregate only gestures at.

## Command shape
  snodo models --benchmark --provider=deepseek --id=deepseek-chat

The benchmark is scoped by the flags that already select a model: `--provider`
and the discrete filters (`--id`, `--id-contains`, cost/context bounds). It is not a new
top-level command; `snodo models` already owns this surface. It never runs
unless `--benchmark` is passed.

Exactly one model must resolve. Zero matches, or more than one, is an error that
names the candidates — benchmarking "a provider" is not a measurement.

## The prompt
The prompt is part of the measurement, not an argument to it. A benchmark whose
prompt varies measures nothing across runs, so it lives in the repository at
`snodo/cli/commands/model_benchmark_prompt.txt` and is read from disk — never
assembled from a string in the command. It is small enough to finish quickly and
is real work rather than a greeting ("write a FIFO queue in Python").

Changing the prompt makes past numbers incomparable. That is stated in the
prompt file's own header; the marker line in the file separates the note from
the text sent to the model.

## What is reported
Throughput alone would hide the distinction between a model that is slow to
start and one that streams slowly. Both matter, and they matter differently to a
coder (waits on a long generation) than to a validator (waits on a verdict). So
the output carries, separately and labelled:

- output tokens and prompt tokens, and **whose counts they are**;
- time to first token;
- total wall time;
- decode throughput (output tok/s after the first token);
- overall throughput (output tok/s including the first-token wait).

Token basis: provider-reported usage when the provider reports it; otherwise a
local tokenizer, and the basis says so. Dividing one provider's token count by
another's timing is a comparison of accounting systems, not of speed, so the
basis is never left implicit.

## Spending
This makes one real, billed API call. Before the call, the command prints the
model, the prompt identity (opening line, char length, content hash) and the
fact that it will spend. It is reachable only through `--benchmark` — not the
gate, the test suite, or any other path.

## Constraints honoured
- No new state, halt type or status value.
- No new top-level command.

## Tests
- The benchmark path makes no model call unless `--benchmark` is passed.
- `--stats` does not reach the benchmark.
- The fixed prompt is read from the repository, and its text is absent from the
  module source (not constructed at runtime).
- Provider-reported counts are preferred and named; the local fallback is named.
- The report states prompt identity, counts + basis, time to first token, wall
  time and both throughput figures.

Commit: feat(models): benchmark one fixed prompt so throughput is comparable
