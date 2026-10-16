# Cloudflare AI Gateway

This guide takes you from a Cloudflare AI Gateway to a successful Snodo model check.

## 1. What you need

You need the Cloudflare account ID that owns the gateway, its gateway ID, and an API token for that same account. Follow Cloudflare's [AI Gateway documentation](https://developers.cloudflare.com/ai-gateway/) for creating a gateway and choosing token permissions.

## 2. Store the key

Set `CLOUDFLARE_AI_GATEWAY_TOKEN` in your shell or secret manager. This guide uses `api_key_env` so Snodo reads that environment variable for the gateway provider.

## 3. Configure

Set `CLOUDFLARE_ACCOUNT_ID` to the account ID and add this provider block to `~/.snodo/config.yml`:

```yaml
providers:
  cf-gateway:
    account_id_env: CLOUDFLARE_ACCOUNT_ID
    base_url: https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/ai/v1
    litellm_provider: openai
    api_key_env: CLOUDFLARE_AI_GATEWAY_TOKEN
    extra_headers:
      cf-aig-gateway-id: YOUR_GATEWAY_ID
```

Replace both placeholders. The account ID in `base_url` must be the same account that owns the API token. Snodo sends `extra_headers` on calls for models in this provider block. This uses Cloudflare's account-specific REST API URL; the gateway's `/compat` endpoint is chat-completions-only and Cloudflare marks it deprecated for single-model calls.

## 4. Choose model IDs

Use `cf-gateway/<vendor>/<model>` for vendor model IDs, replacing the final portion with the model ID supported by your gateway. For example, `cf-gateway/google/<model>` reaches the gateway as `google/<model>`.

For an OpenAI-vendor model, use `cf-gateway/responses/openai/<model>`, for example `cf-gateway/responses/openai/gpt-6-sol`. The `responses/` prefix routes through the gateway's responses endpoint and keeps `openai/` in the model name sent to it.

Avoid `cf-gateway/openai/<model>`: Snodo recognizes the existing `openai/` LiteLLM prefix, so the gateway receives `<model>` without that vendor prefix. The `responses/` form is specific to OpenAI-vendor models; for another vendor it sends a name such as `responses/google/<model>`, which the gateway may report as not found.

Use the native [Google provider guide](google.md) for Gemini models. Gemini models through this gateway have failed in multi-turn use with Cloudflare error 7003; this guide does not assume a cause.

## 5. Verify

Run a live check against configured models:

```bash
snodo models --check
```

A passing line includes `OK` and the configured model ID.

## 6. Troubleshooting

| What you see | Why | What to change |
|---|---|---|
| Cloudflare error 10000 | The API token's account does not match the account in `base_url`. | Use a token belonging to the account whose ID appears in the base URL. |
| `Model not found: responses/google/…` | `responses/` is being used with a non-OpenAI vendor model. | Use `cf-gateway/google/<model>` for Google-vendor models; reserve `responses/` for OpenAI-vendor IDs. |
| OpenAI gpt-6 or gpt-5.6 rejects function tools on chat completions | The provider may reject the default reasoning effort in this request shape. Snodo can apply a requested `reasoning_effort: none` after that rejection and remembers the value per model. | If the rejection asks for `reasoning_effort` to be `none`, retry through Snodo's model check; its shared parameter adaptation applies the requested value for that model. |
| Cloudflare error 7003, “Model execution failed (User Input Error),” with Gemini in multi-turn use | Gemini models through this gateway failed in multi-turn use; the cause is not established here. | Configure Gemini through the native Google provider instead (see [Google](google.md)). |
