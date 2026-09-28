"""Browser and MCP Apps views for following a background job."""

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import quote

WATCH_JOB_RESOURCE_URI = "ui://snodo/watch-job"


class WatchLinkIssuer:
    """Issue and verify short-lived, job-scoped browser capabilities."""

    def __init__(self, ttl_seconds=24 * 60 * 60, secret=None, clock=time.time):
        self.ttl_seconds = int(ttl_seconds)
        if self.ttl_seconds <= 0:
            raise ValueError("watch link lifetime must be positive")
        self._secret = secret or secrets.token_bytes(32)
        self._clock = clock

    def issue(self, job_id):
        payload = {"job": str(job_id), "exp": int(self._clock()) + self.ttl_seconds,
                   "nonce": secrets.token_urlsafe(16)}
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
        return encoded + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    def verify(self, token, job_id=None):
        try:
            encoded, supplied = token.split(".", 1)
            expected = base64.urlsafe_b64encode(
                hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
            ).rstrip(b"=").decode()
            if not hmac.compare_digest(supplied, expected):
                return None
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if (not isinstance(payload.get("job"), str) or
                    int(payload.get("exp", 0)) <= int(self._clock()) or
                    not payload.get("nonce") or
                    (job_id is not None and not hmac.compare_digest(payload["job"], str(job_id)))):
                return None
            return payload["job"]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None


class WatchJobASGI:
    """Expose only the capability page and its read-only event stream."""

    def __init__(self, app, protocol_server, issuer, base_url):
        self.app = app
        self.protocol_server = protocol_server
        self.issuer = issuer
        self.base_url = base_url.rstrip("/")

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        parts = path.strip("/").split("/")
        if scope.get("type") != "http" or len(parts) not in (2, 3) or parts[0] != "watch":
            return await self.app(scope, receive, send)
        # Uvicorn's access logger reads the ASGI scope after this app returns.
        # Keep the bearer capability out of request logs as well as MCP logs.
        scope["path"] = "/watch/[capability]"
        scope["raw_path"] = b"/watch/[capability]"
        job_id = self.issuer.verify(parts[1])
        if job_id is None or (len(parts) == 3 and parts[2] != "events") or scope.get("method") != "GET":
            return await _response(send, 404, b"Not found")
        if len(parts) == 2:
            html = BROWSER_WATCH_HTML.replace("__EVENTS__", quote(path.rstrip("/") + "/events", safe="/"))
            return await _response(send, 200, html.encode(), b"text/html; charset=utf-8")
        await self._stream(job_id, receive, send)

    async def _stream(self, job_id, receive, send):
        async def emit(status, logs):
            payload = json.dumps({"job_id": job_id, "status": status, "logs": logs}, default=str)
            await send({"type": "http.response.body", "body": f"data: {payload}\n\n".encode(), "more_body": True})

        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream"),
                                (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]})
        while True:
            status = await asyncio.to_thread(self.protocol_server.call_tool, "get_job_status", {"job_id": job_id})
            logs = await asyncio.to_thread(self.protocol_server.call_tool, "get_job_logs", {"job_id": job_id, "tail": 10})
            # The same handlers/redaction path as the existing MCP tools is used.
            if isinstance(status, str):
                status = json.loads(status)
            if isinstance(logs, str):
                logs = json.loads(logs)
            public_status = {
                key: status.get(key)
                for key in ("id", "status", "exit_code", "created_at", "started_at", "completed_at")
            }
            await emit(public_status, {"log": logs.get("log", "")})
            if public_status.get("status") in ("completed", "failed", "cancelled", "unmerged"):
                break
            try:
                message = await asyncio.wait_for(receive(), timeout=3)
                if message.get("type") == "http.disconnect":
                    break
            except asyncio.TimeoutError:
                pass
        await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _response(send, status, body, content_type=b"text/plain; charset=utf-8"):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", content_type), (b"cache-control", b"no-store"),
                            (b"x-content-type-options", b"nosniff")]})
    await send({"type": "http.response.body", "body": body, "more_body": False})


BROWSER_WATCH_HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>Snodo job watch</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:auto;padding:16px}header{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}h1{font-size:1.2rem}#status{font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f3f5;padding:12px;border-radius:8px;max-height:65vh;overflow:auto}</style><header><h1>Job <code id="id"></code></h1><span id="status">Connecting…</span><span id="elapsed"></span></header><p id="outcome" hidden></p><pre id="logs">Waiting for output…</pre><div id="message" aria-live="polite"></div><script>const FINAL=new Set(["completed","failed","cancelled","unmerged"]);const start=Date.now();const source=new EventSource("__EVENTS__");source.onmessage=e=>{const d=JSON.parse(e.data),s=d.status;document.querySelector("#id").textContent=d.job_id;document.querySelector("#status").textContent=s.status||"unknown";document.querySelector("#logs").textContent=d.logs.log||"(no stdout output)";const began=Number(s.started_at||s.created_at)*1000;const end=Number(s.completed_at)*1000||Date.now();const sec=Math.max(0,Math.floor((end-(began||start))/1000));document.querySelector("#elapsed").textContent=`${Math.floor(sec/60)}:${String(sec%60).padStart(2,"0")} elapsed`;if(FINAL.has(s.status)){const out=document.querySelector("#outcome");out.textContent=`Final outcome: ${s.status}${s.exit_code==null?"":` · exit code ${s.exit_code}`}`;out.hidden=false;document.querySelector("#message").textContent="Job finished; live updates stopped.";source.close()}};source.onerror=()=>document.querySelector("#message").textContent="Connection interrupted; reconnecting…";</script></html>'''

WATCH_JOB_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Snodo job watch</title>
<style>
:root{color-scheme:light dark;font:14px/1.5 system-ui,sans-serif;color:#202124;background:#fff}
@media(prefers-color-scheme:dark){:root{color:#e8eaed;background:#202124}}
body{margin:0;padding:16px;max-width:900px}header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
h1{font-size:17px;margin:0}#status{font-weight:650}#elapsed{opacity:.72}#outcome{margin:8px 0;color:#16803c}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f4f6;border-radius:8px;padding:12px;max-height:420px;overflow:auto;margin:10px 0 0}
@media(prefers-color-scheme:dark){pre{background:#2b2d31}}#message{opacity:.72;margin-top:8px}
</style>
</head>
<body>
<header><h1>Job <code id="job-id">waiting for job id</code></h1><span id="status">Connecting…</span><span id="elapsed"></span></header>
<div id="outcome" hidden></div><pre id="logs">Waiting for job output…</pre><div id="message" aria-live="polite"></div>
<script>
"use strict";
const FINAL_STATUSES = ["completed", "failed", "cancelled", "unmerged"];
const POLL_INTERVAL_MS = 3000;
const $ = (id) => document.getElementById(id);
let requestId = 0;
let jobId = "";
let timer = null;
let refreshing = false;
let stopped = false;
let startedAt = null;
let lastStatus = null;
let lastLog = null;

function shouldStopWatching(status) { return FINAL_STATUSES.includes(status); }
function rpc(method, params) {
  const id = ++requestId;
  return new Promise((resolve, reject) => {
    const listener = (event) => {
      const message = event.data;
      if (!message || message.id !== id) return;
      window.removeEventListener("message", listener);
      if (message.error) reject(new Error(message.error.message || "Host request failed"));
      else resolve(message.result || {});
    };
    window.addEventListener("message", listener);
    window.parent.postMessage({jsonrpc:"2.0", id, method, params}, "*");
  });
}
function notify(method, params) {
  window.parent.postMessage({jsonrpc:"2.0", method, params}, "*");
}
async function connect() {
  await rpc("ui/initialize", {
    protocolVersion:"2026-01-26",
    appCapabilities:{},
    appInfo:{name:"snodo-job-watch",version:"1.0.0"}
  });
  notify("ui/notifications/initialized", {});
}
function contentText(result) {
  const block = (result.content || []).find((item) => item.type === "text");
  if (block) return block.text || "";
  return typeof result === "string" ? result : "";
}
function parseToolText(result) {
  const text = contentText(result);
  try { return JSON.parse(text); } catch (_) { return text; }
}
async function callTool(name, args) {
  return rpc("tools/call", {name, arguments:args});
}
function formatElapsed(status) {
  const start = Number(status.started_at || status.created_at || startedAt);
  if (!Number.isFinite(start)) return "";
  const end = Number(status.completed_at) || Date.now() / 1000;
  const seconds = Math.max(0, Math.floor(end - start));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")} elapsed`
           : `${m}:${String(s).padStart(2,"0")} elapsed`;
}
async function refresh() {
  if (!jobId || refreshing || stopped) return;
  refreshing = true;
  try {
    const status = parseToolText(await callTool("get_job_status", {job_id:jobId}));
    const logs = parseToolText(await callTool("get_job_logs", {job_id:jobId,tail:10}));
    if (typeof status !== "object" || !status || typeof logs !== "object" || !logs) {
      throw new Error("Unexpected job response");
    }
    startedAt = status.started_at || status.created_at || startedAt;
    const changed = (lastStatus !== null && lastStatus !== status.status) ||
      (lastLog !== null && lastLog !== logs.log);
    lastStatus = status.status;
    lastLog = logs.log;
    $("status").textContent = status.status || "unknown";
    $("elapsed").textContent = formatElapsed(status);
    $("logs").textContent = logs.log || "(no stdout output)";
    if (shouldStopWatching(status.status)) {
      stopped = true;
      if (timer !== null) clearInterval(timer);
      timer = null;
      const exit = status.exit_code == null ? "" : ` · exit code ${status.exit_code}`;
      $("outcome").textContent = `Final outcome: ${status.status}${exit}`;
      $("outcome").hidden = false;
      $("message").textContent = "Job finished; live refresh stopped.";
    } else {
      const duration = formatElapsed(status);
      const activity = changed ? "status/output changed" : "no status change";
      $("message").textContent = `~ watching · ${status.job_type || "job"} ${duration} · ${activity}`;
    }
  } catch (error) {
    $("message").textContent = `Could not refresh job: ${error.message}`;
  } finally {
    refreshing = false;
  }
}
function startWatching(id) {
  if (typeof id !== "string" || !id || jobId) return;
  jobId = id;
  $("job-id").textContent = id;
  startedAt = Date.now() / 1000;
  refresh();
  timer = setInterval(refresh, POLL_INTERVAL_MS);
}
window.addEventListener("message", (event) => {
  const message = event.data;
  if (!message || message.method !== "ui/notifications/tool-input") return;
  startWatching(message.params && message.params.arguments && message.params.arguments.job_id);
});
connect().catch((error) => { $("message").textContent = `Could not connect to host: ${error.message}`; });
</script>
</body>
</html>'''
