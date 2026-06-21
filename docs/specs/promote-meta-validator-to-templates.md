# Promote meta-validator (spec-discipline) to protocol templates

## Intent
The meta-validator was proven in live dogfooding: it blocked a
code-prescriptive task_spec, the orchestrator could not adjudicate around
it (HI-CTRL boundary held), and it caught a verbatim-embedded workaround
too. It enforces the orchestrator's contract — produce intent-level specs,
not transcribed code. Promote it from a hand-added entry into the protocol
templates so new projects get spec-discipline enforcement by default.

## Background
The validator was added by hand to the solo protocol and ran as
validator_type "architecture" at pre_execute. It judges the TASK SPEC
(not coder output): allows signatures/types/constraints/required values,
prohibits literal function bodies and transcribed implementation, with a
~40% literal-code guideline as the rejection anchor. All validators are
fuzzy LLM judges (addressed in the paper); HI-CTRL handles disputes via
adjudication.

## What to do

### 1. Find the protocol templates
Locate where snodo init / protocol scaffolding generates the default
protocol.yml(s). There may be multiple templates (solo, team, 2+n —
the golden tests reference solo/team/2+n). Identify each template that
should carry spec discipline.

### 2. Add the meta-validator to the appropriate template(s)
Add this validator definition to the templates (verbatim criteria —
this is the proven wording):

  - validator_id: "meta-spec"
    validator_type: "architecture"
    evaluation_phase: "pre_execute"
    criteria:
      - "The task spec must define INTENT and CONSTRAINTS — what to build and the boundaries it must respect — and leave implementation decisions to the coder. A spec describes behavior and contracts; it does not transcribe the implementation."
      - "ALLOWED in a spec (these are constraints, not implementation): function and method signatures, exported names, type and interface shapes, behavioral requirements (what it must do, what inputs it must reject), hard constraints (algorithm choice such as HS256, framework limits, dependency restrictions), specific required values that are genuine requirements (e.g. a 7-day expiry, a max-age), acceptance criteria, and occasional short code hints that clarify a constraint."
      - "PROHIBITED in a spec (this is the coder's job): literal function bodies, transcribed implementation logic, exact statement-by-statement code the coder should author, regex literals and exact API-call sequences presented as the implementation, and any spec that leaves the coder no implementation decisions to make."
      - "Guideline for judgment: if more than roughly 40% of the spec is literal implementation code rather than intent, contracts, and constraints, the spec is code-prescriptive and must be rejected as a blocker. The orchestrator must re-issue the task as intent plus constraints, not as transcribed code."
      - "A correctly-scoped spec leaves the coder room to author the implementation while still being unambiguous about what 'done' means. Reject specs that read as code wearing a spec's clothes."

  And add "meta-spec" to each template mode's `validators:` list
  (alongside security/architecture/quality).

### 3. Golden files
The golden tests (solo/team/2+n snapshots) will now differ because the
template changed. Regenerate the golden files to match the new templates
that include meta-spec. Confirm the snapshot tests pass after regeneration.

### 4. Naming consistency
In the dogfood, the validator ran under validator_id "meta-spec" but the
blocker showed validator_type "architecture" labeled as
"architecture-validator". Use validator_id "meta-spec" consistently. Do
NOT introduce a new validator_type yet (architecture works, is proven);
promoting to a first-class "protocol_adherence" type is a later refinement.

## Acceptance criteria
- meta-spec validator present in the appropriate protocol template(s)
- "meta-spec" in those templates' producer (and any other relevant) mode
  validator lists
- snodo init on a fresh project produces a protocol.yml that includes
  meta-spec
- Golden files regenerated; snapshot tests pass
- Criteria wording matches the proven version verbatim

## Testing
- Unit/snapshot: golden template tests pass with the new templates
- Confirm a freshly-init'd project's protocol.yml contains meta-spec
- Full suite passes clean

## Constraints
- Read the protocol template source (wherever snodo init scaffolds
  protocol.yml), the golden snapshot tests (solo/team/2+n), and the
  current solo protocol.yml that has meta-spec working, before editing
- Criteria wording is PROVEN — copy verbatim, do not paraphrase
- Do not introduce a new validator_type — keep "architecture" (proven)
- Regenerate goldens via the established mechanism (SNODO_UPDATE_GOLDENS
  or equivalent) — do not hand-edit golden files
