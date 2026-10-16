# Ollama and OpenAI-compatible servers

## 1. What you need

For local Ollama, install Ollama, start its server, and make sure the model you want is available locally. For Ollama Cloud, have an Ollama Cloud API key. This guide uses a provider block named `ollama` locally and `ollama-cloud` for the hosted service.

## 2. Store the key

Local Ollama needs no key. For Ollama Cloud, choose an environment variable name and set it in your shell, for example:

```bash
export OLLAMA_CLOUD_API_KEY="your-key"
```

You can instead register a credential with `snodo config add ollama-cloud --ref 'env:OLLAMA_CLOUD_API_KEY'`, then use `api_key_ref` in the provider block. `api_key_ref` also accepts `command:COMMAND` references. A literal or encrypted `api_key` takes precedence over `api_key_ref`, which takes precedence over the environment variable named by `api_key_env`.

## 3. Configure

Add the matching block to `~/.snodo/config.yml` (or `$SNODO_HOME/config.yml`):

```yaml
providers:
  ollama:
    base_url: http://localhost:11434/v1
    litellm_provider: openai
  ollama-cloud:
    base_url: https://ollama.com/v1
    litellm_provider: openai
    api_key_env: OLLAMA_CLOUD_API_KEY
```

- `base_url` points requests at the server's OpenAI-compatible API.
- `litellm_provider: openai` selects LiteLLM's OpenAI-compatible route.
- `api_key_env` names the environment variable for Cloud authentication; omit it for local Ollama, which needs no key.

You can also set the local values with `snodo config set providers.ollama.base_url http://localhost:11434/v1` and `snodo config set providers.ollama.litellm_provider openai`.

## 4. Choose model IDs

Write model IDs as `<provider-block>/<model>`. The provider name is the block name you chose, not a built-in Ollama provider name. Model tags containing a colon are valid.

Example model IDs for the four roles (replace them with IDs your server provides):

| Role | Local Ollama | Ollama Cloud |
|---|---|---|
| Coder | `ollama/qwen2.5-coder:14b` | `ollama-cloud/qwen3-coder` |
| Validator | `ollama/llama3.1:8b` | `ollama-cloud/llama3.3` |
| Classifier | `ollama/llama3.2:3b` | `ollama-cloud/deepseek-v4.1-flash` |
| Recon | `ollama/deepseek-r1:14b` | `ollama-cloud/deepseek-v4-pro:0813` |

Choose IDs that your server actually provides. List discovered models with `snodo models --provider ollama` or `snodo models --provider ollama-cloud`.

## 5. Verify

Configure the role models with these IDs in Snodo's model settings, then run the check:

```bash
snodo models --check
```

Passing lines look like `OK ollama-cloud/deepseek-v4.1-flash (classifier) — LiteLLM model: openai/deepseek-v4.1-flash`. A successful check confirms that Snodo can reach the configured endpoint and make its model check call.

## 6. Troubleshooting

| Symptom | What to check |
|---|---|
| The check reports a missing LiteLLM provider route. | Set `litellm_provider: openai` in the block. |
| The endpoint cannot be reached or returns a not-found response. | Check `base_url`: local Ollama uses `http://localhost:11434/v1`; Ollama Cloud uses `https://ollama.com/v1`. Make sure the server is running. |
| The Cloud key is set, but the request is unauthenticated. | Confirm `api_key_env` exactly matches the exported variable. A configured literal or encrypted `api_key`, or an `api_key_ref`, takes precedence over it. |
| No models appear in the listing. | Check the block name in `--provider`, and confirm the server exposes models through its API. |

## Other OpenAI-compatible servers

The same pattern works for llama.cpp, vLLM, LM Studio, and self-hosted gateways: give the server its own provider block, set its compatible API `base_url`, and use `litellm_provider: openai`. Add a key only if that server requires one.
