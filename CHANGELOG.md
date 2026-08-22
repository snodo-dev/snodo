# Changelog

All notable changes to snodo will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
snodo uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Fixed

- The Kleene-closure driver (`run_to_closure`) no longer reports graph
  exceptions (coder/network/DB failures) as `resolved`. `resolved` now requires
  positive completion evidence (`is_complete`); a graph exception, a non-dict
  result, or a missing completion signal is reported as `internal_error` with a
  non-zero exit code and a `recovery_internal_error` audit event. LangGraph
  control-flow exceptions (`GraphInterrupt`/`GraphBubbleUp`) are propagated to
  the caller rather than swallowed.
- `snodo run` now emits the `--- STRUCTURED HALT PAYLOAD ---` block on the
  closure path (the primary execution path). The payload is built by the engine
  (single source of truth) and has `final_decision` equal to `halt_type`, using
  the canonical vocabulary (`escalate` / `blocker` / `validator_error` /
  `internal_error`). `validator_error` and `internal_error` no longer advise
  `snodo authorize`. The legacy single-invocation stream path was removed.

---

## [0.1.0] — 2026-06-01

Initial public release.

### Core protocol engine
- Mode-based capability separation (producer, reviewer, planner)
- Validator quorums with disagreement policies (unanimous, majority, quorum, any)
- JWT-backed single-use validation tokens (WF1–INV5 invariant set)
- Session resumability with file-backed checkpointing
- ESCALATE resolution: halt → resolve → resume pattern
- Constraint predicate framework with two-phase evaluation
- Protocol adherence validator deriving mode profiles from operational primitives

### Protocol templates
- `solo`, `team`, and `2+n` templates ship with the package

### Interfaces
- CLI (`snodo`) with full command surface
- MCP server (`snodo serve`) for AI agent integration
- TUI dashboard (`snodo dashboard` / `snop`)

### Studies
- Policy Monte Carlo study
- Detection probability study
- Overhead benchmarks
- Byzantine robustness study

---

[0.1.0]: https://github.com/snodo-dev/snodo/releases/tag/v0.1.0
