# OpenCode Go gateway

Use this guide to connect the OpenCode Go gateway to snodo and check that its
models are reachable. The gateway serves model families through different API
formats, so Claude and MiniMax need an explicit route rule.

## 1. What you need

You need an OpenCode Go API key and a snodo project where you can edit the
provider configuration. This guide uses `ocgo` as the provider name and
`OCGO_API_KEY` as the environment variable; choose other names if they suit
your setup.

## 2. Store the key

Set the environment variable you chose, for example `OCGO_API_KEY`, in your
shell or secret manager. You can instead register the key with
`snodo config add ocgo <key>`, or save a credential reference with
`snodo config add ocgo --ref 'env:OCGO_API_KEY'`.

The provider's `api_key_ref` can point to an environment variable or command.
A literal `api_key` (including an encrypted provider key) takes precedence over
`api_key_ref`, which takes precedence over `api_key_env`; a configured key
therefore wins over the environment variable.

## 3. Configure the provider

Add this block to your snodo configuration:

```yaml
providers:
  ocgo:
    api_key_env: OCGO_API_KEY
    base_url: https://opencode.ai/zen/go/v1
    litellm_provider: openai
    model_routes:
      - match: claude-*
        litellm_provider: anthropic
      - match: minimax-*
        litellm_provider: anthropic
```

- `api_key_env` names the environment variable containing the gateway key.
- `base_url` points snodo at the OpenCode Go API.
- `litellm_provider` selects the default OpenAI-compatible API format.
- `model_routes` overrides that format for matching model IDs.
- Each `match` sends Claude or MiniMax IDs to the Anthropic Messages format.

Rules are checked in order and match the model ID after the local `ocgo/`
prefix is removed. Other families, such as `ocgo/qwen3.6-plus`, keep the default
OpenAI-compatible route. Without the Claude route rule, a Claude model is sent
in the wrong format and its calls fail.

You can optionally set one gateway session per task under the same `ocgo`
provider block:

```yaml
providers:
  ocgo:
    extra_headers:
      x-opencode-session: "{task_id}"
```

Snodo substitutes `{task_id}` with the current task ID; when there is no task
ID, it substitutes `unknown`.

If you have two gateway accounts, configure separate provider blocks, such as
`ocgo1` and `ocgo2`, with their own keys to spread requests across accounts.

## 4. Choose model IDs

List the models available through this provider:

```sh
snodo models --provider ocgo
```

Use the model IDs from that list, with the `ocgo/` provider prefix, for each
role. For example, you can choose:

| Role | Example model ID |
|---|---|
| Coder | `ocgo/claude-haiku-5-5` |
| Validator | `ocgo/claude-haiku-5-5` |
| Classifier | `ocgo/qwen3.6-plus` |
| Recon | `ocgo/qwen3.6-plus` |

Claude IDs need the Anthropic route rule above; Qwen uses the default route.
Confirm that each example is present in your provider's model listing before
using it.

## 5. Verify the connection

Run the live check against your configured models:

```sh
snodo models --check
```

The check makes a cheap live call for each configured model. A Claude model
should pass with its Anthropic route rule in place.

## 6. Troubleshooting

| Symptom | What to check |
|---|---|
| Claude model calls fail | Add the `claude-*` route with `litellm_provider: anthropic`; without it, Claude requests use the default OpenAI-compatible format. |
| MiniMax model calls fail | Add the `minimax-*` route with `litellm_provider: anthropic`. |
| `snodo models --check` reports HTTP 413 | The request is too large for the gateway. Try a smaller request or a model with a lower context requirement. |
| The key is not picked up | Check the exact `api_key_env` name and that the variable is available to the snodo process. Also check whether a literal or encrypted `api_key`, or `api_key_ref`, is taking precedence. |
| A model is listed but fails using the wrong API format | Check its ID against `model_routes`. Unmatched families use the block's default `openai` route. |
