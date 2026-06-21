# Wave 12 P0: Task branch isolation

## Intent
Every snodo task executes on an isolated git branch. Main is never
touched directly by the engine. The human controls what merges to
main via PR. This implements trunk development with AI-assisted
short-lived branches.

## Branch naming
task/{task_id}/{slug}
  - task_id: the actual task ID from the engine (task_a1b2c3 format)
  - slug: first 5 words of spec, lowercased, hyphenated, alphanumeric only
  - example: task/task_a1b2c3/add-jsdoc-getcookie-function

## What to build

### 1. GitMCP — add checkout_branch (mcp/git.py)
Add a standalone checkout method alongside the existing create_branch:
  checkout_branch(name: str) → None
    git checkout {name}
  Raises GitError if branch does not exist.

Existing create_branch does git checkout -b (create+checkout).
checkout_branch is for switching to an existing branch (retry path).

### 2. Protocol schema — add ExecutionConfig (compiler/models.py)
Add to Protocol model:
  execution: ExecutionConfig = ExecutionConfig()

  class ExecutionConfig(BaseModel):
      max_retries: int = Field(default=3, ge=0, le=10)
      branch_ttl_days: int = Field(default=7, ge=1, le=30)
      branch_prefix: str = Field(default="task")

### 3. Engine — branch creation before execute (engine/loop.py)
In _execute_node or _default_executor, AFTER pre-validators pass
and BEFORE coder.implement() is called:

  branch_name = f"task/{task.id}/{_slugify(task.spec)}"
  if git_mcp:
      if branch_exists(branch_name):  # retry path
          git_mcp.checkout_branch(branch_name)
      else:  # new task
          git_mcp.create_branch(branch_name)

Add helper: _slugify(spec, max_words=5) → str
  first 5 words, lowercase, hyphenated, alphanumeric only

Add helper: branch_exists(name) → bool
  check git_mcp.repo.heads for branch name

### 4. Engine — skip engine operations for OpenCodeAdapter (engine/loop.py)
In _default_executor, after coder.implement() returns:

  if not getattr(coder, "skip_engine_commit", False):
      # write files via workspace_mcp
      for file_op in code_artifact.files:
          workspace_mcp.write_file(...)
      # stage and commit
      git_mcp.stage_files(artifact_paths)
      git_mcp.commit(f"feat: {task.spec}")

### 5. OpenCodeAdapter — set skip flags (coders/opencode_adapter.py)
Add class attributes:
  skip_engine_commit: bool = True
  skip_workspace_write: bool = True

OpenCode writes files directly and commits. Engine must skip both
the workspace_mcp write and the git commit to avoid double-apply.

### 6. snodo init — require git repo (cli/commands/init_cmd.py)
Before creating .snodo/, check for .git in project root or any
parent (same walk-up as resolve_project_root):
  If not found:
    print("Error: snodo requires a git repository. Run git init first.")
    exit(1)

## Acceptance criteria
- snodo run creates branch task/{task_id}/{slug} before execute
- coder executes on that branch (OpenCode sees it as HEAD)
- OpenCode commits on the branch, engine skips its own commit
- Engine skips workspace_mcp writes for OpenCodeAdapter
- post-validators run after the branch commit
- branch remains after task (success or failure) — human raises PR
- snodo init fails loudly in non-git directories
- All existing tests pass
- New tests:
  - branch created with correct name format before execute
  - skip_engine_commit=True skips stage+commit
  - skip_workspace_write=True skips workspace_mcp writes
  - checkout_branch switches to existing branch
  - _slugify generates correct slug from spec
  - init fails in non-git directory

## Constraints
- Read engine/loop.py (_default_executor, _execute_node),
  mcp/git.py (create_branch, existing git ops),
  coders/opencode_adapter.py (implement, class attributes),
  compiler/models.py (Protocol model),
  cli/commands/init_cmd.py before touching anything
- Do not break the LiteLLMAdapter path — skip flags are only on
  OpenCodeAdapter
- Branch creation must happen after pre-validators pass, never before
- File lock (.snodo/run.lock) is V1 concurrency protection —
  not in this ticket (P2)
- Retry mechanism (--retry flag, task_failure_context) is P1 —
  not in this ticket
