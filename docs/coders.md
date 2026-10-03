# Coder Backends

snodo executes protocol tasks under interchangeable code generation backends (**coders**). The coder generates code, while snodo enforces protocol governance, runs validation gates, and maintains an append-only audit log.

![Every snodo run gets its own git worktree on a task branch off main; the coder and gates run inside it, and it merges back on success or is retained on failure.](assets/worktree.svg)

*Isolation comes first and is not the coder's business. A task never runs in your
working tree unless you ask for that with `--no-isolation`.*

![Three coder mechanisms write into the same task worktree and converge on one identical post-execute gate and merge path.](assets/coder-paths.svg)

*All three write into that same worktree. What differs is which process boundary
the write crosses and who makes the commit — everything after is identical.*

## Supported and External Coders

| Coder (`--coder`) | Description | Type | Requirements | Authentication |
|---|---|---|---|---|
| `litellm` *(default)* | Direct LLM completions via LiteLLM (~100+ providers) | Engine-Managed | Python `litellm` (built-in) | Provider API keys for the **validators**, which always run through LiteLLM (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc. or `snodo config add`) |
| `openai` | LiteLLM-backed coder using the OpenAI-native message format; selected for OpenAI-style model prefixes | Engine-Managed | Python `litellm` (built-in) | Same provider credentials as `litellm` |
| `anthropic` | LiteLLM-backed coder for Anthropic models | Engine-Managed | Python `litellm` (built-in) | Same provider credentials as `litellm` |
| `gemini` | LiteLLM-backed coder for Google Gemini models | Engine-Managed | Python `litellm` (built-in) | Same provider credentials as `litellm` |
| `opencode` | OpenCode server running in Docker container over HTTP | In-Place Container | Docker daemon running; image `opencode:latest` | OpenCode config/env variables inside container |
| `opencode-cli` | Host `opencode run` CLI invocation | In-Place Host CLI | `opencode` CLI on PATH | `opencode auth login` or host provider env vars (`OPENROUTER_API_KEY`, etc.) — the coder authenticates against your own subscription |
| `codex-cli` | OpenAI Codex CLI (`codex exec`) | In-Place Host CLI | `codex` CLI on PATH | `codex login` — uses your Codex subscription |
| `claude-cli` | Claude Code CLI (`claude -p`) | In-Place Host CLI | `claude` CLI on PATH | `claude auth login` — uses the CLI's own login |
| `agy` | Antigravity CLI (`agy -p`) host invocation | In-Place Host CLI | `agy` CLI on PATH | `agy login` / Google Cloud host credentials — the coder authenticates against your own subscription |
| `mock` | Deterministic stub for dry-runs and testing | Stub | None | None |

`openai`, `anthropic`, and `gemini` are provider-specific LiteLLM adapters, not
separate CLI integrations. They use the same engine-managed tool loop as
`litellm`; choosing one selects the corresponding adapter and provider model
family. `litellm` remains the generic fallback and supports the broader provider
catalog.

### Authentication: keys are for the validators

Provider API keys are a requirement of the **validators**, which always run
through LiteLLM — not of the coder. The coder need not use one: `opencode-cli`,
`codex-cli`, and `agy` authenticate against the operator's own subscription, so no provider
key is spent on writing the code. `claude-cli` likewise uses Claude Code's own
login rather than Snodo's Anthropic API key. `--mock` needs nothing at all.

Beyond the built-in catalog (Anthropic, OpenAI, Google, OpenRouter, DeepSeek and
Cloudflare Workers AI), any OpenAI-compatible endpoint works by declaring a
provider block with `base_url` and `litellm_provider: openai` — that is the
mechanism, and it covers Ollama Cloud, a local Ollama or `llama.cpp` server,
vLLM, LM Studio, and self-hosted gateways. A local endpoint that needs no key is
not asked for one.

## Model Role Separation: Judging vs Execution

- **`-m` / `--model` sets the JUDGING model**: The model passed via `-m` (e.g. `-m claude-3-5-sonnet` or `-m deepseek/deepseek-v4`) is resolved through LiteLLM for **validators** (pre-execute and post-execute gates) and the **classifier** (intent routing).
- **External CLI coders use their own model catalogs**: Host CLI tools like `agy`, `codex-cli`, or `opencode-cli` maintain their own internal model catalogs and CLI settings. Passing a judging model identifier to an external CLI coder is omitted so the CLI falls back to its own default/last-selected model.
- **Explicit Coder Model Override**: To specify a coder's model explicitly while keeping `-m` for validators, prefix the model string with the coder's namespace:
  ```bash
  snodo run "implement feature" --coder agy --model agy/gemini-2.5-pro
  snodo run "implement feature" --coder opencode-cli --model opencode-cli/claude-3-7-sonnet
  snodo run "implement feature" --coder codex-cli --model codex-cli/gpt-5-codex
  snodo run "implement feature" --coder claude-cli --model claude-cli/sonnet
  ```

## Coder Selection Precedence

`resolve_coder_name()` selects the coder in this order:

1. **Explicit Mock Flag**: `--mock` / `use_mock_coder=True` (always returns `'mock'`).
2. **Explicit CLI Flag**: `--coder <name>` (e.g., `snodo run "task" --coder agy`).
3. **Initial protocol mode**: `coder: <name>` from the mode named by `initial_mode` in `.snodo/protocol.yml`.
4. **Model Prefix Mapping**: Inferred from model prefix: `claude-cli/` → `claude-cli`, `codex-cli/` → `codex-cli`, `opencode-cli/` → `opencode-cli`, `opencode/` → `opencode`, `agy/` → `agy`, `gpt`/`o1`/`o3` → `openai`, `claude` → `anthropic`, `gemini`/`google/` → `gemini`.
5. **Default Fallback**: `litellm`.

For a normal `snodo run`, graph construction resolves the mode-level coder from
the protocol's `initial_mode`, even if execution has entered or resumed another
current mode. An explicit `--coder` overrides that mode setting; `--mock` has
highest priority. Prefix routing applies only when neither is set:
`claude-cli/`, `codex-cli/`, `opencode-cli/`, `opencode/`, and `agy/` select their named
backends; `gpt`, `o1`, or `o3` select `openai`; `claude` selects `anthropic`;
and `gemini` or `google/` select `gemini`. Otherwise the coder is `litellm`.

### Availability and coder settings

Before dispatching a coder, snodo checks its declared runtime requirements in
the process that will invoke it. If a required executable is missing, the run
is stopped with the missing binary and the adapter's install/remediation hint.
For example, host CLI coders require their CLI on `PATH`, while `opencode`
requires Docker. `litellm`, the provider-specific adapters, and `mock` do not
declare an external executable requirement.

Explicit `llm.coder.*` settings that the selected backend does not honour are
reported at info level when that coder is selected. The message names the
setting, its value, and the settings the coder does honour; this is advisory and
does not reject the configuration. Unset defaults are not reported. Current
settings honoured by each backend are:

| Backend(s) | `llm.coder.*` settings honoured |
|---|---|
| `litellm`, `openai`, `anthropic`, `gemini` | `model`, `temperature`, `max_tokens`, `max_tool_turns` |
| `opencode` | `model`, `workspace`, `container`, `sandboxed` |
| `opencode-cli`, `codex-cli`, `claude-cli`, `agy` | `model`, `timeout_seconds`, `workspace` |
| `mock` | `mock_files` |

The provider-specific adapters inherit the LiteLLM settings. The in-place host
CLI adapters share the subprocess adapter's settings. Settings supplied through
a mode's `coder_config` are also checked and reported by their config key.

## In-Place Coders vs `litellm`

External coders (`opencode`, `opencode-cli`, `codex-cli`, `claude-cli`, `agy`) inherit `InPlaceCoderAdapter` (`skip_engine_commit = True`, `skip_workspace_write = True`):

- **In-Place File Writes**: External coders edit files directly in the workspace working tree.
- **Commit Ownership**: The adapter stages and commits changes to git upon completion (`InPlaceCoderAdapter._commit_changes()`), advancing `HEAD` so post-execute validators reviewing `git diff HEAD~1..HEAD` see the exact change produced.
- **`.snodo/` Mutation Guard**: Any attempt by an in-place coder to modify the `.snodo/` directory triggers a `SnodoMutationError` and halts execution as a `snodo_mutation_blocked` blocker (ADR 027).
- **No Per-Turn Usage or Token Records**: Per **ADR 034**, the absence of turn-by-turn usage and token metrics for external coders is a **stated decision (non-goal)**, not an attestation gap. Token and cost data reside in per-job operational telemetry (`state.json` via `snodo meta`), whereas snodo's hash-chained audit trail attests to governance decisions and verification evidence across all coders.

`claude-cli` uses Claude Code's documented `-p --output-format stream-json
--verbose` interface and starts in `bypassPermissions` so unattended runs can
edit and execute tools. It limits setting discovery to the user's settings
(`--setting-sources user`), avoiding project-local Claude settings and hooks.
Its `claude-cli/<model>` namespace is stripped before `--model`; unprefixed
judging models are not passed to Claude Code. Reported input/output/cache usage,
cost, and served model are captured when Claude Code supplies them. Authentication
is managed by the installed CLI and no credential is passed by Snodo. This is
an in-place host process, not an OS/container sandbox: it runs within the task
worktree and shares the host account's access.

## Adding a New Coder Adapter

New host CLI coder adapters inherit `SubprocessCoderAdapter` (`snodo.coders.subprocess_adapter`), which provides shared subprocess execution, prompt construction, git diff readback, and artifact construction.

Creating a new CLI adapter requires specifying four class attributes and implementing `_build_argv`:

```python
from typing import List
from snodo.coders.subprocess_adapter import SubprocessCoderAdapter

class CustomCoderAdapter(SubprocessCoderAdapter):
    binary: str = "custom-coder"
    model_prefix: str = "custom/"
    install_hint: str = "Install custom-coder: https://example.com/install"

    def _build_argv(self, prompt: str, project_root: str, model: str) -> List[str]:
        argv = [self.binary, "run", "--dir", project_root, prompt]
        if model:
            argv.extend(["--model", model])
        return argv
```

Registering the adapter class in `CODER_REGISTRY` (`snodo/coders/__init__.py`) automatically exposes it to `--coder`, enables model prefix routing, and includes it in the adapter conformance test suite (`tests/coders/test_adapter_conformance.py`).
