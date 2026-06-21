# Fix cloud sync: switch from urllib.request to httpx

## Intent
Cloudflare returns error 1010 (bot/access block) when the dispatcher
uses urllib.request — its Python-urllib user-agent triggers bot
detection. httpx is already a dependency (used by model_discovery.py)
and sends proper headers by default. Switch the dispatcher to httpx.

## What to change — cloud_sync.py only

Replace the urllib.request.Request / urllib.request.urlopen calls in
_post_batch with httpx.post():

  import httpx

  response = httpx.post(
      url,
      content=body,
      headers={
          "Authorization": f"Bearer {api_key}",
          "Content-Type": "application/json",
      },
      timeout=30.0,
  )

  if response.status_code == 200:
      # success path
  elif response.status_code == 429:
      # retry_after from response.headers
  elif response.status_code >= 500:
      # exponential backoff
  else:
      # log response.status_code + response.text[:500]
      return False

Remove the urllib imports. Keep all retry/backoff/cursor logic
identical — only the HTTP call mechanism changes.

## Acceptance criteria
- snodo cloud sync --all succeeds (no 1010)
- All existing retry/backoff behaviour preserved
- httpx already a dep — no new dependencies
- urllib.request no longer used in cloud_sync.py

## Testing
- Existing cloud sync tests pass (mock httpx not urllib)
- Full suite passes

## Constraints
- Read infrastructure/cloud_sync.py (_post_batch) and
  infrastructure/model_discovery.py (httpx usage pattern — follow it)
- Touch only cloud_sync.py
- Keep retry/backoff/cursor logic identical
