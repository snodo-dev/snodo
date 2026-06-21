# W4-03: Tool loop fails loud when no files delivered

## Intent
_call_llm_with_tools has exit paths that return raw free-text or empty
string when the model never calls submit_files. These flow to
_parse_response which produces a cryptic "Failed to parse response as
JSON array" error, or silently fails. Apply the W3-05 principle: a tool
loop that ends without delivering files via submit_files should raise a
clear, diagnostic error — not return empty/unparseable content.

This is correct regardless of WHY a model fails to call submit_files
(Gemini history degradation is a separate diagnostic, not this ticket).

## What to change

### coders/litellm.py — _call_llm_with_tools exit paths
Read the method in full first. The exit paths today (per recon):
- Path 1: submit_files found → return json.dumps(files) — KEEP unchanged
- Path 3: free-text after retry exhausted → return msg.content
- Path 4: turn cap, last assistant content → return that content
- Path 5: turn cap, no content → return ""

Change paths 3, 4, 5: instead of returning raw/empty content that may
fail downstream, attempt _parse_response on the content FIRST (inside
the loop). If it parses to a valid file list, return it. If it does
NOT parse (empty, prose, malformed), raise:

  ParseError(
    "Coder completed the tool loop without delivering files via "
    "submit_files. Final response was empty or unparseable. "
    f"Model: {self.model}, turns used: {turn+1}, "
    f"finish_reason: {finish_reason}, "
    f"content preview: {content[:200] if content else '(empty)'}"
  )

The error message must include: model, turns used, finish_reason,
and a content preview. This makes the failure diagnostic instead of
cryptic.

## Acceptance criteria
- submit_files happy path unchanged
- A loop ending with parseable free-text content still succeeds
  (backward compatible — some models legitimately return JSON as text)
- A loop ending with empty content raises ParseError with the
  diagnostic message (model, turns, finish_reason, preview)
- A loop ending with unparseable prose raises the same diagnostic error
- The error is distinguishable from a normal parse failure — it names
  submit_files and the turn count

## Testing
- Unit test: loop returns empty "" → ParseError with diagnostic message
- Unit test: loop returns valid JSON text → still parses successfully
- Unit test: loop returns prose → ParseError with content preview
- Unit test: submit_files happy path → unchanged, no error
- Full suite passes clean

## Constraints
- Read coders/litellm.py _call_llm_with_tools and _parse_response
  in full before touching anything
- Do not change the submit_files extraction path
- Do not attempt to fix WHY a model doesn't call submit_files —
  that is a separate diagnostic ticket
- Touch only coders/litellm.py
