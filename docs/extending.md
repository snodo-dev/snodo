# Extending Snodo

Four extension points. Each maps to an interface or registry in the codebase. You implement the interface, register your implementation, and reference it from `protocol.yml`.

## 1. Custom validators

### Interface

Subclass `ValidatorBase` (`snodo.validators.context`) and implement the required methods:

```python
from snodo.validators.context import ValidatorBase, ValidatorContext
from snodo.core.interfaces import ValidatorResult

class MyValidator(ValidatorBase):
    @classmethod
    def registered_type(cls) -> str:
        return "my_type"

    def evaluate(self, context: ValidatorContext) -> ValidatorResult:
        ...
```

`registered_type()` returns the string you'll use in `protocol.yml` as the `validator_type`. `evaluate(context)` receives a `ValidatorContext` with the task, current mode, protocol, artifacts, working directory, and an optional LLM completion function — read what you need.

### Registration

Register with the default registry:

```python
from snodo.validators.registry import _default_registry
_default_registry.register("my_type", MyValidator)
```

For a validator that handles multiple types, use `register_compound`:

```python
_default_registry.register_compound({"my_type", "my_alias"}, MyValidator)
```

Register an installable validator package with the `snodo.validators` entry-point group. The entry-point name becomes the protocol's `validator_type`; its value must load a `ValidatorBase` subclass:

```toml
# pyproject.toml
[project.entry-points."snodo.validators"]
my_type = "my_package.validator:MyValidator"
```

After publishing and installing the package in Snodo's environment, Snodo discovers it automatically. `snodo ready` lists loaded and failed plugins, including load errors. Validators may still use the default registry's `register`/`register_compound` APIs for in-process registrations.

### Wiring into the protocol

```yaml
validators:
  - validator_id: "my_check"
    validator_type: "my_type"
    evaluation_phase: "pre_execute"
    criteria:
      - "Custom check description"
    severity_cap: "blocker"   # optional — cap at "warn" for experimental validators
```

The engine dispatches to your validator through the validator registry. The `evaluation_phase` controls when it runs: `pre_execute` before code generation, `post_execute` after, `mode_transition` on mode change.

### Worked example

The test suite includes a complete custom-validator proof (`tests/validators/test_custom_validator.py`):

```python
class CustomValidator(ValidatorBase):
    def __init__(self, validator_spec: Validator):
        self.validator_spec = validator_spec
        self.validator_id = validator_spec.validator_id

    @classmethod
    def registered_type(cls) -> str:
        return "custom_type"

    def evaluate(self, context: ValidatorContext) -> ValidatorResult:
        if "safe" in context.task.spec.lower():
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="pass",
                justification="Task spec mentions 'safe'.",
            )
        return ValidatorResult(
            validator_id=self.validator_id,
            severity="warn",
            justification="Task spec does not mention 'safe'.",
        )

from snodo.validators.registry import _default_registry
_default_registry.register("custom_type", CustomValidator)
```

[ADR 005](decisions/005-protocol-adherence-validator.md) for the design rationale. See [`docs/coders.md`](coders.md) for coder-backend implementation details.

---

## 2. Custom predicates

### Interface

Subclass `Predicate` (`snodo.predicates.base`) and implement one method:

```python
from snodo.predicates.base import Predicate, PredicateContext, PredicateResult

class MyPredicate(Predicate):
    def evaluate(self, context: PredicateContext, **params) -> PredicateResult:
        # context.artifacts — list of file paths produced so far
        # context.mode — current mode ID
        # context.workspace_mcp — WorkspaceMCP (or None)
        # **params — constraint-specific params from the YAML
        ...
        return PredicateResult(
            passed=True,
            justification="All files in scope",
            evidence={"matched": [...]},
        )
```

Predicates are deterministic — no LLM calls, no write side effects. They must handle both `"governance"` and `"post_validate"` phases (`context.phase`), passing trivially when context is insufficient.

### Registration

```python
from snodo.predicates.registry import _default_registry
_default_registry.register("my_predicate", MyPredicate())
```

Note the difference from validators: predicates register **instances**, not classes. For an installable package, register the instance or subclass in the `snodo.predicates` entry-point group; the entry-point name is the predicate name. The entry point may resolve to an instance or a no-argument `Predicate` subclass:

```toml
[project.entry-points."snodo.predicates"]
my_predicate = "my_package.predicates:MyPredicate"
```

Snodo discovers installed predicate plugins automatically, and `snodo ready` reports loaded plugins and failures. WF5 verifies referenced predicate names at protocol load time.

### Wiring into the protocol

```yaml
global_constraints:
  - constraint_id: "my_check"
    description: "All artifacts must pass my check"
    predicate: "my_predicate"
    params:
      my_param: "value"
    severity: "blocker"
```

Constraints can be placed at three levels: `global_constraints` (every task), `mode.constraints` (per-mode), or `validator.constraints` (per-validator).

### Shipped predicates

Three predicates ship for reference (`snodo.predicates`):

- `files_in_scope` — verifies all modified files match configured scope paths; a task scoped to a module (ADR 041) is instead bounded by that module's declared paths
- `tests_exist_for_modified` — requires test files for each modified implementation file
- `no_secrets_in_diff` — scans git diff for credential patterns

[ADR 004](decisions/004-constraint-predicate-framework.md) for the design rationale.

---

## 3. Coder adapters

### Interface

Implement `CoderAdapter` (`snodo.coders.base`), the adapter-facing alias for the core `Coder` interface (`snodo.core.interfaces`). Most adapters subclass `CoderAdapter`; adapters that execute a host CLI should normally build on `SubprocessCoderAdapter`, and adapters writing directly into the working tree use `InPlaceCoderAdapter`. See the [coder adapter contract](architecture/coder-adapter-contract.md) and [`docs/coders.md`](coders.md) for shared obligations and a host-CLI example.

```python
from snodo.coders.base import CoderAdapter
from snodo.core.interfaces import TaskSpec, CodeArtifact

class MyCoder(CoderAdapter):
    def implement(self, spec: TaskSpec) -> CodeArtifact:
        # spec.description — the task description
        # spec.constraints — declared constraints
        # spec.memory_summary — agent memory context
        # spec.project_context — project-level metadata
        ...
        return CodeArtifact(files=[...])
```

`implement()` is the only abstract method, but it is not the whole contract. `Coder` also declares several optional capabilities as class attributes with defaults, and the engine sets these on every adapter instance **unconditionally** — never behind a `hasattr` guard — so that an adapter's non-support of a capability is a visible default rather than a silently skipped line:

| Attribute | What the engine gives you | Default if you don't override it |
|---|---|---|
| `workspace_mcp` | The workspace the task runs under, when the task runs under one | `None` — no workspace access |
| `progress_callback` | A sink to emit per-turn progress to | `None` — the adapter reports no progress |
| `_job_id`, `_task_id` | Correlation ids for adapter-side logging/telemetry | `""` — unset |
| `skip_workspace_write` | Set `True` if your adapter writes the working tree itself; the executor then does **not** replay the returned artifacts through `WorkspaceMCP` | `False` — the executor writes the returned artifacts |
| `skip_engine_commit` | Set `True` if your adapter (or its base class) owns the commit; the executor then does **not** stage or commit | `False` — the executor commits |

A coder that overrides none of these is a complete, valid adapter: it gets no workspace, reports no progress, and has the executor write and commit its `CodeArtifact` on its behalf. That's a legitimate choice, but make it deliberately — the way to find out otherwise is a task that reports no progress or ignores a workspace it needed.

Opting into `skip_workspace_write` or `skip_engine_commit` does not waive the underlying obligation: an adapter that owns its own commit still has to leave the workspace in a state where what changed is observable and attributable. "The coder produced nothing" is a fault regardless of who commits.

A `CodeArtifact` is a list of `FileArtifact` objects (path, content, action="write"|"delete"). `LiteLLMAdapter` (routes to 100+ LLM backends via litellm) and `MockAdapter` (deterministic stub for testing) are two of the adapters that ship with snodo.

### Wiring

Coder names resolve through `CODER_REGISTRY` in `snodo.coders`. An installable adapter package registers its class in the `snodo.coders` entry-point group; the entry-point name is the coder name used by the protocol:

```toml
[project.entry-points."snodo.coders"]
my_coder = "my_package.coder:MyCoder"
```

The entry point must load a `CoderAdapter` subclass. Snodo discovers installed adapters automatically, and `snodo ready` reports loaded plugins and failures. Built-in coder names are reserved.

```yaml
modes:
  - mode_id: "producer"
    coder: "litellm"
    coder_config:
      model: "claude-sonnet-4-20250514"
      temperature: 0.7
```

The engine resolves `coder` to an adapter class at graph build time. The `--mock` CLI flag overrides to `MockAdapter`. For the in-repository adapter registration pattern and conformance expectations, see [`docs/coders.md`](coders.md).

[ADR 007](decisions/007-coder-adapter-provider-pattern.md) for the design rationale.

---

## 4. Code-host providers

### Interface

Implement the v1 `CodeHostProvider` contract (`snodo/providers/base.py`). Identifiers are opaque strings so they can represent pull requests, merge requests, or another host's change request. The discussion methods exchange JSON containing `title`, `comments`, and `reviews`; each entry has an `author` string and `body` string, and reviews may also have a `state` string.

Change requests opened by Snodo are labelled `snodo` when the code host permits it.

```python
from snodo.providers.base import CodeHostProvider

class GitLabProvider(CodeHostProvider):
    def __init__(self, project_root: str = "", metadata: dict | None = None):
        ...

    def create_change_request(
        self, branch: str, title: str, body: str,
        target_branch: str | None = None,
    ) -> str: ...
    def read_change_request_diff(self, change_request_id: str) -> str: ...
    def post_change_request_comment(self, change_request_id: str, comment: str) -> str: ...
    def approve_change_request(self, change_request_id: str) -> str: ...
    def request_change_request_changes(self, change_request_id: str, reason: str) -> str: ...
    def merge_change_request(self, change_request_id: str) -> str: ...
    def read_change_request_discussion(self, change_request_id: str) -> str: ...
```

All seven methods must be implemented. Return strings (URLs, confirmations, or JSON payloads) and raise `ProviderError` for failures. Provider constructors may accept `project_root` and `metadata` keyword arguments; the registry also supports no-argument constructors. For remote auto-detection, implement `claims_remote(cls, url)`: it receives the complete git origin remote URL as a string (SSH, HTTPS, or scp-style form), and should return whether this provider recognizes that URL. `remote_hosts` is supported as a compatibility fallback for older plugins when the hook does not claim the URL.

The old `create_pr`, `read_pr_diff`, `post_review_comment`, `approve_pr`, `reject_pr`, `merge_pr`, and `read_pr_comments` names are deprecated compatibility wrappers. New providers should implement only the neutral method names.

### Registration

Register an installable provider with the setuptools entry-point group:

**Setuptools entry point** (recommended for installable plugins; register the provider class in the `snodo.providers` group):

```toml
# pyproject.toml
[project.entry-points."snodo.providers"]
gitlab = "my_package.gitlab:GitLabProvider"
```

The entry-point name is the provider name referenced by `metadata.provider`. The registry discovers installed plugins from the `snodo.providers` entry-point group; for remote matching, it calls each plugin's `claims_remote(url)` hook. Plugins that do not yet implement that hook may declare `remote_hosts` as a compatibility fallback. Entry points are not independently selected when no remote match exists. If multiple plugins claim the same URL, the registry selects the first matching entry point returned by Python's entry-point discovery; do not rely on an ordering among competing claims. If an explicitly named provider is unavailable, the error suggests installing `snodo-provider-<name>` with `uv add` or `pip install`.

### Verify your plugin

After packaging and installing the distribution in the same environment as Snodo, run `snodo ready` in a project that uses it. The readiness report lists installed code-host plugins (and plugins that failed to load), then reports which provider the project resolves to and why. For remote auto-detection, use a project whose `origin` URL your `claims_remote(url)` recognizes; the report should say it was detected from that remote host. For explicit selection, set `metadata.provider` in the protocol and readiness reports that it was selected by `metadata.provider`. A plugin listed as installed confirms entry-point loading, while a failed status includes the load error; provider construction failures are also reported. If multiple plugins claim the remote, readiness reports the selected one.

Two working code-host plugins are available as examples to copy. Both implement this neutral contract and register in the `snodo.providers` entry-point group; plugins should ship their own top-level import package rather than adding modules beneath Snodo's `snodo.providers` package:

- **GitHub**: `snodo-provider-github` (import package `snodo_provider_github`) is bundled with the Snodo distribution. It claims remotes on `github.com`. It reads `GITHUB_TOKEN`, `metadata.github_token`, or the configured GitHub key (`snodo config set github <token>`); repository identity can come from `metadata.github_repo` or the remote.
- **GitLab**: `snodo-provider-gitlab` (import package `snodo_provider_gitlab`) is installed separately, for example with `pip install snodo-provider-gitlab`, or used as a workspace package during development. It claims `gitlab.com` remotes and self-hosted GitLab remotes when `GITLAB_HOST` or `GITLAB_URL` is set. It reads `GITLAB_TOKEN` or `metadata.gitlab_token`; `metadata.gitlab_repo` and `metadata.gitlab_url` can configure the project and host.

Explicit provider selection is configured in protocol metadata (also useful for in-project providers):

```yaml
# protocol.yml
metadata:
  provider: "gitlab"
  gitlab_repo: "my-org/my-project"
  gitlab_token: "${GITLAB_TOKEN}"
```

### Resolution order

1. `metadata.provider` if set (provider resolved by name, including installed entry points)
2. Auto-detect from git remote URL: installed entry points in the `snodo.providers` group are checked using `claims_remote(url)`; `remote_hosts` is used only when the hook does not claim the URL
3. Fallback to `LocalProvider` when no provider matches (`local` is the only built-in)

With `execution.delivery: change_request`, Snodo pushes the task branch and then uses this same provider resolution to open a change request targeting the project's active branch. If no installed plugin claims the remote (and `metadata.provider` is not set), resolution falls back to `local`; change-request delivery then fails with a no-provider message. If the selected plugin cannot obtain its token or otherwise fails to construct or open the request, delivery reports the failure and leaves the pushed branch and task unmerged for manual resolution. See the [`execution.delivery` reference](protocol.md#execution-configuration) for configuring delivery modes and their requirements.

### Shipped providers

The `local` provider is built in (no remote; change-request operations raise `ProviderError`). GitHub (`snodo-provider-github`, module `snodo_provider_github`, backed by PyGithub) and GitLab (`snodo-provider-gitlab`, module `snodo_provider_gitlab`, backed by python-gitlab) are discovered as entry-point plugins; GitHub is bundled with Snodo while GitLab is installed separately.

[ADR 007](decisions/007-coder-adapter-provider-pattern.md) for the design rationale.

---

## Where extensions run in the loop

Every extension plugs into the orchestration graph at a specific point:

```
Governance → Validate → Execute → Post-validate → Move-next → Complete
    │           │          │            │
    │     validators    coder     predicates
    │     (pre_execute) adapter   validators
    │                            (post_execute)
 predicates
 (governance)
  providers
  (via PrMCP
  during execute)
```

- **Validators** run in the `validate` node (`pre_execute` phase) or the `post_validate` node (`post_execute` phase). The engine builds a single `ValidatorContext` per pass and dispatches each validator spec through the registry.
- **Predicates** run in the `governance` node (pre-execute constraints) or `post_validate` node (post-execute constraints). The engine builds a `PredicateContext` from `LoopState` and calls `evaluate(context, **params)`.
- **Coders** run in the `execute` node. The engine passes a `TaskSpec` and receives a `CodeArtifact` — file operations are then applied via WorkspaceMCP and committed via GitMCP.
- **Providers** are used by `PrMCP` during the `execute` node when change-request operations are requested, and by `snodo run --from-pr` to prepend discussion and diff context. The provider is resolved via `detect_provider()`.

All extensions are referenced from `protocol.yml` — no code changes needed in the engine.
