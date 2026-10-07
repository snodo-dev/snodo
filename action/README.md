# Snodo protocol validation GitHub Action

Run `snodo ready` and judge a pull request's three-dot diff against the target
repository's protocol. A blocking verdict fails the step. Verdict details are
written to the GitHub Actions job summary. Provider credentials are supplied by
the calling workflow and are never persisted by the action.

## Inputs

| Input | Required | Default | Description |
|---|---:|---|---|
| `protocol` | no | `.snodo/protocol.yml` | Protocol file path |
| `mode` | no | active mode | Mode to validate |
| `model` | no | — | Model identifier for model-backed validators |
| `provider-api-key` | no | — | Provider secret value |
| `provider-api-key-env` | no | `OPENAI_API_KEY` | Environment variable name for that key |
| `pr-number` | no | Pull request event number | PR number to resolve with the GitHub provider |
| `github-token` | no | `${{ github.token }}` | Token for the GitHub provider; grant `pull-requests: read` |

The action installs the published `snodo` package. Any model/provider secrets
must be passed from repository or organization secrets by the workflow caller.
They are only placed in the action process environment for the validation run.

## Outputs

| Output | Description |
|---|---|
| `verdict` | `pass`, `blocker`, `escalate`, or `validator_error` |
| `status` | Snodo validation exit code (0–4) |

## Copyable workflow

```yaml
name: Snodo protocol
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: read
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: snodo-dev/snodo@main
        with:
          protocol: .snodo/protocol.yml
          mode: implementation
          model: openai/gpt-4o-mini
          provider-api-key: ${{ secrets.OPENAI_API_KEY }}
          github-token: ${{ github.token }}
```

Use the protocol's configured mode and provider as appropriate for your project.
The PR number is taken from the event by default. Validation outcomes use
Snodo's machine-interface exit codes: pass `0`, blocker `1`, escalate `2`,
validator error `3`, internal error `4`.
