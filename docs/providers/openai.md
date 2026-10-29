# OpenAI

## 1. What you need

You need an OpenAI API key and a Snodo configuration with the model roles you want to check. Direct OpenAI model calls are billed by OpenAI.

## 2. Store the key

The built-in `openai` provider reads `OPENAI_API_KEY` by default. You can export it in your shell, or store the key with:

```sh
snodo config add openai YOUR_OPENAI_API_KEY
```

You can instead save a named credential reference:

```sh
snodo config add openai --ref env:OPENAI_API_KEY
```

The same reference can be set as `api_key_ref` in the provider configuration:

```yaml
providers:
  openai:
    api_key_ref: env:OPENAI_API_KEY
```

A literal `api_key` takes precedence over `api_key_ref`, which takes precedence over the `api_key_env` environment variable. An encrypted `api_key` reference such as `@keys/openai.key` is still an `api_key` and takes precedence too.

## 3. Configure

The built-in catalog already supplies OpenAI's `OPENAI_API_KEY` variable, model-list endpoint, and probe model. You only need a `providers:` block if you want to set provider fields explicitly:

```yaml
providers:
  openai: {}
```

Set the role models in your project configuration:

```yaml
llm:
  coder:
    model: openai/gpt-4o
  validator:
    model: openai/gpt-4o-mini
  classifier:
    model: openai/gpt-4o-mini
  recon:
    models:
      - openai/gpt-4o-mini
```

## 4. Choose model IDs

The `openai/<model>` form is recognized as the built-in OpenAI provider. For example, use `openai/gpt-4o` for a coder, `openai/gpt-4o-mini` for a validator, `openai/gpt-4o-mini` for a classifier, or `openai/gpt-4o-mini` for recon. These IDs make direct OpenAI API calls using this provider's key.

List the model IDs Snodo discovers for this provider:

```sh
snodo models --provider openai
```

### Similar-looking IDs go elsewhere

- `openai/@cf/...` is routed to the Cloudflare provider, not OpenAI.
- Coder IDs beginning `opencode-cli/openai/...` run through the OpenCode CLI on your OpenCode subscription, not this provider's key. A check sends one short prompt through the CLI, so it counts as a small call on that subscription.
- A custom provider block with `litellm_provider: openai` sends IDs in that block's namespace through LiteLLM's OpenAI-compatible format to that block's own `base_url`. The `LiteLLM model: openai/...` text in check output describes the request format, not the destination vendor.

## 5. Verify

Check the configured model roles with a live canary call:

```sh
snodo models --check
```

For direct OpenAI models, a passing check reports `OK`. An `opencode-cli/openai/...` coder line reports `OK` when the CLI accepts the prompt, or `FAILED` when it does not. The failure includes the CLI's message, so issues such as a renamed flag or unknown model can be spotted before a task runs. Resolve any `FAILED` result before relying on the model configuration. Other subprocess coders may still be `NOT CHECKABLE`.

## 6. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Authentication fails | Confirm the key is valid. Check whether a configured `api_key` or `api_key_ref` is taking precedence over `OPENAI_API_KEY`. |
| A model is routed to Cloudflare | Check whether its ID begins `openai/@cf/`; that prefix selects Cloudflare. |
| An OpenCode CLI coder is `FAILED` | Read the CLI message, check the model with `opencode models`, and confirm your OpenCode CLI version. |
| A coder is `NOT CHECKABLE` | This remains expected for subprocess coders that do not have a live check; OpenCode CLI coders are checked with a prompt instead. |
| Check output says `LiteLLM model: openai/...` | This is the LiteLLM request-format model string. For a custom provider, confirm its `base_url` to identify the actual endpoint. |
| Model listing is empty or unavailable | Confirm the OpenAI key is resolved and run `snodo models --provider openai` again. |
