# Snodo TypeSafe Jev validator tool

An optional plugin that adds the `decide` function to opted-in LLM validators.
Jev returns typed answers and calibrated probabilities; the LLM remains
responsible for writing the validator verdict.

Install it into the same Python environment as Snodo and configure
`validator_tools: [{name: decide}]` on an LLM validator. The tool accepts
`context` as `diff`, `spec`, or `files` (with up to a small number of repository
relative file paths), and a list of named `questions` using `noul`, `choice`, or
`score` types. Choice criteria map option names to descriptions; score criteria
are ordered level labels.

Per-tool protocol options are `model` (default `jev-latest`), `api_key_env`
(default `TYPESAFE_API_KEY`), `base_url` (default `https://api.typesafe.ai`),
`timeout_seconds`, and `max_input_chars`. The key is read only from the named
environment variable. Each result line reports the typed answer and
probabilities; usage and any context truncation follow. Jev pricing is $0.042
per million input tokens; its maximum context length is unpublished.
