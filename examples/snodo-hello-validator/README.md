# Snodo hello validator

A minimal installable third-party validator. It warns when a task specification
does not have a standalone `ACCEPTANCE` heading and passes when it does.

Run these commands from your Snodo project:

```sh
# Install into the same Python environment as Snodo.
uv pip install -e /path/to/snodo/examples/snodo-hello-validator

# Confirm automatic entry-point discovery.
snodo ready --json
```

The output's `extensions` → `snodo.validators` object should include
`hello_acceptance` with status `installed`. Reference it in `.snodo/protocol.yml`:

```yaml
validators:
  - validator_id: hello_acceptance_check
    validator_type: hello_acceptance
    evaluation_phase: pre_execute
    criteria:
      - Task specification includes an ACCEPTANCE section
```

Run validation using the normal project task and protocol:

```sh
snodo validate <task-id>
```

The plugin reports `pass` for a spec with an `ACCEPTANCE` heading and `warn`
without one. To remove it:

```sh
uv pip uninstall snodo-hello-validator
```

The protocol entry can then be removed if no longer needed.
