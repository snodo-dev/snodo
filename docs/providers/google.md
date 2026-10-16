# Google Gemini API

## 1. What you need

You need a Google Gemini API key and a model enabled for your Google account. Keep the key available either as an environment variable or in Snodo's provider configuration.

## 2. Store the key

The built-in Google provider reads `GEMINI_API_KEY` by default. You can store the key with `snodo config add google YOUR_GEMINI_API_KEY` instead. You can also configure an `api_key_ref` in the `google` provider block to resolve a named key reference.

When Snodo resolves credentials, a literal `api_key` takes precedence over `api_key_ref`, and `api_key_ref` takes precedence over the environment variable. An encrypted key stored as `api_key: "@keys/google.key"` also takes precedence over `GEMINI_API_KEY`.

## 3. Configure the provider

The built-in catalog supplies Google's API endpoint, the default key variable, and the model route. The smallest provider block is:

```yaml
providers:
  google: {}
```

## 4. Choose model IDs

Snodo's provider name is `google`; LiteLLM's Gemini route is `gemini/`. Both `google/gemini/gemini-3.8-flash` and `gemini/gemini-3.8-flash` resolve to LiteLLM model `gemini/gemini-3.8-flash`. List models available to your key with:

```sh
snodo models --provider google
```

For example, you can use a listed model ID for each role:

```yaml
llm:
  coder:
    model: google/gemini/gemini-3.8-flash
  validator:
    model: google/gemini/gemini-3.8-flash
  classifier:
    model: google/gemini/gemini-3.8-flash
  recon:
    models:
      - google/gemini/gemini-3.8-flash
```

## 5. Verify

Check that the configured models can be reached with your credentials:

```sh
snodo models --check
```

## 6. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Missing-key or authentication error | Set `GEMINI_API_KEY`, or store a key with `snodo config add google YOUR_GEMINI_API_KEY`. |
| HTTP 429 `RESOURCE_EXHAUSTED` | Google may have limited the account's per-minute input-token quota, including on a paid tier. Check the quota and limits on the Google side. |
| The environment key seems ignored | A configured literal or encrypted `api_key` wins over `GEMINI_API_KEY`. Update the stored key or remove it to use the environment variable. |
