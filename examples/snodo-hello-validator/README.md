# Snodo hello validator

A minimal installable third-party validator. It warns when a task specification
does not have a standalone `ACCEPTANCE` heading and passes when it does.

Run these commands from your Snodo project. First, check the Python interpreter
used by the `snodo` command (the first line is its shebang):

```sh
sed -n '1p' "$(command -v snodo)"
```

For a Snodo installed with `uv tool install`, install the example into its tool
environment (replace the path with your checkout):

```sh
uv pip install --python ~/.local/share/uv/tools/snodo/bin/python -e /path/to/snodo/examples/snodo-hello-validator
```

For Snodo installed in an activated virtualenv or with pip, use that environment's
Python:

```sh
python -m pip install -e /path/to/snodo/examples/snodo-hello-validator
```

Then confirm automatic entry-point discovery:

```sh
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
without one. Uninstall from the same environment where Snodo is installed.

For a uv-tool installation:

```sh
uv pip uninstall --python ~/.local/share/uv/tools/snodo/bin/python snodo-hello-validator
```

For an activated virtualenv or pip installation:

```sh
python -m pip uninstall snodo-hello-validator
```

The protocol entry can then be removed if no longer needed.
