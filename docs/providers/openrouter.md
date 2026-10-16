# OpenRouter

## 1. What you need

You need an OpenRouter API key and a model available to your account. Snodo has OpenRouter in its built-in provider catalog.

## 2. Store the key

The default environment variable is `OPENROUTER_API_KEY`. You can export it in your shell, store the key with `snodo config add openrouter <key>`, or store a credential reference with `snodo config add openrouter --ref 'env:MY_OPENROUTER_KEY'`. A literal `api_key` (including a key stored encrypted) takes precedence over `api_key_ref`, which takes precedence over `api_key_env` and the environment variable it names.

## 3. Configure

The built-in catalog already supplies the environment variable name and the OpenRouter models endpoint, so you do not need to repeat them. If you want an explicit provider entry, the smallest block is:

```yaml
providers:
  openrouter: {}
```

## 4. Choose model IDs

Use `openrouter/<vendor>/<model>`: for example, `openrouter/anthropic/claude-3.5-sonnet`. Snodo resolves the `openrouter` block as the provider; the vendor part identifies the upstream model and does not change which provider key is used. LiteLLM's own `openrouter/` model routing also resolves to the OpenRouter provider when there is no matching configured block name.

List models available from the provider with:

```sh
snodo models --provider openrouter
```

Use a listed ID for each role you configure. For example:

```yaml
llm:
  coder:
    model: openrouter/anthropic/claude-3.5-sonnet
  validator:
    model: openrouter/openai/gpt-4o-mini
  classifier:
    model: openrouter/google/gemini-2.0-flash
  recon:
    models:
      - openrouter/deepseek/deepseek-chat
```

## 5. Verify

Check provider credentials and model access with:

```sh
snodo models --check
```

## 6. Troubleshooting

| Problem | What to check |
|---|---|
| No key is found | Export `OPENROUTER_API_KEY`, or add a key with `snodo config add openrouter <key>`. If using `api_key_ref`, confirm its environment variable or command resolves to a non-empty value. |
| The environment key seems ignored | A stored literal or encrypted `api_key` takes precedence over the environment variable. Remove or update that stored key if it is stale; `api_key_ref` also takes precedence over `api_key_env`. |
| A model ID does not work | Use the `openrouter/<vendor>/<model>` form and verify the exact ID with `snodo models --provider openrouter`. |
