---
adr: 004
status: Accepted
---

## 004: Constraint predicate framework

- **Status**: Accepted
- **Context**: The original constraint system used boolean expression strings that were parsed and evaluated ad-hoc. Adding a new constraint meant modifying the expression parser. The 2+N reference protocol needed three concrete constraints (files_in_scope, tests_exist, no_secrets_in_diff) that inspect artifacts, diffs, and file paths — not boolean expressions.
- **Decision**: Replace expression-string evaluation with a predicate registry (`predicates/registry.py:15`) where each constraint references a registered predicate name and a params dict. Predicates implement `Predicate.evaluate(context) → PredicateResult`. Registration is self-registering (module import triggers registration via `_default_registry`). The engine evaluates constraints per phase: pre_execute constraints gate execution, post_execute constraints check artifacts.
- **Consequences**: Adding a constraint is now a matter of writing a predicate class, registering it, and referencing it in the protocol YAML. Three predicates ship: `files_in_scope` (path-matching), `tests_exist_for_modified` (artifact/test pairing), `no_secrets_in_diff` (credential-pattern scanning). WF5 verifies that referenced predicate names are registered at load time.
- **Alternatives considered**: Keep expression parser — rejected; string-parsed constraints are fragile and opaque. Hard-code constraints — rejected; protocols need custom constraints.
- **Evidence**: Audit log entry 53 (2025-05-27, Task 7.8), commit `b66fa59a`; `predicates/registry.py:15-52`, `predicates/__init__.py:8-9`.

---
