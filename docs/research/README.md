# Research

## Paper

**Snodo: Protocol-Governed Software Development with AI Agents**

Under review at *ACM Transactions on Software Engineering and Methodology* (TOSEM). arXiv link pending acceptance.

## Claims → code map

The paper's architectural claims are grounded in the codebase. Each invariant and enforcement mechanism maps to a specific module and test suite.

| Paper claim | Code module | Tests |
|------------|-------------|-------|
| WF1 — Mode separation (disjoint tool sets) | `snodo/compiler/verifier.py:check_wf1()` | `tests/compiler/test_models.py` |
| WF2 — Role uniqueness | `snodo/compiler/verifier.py:check_wf2()` | `tests/compiler/test_models.py` |
| WF3 — Validator coverage | `snodo/compiler/verifier.py:check_wf3()` | `tests/engine/test_loop.py` |
| WF4 — Policy completeness | `snodo/compiler/verifier.py:check_wf4()` | `tests/compiler/test_models.py` |
| WF5 — Constraint consistency | `snodo/compiler/verifier.py:check_wf5()` | `tests/compiler/test_models.py` |
| INV1 — JWT token integrity | `snodo/infrastructure/tokens.py` | `tests/properties/test_invariants.py` |
| INV2 — Mode boundary (MCP servers) | `snodo/mcp/server.py` | `tests/mcp/test_server.py` |
| INV3 — Blocker unconditional halt | `snodo/engine/policy.py:113-123` | `tests/engine/test_policy.py` |
| INV4 — Hash-chained audit | `snodo/infrastructure/audit.py` | `tests/infrastructure/test_session.py` |
| INV5 — Session resumability | `snodo/infrastructure/session.py` | `tests/infrastructure/test_session.py` |
| 2+N reference protocol | `snodo/protocols/templates/2+n.yml` | `tests/e2e/test_2plus_n_journey.py` |
| Kleene closure (recursive subtasks) | `snodo/engine/loop.py` | `tests/engine/test_loop.py` |

## Empirical studies

The Wave 8 studies (`studies/`) produce the paper's Section 5 figures:

| Study | Section | What it shows |
|-------|---------|---------------|
| [policy_monte_carlo](../studies/policy_monte_carlo/notebook.py) | 5.1 | False-block vs false-pass trade-off curves across the four disagreement policies, using the real PolicyEvaluator |
| [detection_probability](../studies/detection_probability/notebook.py) | 5.2 | Monte Carlo validation of the paper's quorum-miss formula and structural-vs-behavioral failure rate bounds |
| [byzantine_robustness](../studies/byzantine_robustness/notebook.py) | 5.3 | Adversarial validator sensitivity — quantifying the design cost of INV3's unconditional blocker semantics |
| [overhead_benchmarks](../studies/overhead_benchmarks/notebook.py) | 5.4 | Real governance overhead: per-operation latency + end-to-end governed-vs-ungoverned comparison |

All studies are deterministic (seeded RNG) and produce paper-styled SVG figures + CSV data. Run with `make studies` from the repository root.

## Hypothesis property tests

130,000+ examples tested under Hypothesis with zero invariant violations committed to the audit log (`studies/hypothesis_paper_run.log`). Key properties: JWT tampering detection, WF1 tool disjointness, policy HALT on any blocker, and protocol templates pass all WF1-WF5 checks.
