# ADR 061: ChatGPT subscription authentication through an explicit provider mode

**Status:** Accepted  
**Date:** 2026-11-01

## Context

Operators need to use a ChatGPT subscription for model access without confusing it with OpenAI API billing or Snodo Cloud authentication. Subscription OAuth is not an API key, and a headless operator must be able to complete sign-in deliberately. Token refresh also needs coordination because LiteLLM does not lock refreshes.

## Decision

- Configure subscription authentication explicitly with `auth: chatgpt-oauth`, rather than manufacturing a fake API key.
- Start sign-in only through `snodo models --provider <name> --authenticate`; model requests must not fall through to interactive authentication.
- Refresh tokens under Snodo's own lock because LiteLLM's refresh path takes no lock.
- Support one ChatGPT account per machine for now, storing the token in LiteLLM's token location with owner-only file permissions.
- Validators use LiteLLM only, so subscription-authenticated models work for validators through the same provider integration.

## Consequences

Snodo calls LiteLLM's private ChatGPT authenticator method `_refresh_tokens` to avoid its public token method's possible interactive sign-in fallback. This is a compatibility dependency; a test guards against upgrades removing the method. If refresh can no longer recover the sign-in, the operator must authenticate again with `--authenticate`. Provider spend is shown as unpriced.

## Alternatives

- Treat the OAuth token as an API key: rejected because it misrepresents the credential and blurs API-key precedence.
- Allow model calls to initiate sign-in: rejected because it can block unattended or headless runs.
- Support multiple accounts initially: deferred to keep machine-level token selection unambiguous.
