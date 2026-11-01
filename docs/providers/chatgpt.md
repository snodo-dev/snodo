# ChatGPT subscription

Use the `chatgpt` provider to make model calls through a ChatGPT subscription instead of paying for OpenAI API usage. This is distinct from the `openai` provider, which calls the API and requires an API key. Subscription usage is not priced by Snodo, so spend is shown as unpriced.

## Configure

Set `auth: chatgpt-oauth` on the provider and select models using the `chatgpt/<model>` name. No API key is needed:

```yaml
providers:
  chatgpt:
    auth: chatgpt-oauth
llm:
  coder:
    model: chatgpt/<model>
  validator:
    model: chatgpt/<model>
```

Use the exact model identifier supported by your account. The ChatGPT subscription provider may not offer model listing; you can still benchmark a known model with `snodo models --benchmark chatgpt/<model>`.

## Sign in

Authenticate explicitly with:

```sh
snodo models --provider chatgpt --authenticate
```

On a headless machine, Snodo prints a sign-in address and a code, then waits for authorization and saves the token. Open the address in a browser where you can sign in to the ChatGPT account and enter the displayed code. On a machine with a browser, open the printed address there and complete sign-in. Snodo does not start an interactive sign-in during model calls.

The first version supports one ChatGPT account per machine. The token is stored in LiteLLM's token location and its file is restricted to the machine user's permissions; protect access to that account and home directory.

## Token lifetime and renewal

A sign-in lasts about ten days. Snodo refreshes the token automatically when models are called, coordinating refreshes with a lock so concurrent calls do not race. If the sign-in can no longer be refreshed, Snodo asks you to authenticate again. Run `snodo models --provider chatgpt --authenticate` to sign in again, then retry the model call.

`snodo models --provider chatgpt` reports the provider's sign-in state, and `snodo models --check` reports whether configured model access is available. These checks do not replace authentication when Snodo asks you to sign in again.

## How this differs from other credentials

`auth: chatgpt-oauth` selects subscription OAuth authentication; it is not a synthetic API key, and API-key precedence does not supply this sign-in. In contrast, `openai` uses an OpenAI API key and API billing. `snodo cloud login` is separate again: it authenticates Snodo Cloud features such as cloud sync, not model access through a ChatGPT subscription.
