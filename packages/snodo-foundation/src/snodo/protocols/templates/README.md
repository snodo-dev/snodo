# Snodo Protocol Templates

This directory contains the shipped protocol templates for snodo.
Each `.yml` file defines a complete protocol specification following
the snodo protocol schema.

Job notifications (Slack, Discord, Teams, ntfy, and webhook) are configured per
user in `~/.snodo/config.yml` under `notifications:`, not in the protocol.
Keep destination URLs and credentials out of committed templates. See the
[user configuration reference](../../../../../../docs/configuration.md) for the
full schema and examples.

## Available Templates

| File | Template Name | CLI Flag | Description |
|------|--------------|----------|-------------|
| `solo.yml` | Solo Developer | `--template solo` | Single mode: producer with full access (edit, dispatch, test, validate, commit, merge) |
| `team.yml` | Team Workflow | `--template team` | Three modes: producer, reviewer, planner with Separation of Duties |
| `2+n.yml` | 2+N Reference | `--template 2+n` | Paper Listing 1 reference: producer + reviewer with 4 validators |
| `intent.yml` | Intent | `--template intent` | Intent-driven producer workflow with warn-only spec validators |
| `bugfix-surgeon.yml` | Bugfix Surgeon | `--template bugfix-surgeon` | Bug-fix flow with a post-execute review gate |
| `feature-warden.yml` | Feature Warden | `--template feature-warden` | Feature flow with a scope guard |
| `greenfield.yml` | Greenfield | `--template greenfield` | Phased decide → scaffold → build workflow with per-phase exit gates |
| `product.yml` | Product | `--template product` | Product Markdown documents with role-specific validation and change-request delivery |
| `design.yml` | Design | `--template design` | Design briefs, UX specifications, UI copy, and component specifications in Markdown |
| `docs.yml` | Documentation | `--template docs` | Technical documentation with accuracy, example, and link validation |
| `research.yml` | Research Analyst | `--template research` | Evidence-led Markdown reports and decision memos |

See the [non-engineering role templates guide](../../../../../../docs/non-engineering-roles.md)
for deliverables, validator checks, documentation quality setup, and human review.

## Usage

```bash
# Initialize a new project with a template
snodo init --template solo
snodo init --template team
snodo init --template 2+n
snodo init --template intent
snodo init --template bugfix-surgeon
snodo init --template feature-warden
snodo init --template greenfield
snodo init --template product
snodo init --template design
snodo init --template docs
snodo init --template research

# Or with the interactive prompt
snodo init
```

## Adding Custom Templates

1. Create a new `.yml` file in this directory (e.g., `enterprise.yml`)
2. Follow the existing schema — mirror one of the shipped templates
3. Available fields per mode:
   - `mode_id`, `name`, `description`, `tools`, `validators`, `transitions`,
     `constraints`, `coder`, `coder_config`, `auto_merge`,
     `max_recovery_depth`, `concurrency`
4. Available fields per validator:
   - `validator_id`, `validator_type`, `criteria`, `constraints`,
     `evaluation_phase`, `scope`, `tooling`, `severity_cap`, `tools`,
     `judges_spec`, `check_tool_access`, `model`, `max_tool_turns`
   - Shipped validator types: `architecture`, `security`, `conventions`,
     `performance`, `testing`, `planning`, `protocol`, `quality`, and
     `acceptance`; custom types can be registered by applications.
   - Evaluation phases: `pre_execute`, `post_execute`, `mode_transition`.
5. No registry edit is needed: `_discover_templates()` in
   `snodo/protocols/__init__.py` discovers, parses, and verifies every `*.yml`
   file in this directory at import time. The filename stem becomes the
   selectable template name.

## Schema Reference

See `snodo/compiler/models.py` for the complete Pydantic model definitions.
Protocols must pass WF1-WF5 well-formedness checks at load time.
