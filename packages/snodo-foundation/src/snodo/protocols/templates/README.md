# Snodo Protocol Templates

This directory contains the shipped protocol templates for snodo.
Each `.yml` file defines a complete protocol specification following
the snodo protocol schema.

## Available Templates

| File | Template Name | CLI Flag | Description |
|------|--------------|----------|-------------|
| `solo.yml` | Solo Developer | `--template solo` | Single mode: producer with full access (edit, dispatch, test, validate, commit, merge) |
| `team.yml` | Team Workflow | `--template team` | Three modes: producer, reviewer, planner with Separation of Duties |
| `2+n.yml` | 2+N Reference | `--template 2+n` | Paper Listing 1 reference: producer + reviewer with 4 validators |

## Usage

```bash
# Initialize a new project with a template
snodo init --template solo
snodo init --template team
snodo init --template 2+n

# Or with the interactive prompt
snodo init
```

## Adding Custom Templates

1. Create a new `.yml` file in this directory (e.g., `enterprise.yml`)
2. Follow the existing schema — mirror one of the shipped templates
3. Available fields per mode:
   - `mode_id`, `name`, `tools`, `validators`, `transitions`
4. Available fields per validator:
   - `validator_id`, `validator_type` (one of: architecture, security, conventions, protocol, performance, testing, quality, planning)
   - `evaluation_phase`: `"pre_execute"` or `"post_execute"`
   - `criteria`: list of LLM prompt strings (or `tooling: {}` for quality validators)
5. Register the template in `snodo/cli/commands/__init__.py`:
   ```python
   MY_TEMPLATE = _load_template("my_template_name")
   PROTOCOL_TEMPLATES = {**PROTOCOL_TEMPLATES, "my_template_name": MY_TEMPLATE}
   ```

## Schema Reference

See `snodo/compiler/models.py` for the complete Pydantic model definitions.
Protocols must pass WF1-WF5 well-formedness checks at load time.
