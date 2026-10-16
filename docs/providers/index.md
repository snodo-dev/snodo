# Model providers

Each provider guide shows how to configure a model service for Snodo. Model IDs are associated with a provider in this order: Snodo's built-in model-prefix rules, the configured provider block whose name comes before the first `/`, then LiteLLM's own provider detection.

For credentials, Snodo checks `api_key` first (including encrypted `@keys/<provider>.key` references), then `api_key_ref`, then the environment variable named by `api_key_env`. A provider block's `base_url` sets its API endpoint. `litellm_provider` selects the LiteLLM provider format used to route its models; `model_routes` can select that format for matching model IDs, taking precedence over the block default. Run `snodo models --check` to confirm a setup.

| Guide | Use this when |
| --- | --- |
| [Cloudflare Workers AI](cloudflare.md) | You want to use models served by Cloudflare Workers AI. |
| [Cloudflare AI Gateway](cloudflare-ai-gateway.md) | You want to route models from multiple vendors through an AI Gateway. |
| [Ollama and OpenAI-compatible servers](ollama.md) | You want local Ollama or another OpenAI-compatible endpoint. |
| [OpenCode Go](opencode-go.md) | You want to use models through the OpenCode Go gateway. |
| [Anthropic](anthropic.md) | You want to connect to Anthropic's Claude models. |
| [OpenAI](openai.md) | You want to connect to OpenAI models. |
| [Google](google.md) | You want to use Google's Gemini models. |
| [OpenRouter](openrouter.md) | You want to access models through OpenRouter. |
| [DeepSeek](deepseek.md) | You want to connect to DeepSeek models. |
