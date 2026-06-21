# Spec: replace --filter expression with discrete, shell-safe flags

## Why
snodo models --filter uses a field-op-value mini-language with < > =
operators. Problems (confirmed by recon):
- < and > are shell redirects — --filter=output_cost<5 makes the shell
  redirect to a file named 5. Requires quoting users won't remember.
- : isn't an operator, so id:gemma silently substring-matched the
  literal "id:gemma" and returned nothing.
- requires knowing internal field names.

Replace with discrete flags. No shell collision, no quoting, visible
in --help.

## New flags on `snodo models`
Replace --filter with:
  --id-contains TEXT        substring match on id/display_name (case-insensitive)
  --max-output-cost FLOAT   keep models with output cost per 1M <= value
  --min-output-cost FLOAT   keep models with output cost per 1M >= value
  --max-input-cost FLOAT    keep models with input cost per 1M <= value
  --min-context INT         keep models with context_window >= value

Multiple flags combine with AND. All optional.

## Handling unknown/missing values
Models with unknown price (unpriced CF models) or context_window==0:
- a --max-output-cost / --min-* filter EXCLUDES models whose value is
  unknown (can't satisfy a numeric bound). This is the desired effect:
  --max-output-cost=5 naturally drops the embedding/image models that
  show "unknown", leaving only priced chat models. Document this.
- --id-contains is independent of price/context, matches on text only.

## Remove
Delete _apply_filter expression parser and the --filter option.
(No back-compat shim — it never worked from a shell anyway.)

## Tests
- --id-contains=gemma -> only gemma* models
- --max-output-cost=5 -> only priced models <=5/1M out (excludes unknowns)
- --max-output-cost=1 -> Gemma + Mistral (the cheap ones)
- --min-context=100000 -> only models with context >= 100k (excludes
  the "—"/0 context ones)
- combined: --id-contains=gemma --max-output-cost=1 -> AND semantics
- no flags -> all models (unchanged)

## Touch only
cli/commands/models_cmd.py (swap --filter for discrete flags),
cli/main.py if flag registration lives there

Commit: feat(models): shell-safe discrete filter flags, drop --filter expression
