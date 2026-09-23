<!-- snodo-guide topic="discovery" aliases="breakdown,exploration" summary="Break work down and ask the codebase" section="# Break work down and ask the codebase" -->
# Break work down and ask the codebase

Use `decompose` when you have a clear intent and want snodo to create the
plan's starting structure. It creates the plan directory, `plan.yml` with
sequential empty wave slots, and an empty `status.json`; it returns the plan
data (`name`, `intent`, and `waves`) plus the surrounding plan-tool response.
The default is one empty wave. It does **not** infer tasks, write task specs,
choose the correct wave boundaries, or execute anything. Fill the scaffold with
task specs, then validate and review the complete plan before running it.

Write waves and specs by hand instead when the graph is already known, when you
need exact control over dependencies and task wording, or when you are editing
an existing plan. Hand authoring is also the right choice when the structure
cannot be represented by an empty scaffold alone. In either case, dependencies
are between waves, and every task spec must stand on its own.

## Recon before planning

Call `recon` before planning when an important part of the intent is still a
codebase question: for example, which module owns a behavior, where a similar
path is implemented, or which files and tests a change is likely to touch.
Recon is optional. Skip it when the repository facts and scope are already
known, or when the question can be answered from the files you have already
read.

Recon is read-only. Agents can use only `read_file` and `list_files`; they do
not edit files, run commands, create commits, or write a plan. The answers are
raw text, so use them as evidence while you author the intent, waves, and
standalone specs rather than treating them as an automatically generated plan.

## Ask an answerable question

Ask one bounded question with a concrete subject, a desired conclusion, and a
search scope. Name the behavior or decision you need to make and include the
paths that contain the relevant code. Questions such as these are answerable:

```text
Which files implement session switching, and what state is persisted when a
session becomes active? Inspect snodo/session and the tests; cite the relevant
functions and identify tests a plan should preserve.
```

```text
Where is the existing request validation for card answers, and which tests
establish its error behavior? Inspect src/cards and tests/cards only.
```

Avoid a vague request such as “understand the repository” or a request for a
solution such as “design and implement the refactor.” Recon should establish
facts and uncertainties; planning decides the work and its acceptance checks.
If the question has unrelated parts, run separate recons so each answer can be
compared against the same clear criterion.

## Compare agents deliberately

Use several agents on the same question when independent readings are useful:
to reduce the chance of missing a path, compare interpretations of an unclear
boundary, or distinguish a repository fact from one agent's guess. Set
`num_agents` greater than one, or provide an explicit `agents` list. Each lane
answers the same query independently, and the results are returned one per
agent.

Do not confuse fan-out with the configured model priority list. With one lane,
`llm.recon.models` is an ordered failover chain: the next model is tried only
when the previous one fails or returns an empty answer. With `num_agents > 1`,
the configured models become separate single-model lanes for deliberate
fan-out. An explicit agents list likewise asks every named agent. A poor but
non-empty answer is not retried.

Read the raw answers side by side. Convergence means independent agents point
to the same files, symbols, behavior, and tests; record those repeated facts in
the plan. Treat disagreements as questions to resolve by reading the cited
files yourself, narrowing the paths, or running another focused recon. Do not
turn a majority answer into an unverified requirement.

## Recon is asynchronous

`recon` returns immediately with a `recon_id` and `status: "running"`; that
response means the work was dispatched, not completed. Save the id, then poll
`get_recon_status` until its status is `complete` or `failed`. While it is
running, status reports no completed answer to consume. Once terminal, call
`get_recon_results` with the same id to retrieve the raw result for each agent.

```text
recon(query, paths, num_agents) -> recon_id
repeat:
  get_recon_status(recon_id)
get_recon_results(recon_id) -> one raw result per agent
```

A failed recon still reports its recorded failure reason through the terminal
status/results response. Do not start planning from the initial dispatch
response, and do not treat a successful dispatch as evidence that the question
was answered.
