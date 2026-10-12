# Snodo diff line-count validator tool

A minimal installable validator-tool plugin. Its `count_diff_lines` function
counts added and removed lines from diff hunks whose new-file paths match a
glob. It reads only the diff supplied through the validator context and makes
no network requests.

Install this package into the same Python environment as Snodo:

```sh
python -m pip install -e /path/to/snodo/examples/snodo-diff-line-tool
```

Select it on an LLM validator in `.snodo/protocol.yml`:

```yaml
validators:
  - validator_id: diff_review
    validator_type: quality
    validator_tools:
      - name: count_diff_lines
```

The tool is discovered from the `snodo.validator_tools` entry-point group. The
handler gets the compared refs in `context.diff_refs` and calls the provided
`context.diff_accessor`; the model receives its text result and continues to
produce the validator verdict. The registry discovery and handler behavior are
covered by `tests/test_tool_plugin.py`; run it from the repository root with:

```sh
uv run pytest examples/snodo-diff-line-tool/tests/test_tool_plugin.py
```
