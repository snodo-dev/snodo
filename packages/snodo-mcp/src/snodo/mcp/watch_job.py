"""Self-contained MCP Apps view for following a background job."""

WATCH_JOB_RESOURCE_URI = "ui://snodo/watch-job"

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
