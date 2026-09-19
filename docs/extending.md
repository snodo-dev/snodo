# Extending Snodo

Four extension points. Each maps to an interface or registry in the codebase. You implement the interface, register your implementation, and reference it from `protocol.yml`.

## 1. Custom validators

### Interface

Subclass `ValidatorBase` (`snodo/validators/context.py:34-48`) and implement two methods:

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

Registration must happen at import time — put it at the bottom of your validator module. If your module is imported (directly or via `snodo.validators.__init__`), the registration fires automatically.

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

The engine dispatches to your validator during `_dispatch_one()` in the orchestration loop (`loop.py:776-804`). The `evaluation_phase` controls when it runs: `pre_execute` before code generation, `post_execute` after, `mode_transition` on mode change.

### Worked example

The test suite includes a complete third-party validator proof (`tests/validators/test_custom_validator.py:32-62`):

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

[ADR 005](decisions/005-protocol-adherence-validator.md) for the design rationale.

---

## 2. Custom predicates

### Interface

Subclass `Predicate` (`snodo/predicates/base.py:37-57`) and implement one method:

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

Note the difference from validators: predicates register **instances**, not classes. Put registration at import time in your predicate module. WF5 verifies that referenced predicate names are registered at protocol load time.

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

Three predicates ship for reference (`snodo/predicates/`):

- `files_in_scope` — verifies all modified files match configured scope paths
- `tests_exist_for_modified` — requires test files for each modified implementation file
- `no_secrets_in_diff` — scans git diff for credential patterns

[ADR 004](decisions/004-constraint-predicate-framework.md) for the design rationale.

---

## 3. Coder adapters

### Interface

Implement `Coder` (`snodo/core/interfaces.py:24-89`):

```python
from snodo.core.interfaces import Coder, TaskSpec, CodeArtifact

class MyCoder(Coder):
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

No registry — coders are not plugin-resolved. Set the coder backend on the mode:

```yaml
modes:
  - mode_id: "producer"
    coder: "litellm"
    coder_config:
      model: "claude-sonnet-4-20250514"
      temperature: 0.7
```

The engine resolves `coder` to an adapter class at graph build time (`loop.py` passes the `coder` parameter to `GraphBuilder`). The `--mock` CLI flag overrides to `MockAdapter`.

[ADR 007](decisions/007-coder-adapter-provider-pattern.md) for the design rationale.

---

## 4. Code-host providers

### Interface

Implement `CodeHostProvider` (`snodo/providers/base.py:16-106`):

```python
from snodo.providers.base import CodeHostProvider

class GitLabProvider(CodeHostProvider):
    def __init__(self, project_root: str = "", metadata: dict | None = None):
        ...

    def create_pr(self, branch: str, title: str, body: str) -> str: ...
    def read_pr_diff(self, pr_number: int) -> str: ...
    def post_review_comment(self, pr_number: int, comment: str) -> str: ...
    def approve_pr(self, pr_number: int) -> str: ...
    def reject_pr(self, pr_number: int, reason: str) -> str: ...
    def merge_pr(self, pr_number: int) -> str: ...
    def read_pr_comments(self, pr_number: int) -> str: ...
```

All seven methods must be implemented. Return types are strings (PR URLs, confirmation messages, JSON payloads). Raise `ProviderError` for failures.

### Registration

Two paths:

**Setuptools entry point** (recommended for pip-installable plugins):

```toml
# pyproject.toml
[project.entry-points."snodo.providers"]
gitlab = "my_package.gitlab:GitLabProvider"
```

The provider registry (`providers/registry.py:178-197`) loads entry points from the `snodo.providers` group.

**Explicit metadata** (for in-project providers):

```yaml
# protocol.yml
metadata:
  provider: "gitlab"
  gitlab_repo: "my-org/my-project"
  gitlab_token: "${GITLAB_TOKEN}"
```

### Resolution order

1. `metadata.provider` if set
2. Auto-detect from git remote URL (`github.com` → GitHub)
3. Entry points in the `snodo.providers` group
4. Fallback to `LocalProvider` (no remote)

### Shipped providers

Two ship: `GitHubProvider` (`snodo/providers/github.py`, backed by PyGithub) and `LocalProvider` (no-op remote, PR operations raise `ProviderError`).

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
- **Providers** are used by `PrMCP` during the `execute` node when PR operations are requested. The provider is resolved at MCP server construction time via `detect_provider()`.

All extensions are referenced from `protocol.yml` — no code changes needed in the engine.
