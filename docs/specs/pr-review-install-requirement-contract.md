# Spec: PR-review installation requirement contract

## Problem

The PR-review workflow adds `litellm<1.106` alongside the selected Snodo
requirement. Current Snodo releases require `litellm>=1.106`, so the default
installation and explicit selections of these releases cannot resolve.
An older explicitly selected release may still resolve; no failing GitHub
Actions run is required to reproduce the contradictory requirements.

## Fix

The `Install released snodo` step installs exactly one requirement:
`snodo==$SNODO_VERSION` when the version is set, otherwise `snodo`.
Snodo's published dependency metadata governs its dependencies; this step
does not impose additional constraints.

Remove the stale LiteLLM constraint from both branches. Reviewer behavior,
credentials, cloud synchronization, fork restrictions, and publication steps
remain unchanged. This does not downgrade Snodo or alter its dependency floor.

## Verify

Execute the actual installation step's shell script with a fake `uv` on
`PATH`. Record its arguments and verify one virtual-environment creation
followed by one installation of exactly the selected Snodo requirement.
Cover current and older explicit versions, an empty version, and an unset
version. Include a temporary directory containing spaces to check quoting.

Replay the old script through the same harness and assert that the contract
rejects its extra LiteLLM requirement. These tests are offline: they verify
what the workflow requests, not dependency resolution. A future intentional
dependency override must revise this contract and its tests together.

Separately, smoke-test a real installation in a temporary environment, import
the review dependencies, and run the review script with canned results. No
provider call, GitHub publication, or private gate-host access is needed.

## Touch

- `.github/workflows/pr-review.yml`
- `tests/golden/test_pr_review_install.py`
- `CHANGELOG.md`
- `docs/specs/pr-review-install-requirement-contract.md`
