# User configuration reference

Snodo's user-level configuration lives at `~/.snodo/config.yml`. It is separate
from a project's `.snodo/protocol.yml`: user config holds machine-wide model
choices, credentials, cloud settings, and notification destinations; the
protocol declares project policy. Keep credentials in user config (or resolve
them from the environment), not in a protocol committed to a repository.

Snodo creates a minimal config on first use. The effective defaults below also
apply when a section or key is omitted. `snodo config show` redacts configured
credentials and notification destinations. The config file is saved with
owner-only (`0600`) permissions. Saving through Snodo rewrites YAML, so comments
in the file are not preserved.

## Top-level settings

| Key | Default | Description |
|---|---|---|
| `model` | `claude-sonnet-4-20250514` | Default model for coder and other LLM roles that do not specify a role-specific model. |
| `default_model` | unset | Legacy fallback read after `model`; if set, it takes precedence over `model` in the model getter. Prefer `model`. |
| `providers` | Built-in provider catalog; no user overrides | Provider-specific credentials, model discovery endpoints, routing and headers. See [Providers](#providers). |
| `engine` | `max_subtask_depth: 3`, `max_session_age_days: 30`, `token_ttl_seconds: 600` | Engine-wide limits. See [Engine](#engine). |
| `llm` | See [LLM tuning](#llm-tuning) | Model roles, request budgets, retries and recon fan-out. |
| `cloud` | See [Cloud](#cloud) | Cloud sync credentials and service endpoints. |
| `notifications` | No targets; all supported event types; silence threshold `900` seconds | Optional background-job notifications. See [Notifications](#notifications). |
| `api_keys` | Unsupported legacy format | A non-empty `api_keys` section without `providers` is rejected. Migrate credentials to `providers`. |

The `mcp` and `opencode` objects emitted by older/default config generation
(`mcp.port: 55441`, `opencode.session_token_warning: 150000`, and
`opencode.session_reset_on_model_change: false`) are retained data, not active
Snodo user-config settings; current callers do not read them.

## Providers

Each entry under `providers` is keyed by a provider name. Built-in entries are
`anthropic`, `openai`, `openrouter`, `google`, `cloudflare`, and `deepseek`;
unlisted entries can configure custom/OpenAI-compatible providers. For a
built-in provider, omitted settings inherit the catalog values; for a custom
provider, the defaults are empty strings and an empty `extra_headers` map.

| Provider key | Default | Description |
|---|---|---|
| `api_key` | `""` | Literal API key, encrypted reference `@keys/<provider>.key`, or empty to use another source. Prefer an environment reference for portable setups. |
| `api_key_env` | `""` | Name of an environment variable containing the key. Built-in provider defaults set this (for example `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `CLOUDFLARE_API_KEY`, or `DEEPSEEK_API_KEY`). |
| `api_key_ref` | `""` | Named credential reference: `env:VARIABLE_NAME` or `command:COMMAND`. A command is split into arguments, has a 10-second timeout and uses trimmed stdout. |
| `models_endpoint` | `""` | Provider model-catalog endpoint; built-ins supply their own endpoint. |
| `account_id` | `""` | Provider account identifier, used by Cloudflare. |
| `account_id_env` | `""` | Environment-variable name for the account identifier; Cloudflare defaults to `CLOUDFLARE_ACCOUNT_ID`. |
| `base_url` | `""` | Custom API base URL for LiteLLM requests. |
| `litellm_provider` | `""` | LiteLLM provider route name, useful for custom-compatible providers. |
| `extra_headers` | `{}` | Extra HTTP headers; `{task_id}` in a value is substituted with the current task ID (`unknown` when absent). |
| `probe_model` | `""` | Model used by provider key checks. Built-ins supply a probe model. |
| `catalog_provider` | `""` | Provider name used for model catalog lookup when it differs from the config entry name. |

Example:

```yaml
providers:
  openai:
    api_key_env: OPENAI_API_KEY
  local:
    base_url: http://localhost:11434/v1
    litellm_provider: openai
    api_key: local
    extra_headers:
      x-task: "{task_id}"
```

Credential selection prefers a configured `api_key`, then `api_key_env`, then
`api_key_ref`. `env:` and `command:` references are resolved only when the
credential is used. Provider keys can also be moved to local encrypted files
with `snodo config --encrypt-provider-keys`.

## Engine

| Key | Default | Description |
|---|---:|---|
| `engine.max_subtask_depth` | `3` | Maximum subtask recovery depth; setter accepts integers 1–10. |
| `engine.max_session_age_days` | `30` | Maximum session age in days; setter accepts integers 1–365. |
| `engine.token_ttl_seconds` | `600` | Validation-token lifetime in seconds; setter accepts integers 60–86400. |

```yaml
engine:
  max_subtask_depth: 3
  max_session_age_days: 30
  token_ttl_seconds: 600
```

## LLM tuning

The `llm` section is optional and validated against a strict schema: unknown
keys are errors. Role models fall back to top-level `model`. A protocol
validator's own `model` setting takes precedence over the configured role model.

| Key | Default | Description |
|---|---:|---|
| `llm.num_retries` | `3` | LiteLLM retry count for transient errors (0–10). |
| `llm.coder.model` | unset | Coder model; falls back to `model`. |
| `llm.coder.sandboxed` | `false` | Run coder against a discarded copy of the task workspace. |
| `llm.coder.max_tokens` | `16000` | Coder completion token budget; minimum 1. |
| `llm.coder.max_tool_turns` | `6` | Coder tool-turn limit (1–200). |
| `llm.coder.timeout_seconds` | `1800` | Coder wall-clock timeout; minimum 1. |
| `llm.coder.silence_timeout_seconds` | `600` | Stop a subprocess coder after this many seconds without output; minimum 1. |
| `llm.coder.concurrency` | `1` | Maximum concurrent coders for this operator; minimum 1. |
| `llm.validator.model` | unset | Validator model; falls back to `model`. |
| `llm.validator.max_tokens` | `1500` | Validator completion token budget; minimum 1. |
| `llm.validator.max_tool_turns` | `6` | Validator read-tool turn limit (1–200). |
| `llm.validator_llm.model` | unset | Legacy-compatible validator role model; falls back to `model`. |
| `llm.classifier.model` | unset | Classifier model; falls back to `model`. |
| `llm.classifier.max_tokens` | `500` | Classifier completion token budget; minimum 1. |
| `llm.classifier.temperature` | `0.0` | Classifier temperature (0–2). |
| `llm.recon.num_agents` | `1` | Default number of recon agents; minimum 1. |
| `llm.recon.models` | `[]` | Ordered model priority list for recon. Empty uses the configured default model. |
| `llm.wave.max_age_days` | `14` | Hard expiry age for a wave; minimum 1. |
| `llm.wave.max_idle_days` | `5` | Idle timeout before a wave closes; minimum 1. |

Example:

```yaml
llm:
  num_retries: 3
  coder:
    model: claude-sonnet-4-20250514
    max_tokens: 16000
    max_tool_turns: 6
    timeout_seconds: 1800
    silence_timeout_seconds: 600
    concurrency: 1
  validator:
    model: claude-sonnet-4-20250514
    max_tokens: 1500
    max_tool_turns: 6
  classifier:
    max_tokens: 500
    temperature: 0.0
  recon:
    num_agents: 1
    models: []
  wave:
    max_age_days: 14
    max_idle_days: 5
```

Older `llm.wave.max_tokens` and `llm.wave.temperature` keys are migrated to
`llm.classifier` with a deprecation warning. Remove them after migration.

## Cloud

| Key | Default | Description |
|---|---|---|
| `cloud.api_key` | `""` | Credential for cloud admission, audit sync and liveness. |
| `cloud.api_url` | `https://api.snodo.dev` | Cloud API base for audit ingest. |
| `cloud.tunnel_api_url` | `https://app.snodo.dev` | Cloud app base for tunnel provisioning; independent of `api_url`. |
| `cloud.sync_enabled` | `false` | Enable cloud audit sync when an API key is configured. |
| `cloud.liveness_interval_seconds` | `60` | Liveness push cadence/throttle in seconds; non-positive or unreadable values use 60. |
| `cloud.liveness_url` / `cloud.liveness_api_url` | Derived from `api_url` | Optional equivalent override for the liveness app base. A trailing `/v1` is removed. |
| `cloud.lease_url` / `cloud.lease_api_url` | Derived from `api_url` | Optional equivalent override for the session-admission app base. |

```yaml
cloud:
  api_key: env:SNODO_CLOUD_API_KEY
  api_url: https://api.snodo.dev
  tunnel_api_url: https://app.snodo.dev
  sync_enabled: false
  liveness_interval_seconds: 60
```

Cloud's API key is a literal string when set here; keep it private. The URL
overrides are optional and normally need not be configured.

## Notifications

Notifications are per-user settings in `~/.snodo/config.yml`, not project
protocol settings. They are opt-in: with no valid targets, no delivery occurs.
Delivery is detached and best-effort.

| Key | Default | Description |
|---|---|---|
| `notifications.targets` | `[]` | Destinations. Supported `type` values are `ntfy`, `webhook`, `slack`, `discord`, and `teams`. |
| `notifications.events` | `job_finished`, `task_halted`, `authorization_needed`, `job_silent` | Event names to deliver; set a subset to filter. |
| `notifications.silence_threshold_seconds` | `900` | Log-silence interval before a `job_silent` notification; invalid values use 900 and values are clamped to at least 1 second. |

Each target has a required-in-practice `type` and `url`; `name` is an optional
display label. `token` is optional and sent as a bearer token. `headers` is an
optional extra-header map for generic `webhook` targets. `ntfy` sends plain text;
Slack and Discord use their incoming-webhook message shapes; Teams sends an
Adaptive Card envelope to a Workflows/Power Automate webhook. Generic `webhook`
receives Snodo's JSON event. URLs and credentials are redacted by `snodo config
show`. Check delivery with `snodo notify test`.

```yaml
notifications:
  targets:
    - type: ntfy
      name: phone
      url: env:SNODO_NTFY_URL
      token: env:SNODO_NTFY_TOKEN
    - type: slack
      name: slack
      url: env:SNODO_SLACK_WEBHOOK_URL
    - type: discord
      name: discord
      url: env:SNODO_DISCORD_WEBHOOK_URL
    - type: teams
      name: teams
      url: env:SNODO_TEAMS_WORKFLOW_WEBHOOK_URL
      token: env:SNODO_TEAMS_TOKEN
    - type: webhook
      name: generic-hook
      url: env:SNODO_WEBHOOK_URL
      token: env:SNODO_WEBHOOK_TOKEN
      headers:
        X-Source: snodo
  events:
    - job_finished
    - task_halted
    - authorization_needed
    - job_silent
  silence_threshold_seconds: 900
```

For any target `url` or optional `token`, an `env:VARIABLE_NAME` or
`command:COMMAND` reference is resolved at delivery time. In particular,
`url: env:SNODO_WEBHOOK_URL` reads the webhook endpoint from that environment
variable, keeping it out of the YAML. Supply only the token when the endpoint
requires bearer authentication.

## Related guides

- [Choosing models](choosing-models.md)
- [Running Snodo unattended](running-unattended.md)
- [Protocol reference](protocol.md)
