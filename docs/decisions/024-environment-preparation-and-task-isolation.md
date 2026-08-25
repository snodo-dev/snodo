# ADR 024 — Environment Preparation Before Task Execution

## Status
Accepted

## Context
Every task in snodo runs inside a fresh git worktree (`.snodo-worktrees/task_<id>`) for parallel filesystem isolation. A clean worktree checkout does not contain un-versioned dependencies or build artifacts (`node_modules`, `.venv`, `target`, `vendor`).

Previously, snodo ran configured test commands in fresh worktrees without installing dependencies first. In projects without vendored dependencies (e.g. Node or Python projects), test execution exited with code 127 (missing binary). The validator reported this operational failure as a test failure, causing snodo to enter recovery and spend multiple fix attempts trying to code around a missing binary.

The prior workaround folded package installation into the test command (e.g. `npm ci && npm test`), which every consumer had to rediscover.

## Decision

1. **Automatic Ecosystem Environment Preparation**:
   An environment preparation step executes between worktree creation and task validation/execution. Marker files in the worktree root auto-detect the project ecosystem and execute lockfile-driven dependency installation:
   - Node: `package-lock.json` (`npm ci`), `pnpm-lock.yaml` (`pnpm install --frozen-lockfile`), `yarn.lock` (`yarn install --frozen-lockfile`), `bun.lockb`/`bun.lock` (`bun install`), `package.json` (`npm install`).
   - Python: `uv.lock`/`pyproject.toml` (`uv sync`), `requirements.txt` (`pip install -r requirements.txt`).
   - Rust: `Cargo.lock`/`Cargo.toml` (`cargo fetch`).
   - Go: `go.sum`/`go.mod` (`go mod download`).

2. **Schema Override (`execution.prepare_command`)**:
   Protocols may declare an explicit prepare command under `execution.prepare_command` in `protocol.yml`, overriding auto-detection. Setting `prepare_command: "none"` explicitly disables preparation.

3. **Operational Fault Classification**:
   Failure of an environment preparation command is an operational fault (`validator_error`). It is never recorded as a validator judgement and never enters recovery (no recovery subtasks are spawned).

4. **Skipping Unneeded Preparation**:
   Preparation is skippable. If an ecosystem's target directory (`node_modules`, `.venv`, `target`, `vendor`) already exists in the worktree, or if no markers/commands match, preparation is skipped cleanly without error.

5. **Trust Decision**:
   Executing package installation commands (`npm ci`, `uv sync`, etc.) before every task is both a computational cost and a trust decision. Package manager scripts (such as `postinstall` hooks) execute repository code. Under ADR 014 (Trusted-Repository Threat Model), running environment preparation is explicitly governed by the repository trust model accepted at `snodo init`.

6. **Shared Cache Concurrency Decision**:
   We evaluated whether snodo should maintain a custom shared dependency cache across worktrees. Mutating a single shared dependency directory (e.g. symlinking a shared `node_modules` or `.venv`) across concurrent worktrees without lock isolation is unsafe: concurrent tasks could mutate or prune dependencies simultaneously, corrupting filesystem state.
   Instead, snodo relies on standard package manager global caches (`~/.npm`, `~/.cache/uv`, `~/.cargo`, `$GOPATH/pkg/mod`). These package manager caches implement process-safe atomic file locking and content-addressable storage. Running lockfile installs (`npm ci`, `uv sync`) per worktree leveraging these global caches provides fast, process-safe preparation across concurrent tasks without introducing fragile custom symlink caches.

## Consequences
- Clean worktree checkouts automatically install dependencies before running pre-execute or post-execute validators.
- Existing protocols whose test commands include `npm ci && npm test` continue working without double installation or breakage.
- Operational install failures halt immediately with `validator_error` without wasting recovery cycles.
