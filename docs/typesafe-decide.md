# TypeSafe Jev `decide` validator tool

The optional `snodo-validator-tool-jev` package adds TypeSafe Jev's `decide`
function to LLM validators that opt into it. Jev evaluates typed questions and
returns answers with probabilities. The LLM validator remains responsible for
the final `pass`, `warn`, or `block` verdict and its written justification.

## Install and configure the API key

Install the package into the same Python environment as Snodo:

```sh
pip install snodo-validator-tool-jev
```

Set `TYPESAFE_API_KEY` in the environment where Snodo runs. The key is read
from that environment variable and is not stored in the protocol:

```sh
export TYPESAFE_API_KEY="your-api-key"
```

You can choose another environment variable with the `api_key_env` tool option.
Keep the key in your deployment's secret store and make it available to the
Snodo process.

## Opt a validator in

Add `decide` to the `tools` list for an LLM validator. Tool options are scoped to
that validator:

```yaml
validators:
  - validator_id: acceptance_review
    validator_type: quality
    tools:
      - name: decide
        options:
          model: jev-latest
          api_key_env: TYPESAFE_API_KEY
          timeout_seconds: 30
          max_input_chars: 12000
```

The model defaults to `jev-latest`, the API key environment variable defaults
to `TYPESAFE_API_KEY`, and the base URL defaults to `https://api.typesafe.ai`.
`timeout_seconds` bounds the request duration. `max_input_chars` caps the
context sent to Jev; set it deliberately because Jev's maximum context length
has not been published.

## Ask one question per acceptance check

The validator can call `decide` with the current diff and one yes/no question
for each acceptance bullet. Each question has a unique name, type `noul`, and
clear instructions. Jev evaluates the questions; its typed answers and
probabilities are returned to the LLM, which uses them as evidence for its own
verdict and justification. For example, given a task with two acceptance
checks:

```json
{
  "context": "diff",
  "questions": [
    {
      "name": "handles_empty_input",
      "type": "noul",
      "instructions": "Does the diff handle empty input as required by acceptance check 1?"
    },
    {
      "name": "preserves_existing_records",
      "type": "noul",
      "instructions": "Does the diff preserve existing records as required by acceptance check 2?"
    }
  ]
}
```

The model-facing tool result includes each named answer and its probability.
The validator should cite those results in its justification, for example:
“The diff handles empty input (probability 0.94) and preserves existing
records (probability 0.81); I pass the acceptance checks.” Probabilities are
evidence for the LLM's assessment, not a verdict produced by Jev.

The tool can also evaluate `spec` context, or selected repository-relative
files using `context: files` and a `files` list. Its question types include
`noul`, `choice`, and `score`; use `noul` for yes/no acceptance checks.

## Cost and limits

TypeSafe lists Jev input pricing as $0.042 per million input tokens, with output
free. Actual cost depends on the amount of input sent for evaluation. The
service's maximum context length is not published, so `max_input_chars` bounds
the supplied context. Long context may be truncated to fit that cap; the tool
reports usage and any truncation in its result. The configured timeout also
limits how long a request can take.

See [validator-tool plugins](extending.md#4-validator-tools) for the registry and
protocol integration mechanism.
