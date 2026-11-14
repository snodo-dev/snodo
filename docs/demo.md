# scikit-learn bug-fix demo

The `scikit-learn-26194` demo runs one public bug-fix task against a model you
choose. Each arm starts from the task's recorded scikit-learn base commit in its
own clone. Snodo initializes the clone with the shipped `bugfix-surgeon`
protocol, then runs the issue task. This is a demonstration of the workflow and
its records; it is not a supported workflow for untrusted repositories. Snodo's
initialization asks you to acknowledge that the repository is trusted before
proceeding.

## Run it

From the repository root, run the script with a fully qualified model string.
For a hosted OpenAI model:

```bash
python demo/run_demo.py --model openai/gpt-4o
```

For a free model available through OpenRouter, for example:

```bash
python demo/run_demo.py --model openrouter/deepseek/deepseek-chat
```

For a local Ollama model:

```bash
python demo/run_demo.py --model ollama/llama3.2:3b
```

The demo uses the LiteLLM coder by default. The model identifier forms follow
the [model provider guides](providers/openai.md),
[OpenRouter guide](providers/openrouter.md), and
[Ollama guide](providers/ollama.md); configure each provider and its credentials
as described there. Local Ollama models require a running local Ollama service
and the model to be available to it.

The repository metadata currently identifies Snodo as version `0.28.3`. The
runner uses the current checkout's Snodo unless you request an installation
version explicitly with `--snodo-version`:

```bash
python demo/run_demo.py --model openai/gpt-4o --snodo-version 0.28.3
```

The complete command form is:

```text
python demo/run_demo.py --model <provider/model> [--arm governed|bare|both] [--workdir DIR] [--snodo-version VERSION] [--coder NAME]
```

`--arm` selects one arm or both; `both` is the default. `--workdir` chooses
where the run workspaces and outputs are kept. `--coder` selects the coder
backend; see [Coder backends](coders.md) for available names and requirements.
For full Snodo command details, see the [command reference](command-reference.md).

## The two arms

The **governed** arm uses the shipped `bugfix-surgeon` protocol. The **bare**
arm is built from that same protocol: its producer mode keeps only the always-pass
`triage` pre-execute validator, removes the `minimal-diff` reviewer, and sets
recovery depth to zero. It therefore runs without a validator judging the patch
and without recovery. Each arm gets its own clone at the same recorded base
commit, so their task runs start from the same source revision.

The shipped protocol sends the raw issue to the coder without spec-authoring.
Its `triage` validator is a no-op that always passes. After execution, the
`minimal-diff` reviewer reads the patch and judges minimality and localization,
warning about unrelated, sprawling, or out-of-scope changes. This reviewer is
the protocol's check that judges the patch. The template does not run the test
suite and does not require new tests; correctness verification is separate.

## Reading the outputs

The runner writes a plain-text report and a JSON record for each arm. The report
presents the arm and model, the run outcome and timing, usage when available,
validator findings, fail-to-pass verification, and patch information. The
versioned record (`snodo.demo-run-record.v1`) contains the arm, model, task ID,
outcome, halt type, wall time, input and output tokens, cost and its source,
worktree path, and validator findings. Unavailable measurements are represented
as unavailable or null values.

The task comes from public SWE-bench data, and its fix is public. A model may
have seen the issue or its fix during training, so treat any run as a
demonstration rather than a benchmark. The task and public fix also mean the
run should not be interpreted as a blind evaluation.

Fail-to-pass verification runs only when Docker and the SWE-bench harness are
available. If Docker is missing (or the required harness is unavailable), the
runner skips verification and says so in the report; the demo can still provide
the run, patch, and per-arm records.
