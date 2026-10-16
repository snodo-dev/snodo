# Cloudflare Workers AI

This guide takes you from a Cloudflare account to a successful Snodo model check.

## 1. What you need

You need the Cloudflare account ID for the account that serves inference, and an API token for that same account. Give the token **Account > Workers AI > Read** and **Account > Workers AI > Edit**, scoped to that account. Cloudflare's [Create a Workers AI API Token](https://developers.cloudflare.com/workers-ai/get-started/rest-api/) template fills in these permissions.

## 2. Store the key

Snodo's Cloudflare provider reads `CLOUDFLARE_API_KEY` by default. Set it in your shell or secret manager; alternatively, run `snodo config add cloudflare <token>` to store a key, or configure `api_key_ref` as an `env:NAME` or `command:COMMAND` reference.

The effective precedence is `api_key` (including an encrypted `@keys/cloudflare.key` value), then `api_key_ref`, then `api_key_env` (default `CLOUDFLARE_API_KEY`). A stored or referenced key therefore overrides the environment variable. Remove or replace a stale stored key if the environment token appears to be ignored.

## 3. Configure

Set `CLOUDFLARE_ACCOUNT_ID` to the account ID and put this block in `~/.snodo/config.yml`:

```yaml
providers:
  cloudflare:
    account_id_env: CLOUDFLARE_ACCOUNT_ID # Selects the account for model discovery.
    base_url: https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/ai/v1 # Sends inference to this account's OpenAI-compatible endpoint.
    litellm_provider: openai # Routes inference through the OpenAI-compatible API.
    models_endpoint: https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search # Uses Cloudflare's model-search endpoint for discovery.
```

The built-in catalog already supplies `api_key_env: CLOUDFLARE_API_KEY` and `account_id_env: CLOUDFLARE_ACCOUNT_ID`; you only need the keys shown above. Replace `YOUR_ACCOUNT_ID` in `base_url` with the same account ID as the environment variable. Discovery substitutes `{account_id}` from `account_id` if set, otherwise from the variable named by `account_id_env`.

For inference, `litellm_provider: openai` routes requests to the OpenAI-compatible endpoint in `base_url`. Without it, `cloudflare/...` IDs use LiteLLM's native Cloudflare route instead.

## 4. Choose model IDs

Use `cloudflare/@cf/<vendor>/<model>` IDs. For a text-generation model, this real ID works for each of these roles:

| Role | Example model ID |
|---|---|
| Coder | `cloudflare/@cf/openai/gpt-oss-120b` |
| Validator | `cloudflare/@cf/openai/gpt-oss-120b` |
| Classifier | `cloudflare/@cf/openai/gpt-oss-120b` |
| Recon | `cloudflare/@cf/openai/gpt-oss-120b` |

For example, set `llm.validator.model` to the validator ID in your normal model configuration.

You can also use `openai/@cf/...` or `@cf/...`. In `openai/@cf/...`, `openai/` is a LiteLLM routing detail, not a request to OpenAI. `snodo models --provider cloudflare` currently lists IDs in that `openai/@cf/...` form; both forms work.

List available IDs with:

```bash
snodo models --provider cloudflare
```

## 5. Verify

Run a live check against configured models:

```bash
snodo models --check
```

A passing line looks like:

```text
OK cloudflare/@cf/openai/gpt-oss-120b (validator) — LiteLLM model: openai/@cf/openai/gpt-oss-120b
```

## 6. Troubleshooting

| What you see | Why | What to change |
|---|---|---|
| Cloudflare error 10000, “Authentication error” | The token is invalid for the account in `base_url`, or the discovery account and inference account differ. | Use a token with Workers AI Read and Edit access scoped to the called account. Make `base_url`'s literal account ID match `CLOUDFLARE_ACCOUNT_ID` (or `account_id`). |
| Error 5007, “No such model openai/@cf/…” | A `cloudflare/...` ID went through LiteLLM's native Cloudflare route because the OpenAI-compatible provider was not selected. | Set `litellm_provider: openai` and use the account-specific OpenAI-compatible `base_url` above. |
| A valid environment token seems ignored | A configured literal `api_key` or encrypted `@keys/cloudflare.key` takes precedence over `api_key_ref`, which takes precedence over `api_key_env`. | Replace/remove the stale stored key or reference so Snodo can use `CLOUDFLARE_API_KEY`. |
