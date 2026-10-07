# Running Snodo in CI

Snodo can run the project's protocol validators against a pull request or merge
request without dispatching a coder. `snodo validate --pr <number>` resolves the
change request through the installed code-host provider and validates its
three-dot diff. The result is a validation outcome suitable for a pipeline gate.

## GitHub Actions

The repository provides the composite action in [`action.yml`](../action.yml),
with its inputs documented in [`action/README.md`](../action/README.md). It
checks readiness, runs `snodo validate --pr`, and writes the verdict to the job
summary. The repository's [example workflow](../action/README.md#copyable-workflow)
shows the supported invocation; the action takes the PR number from the
`pull_request` event unless `pr-number` is supplied explicitly.

Pass provider credentials through Actions secrets, never as committed workflow
text. The example supplies `secrets.OPENAI_API_KEY` to `provider-api-key`; set
`provider-api-key-env` when the configured model provider expects another
environment variable. The GitHub provider token is passed as `github-token`
(default `${{ github.token }}`) and needs `pull-requests: read` permission.

This action is a validation gate. For the separate advisory workflow that posts
review recommendations, see [Automated pull request reviews](configuration.md#automated-pull-request-reviews).

## GitLab CI

`docker/Dockerfile` defines the Snodo image; it installs `snodo` and uses
`snodo` as its entrypoint. Build and publish that image for your runner, then
make the GitLab provider plugin available in the same Python environment as
Snodo (`snodo-provider-gitlab`). For a derived image, install Snodo and the
plugin in one uv tool environment with `uv tool install --with
snodo-provider-gitlab snodo`. The plugin resolves the project from the `origin`
Git remote and reads its token from `GITLAB_TOKEN`.

For example, after publishing the image (here shown as
`registry.example.com/team/snodo:ci`), a job can use it as follows:

```yaml
stages:
  - validate

snodo-validate:
  stage: validate
  image:
    name: registry.example.com/team/snodo:ci
    entrypoint: [""]
  variables:
    GIT_DEPTH: "0"
  script:
    - snodo validate --pr "$CI_MERGE_REQUEST_IID" --json
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

Configure `GITLAB_TOKEN` as a masked CI/CD variable (and protect it according to
your project's pipeline policy). The token must be able to read the project and
merge-request data. Keep model-provider keys in masked CI/CD variables too, and
expose them only to the validation job; the protocol's configured provider
environment variable is what the validator uses. For self-hosted GitLab, set
the provider's host configuration as described in the
[GitLab provider package](../packages/snodo-provider-gitlab/pyproject.toml).

The example uses `entrypoint: [""]` so GitLab Runner can execute its script
instead of passing it to the image's `snodo` entrypoint. Full-depth checkout
lets Git resolve the target and source refs required for the diff.

## Pipeline exit codes

`snodo validate` returns a stable outcome code. A zero exit passes the pipeline;
non-zero outcomes fail the job unless the CI configuration explicitly changes
that behavior.

| Exit code | Outcome | Pipeline meaning |
|---:|---|---|
| 0 | `pass` | Validators passed. |
| 1 | `blocker` | A blocking finding; fail the pipeline. |
| 2 | `escalate` | Human judgement is needed; fail the pipeline pending review. |
| 3 | `validator_error` | Validation could not produce a valid judgement; fail the pipeline. |
| 4 | `internal_error` | Snodo could not perform validation (for example, a missing or unresolvable change request). |

The codes are part of the [machine interface](machine-interface.md#exit-codes).
