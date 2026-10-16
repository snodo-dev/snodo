# Anthropic provider

<!-- snodo-guide topic="providers" aliases="anthropic" summary="Configure and verify the built-in Anthropic provider" section="Anthropic provider" -->

## 1. What you need

You need an Anthropic API key and a model available to your account. Snodo includes a built-in Anthropic provider entry, so you usually do not need to add provider settings.

## 2. Store the key

The default environment variable is `ANTHROPIC_API_KEY`. You can also store the key with `snodo config add anthropic <key>`, or store a reference with `snodo config add anthropic --ref 'env:ANTHROPIC_API_KEY'`.

In configuration, `providers.anthropic.api_key_ref` can use an `env:` or `command:` reference. A literal `api_key` takes precedence over `api_key_ref`, and an encrypted `api_key` takes precedence too; either stored key wins over the environment variable. Without either, Snodo uses the reference if set, then the environment variable.

## 3. Configure

The smallest provider block is:

```yaml
providers:
  anthropic: {}
```

The built-in catalog already supplies the `ANTHROPIC_API_KEY` variable, Anthropic models endpoint, and probe model `claude-3-haiku-20240307`. Leave the block out unless you need to override provider settings.

## 4. Choose model ids

Snodo recognizes IDs beginning with `claude-` as Anthropic models. For example, you can use `claude-sonnet-4-20250514` for a coder, `claude-3-7-sonnet-20250219` for a validator, `claude-3-5-haiku-20241022` for a classifier, and `claude-3-5-haiku-20241022` for recon. These are example IDs; use models enabled for your account.

List the models discovered from Anthropic:

```bash
snodo models --provider anthropic
```

Claude models reached through a gateway such as OpenCode Go belong in that gateway's provider block: the ID's leading provider name decides which credentials Snodo uses.

## 5. Verify

Check that the configured model can be reached:

```bash
snodo models --check
```

## 6. Troubleshooting

| Problem | What to check |
| --- | --- |
| The key is missing | Set `ANTHROPIC_API_KEY` or run `snodo config add anthropic <key>`. Check that the environment variable is available to the Snodo process. |
| Snodo keeps using a different key than the environment variable | A literal or encrypted `api_key` wins over `api_key_ref`, and the reference wins over the environment variable. Update the stored credential or its reference. |
| A model uses credentials for the wrong provider | Check the ID's leading provider name. `claude-…` IDs resolve to Anthropic; a gateway-prefixed ID resolves to that gateway instead. |
| The model check does not pass | Run `snodo models --provider anthropic` to inspect discovered models, then confirm the key and model access with your provider account. |
