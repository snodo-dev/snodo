# Fix cloud sync: log response body on error + add debug logging

## Intent
HTTP 403 from the sync endpoint is silently swallowed — the server's
error message is never read or logged. And --verbose shows nothing
useful because there are zero _logger.debug() calls. Both are in
infrastructure/cloud_sync.py.

## What to change — cloud_sync.py only

### 1. Read response body on ALL HTTP errors (403, 429, 5xx)
In the urllib.error.HTTPError handler, read e.fp before the status
checks:
  body = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""

Then include body in every error log:
  403/other: _logger.warning("HTTP %d: %s", e.code, body)
  429: _logger.warning("HTTP 429 retry_after=%s: %s", retry_after, body)
  5xx: _logger.warning("HTTP %d attempt %d: %s", e.code, attempt, body)

### 2. Add debug logging at key points
Before the request:
  _logger.debug("POST %s — %d events (seq %d-%d)",
      url, len(batch), batch[0].sequence, batch[-1].sequence)
  _logger.debug("Authorization: Bearer %s...", api_key[:16])

After success (200):
  _logger.debug("Response 200 — accepted=%s", resp_body)

On cursor advance:
  _logger.debug("Cursor advanced to sequence %d", last_seq)

## Acceptance criteria
- 403 response body visible in output (even without --verbose)
  as a WARNING — the engineer needs to see WHY it's 403
- snodo --verbose cloud sync shows: URL, key prefix, event range,
  response body on success and failure
- No sensitive data logged in full (key truncated to 16 chars)

## Testing
- Unit: HTTPError response body read and included in warning
- Unit: debug log emitted before request (mock _logger.debug)
- Full suite passes

## Constraints
- Read infrastructure/cloud_sync.py (_post_batch, error handlers)
- Touch only cloud_sync.py
- WARNING level for HTTP errors (visible without --verbose)
- DEBUG level for request details (only with --verbose)
- Key truncated to first 16 chars in all logs
