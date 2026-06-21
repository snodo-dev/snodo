# W5-05a: Asymmetric RS256 signing foundation for decision records

## Intent
HI-CTRL adjudication is currently configurational, not structural: the
HS256 secret lives in every process (engine, MCP, CLI), so nothing
cryptographically prevents the agent from minting its own decision
records. Migrate to RS256: the CLI holds the private key and is the ONLY
thing that can mint; engine and MCP hold the public key and can only
verify. "The agent cannot self-authorize" becomes a cryptographic fact —
it lacks the private key.

This ticket migrates the EXISTING adjudicate flow to RS256. No new
decision types (authorize command is W5-05b, set_model is W5-05c).

## Threat model (document in code, honestly)
The private key on the filesystem is an INTERIM, intentionally-weak
solution. A co-located orchestrator with filesystem tool access (e.g.
Claude Desktop) can read the key file after a rubber-stamped permission.
This is mitigated by:
- Path-based deterrence: key at ~/.ssh/NO-AGENT/snodo.pem — the path
  name is a human-attention signal at the approval moment
- Process isolation: engine/MCP processes never load the private key
The LONG-TERM hardening path (OS keychain / secret store) is documented
as a TODO. This assumes agent positive intent; it is a deterrent, not a
boundary. State this plainly in a module docstring and a SECURITY note.

## What to change

### Keypair generation
- A helper (infrastructure/, e.g. signing_keys.py) that:
  - Generates an RSA keypair (2048-bit) at:
    ~/.ssh/NO-AGENT/snodo.pem      (private, 0600)
    ~/.ssh/NO-AGENT/snodo.pub.pem  (public)
  - Creates the NO-AGENT dir if absent (0700)
  - Idempotent — does not overwrite existing keys
  - load_private_key() — used ONLY by CLI signing paths
  - load_public_key() — used by verifiers (engine, MCP, CLI)
- snodo init calls keypair generation (alongside existing init work)
- Include a README or marker in NO-AGENT/ explaining the danger and that
  agents must not read it

### infrastructure/decisions.py — RS256
- DecisionRecordIssuer split into two construction modes:
  - Signing issuer: loads private key, can mint + verify (CLI only)
  - Verify-only issuer: loads public key only, can verify, RAISES if
    asked to mint
- issue_record signs RS256 with the private key
- verify_record verifies RS256 with the public key
- Keep _log_event audit hooks intact

### Secret boundary — the crux
- mcp/server.py: construct a verify-only issuer (public key) — no private
  key in the MCP process
- engine/loop.py: construct a verify-only issuer for policy verification
  (engine verifies adjudications, never mints)
- cli/commands/adjudicate_cmd.py: construct a signing issuer (private key)
- CRITICAL: verify no shared import path loads the private key into the
  engine or MCP process. The private-key load must happen ONLY in CLI
  command code, never in a module that engine/MCP import at module load
  time. Trace this explicitly.

### Clean break on HS256
- Existing HS256 decision records are no longer valid. Pre-release,
  single operator — no migration path. A stale HS256 record fails
  verification with a clear message ("decision record signed with
  retired HS256 scheme; re-adjudicate"). Do not support both schemes.

## Acceptance criteria
- snodo init generates RSA keypair at ~/.ssh/NO-AGENT/ with correct perms
  (dir 0700, private 0600), idempotent
- DecisionRecordIssuer signs RS256 with private key
- Verify-only issuer (public key) verifies, RAISES on mint attempt
- MCP server and engine construct verify-only issuers — no private key
  loadable in those processes (traced and asserted in a test)
- Existing adjudicate flow works end-to-end under RS256
- Tampered RS256 record fails verification
- HS256 record → clear retirement error
- Module docstring + SECURITY note document the interim threat model
  and the keychain hardening TODO

## Testing
- Unit test: keypair generation, perms (0700/0600), idempotent
- Unit test: RS256 sign with private → verify with public
- Unit test: verify-only issuer raises on mint
- Unit test: tampered RS256 record fails verification
- Unit test: HS256 record → retirement error
- Unit test: adjudicate end-to-end under RS256
- Test/assertion: the MCP server and engine code paths construct
  verify-only issuers (no private key) — guard against regression
- Full suite passes clean

## Constraints
- Read infrastructure/decisions.py (DecisionRecordIssuer sign/verify),
  infrastructure/tokens.py (current secret handling),
  cli/commands/adjudicate_cmd.py (mint path), mcp/server.py and
  engine/loop.py (issuer construction), cli/commands/init_cmd.py
  before touching anything
- RS256 via PyJWT + cryptography (both already deps)
- The private key must be loadable ONLY in CLI paths — this is the
  load-bearing security property; trace it explicitly, test it
- Clean break on HS256 — no dual-scheme support
- This does NOT add the authorize command or set_model — foundation only
