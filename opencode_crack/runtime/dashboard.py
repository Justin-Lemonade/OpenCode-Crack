"""Modern read-only local dashboard for opencode_crack + OpenCode Swarm.

Run with::
    python -m opencode_crack.runtime.dashboard

The dashboard observes both control.db and the documented OpenCode Swarm
SQLite database. It never mutates runtime state.
"""
from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from opencode_crack.config import CONTROL_DB_PATH, SWARM_DB_PATH
from opencode_crack.runtime import control_db

HOST = "127.0.0.1"
PORT = 8765

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenCode-Crack Swarm Control</title>
<style>
:root{color-scheme:dark;--bg:#090b10;--panel:#11151d;--panel2:#171c26;--line:#28303d;--text:#eef2f7;--muted:#8f9aaa;--accent:#8ab4ff;--good:#69d39a;--warn:#f4c76a;--bad:#ff7f86}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#090b10,#0d1118 55%,#0b1018);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1500px;margin:auto;padding:28px 22px 60px}
header{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;margin-bottom:24px}h1{font-size:28px;margin:0}.sub{color:var(--muted);margin-top:5px}.refresh{font-size:12px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px}.card{background:rgba(17,21,29,.9);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 10px 30px rgba(0,0,0,.15)}.metric .label{color:var(--muted);font-size:12px}.metric .value{font-size:27px;font-weight:700;margin-top:4px}
.section{margin-top:18px}.section h2{font-size:16px;margin:0 0 12px}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse;min-width:850px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);background:var(--panel2)}tr:last-child td{border-bottom:0}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}.muted{color:var(--muted)}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:11px}.running{color:var(--good);border-color:#27583f}.idle{color:var(--muted)}.error,.failed{color:var(--bad);border-color:#633238}.working{color:var(--accent);border-color:#304c78}.warn{color:var(--warn);border-color:#66542a}.task{max-width:440px}.message{max-width:600px;white-space:pre-wrap;word-break:break-word}.detail{font-size:12px;color:var(--muted);margin-top:3px}.empty{text-align:center;color:var(--muted);padding:24px}.foot{margin-top:22px;color:var(--muted);font-size:11px}
@media(max-width:900px){.grid{grid-template-columns:repeat(3,1fr)}header{align-items:flex-start;flex-direction:column}}@media(max-width:560px){.grid{grid-template-columns:repeat(2,1fr)}main{padding:18px 12px}}
</style></head><body><main>
<header><div><h1>OpenCode-Crack Swarm Control</h1><div class="sub">Agent roles, exact task ownership, live sessions, communication and runtime state</div></div><div class="refresh">Auto-refresh: 5s · <span id="updated">loading</span></div></header>
<div id="app"><div class="card empty">Loading runtime state…</div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const pill=v=>`<span class="pill ${String(v||'').toLowerCase().replace(/[^a-z]/g,'')}">${esc(v||'—')}</span>`;
function taskFor(agent,d){const leases=(d.leases||[]).filter(x=>x.agent_id===agent.agent_id);const sessions=(d.sessions||[]).filter(x=>x.agent_id===agent.agent_id);return {lease:leases[0],session:sessions[0]}}
async function load(){try{const r=await fetch('/api/state',{cache:'no-store'});const d=await r.json();
const agents=d.agents||[],sessions=d.sessions||[],leases=d.leases||[],events=d.events||[],msgs=d.messages||[],sa=d.swarm_agents||[],sm=d.swarm_messages||[],sw=d.swarms||[];
const activeLeases=leases.filter(x=>new Date(x.expires_at)>new Date());
let h=`<div class="grid">${[['Agents',agents.length],['Working',agents.filter(x=>['running','working'].includes(x.status)).length],['Sessions',sessions.length],['Active leases',activeLeases.length],['Swarms',sw.length],['Messages',sm.length+msgs.length]].map(x=>`<div class="card metric"><div class="label">${x[0]}</div><div class="value">${x[1]}</div></div>`).join('')}</div>`;
h+=`<section class="section card"><h2>Every agent · role + exact current work</h2><div class="tablewrap"><table><tr><th>Agent</th><th>Role</th><th>Manager</th><th>Status</th><th>Exact task</th><th>Session</th><th>Lease / heartbeat</th><th>Model</th></tr>${agents.map(a=>{const t=taskFor(a,d);return `<tr><td><b>${esc(a.agent_id)}</b><div class="detail">${esc(a.personality||a.notes||'')}</div></td><td>${esc(a.role)}</td><td>${esc(a.manager_id||'—')}</td><td>${pill(a.status)}</td><td class="task">${t.lease?`<b>${esc(t.lease.task_id)}</b><div class="detail">Claimed ${esc(t.lease.claimed_at)}<br>Expires ${esc(t.lease.expires_at)}</div>`:'<span class="muted">No active task lease</span>'}</td><td class="mono">${t.session?esc(t.session.session_id):'—'}</td><td>${t.lease?`<span class="mono">${esc(t.lease.heartbeat_at||'—')}</span>`:'—'}</td><td>${esc(a.model)}</td></tr>`}).join('')||'<tr><td colspan="8" class="empty">No registered agents</td></tr>'}</table></div></section>`;
h+=`<section class="section card"><h2>Swarm runtime · what OpenCode is actually running</h2><div class="tablewrap"><table><tr><th>Swarm</th><th>Agent</th><th>Session</th><th>Status</th><th>Cost</th><th>Result / latest state</th><th>Updated</th></tr>${sa.map(a=>`<tr><td>${esc(a.swarm_id)}</td><td><b>${esc(a.name)}</b></td><td class="mono">${esc(a.session_id||'—')}</td><td>${pill(a.status)}</td><td>$${Number(a.cost_usd||0).toFixed(4)}</td><td class="message">${esc(a.result||'No result recorded')}</td><td>${esc(a.updated_at)}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">No Swarm runtime state yet</td></tr>'}</table></div></section>`;
h+=`<section class="section card"><h2>Active sessions</h2><div class="tablewrap"><table><tr><th>Agent</th><th>Task</th><th>Session</th><th>Status</th><th>Model</th><th>Started</th></tr>${sessions.map(s=>`<tr><td>${esc(s.agent_id)}</td><td><b>${esc(s.task_id||'—')}</b></td><td class="mono">${esc(s.session_id)}</td><td>${pill(s.status)}</td><td>${esc(s.model||'—')}</td><td>${esc(s.started_at)}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No active sessions</td></tr>'}</table></div></section>`;
h+=`<section class="section card"><h2>Agent communication</h2><div class="tablewrap"><table><tr><th>Source</th><th>From</th><th>To</th><th>Session</th><th>Message</th><th>Time</th></tr>${sm.map(m=>`<tr><td>Swarm</td><td>${esc(m.from_agent)}</td><td>${esc(m.to_agent)}</td><td>—</td><td class="message">${esc(m.body)}</td><td>${esc(m.created_at)}</td></tr>`).join('')}${msgs.map(m=>`<tr><td>Control</td><td>${esc(m.from_agent_id)}</td><td>${esc(m.to_agent_id)}</td><td class="mono">${esc(m.session_id||'—')}</td><td class="message">${esc(m.content)}</td><td>${esc(m.sent_at)}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No messages</td></tr>'}</table></div></section>`;
h+=`<section class="section card"><h2>Recent lifecycle events</h2><div class="tablewrap"><table><tr><th>When</th><th>Event</th><th>Agent</th><th>Task</th><th>Session</th><th>Payload</th></tr>${events.map(e=>`<tr><td>${esc(e.occurred_at)}</td><td>${pill(e.event_type)}</td><td>${esc(e.agent_id||'—')}</td><td>${esc(e.task_id||'—')}</td><td class="mono">${esc(e.session_id||'—')}</td><td class="message">${esc(e.payload||'{}')}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No lifecycle events</td></tr>'}</table></div></section>`;
h+=`<div class="foot">Control DB: ${esc(d.control_db)} · Swarm DB: ${esc(d.swarm_db)} · Read-only dashboard</div>`;document.getElementById('app').innerHTML=h;document.getElementById('updated').textContent=new Date().toLocaleTimeString()}catch(e){document.getElementById('app').innerHTML=`<div class="card empty">Dashboard error: ${esc(e.message)}</div>`}}
load();setInterval(load,5000);
</script></main></body></html>'''


def state() -> dict:
    return {
        "agents": control_db.list_agents(db_path=CONTROL_DB_PATH),
        "sessions": control_db.list_active_sessions(db_path=CONTROL_DB_PATH),
        "leases": _all_leases(),
        "messages": _recent_messages(),
        "events": control_db.get_recent_events(limit=100, db_path=CONTROL_DB_PATH),
        "swarms": _swarm_rows("SELECT id,name,status,created_at,updated_at FROM swarms ORDER BY updated_at DESC LIMIT 20"),
        "swarm_agents": _swarm_rows("SELECT swarm_id,name,session_id,status,result,cost_usd,updated_at FROM agents ORDER BY updated_at DESC LIMIT 100"),
        "swarm_messages": _swarm_rows("SELECT swarm_id,from_agent,to_agent,body,created_at,delivered_at FROM messages ORDER BY id DESC LIMIT 100"),
        "control_db": str(CONTROL_DB_PATH),
        "swarm_db": str(SWARM_DB_PATH),
    }


def _all_leases() -> list[dict]:
    with control_db._connect(CONTROL_DB_PATH) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM leases ORDER BY expires_at DESC").fetchall()]


def _recent_messages(limit: int = 100) -> list[dict]:
    with control_db._connect(CONTROL_DB_PATH) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def _swarm_rows(query: str) -> list[dict]:
    if not Path(SWARM_DB_PATH).is_file():
        return []
    try:
        conn = sqlite3.connect(SWARM_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(query).fetchall()]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", HTML.encode())
            return
        if path == "/api/state":
            self._send(200, "application/json", json.dumps(state()).encode())
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, *_args) -> None:
        return

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = HOST, port: int = PORT) -> None:
    control_db.init_db(CONTROL_DB_PATH)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"OpenCode-Crack Swarm dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
