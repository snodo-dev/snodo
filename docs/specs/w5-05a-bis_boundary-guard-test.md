# W5-05a-bis: Import-boundary regression guard for the signing key

## Intent
W5-05a's security claim — engine and MCP processes cannot mint decision
records because they cannot reach the private key — is currently protected
only by current code structure. No test enforces it. signing_keys.py even
references tests/infrastructure/test_signing_keys.py as "the boundary
guard," but that file does not exist. A future import of load_private_key
into any engine/MCP module would silently break the boundary with a green
suite. The invariant must be defended by a test, or it is not an invariant.

## What to build

### tests/infrastructure/test_signing_keys.py (the missing file)
A regression guard that proves the boundary structurally:

1. Import-graph test: import engine.loop and mcp.server modules, then
   assert that load_private_key and signing_issuer (and
   SigningDecisionRecordIssuer) are NOT reachable in their transitive
   import graph. Use ast or module inspection — walk the imports of the
   engine and MCP module trees and assert none reference the private-key
   load path. The test must FAIL if someone adds
   `from snodo.infrastructure.signing_keys import load_private_key`
   to any engine or MCP module.

2. Behavioral test (the class-level guarantee, if not already present):
   VerifyOnlyDecisionRecordIssuer raises on mint attempt.

3. Positive control: confirm adjudicate_cmd.py (the legitimate CLI path)
   DOES reach load_private_key — so the test proves it's checking the
   right thing, not just asserting absence everywhere.

### signing_keys.py
Fix the docstring that references the now-existing test file.

## Acceptance criteria
- tests/infrastructure/test_signing_keys.py exists
- The import-graph test fails if load_private_key/signing_issuer is
  imported into any engine or mcp module
- The test passes today (boundary currently intact)
- Positive control confirms the CLI path DOES reach the private key
- VerifyOnlyDecisionRecordIssuer raises on mint (behavioral)
- signing_keys.py docstring no longer references a missing file

## Testing
This ticket IS the test. Plus run the full suite to confirm the new
test passes against current (intact) boundary.

## Constraints
- The import-graph check must be real — walk actual module imports, not
  just assert a string isn't in one file. The point is to catch a future
  regression anywhere in the engine/MCP trees.
- Decide the mechanism: ast-parse the module trees, or import the modules
  and inspect sys.modules / module.__dict__ for the forbidden symbols.
  Either is acceptable if it catches a new import of the private-key path.
- Touch only the new test file and the signing_keys.py docstring.
