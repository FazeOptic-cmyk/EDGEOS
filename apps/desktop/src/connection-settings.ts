import { randomBytes } from "node:crypto";

const nonce = randomBytes(24).toString("base64");

// A self-contained document avoids file:// access and needs no asset copying
// during packaging. Server values are inserted with textContent/value over IPC.
export const CONNECTION_SETTINGS_URL = `data:text/html;charset=utf-8,${encodeURIComponent(`<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EDGEOS connection settings</title>
<style>
body{margin:0;padding:32px;background:#080d18;color:#e6edf7;font:15px/1.5 system-ui,sans-serif}
h1{font-size:24px;margin:0 0 12px}p{color:#b4c3d9;margin:12px 0}
label{display:block;font-weight:600;margin-top:20px}input{box-sizing:border-box;width:100%;margin:8px 0 14px;padding:12px;border:1px solid #52627c;border-radius:6px;background:#111c30;color:#fff;font:inherit}
button{border:1px solid #52627c;border-radius:6px;padding:10px 16px;background:#203651;color:#fff;font:inherit;cursor:pointer}button.primary{background:#1a775c;border-color:#38b891}
button:disabled{opacity:.55;cursor:wait}button:focus-visible,input:focus-visible{outline:2px solid #69ddbe;outline-offset:3px}
.actions{display:flex;gap:10px;flex-wrap:wrap}.note{font-size:13px}#status{min-height:48px;white-space:pre-line;color:#f6d58e}#current{word-break:break-word}
</style></head><body>
<h1>Connection settings</h1>
<p id="current">Reading current connection…</p>
<p>Connect to an EDGEOS web server you trust. Sign in with your account after connecting. Live mode uses current provider data for research and paper trading only.</p>
<form id="connection-form"><label for="origin">Hosted web server origin</label>
<input id="origin" type="url" placeholder="https://edgeos.your-company.com" autocomplete="off" spellcheck="false" required maxlength="2048" aria-describedby="requirements">
<div class="actions"><button class="primary" id="connect" type="submit">Verify, save &amp; restart</button><button id="local" type="button">Use local demo &amp; restart</button></div></form>
<p id="status" role="status" aria-live="polite"></p>
<p class="note" id="requirements">Your administrator must deploy the live web/API, worker, and Supabase database/authentication first. Verification checks the server identity, data mode, and authentication configuration; it does not establish provider availability or data freshness.</p>
<p class="note">Only the server origin is saved here. Sign-in is kept in a separate browser session for each server. Local demo data stays on this device. Returning to local demo does not erase saved server sign-ins; use “Sign out” in the hosted app first when needed.</p>
<script nonce="${nonce}">
const api = window.edgeosConnection;
const status = document.getElementById("status");
const input = document.getElementById("origin");
const connect = document.getElementById("connect");
const local = document.getElementById("local");
async function submit(action) {
  connect.disabled = true; local.disabled = true; input.disabled = true;
  status.textContent = "Checking connection…";
  try {
    const result = await action();
    status.textContent = result.ok ? "Connection saved. Restarting EDGEOS…" : result.error;
    if (result.ok) return;
  } catch { status.textContent = "Connection settings could not be saved. Close this window and try again."; }
  connect.disabled = false; local.disabled = false; input.disabled = false;
}
document.getElementById("connection-form").addEventListener("submit", (event) => {
  event.preventDefault(); const origin = input.value; void submit(() => api.connect(origin));
});
local.addEventListener("click", () => { void submit(() => api.localDemo()); });
api.read().then((result) => {
  document.getElementById("current").textContent = result.origin
    ? "Current connection: " + result.origin : "Current connection: local demo (offline)";
  input.value = result.origin || "";
  if (result.error) status.textContent = result.error;
  input.focus();
}).catch(() => { status.textContent = "Unable to read connection settings."; });
</script></body></html>`)}`;
