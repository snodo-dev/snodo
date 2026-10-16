# DeepSeek

This guide connects Snodo to DeepSeek's own API and checks your configured models.

## 1. What you need

You need a DeepSeek API key and a model ID that DeepSeek serves. Snodo's built-in DeepSeek provider uses DeepSeek's API directly.

## 2. Store the key

The provider's default environment variable is `DEEPSEEK_API_KEY`. You can export it in your shell, or store the key with `snodo config add deepseek <key>`. To refer to an environment variable or command instead, use `snodo config add deepseek --ref 'env:MY_DEEPSEEK_KEY'` or `snodo config add deepseek --ref 'command:your-secret-command'`.

You can also set `api_key_ref` in `~/.snodo/config.yml`, for example `api_key_ref: env:MY_DEEPSEEK_KEY`. A literal or encrypted `api_key` takes precedence over `api_key_ref`, which takes precedence over the environment variable.

## 3. Configure the provider

The built-in catalog supplies the API endpoint and default key variable, so the smallest provider block is:

```yaml
providers:
  deepseek: {}
```

## 4. Choose model IDs

Snodo maps IDs beginning with `deepseek/` to this provider. For example, use `deepseek/deepseek-chat` for each role:

```yaml
llm:
  coder:
    model: deepseek/deepseek-chat
  validator:
    model: deepseek/deepseek-chat
  classifier:
    model: deepseek/deepseek-chat
  recon:
    model: deepseek/deepseek-chat
```

List models available through the provider with `snodo models --provider deepseek`.

DeepSeek models served by another host, such as `ollama-cloud/deepseek-v4-pro:0813`, belong under that host's provider block and key, not `deepseek`.

## 5. Verify

Run `snodo models --check` to make a live check against each configured model. Continue when each check passes.

## 6. Troubleshooting

| Problem | What to check |
| --- | --- |
| The check reports a missing key. | Set `DEEPSEEK_API_KEY` or store a key with `snodo config add deepseek <key>`. |
| The check uses a different key than the environment variable. | A configured literal or encrypted `api_key` wins over the environment variable. Remove or replace the stored key, or use `api_key_ref` if you want a reference to supply the credential. |
| A model is not listed for DeepSeek. | Confirm the ID belongs to DeepSeek's own API and uses the `deepseek/` prefix. Models served by another host need that host's provider configuration. |
