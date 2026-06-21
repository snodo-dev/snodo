# W5-05c-1: Apply authorized set_model to validators

## Intent
authorized_decisions (set_model JWTs minted by W5-05b) is a write-only
sink — nothing reads it. Implement the validator application path: a
signed set_model record scoped to a validator overrides that validator's
model on the next pass. The signature must be verified (RS256, verify-only)
before applying. This closes the validator half of model fluidity.

Coder kill+respawn is W5-05c-2 — NOT this ticket.

## Background (from recon)
- set_model JWT payload: {task_ref, type:"set_model", proposed_model,
  scope ("coder" | "validator:<id>"), justification, resolved_by}
- Written to checkpoint.decisions["authorized_decisions"] as a list of
  JWT strings (authorize_cmd.py:142-149)
- find_adjudicated (decisions.py) is adjudicate-only — won't match
  set_model (different fields). A new lookup is needed.
- ValidatorRunner.run() sets context.model per pass via cascade
  (validators.py:99): v.model or self._default_model or DEFAULT_MODEL

## What to build

### decisions.py — set_model lookup + verify
Add a method on the verify-only issuer (or a helper) that, given the
authorized_decisions JWT list, returns verified set_model overrides:
  find_set_model_overrides(authorized_jwts) -> list of verified payloads
  - For each JWT: verify RS256 signature (public key). Invalid/tampered
    → skip + log (do not apply unverified).
  - Return only payloads where type == "set_model", with their scope and
    proposed_model.
This is the verify gate — an unverified set_model record is never applied.

### engine/validators.py — apply override in cascade
ValidatorRunner needs access to authorized_decisions for the active
session. At run(), before setting context.model:
  1. Load authorized_decisions from the session checkpoint
  2. Get verified set_model overrides (via the verify-only issuer)
  3. For the current validator v, look for a scope match:
     scope == f"validator:{v.validator_id}"
  4. If a verified override exists for this validator → effective_model
     = override.proposed_model (highest precedence)
  5. Else → existing cascade (v.model or default_model or DEFAULT_MODEL)

Precedence: authorized set_model override > v.model > default_model >
DEFAULT_MODEL.

How ValidatorRunner gets the session/authorized_decisions: it currently
takes completion_fn + default_model + validator_config at construction.
It needs the active session's authorized_decisions. Determine the
cleanest wiring — likely pass the session checkpoint decisions (or a
read accessor) into run(), consistent with how decision_records reach
the policy evaluator. Read how policy.py gets decision_records and follow
that pattern. Do NOT make ValidatorRunner reach into the filesystem
itself — pass the data in, same as the existing decision-record flow.

### Verify-only issuer construction
Reuse the verify_only_issuer() already constructed in the engine
(loop.py:167) — same public-key verifier used for adjudicate. set_model
verification uses the same issuer.

## Acceptance criteria
- A verified set_model record scoped to validator:X overrides X's model
  on the next pass
- An unverified/tampered set_model record is NEVER applied (skipped + logged)
- Precedence correct: override > v.model > default_model > DEFAULT_MODEL
- A set_model scoped to "coder" is IGNORED by the validator path (that's
  W5-05c-2)
- No set_model record → existing cascade unchanged
- ValidatorRunner does not read the filesystem directly — data passed in
- Adjudicate path (decision_records) unaffected

## Testing
- Unit: verified validator-scoped set_model → validator uses new model
- Unit: tampered set_model JWT → not applied, validator uses cascade
- Unit: coder-scoped set_model → validator path ignores it
- Unit: no set_model → cascade unchanged (v.model / default / DEFAULT_MODEL)
- Unit: precedence — override beats v.model
- Unit: find_set_model_overrides verifies signature, skips invalid
- Full suite passes clean

## Constraints
- Read engine/validators.py (run, cascade), engine/loop.py (verify_only_issuer
  construction, how decision_records reach policy), engine/policy.py (the
  decision-record consumption pattern to mirror), infrastructure/decisions.py
  (verify path, find_adjudicated as reference), authorize_cmd.py (set_model
  payload shape) before touching anything
- set_model records MUST be signature-verified before application — reuse
  the verify-only issuer, never apply an unverified record
- ValidatorRunner takes data in, does not read the filesystem
- Coder application is W5-05c-2 — validator-scoped only here; coder-scoped
  records are ignored by this path
