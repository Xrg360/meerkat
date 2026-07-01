import json
import logging
import hmac
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ApiServer:
    def __init__(self, config: dict[str, Any], status_service: Any, action_service: Any) -> None:
        api_config = config.get("api", {}) or {}
        self.enabled = bool(api_config.get("enabled", True))
        self.host = str(api_config.get("host", "0.0.0.0"))
        self.port = int(os.getenv("MEERKAT_API_PORT") or api_config.get("port", 8710))
        self.status_service = status_service
        self.action_service = action_service
        action_config = config.get("actions", {}) or {}
        self.action_token = os.getenv("MEERKAT_ACTION_TOKEN") or action_config.get("token")
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return

        status_service = self.status_service
        action_service = self.action_service
        action_token = self.action_token

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                routes = {
                    "/health": status_service.status,
                    "/status": status_service.status,
                    "/api/status": status_service.status,
                    "/api/health": status_service.health,
                    "/api/network": status_service.network,
                    "/api/docker": status_service.docker,
                    "/api/sites": status_service.sites,
                    "/api/events": lambda: {"events": status_service.history.recent(100)},
                    "/metrics": lambda: metrics_payload(status_service),
                    "/": lambda: dashboard_html("home"),
                    "/monitoring": lambda: dashboard_html("monitoring"),
                    "/settings": lambda: dashboard_html("settings"),
                }
                handler = routes.get(self.path.split("?")[0])
                if not handler:
                    self.send_response(404)
                    self.end_headers()
                    return

                payload = handler()
                if isinstance(payload, str):
                    content_type = "text/plain; charset=utf-8" if self.path == "/metrics" else "text/html; charset=utf-8"
                    self._write(200, payload.encode("utf-8"), content_type)
                    return
                self._write(200, json.dumps(payload, indent=2).encode("utf-8"), "application/json")

            def do_POST(self) -> None:
                if not self._authorized_action(action_token):
                    self._json({"ok": False, "error": "missing or invalid action token"}, status=403)
                    return

                path = self.path.split("?")[0]
                if path in ("/clearRamCache", "/api/actions/clear-ram-cache"):
                    self._json(action_service.clear_ram_cache())
                    return
                if path == "/api/actions/docker/restart":
                    body = self._read_json()
                    self._json(action_service.restart_container(str(body.get("container", ""))))
                    return
                if path == "/api/actions/sites/add":
                    self._json(action_service.add_site(self._read_json()))
                    return
                if path == "/api/actions/sites/remove":
                    body = self._read_json()
                    self._json(action_service.remove_site(str(body.get("name", ""))))
                    return
                if path == "/api/actions/events/clear":
                    self._json(action_service.clear_events())
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                logging.debug("api: " + format, *args)

            def _write(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, payload: dict[str, Any], status: int | None = None) -> None:
                status = status if status is not None else 200 if payload.get("ok", True) else 400
                self._write(status, json.dumps(payload, indent=2).encode("utf-8"), "application/json")

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0:
                    return {}
                if length > 4096:
                    return {}
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8"))
                except json.JSONDecodeError:
                    return {}

            def _authorized_action(self, token: str | None) -> bool:
                if not token:
                    return True
                supplied = self.headers.get("X-Meerkat-Action-Token", "")
                return hmac.compare_digest(str(token), supplied)

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="api-server", daemon=True)
        self.thread.start()
        logging.info("API server listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()


def metrics_payload(status_service: Any) -> str:
    health = status_service.health()
    status = status_service.status()
    network = status_service.network()
    sites = status_service.sites()
    lines = [
        "# HELP meerkat_cpu_percent Current CPU usage percent",
        "# TYPE meerkat_cpu_percent gauge",
        f"meerkat_cpu_percent {health['cpu_percent']}",
        "# HELP meerkat_ram_percent Current RAM usage percent",
        "# TYPE meerkat_ram_percent gauge",
        f"meerkat_ram_percent {health['ram_percent']}",
        "# HELP meerkat_disk_percent Current disk usage percent",
        "# TYPE meerkat_disk_percent gauge",
        f"meerkat_disk_percent {health['disk_percent']}",
        "# HELP meerkat_internet_up Internet reachability state",
        "# TYPE meerkat_internet_up gauge",
        f"meerkat_internet_up {1 if status.get('internet_up') else 0}",
        "# HELP meerkat_alerts_active Active alert count",
        "# TYPE meerkat_alerts_active gauge",
        f"meerkat_alerts_active {len(status.get('active_alerts', []))}",
        "# HELP meerkat_sites_up Number of configured sites currently up",
        "# TYPE meerkat_sites_up gauge",
        f"meerkat_sites_up {sites['up']}",
        "# HELP meerkat_sites_down Number of configured sites currently down",
        "# TYPE meerkat_sites_down gauge",
        f"meerkat_sites_down {sites['down']}",
    ]
    for label, details in network.get("interfaces", {}).items():
        lines.append(f'meerkat_network_interface_up{{interface="{details["name"]}",type="{label}"}} {1 if details["up"] else 0}')
    for site in sites.get("sites", []):
        name = str(site.get("name", "unknown")).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'meerkat_site_up{{name="{name}"}} {1 if site.get("up") else 0}')
        latency = site.get("latency_ms")
        if latency is not None:
            lines.append(f'meerkat_site_latency_ms{{name="{name}"}} {latency}')
    return "\n".join(lines) + "\n"


def dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Meerkat</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #111827;
      --muted: #667085;
      --line: #d9dee7;
      --good: #138a43;
      --warn: #b76b00;
      --bad: #c62828;
      --info: #1f6feb;
      --info-soft: #eef6ff;
      --good-soft: #e8f6ee;
      --warn-soft: #fff4df;
      --bad-soft: #fdecec;
      --track: #e8edf4;
    }
    body[data-theme="dark"] {
      --bg: #0f172a;
      --panel: #111827;
      --ink: #e5e7eb;
      --muted: #9ca3af;
      --line: #334155;
      --track: #1f2937;
      --info-soft: #10233f;
      --good-soft: #0d2d1d;
      --warn-soft: #352408;
      --bad-soft: #3a1111;
    }
    body[data-accent="green"] { --info: #138a43; --info-soft: #e8f6ee; }
    body[data-accent="red"] { --info: #c62828; --info-soft: #fdecec; }
    body[data-accent="violet"] { --info: #6d28d9; --info-soft: #f1eafe; }
    body[data-theme="dark"][data-accent="green"] { --info-soft: #0d2d1d; }
    body[data-theme="dark"][data-accent="red"] { --info-soft: #3a1111; }
    body[data-theme="dark"][data-accent="violet"] { --info-soft: #22123d; }
    * { box-sizing: border-box; }
    html, body { min-width: 0; overflow-x: hidden; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
      font-size: 14px;
    }
    body[data-density="compact"] { font-size: 13px; }
    body[data-density="compact"] .tile { min-height: 76px; padding: 10px; }
    body[data-density="compact"] .section-body { padding: 10px; }
    body[data-density="compact"] th, body[data-density="compact"] td { padding: 7px 8px; }
    header {
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    .bar {
      max-width: 1280px;
      margin: 0 auto;
      padding: 14px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .mark {
      width: 36px;
      height: 36px;
      display: grid;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--info-soft);
      color: var(--info);
      font-weight: 800;
    }
    h1 { margin: 0; font-size: 20px; font-weight: 750; }
    .subtitle { color: var(--muted); font-size: 12px; margin-top: 2px; }
    .right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: var(--panel);
      color: var(--muted);
      white-space: nowrap;
      font-size: 12px;
    }
    .dot { width: 8px; height: 8px; border-radius: 999px; background: var(--muted); }
    .dot.good { background: var(--good); }
    .dot.warn { background: var(--warn); }
    .dot.bad { background: var(--bad); }
    main { max-width: 1280px; margin: 0 auto; padding: 20px; }
    .summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .tile, section {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }
    .tile { padding: 14px; min-height: 96px; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
    .value { font-size: 26px; font-weight: 760; line-height: 1.1; overflow-wrap: anywhere; }
    .subvalue { color: var(--muted); font-size: 12px; margin-top: 8px; }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(340px, .85fr);
      gap: 14px;
      align-items: start;
    }
    section { margin-bottom: 14px; overflow: hidden; }
    .section-head {
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    h2 { margin: 0; font-size: 14px; font-weight: 760; }
    .section-body { padding: 14px; }
    .metrics { display: grid; gap: 14px; }
    .metric-row {
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr) 86px;
      gap: 12px;
      align-items: center;
    }
    .metric-name { color: var(--muted); }
    .track { height: 10px; border-radius: 999px; background: var(--track); overflow: hidden; }
    .fill { height: 100%; width: 0%; background: var(--good); border-radius: inherit; transition: width .2s ease; }
    .fill.warn { background: var(--warn); }
    .fill.bad { background: var(--bad); }
    .metric-value { text-align: right; font-variant-numeric: tabular-nums; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: middle; }
    th { color: var(--muted); font-size: 12px; font-weight: 650; }
    tr:last-child td { border-bottom: 0; }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      border-radius: 999px;
      padding: 4px 8px;
      background: #eef2f7;
      color: var(--muted);
    }
    .status.good { background: var(--good-soft); color: var(--good); }
    .status.warn { background: var(--warn-soft); color: var(--warn); }
    .status.bad { background: var(--bad-soft); color: var(--bad); }
    .events { display: grid; gap: 10px; }
    .event {
      border-left: 3px solid var(--line);
      padding: 2px 0 2px 10px;
    }
    .event.warning { border-left-color: var(--warn); }
    .event.critical, .event.emergency { border-left-color: var(--bad); }
    .event.info { border-left-color: var(--info); }
    .event-title { font-weight: 700; }
    .event-meta { color: var(--muted); font-size: 12px; margin: 3px 0 5px; }
    .event-body { color: var(--ink); white-space: pre-wrap; line-height: 1.35; }
    .empty { color: var(--muted); padding: 8px 0; }
    .actions { display: grid; grid-template-columns: 180px minmax(0, 1fr) 180px; gap: 10px; }
    .site-actions { display: grid; grid-template-columns: 1fr 1.3fr 110px 140px; gap: 10px; margin-bottom: 12px; }
    .customize { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    .settings-panel { display: none; border-bottom: 1px solid var(--line); background: var(--panel); }
    .settings-panel.open { display: block; }
    button, select, input {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
    }
    button { cursor: pointer; font-weight: 700; }
    button:hover { border-color: var(--info); }
    .primary { background: var(--info); border-color: var(--info); color: #ffffff; }
    code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    @media (max-width: 900px) {
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .layout { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .bar { align-items: flex-start; flex-direction: column; }
      .summary { grid-template-columns: 1fr; }
      .metric-row { grid-template-columns: 78px minmax(0, 1fr) 70px; }
      .actions { grid-template-columns: 1fr; }
      .site-actions { grid-template-columns: 1fr; }
      .customize { grid-template-columns: 1fr; }
      main { padding: 12px; }
    }
  </style>
</head>
<body>
<header>
  <div class="bar">
    <div class="brand">
      <div class="mark">M</div>
      <div>
        <h1>Meerkat</h1>
        <div class="subtitle">Docker homelab monitoring and alerting</div>
      </div>
    </div>
    <div class="right">
      <span class="pill"><span id="liveDot" class="dot"></span><span id="liveText">Loading</span></span>
      <span class="pill">Refresh <span id="refreshAge">--</span>s</span>
      <span class="pill">API <code>/metrics</code></span>
      <button title="Display settings" onclick="toggleSettings()">⚙</button>
    </div>
  </div>
</header>
<main>
  <section class="settings-panel" id="settingsPanel">
    <div class="section-head"><h2>Display</h2><span class="pill">Local preferences</span></div>
    <div class="section-body">
      <div class="customize">
        <select id="themeSelect" onchange="savePreferences()">
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
        <select id="densitySelect" onchange="savePreferences()">
          <option value="comfortable">Comfortable</option>
          <option value="compact">Compact</option>
        </select>
        <select id="accentSelect" onchange="savePreferences()">
          <option value="blue">Blue accent</option>
          <option value="green">Green accent</option>
          <option value="red">Red accent</option>
          <option value="violet">Violet accent</option>
        </select>
        <select id="refreshSelect" onchange="savePreferences()">
          <option value="5000">5s refresh</option>
          <option value="10000">10s refresh</option>
          <option value="30000">30s refresh</option>
        </select>
      </div>
    </div>
  </section>
  <div class="summary" id="summary"></div>
  <div class="layout">
    <div>
      <section>
        <div class="section-head"><h2>System Health</h2><span class="pill" id="tempState">Temperature --</span></div>
        <div class="section-body"><div class="metrics" id="metrics"></div></div>
      </section>
      <section>
        <div class="section-head"><h2>Quick Actions</h2><span class="pill" id="actionState">Ready</span></div>
        <div class="section-body">
          <div class="actions">
            <button onclick="clearCache()">Clear RAM cache</button>
            <select id="restartSelect"></select>
            <button onclick="restartSelected()">Restart container</button>
          </div>
        </div>
      </section>
      <section>
        <div class="section-head"><h2>Network</h2><span class="pill" id="routeState">Route unknown</span></div>
        <div class="section-body"><table id="networkTable"></table></div>
      </section>
      <section>
        <div class="section-head"><h2>Docker Containers</h2><span class="pill" id="dockerCount">0 containers</span></div>
        <div class="section-body"><table id="dockerTable"></table></div>
      </section>
      <section>
        <div class="section-head"><h2>Website Monitors</h2><span class="pill" id="siteCount">0 sites</span></div>
        <div class="section-body">
          <div class="site-actions">
            <input id="siteNameInput" placeholder="Name">
            <input id="siteUrlInput" placeholder="https://example.com">
            <button class="primary" onclick="addSite()">Add site</button>
            <button onclick="removeSelectedSite()">Remove selected</button>
          </div>
          <table id="sitesTable"></table>
        </div>
      </section>
    </div>
    <div>
      <section>
        <div class="section-head"><h2>Active Alerts</h2><span class="pill" id="alertMode">Alerts active</span></div>
        <div class="section-body" id="activeAlerts"></div>
      </section>
      <section>
        <div class="section-head"><h2>Recent Events</h2><button onclick="clearEvents()">Clear</button></div>
        <div class="section-body"><div class="events" id="events"></div></div>
      </section>
    </div>
  </div>
</main>
<script>
let lastRefresh = 0;
let refreshTimer = null;
function pct(value) { return Math.round(Number(value || 0)); }
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[ch]));
}
function tone(value) { return value >= 90 ? 'bad' : value >= 75 ? 'warn' : 'good'; }
function statusClass(value) { return value === true ? 'good' : value === false ? 'bad' : 'warn'; }
function statusLabel(value) { return value === true ? 'up' : value === false ? 'down' : 'unknown'; }
function tempLabel(value) {
  if (value === null || value === undefined) return 'unavailable';
  if (value >= 85) return 'very hot';
  if (value >= 75) return 'hot';
  if (value >= 60) return 'warm';
  return 'normal';
}
function metric(name, value, suffix = '%') {
  const rounded = pct(value);
  const cls = tone(rounded);
  return `<div class="metric-row">
    <div class="metric-name">${name}</div>
    <div class="track"><div class="fill ${cls}" style="width:${Math.max(0, Math.min(100, rounded))}%"></div></div>
    <div class="metric-value">${rounded}${suffix}</div>
  </div>`;
}
function statusPill(value, text) {
  return `<span class="status ${statusClass(value)}"><span class="dot ${statusClass(value)}"></span>${text || statusLabel(value)}</span>`;
}
function applyPreferences() {
  const prefs = JSON.parse(localStorage.getItem('meerkatUiPrefs') || '{}');
  const theme = prefs.theme || 'light';
  const density = prefs.density || 'comfortable';
  const accent = prefs.accent || 'blue';
  const refresh = String(prefs.refresh || 10000);
  document.body.dataset.theme = theme;
  document.body.dataset.density = density;
  document.body.dataset.accent = accent;
  document.getElementById('themeSelect').value = theme;
  document.getElementById('densitySelect').value = density;
  document.getElementById('accentSelect').value = accent;
  document.getElementById('refreshSelect').value = refresh;
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, Number(refresh));
}
function savePreferences() {
  localStorage.setItem('meerkatUiPrefs', JSON.stringify({
    theme: document.getElementById('themeSelect').value,
    density: document.getElementById('densitySelect').value,
    accent: document.getElementById('accentSelect').value,
    refresh: Number(document.getElementById('refreshSelect').value)
  }));
  applyPreferences();
}
async function refresh() {
  try {
    const [status, health, network, docker, sites] = await Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch('/api/health').then(r => r.json()),
      fetch('/api/network').then(r => r.json()),
      fetch('/api/docker').then(r => r.json()),
      fetch('/api/sites').then(r => r.json())
    ]);
    lastRefresh = Date.now();
    document.getElementById('liveDot').className = 'dot good';
    document.getElementById('liveText').textContent = 'Live';

    const running = docker.containers.filter(c => c.status === 'running').length;
    document.getElementById('summary').innerHTML = [
      ['Internet', statusLabel(status.internet_up), status.internet_up ? 'Reachable' : 'Check probes failing'],
      ['Active alerts', status.active_alerts.length, status.alerts_silenced ? 'Notifications silenced' : 'Notifications active'],
      ['Containers', `${running}/${docker.containers.length}`, docker.available ? 'Docker socket connected' : 'Docker unavailable'],
      ['Sites', `${sites.up}/${sites.total}`, sites.down ? `${sites.down} down` : 'All configured sites up'],
      ['Default route', status.default_route_label || 'unknown', 'Current outbound interface']
    ].map(item => `<div class="tile"><div class="label">${esc(item[0])}</div><div class="value">${esc(item[1])}</div><div class="subvalue">${esc(item[2])}</div></div>`).join('');

    const temp = health.cpu_temperature;
    document.getElementById('metrics').innerHTML = [
      metric('CPU', health.cpu_percent),
      metric('RAM', health.ram_percent),
      metric('Disk', health.disk_percent),
      temp === null || temp === undefined ? '' : metric('Temp', temp, 'degC')
    ].join('');
    document.getElementById('tempState').textContent = `CPU ${temp === null || temp === undefined ? 'unavailable' : Math.round(temp) + 'degC (' + tempLabel(temp) + ')'}`;

    document.getElementById('routeState').textContent = `Route ${network.default_route_label || 'unknown'}`;
    const interfaces = Object.entries(network.interfaces);
    document.getElementById('networkTable').innerHTML = interfaces.length ? `<thead><tr><th>Type</th><th>Interface</th><th>Status</th><th>Carrier</th><th>IPv4</th></tr></thead><tbody>${interfaces.map(([type, item]) => `<tr><td>${esc(type)}</td><td><code>${esc(item.name)}</code></td><td>${statusPill(item.up)}</td><td>${esc(item.carrier)}</td><td>${esc(item.has_ip)}</td></tr>`).join('')}</tbody>` : '<tbody><tr><td class="empty">No interfaces configured.</td></tr></tbody>';

    document.getElementById('dockerCount').textContent = `${docker.containers.length} containers`;
    document.getElementById('restartSelect').innerHTML = docker.containers.length
      ? docker.containers.map(c => `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join('')
      : '<option value="">No containers</option>';
    document.getElementById('dockerTable').innerHTML = docker.available && docker.containers.length
      ? `<thead><tr><th>Name</th><th>Status</th><th>Image</th></tr></thead><tbody>${docker.containers.map(c => `<tr><td><code>${esc(c.name)}</code></td><td>${statusPill(c.status === 'running', esc(c.status))}</td><td>${esc(c.image || 'untagged')}</td></tr>`).join('')}</tbody>`
      : `<tbody><tr><td class="empty">${docker.available ? 'No containers found.' : esc(docker.error)}</td></tr></tbody>`;

    document.getElementById('siteCount').textContent = `${sites.total} sites`;
    document.getElementById('sitesTable').innerHTML = sites.sites.length
      ? `<thead><tr><th></th><th>Name</th><th>Status</th><th>Latency</th><th>HTTP</th><th>URL</th></tr></thead><tbody>${sites.sites.map(s => `<tr><td><input type="radio" name="selectedSite" value="${esc(s.name)}"></td><td>${esc(s.name)}</td><td>${statusPill(s.up)}</td><td>${esc(s.latency_ms ?? 'unknown')}ms</td><td>${esc(s.status_code ?? 'none')}</td><td><code>${esc(s.url)}</code></td></tr>`).join('')}</tbody>`
      : '<tbody><tr><td class="empty">No website monitors configured.</td></tr></tbody>';

    document.getElementById('alertMode').textContent = status.alerts_silenced ? 'Alerts silenced' : 'Alerts active';
    document.getElementById('activeAlerts').innerHTML = status.active_alerts.length
      ? status.active_alerts.map(a => `<div class="event critical"><div class="event-title">${esc(a)}</div><div class="event-meta">currently active</div></div>`).join('')
      : '<div class="empty">No active alerts.</div>';

    document.getElementById('events').innerHTML = status.recent_events.length
      ? status.recent_events.map(e => `<div class="event ${esc(e.severity)}"><div class="event-title">${esc(e.title)}</div><div class="event-meta">${esc(e.ts)} - ${esc(e.severity)} - ${esc(e.source)}</div><div class="event-body">${esc(e.body)}</div></div>`).join('')
      : '<div class="empty">No events yet.</div>';
  } catch (error) {
    document.getElementById('liveDot').className = 'dot bad';
    document.getElementById('liveText').textContent = 'Disconnected';
  }
}
async function postJson(url, body = {}) {
  let token = localStorage.getItem('meerkatActionToken');
  if (!token) {
    token = prompt('Action token');
    if (!token) return;
    localStorage.setItem('meerkatActionToken', token);
  }
  const response = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Meerkat-Action-Token': token},
    body: JSON.stringify(body)
  });
  const payload = await response.json();
  if (response.status === 403) {
    localStorage.removeItem('meerkatActionToken');
  }
  document.getElementById('actionState').textContent = payload.ok ? payload.message : payload.error;
  await refresh();
}
async function clearCache() {
  await postJson('/api/actions/clear-ram-cache');
}
async function restartSelected() {
  const container = document.getElementById('restartSelect').value;
  if (!container) return;
  await postJson('/api/actions/docker/restart', {container});
}
async function addSite() {
  const name = document.getElementById('siteNameInput').value.trim();
  const url = document.getElementById('siteUrlInput').value.trim();
  if (!name || !url) {
    document.getElementById('actionState').textContent = 'Site name and URL are required';
    return;
  }
  await postJson('/api/actions/sites/add', {name, url});
}
async function removeSelectedSite() {
  const selected = document.querySelector('input[name="selectedSite"]:checked');
  if (!selected) {
    document.getElementById('actionState').textContent = 'Select a runtime site first';
    return;
  }
  await postJson('/api/actions/sites/remove', {name: selected.value});
}
async function clearEvents() {
  await postJson('/api/actions/events/clear');
}
function toggleSettings() {
  document.getElementById('settingsPanel').classList.toggle('open');
}
refresh();
applyPreferences();
setInterval(() => {
  document.getElementById('refreshAge').textContent = lastRefresh ? Math.floor((Date.now() - lastRefresh) / 1000) : '--';
}, 1000);
</script>
</body>
</html>"""


def dashboard_html(page: str = "home") -> str:
    page = page if page in {"home", "monitoring", "settings"} else "home"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Meerkat</title>
  <style>
    :root {{
      --bg: #eef3f8;
      --surface: #ffffff;
      --soft: #f7fafc;
      --ink: #111827;
      --muted: #667085;
      --line: #d8e0ea;
      --brand: #16b970;
      --brand-dark: #0d8d58;
      --bad: #dc3f3f;
      --warn: #d98b16;
      --blue: #2563eb;
      --shadow: 0 12px 28px rgba(17, 24, 39, .08);
    }}
    body[data-theme="dark"] {{
      --bg: #0d1218;
      --surface: #151d27;
      --soft: #101720;
      --ink: #edf2f7;
      --muted: #9aa6b2;
      --line: #273342;
      --shadow: none;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-width: 0; overflow-x: hidden; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }}
    a {{ color: inherit; text-decoration: none; }}
    button, input, select {{
      width: 100%;
      min-width: 0;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
    }}
    button {{ cursor: pointer; font-weight: 760; }}
    button:hover {{ border-color: var(--brand); }}
    input[type="radio"], input[type="checkbox"] {{ width: auto; min-height: 0; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; white-space: normal; }}
    .app {{ min-height: 100vh; display: grid; grid-template-columns: 260px minmax(0, 1fr); }}
    .sidebar {{
      min-width: 0;
      height: 100vh;
      position: sticky;
      top: 0;
      overflow: auto;
      padding: 18px;
      background: var(--surface);
      border-right: 1px solid var(--line);
    }}
    .brand {{ display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 12px; align-items: center; margin-bottom: 18px; }}
    .logo {{ width: 42px; height: 42px; border-radius: 8px; box-shadow: 0 10px 24px rgba(22, 185, 112, .22); }}
    h1, h2, h3 {{ margin: 0; }}
    h1 {{ font-size: 20px; line-height: 1.1; font-weight: 900; }}
    h2 {{ font-size: 22px; line-height: 1.2; font-weight: 900; }}
    h3 {{ font-size: 14px; font-weight: 850; }}
    .muted, .subtitle {{ color: var(--muted); }}
    .subtitle {{ margin-top: 4px; font-size: 12px; overflow-wrap: anywhere; }}
    .nav {{ display: grid; gap: 8px; margin-top: 18px; }}
    .nav a {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 42px;
      padding: 0 12px;
      border-radius: 8px;
      color: var(--muted);
      border: 1px solid transparent;
    }}
    .nav a.active {{ color: var(--ink); background: rgba(22, 185, 112, .12); border-color: rgba(22, 185, 112, .28); }}
    .content {{ min-width: 0; max-width: 1280px; width: 100%; padding: 22px; }}
    .topbar {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 14px; }}
    .top-actions {{ display: flex; width: auto; gap: 8px; flex-wrap: wrap; }}
    .top-actions a, .top-actions button {{
      width: auto;
      min-height: 38px;
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      padding: 0 12px;
      font-weight: 800;
    }}
    .primary {{ border-color: var(--brand) !important; background: var(--brand) !important; color: #fff !important; }}
    .grid {{ display: grid; gap: 14px; min-width: 0; }}
    .summary {{ grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 14px; }}
    .two {{ grid-template-columns: minmax(0, 1.25fr) minmax(280px, .75fr); align-items: start; }}
    .card {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .card-body {{ padding: 16px; min-width: 0; }}
    .card-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}
    .stat {{ min-height: 92px; padding: 14px; }}
    .label {{ color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
    .value {{ font-size: 25px; font-weight: 900; line-height: 1.05; overflow-wrap: anywhere; }}
    .subvalue {{ color: var(--muted); font-size: 12px; margin-top: 8px; overflow-wrap: anywhere; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      width: auto;
      max-width: 100%;
      min-height: 26px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      color: var(--muted);
      background: var(--soft);
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .dot {{ width: 7px; height: 7px; border-radius: 99px; background: var(--muted); flex: 0 0 auto; }}
    .dot.good {{ background: var(--brand); }}
    .dot.bad {{ background: var(--bad); }}
    .dot.warn {{ background: var(--warn); }}
    .status-mini {{
      display: inline-grid;
      place-items: center;
      min-width: 34px;
      height: 24px;
      border-radius: 999px;
      color: #fff;
      background: var(--brand);
      font-size: 11px;
      font-weight: 900;
    }}
    .status-mini.down {{ background: var(--bad); }}
    .status-mini.warn {{ background: var(--warn); }}
    .site-list {{ display: grid; gap: 10px; }}
    .site-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--soft);
      text-align: left;
    }}
    .site-name {{ font-weight: 900; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .site-url {{ color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bars {{
      display: grid;
      grid-template-columns: repeat(24, minmax(0, 1fr));
      gap: 3px;
      min-width: 0;
      margin-top: 10px;
    }}
    .bars span {{ height: 22px; border-radius: 99px; background: var(--brand); min-width: 0; }}
    .bars span.down {{ background: var(--bad); }}
    .bars span.empty {{ background: var(--line); opacity: .75; }}
    .detail-title {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: start; }}
    .monitor-name {{ font-size: 25px; line-height: 1.12; font-weight: 900; overflow-wrap: anywhere; }}
    .metric-cards {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }}
    .metric-card {{ min-width: 0; padding: 13px; border: 1px solid var(--line); border-radius: 8px; background: var(--soft); text-align: center; }}
    .metric-card strong {{ display: block; margin-top: 5px; font-size: 21px; overflow-wrap: anywhere; }}
    .chart-wrap {{ height: 250px; }}
    #latencyChart {{ width: 100%; height: 100%; display: block; }}
    .metrics {{ display: grid; gap: 12px; }}
    .metric-row {{ display: grid; grid-template-columns: 70px minmax(0, 1fr) 58px; gap: 8px; align-items: center; }}
    .track {{ height: 10px; border-radius: 99px; background: var(--line); overflow: hidden; }}
    .fill {{ height: 100%; width: 0%; border-radius: inherit; background: var(--brand); }}
    .fill.warn {{ background: var(--warn); }}
    .fill.bad {{ background: var(--bad); }}
    .form-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.5fr) 96px; gap: 10px; }}
    .settings-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .field {{ display: grid; gap: 6px; min-width: 0; }}
    .field label {{ color: var(--muted); font-size: 12px; font-weight: 850; }}
    .check-row {{ display: flex; gap: 8px; align-items: center; min-height: 40px; }}
    .check-row input {{ flex: 0 0 auto; }}
    .table-scroll {{ max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: middle; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 850; }}
    .event {{ border-left: 3px solid var(--line); padding-left: 10px; min-width: 0; }}
    .event.critical, .event.emergency {{ border-left-color: var(--bad); }}
    .event.warning {{ border-left-color: var(--warn); }}
    .event.info {{ border-left-color: var(--blue); }}
    .event-title {{ font-weight: 850; overflow-wrap: anywhere; }}
    .event-meta, .event-body {{ overflow-wrap: anywhere; }}
    .event-meta {{ color: var(--muted); font-size: 12px; margin: 3px 0 5px; }}
    .event-body {{ line-height: 1.4; white-space: pre-wrap; }}
    .empty {{ color: var(--muted); padding: 10px 0; }}
    .toast-stack {{ position: fixed; right: 14px; bottom: 14px; z-index: 50; display: grid; gap: 10px; max-width: min(360px, calc(100vw - 28px)); }}
    .toast {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--bad); border-radius: 8px; box-shadow: var(--shadow); padding: 12px; }}
    .skeleton {{
      overflow: hidden;
      color: transparent;
      background: linear-gradient(90deg, var(--soft), rgba(22, 185, 112, .12), var(--soft));
      background-size: 220% 100%;
      border-radius: 8px;
      animation: shimmer 1.35s ease-in-out infinite;
    }}
    .sk-line {{ height: 12px; width: 100%; margin-bottom: 10px; }}
    .sk-line.short {{ width: 46%; }}
    .sk-line.medium {{ width: 70%; }}
    .sk-value {{ height: 28px; width: 62%; margin: 8px 0 12px; }}
    @keyframes shimmer {{ 0% {{ background-position: 120% 0; }} 100% {{ background-position: -120% 0; }} }}
    @media (max-width: 960px) {{
      .app {{ display: block; }}
      .sidebar {{ position: relative; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      .nav {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .two {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 560px) {{
      body {{ font-size: 13px; }}
      .sidebar, .content {{ padding: 12px; }}
      .nav {{ grid-template-columns: 1fr; }}
      .topbar, .card-head {{ display: grid; }}
      .top-actions {{ width: 100%; display: grid; grid-template-columns: 1fr 1fr; }}
      .top-actions a, .top-actions button {{ width: 100%; justify-content: center; }}
      .summary, .metric-cards, .settings-grid, .form-grid {{ grid-template-columns: 1fr; }}
      .card {{ box-shadow: none; }}
      .monitor-name {{ font-size: 22px; }}
      .detail-title {{ grid-template-columns: 1fr; }}
      .chart-wrap {{ height: 210px; }}
      .bars {{ grid-template-columns: repeat(18, minmax(0, 1fr)); }}
      .table-scroll {{ overflow: visible; }}
      table, thead, tbody, tr, th, td {{ display: block; width: 100%; }}
      thead {{ display: none; }}
      tr {{ border: 1px solid var(--line); border-radius: 8px; background: var(--soft); padding: 9px; margin-bottom: 10px; }}
      td {{ border-bottom: 0; padding: 5px 0; white-space: normal; overflow-wrap: anywhere; }}
      td[data-label] {{ display: grid; grid-template-columns: 82px minmax(0, 1fr); gap: 8px; }}
      td[data-label]::before {{ content: attr(data-label); color: var(--muted); font-size: 12px; font-weight: 850; }}
    }}
  </style>
</head>
<body data-page="{page}">
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <svg class="logo" viewBox="0 0 64 64" role="img" aria-label="Meerkat logo">
        <defs><linearGradient id="logoGradient" x1="8" y1="7" x2="56" y2="58" gradientUnits="userSpaceOnUse"><stop stop-color="#20c977"/><stop offset="1" stop-color="#2563eb"/></linearGradient></defs>
        <rect width="64" height="64" rx="12" fill="url(#logoGradient)"/>
        <path d="M18 45V22c0-4 3-7 7-7h14c4 0 7 3 7 7v23h-8V25l-6 17h-5l-6-17v20h-8Z" fill="#fff"/>
        <circle cx="24" cy="18" r="3" fill="#0e131b" opacity=".26"/><circle cx="40" cy="18" r="3" fill="#0e131b" opacity=".26"/>
      </svg>
      <div><h1>Meerkat</h1><div class="subtitle">Uptime and homelab monitoring</div></div>
    </div>
    <div class="pill"><span id="liveDot" class="dot"></span><span id="liveText">Loading</span><span id="refreshAge">--s</span></div>
    <nav class="nav">
      <a href="/" class="{"active" if page == "home" else ""}">Home <span>Overview</span></a>
      <a href="/monitoring" class="{"active" if page == "monitoring" else ""}">Monitoring <span>HTTP</span></a>
      <a href="/settings" class="{"active" if page == "settings" else ""}">Settings <span>Prefs</span></a>
    </nav>
  </aside>
  <main class="content">
    <div class="topbar">
      <div>
        <h2>{"Operations Home" if page == "home" else "HTTP Monitoring" if page == "monitoring" else "Settings"}</h2>
        <div class="subtitle">{"Clean system overview." if page == "home" else "Uptime bars, latency chart, and site controls." if page == "monitoring" else "Display, notifications, token, and dashboard preferences."}</div>
      </div>
      <div class="top-actions">
        <a href="/monitoring" class="primary">Monitoring</a>
        <button onclick="refresh()">Refresh</button>
      </div>
    </div>

    <section id="homePage" style="display: {"block" if page == "home" else "none"}">
      <div class="grid summary" id="summary">{skeleton_stats()}</div>
      <div class="grid two">
        <section class="card">
          <div class="card-head"><h3>Pinned Monitors</h3><a class="pill" href="/settings">Choose</a></div>
          <div class="card-body"><div class="site-list" id="pinnedSites">{skeleton_list()}</div></div>
        </section>
        <section class="card">
          <div class="card-head"><h3>Active Alerts</h3><span class="pill" id="alertMode">Loading</span></div>
          <div class="card-body" id="activeAlerts"><div class="skeleton sk-line"></div><div class="skeleton sk-line medium"></div></div>
        </section>
      </div>
    </section>

    <section id="monitoringPage" style="display: {"block" if page == "monitoring" else "none"}">
      <div class="grid two">
        <div class="grid">
          <section class="card">
            <div class="card-body">
              <div class="detail-title">
                <div><div class="monitor-name" id="selectedSiteName"><span class="skeleton sk-line medium" style="display:block;height:28px"></span></div><a class="site-url" id="selectedSiteUrl" href="#" target="_blank" rel="noreferrer"></a></div>
                <span class="status-mini warn" id="selectedSiteStatus">--</span>
              </div>
              <div class="bars" id="uptimeBars">{empty_bars(24)}</div>
              <div class="subtitle" id="checkCaption">Loading monitor data...</div>
              <div class="metric-cards">
                <div class="metric-card"><span class="muted">Response</span><strong id="currentLatency">--</strong></div>
                <div class="metric-card"><span class="muted">Average</span><strong id="averageLatency">--</strong></div>
                <div class="metric-card"><span class="muted">Uptime</span><strong id="uptimePercent">--</strong></div>
              </div>
            </div>
          </section>
          <section class="card">
            <div class="card-head"><h3>Response Time</h3><span class="pill">Recent samples</span></div>
            <div class="card-body chart-wrap"><svg id="latencyChart" role="img" aria-label="HTTP response time chart"></svg></div>
          </section>
          <section class="card">
            <div class="card-head"><h3>Add Site</h3><span class="pill" id="actionState">Ready</span></div>
            <div class="card-body"><div class="form-grid"><input id="siteNameInput" placeholder="Name"><input id="siteUrlInput" placeholder="https://example.com"><button class="primary" onclick="addSite()">Add</button></div></div>
          </section>
        </div>
        <div class="grid">
          <section class="card">
            <div class="card-head"><h3>Sites</h3><button onclick="removeSelectedSite()">Remove selected</button></div>
            <div class="card-body"><div class="site-list" id="siteList">{skeleton_list()}</div></div>
          </section>
          <section class="card">
            <div class="card-head"><h3>System Health</h3><span class="pill" id="tempState">Temperature --</span></div>
            <div class="card-body"><div class="metrics" id="metrics"><div class="skeleton sk-line"></div><div class="skeleton sk-line"></div><div class="skeleton sk-line"></div></div></div>
          </section>
        </div>
      </div>
    </section>

    <section id="settingsPage" style="display: {"block" if page == "settings" else "none"}">
      <div class="grid two">
        <section class="card">
          <div class="card-head"><h3>Preferences</h3><span class="pill">Browser local</span></div>
          <div class="card-body">
            <div class="settings-grid">
              <div class="field"><label for="themeSelect">Theme</label><select id="themeSelect" onchange="savePreferences()"><option value="light">Light</option><option value="dark">Dark</option></select></div>
              <div class="field"><label for="refreshSelect">Refresh interval</label><select id="refreshSelect" onchange="savePreferences()"><option value="5000">5 seconds</option><option value="10000">10 seconds</option><option value="30000">30 seconds</option></select></div>
              <div class="field"><label for="tokenInput">Action token</label><input id="tokenInput" placeholder="Only needed if configured"></div>
              <div class="field"><label>&nbsp;</label><button onclick="saveActionToken()">Save token</button></div>
              <div class="field"><label>Alert popups</label><label class="check-row"><input id="popupToggle" type="checkbox" onchange="savePreferences()"> Show in-app alert popups</label></div>
              <div class="field"><label>Desktop notifications</label><button onclick="enableNotifications()">Enable notifications</button></div>
            </div>
          </div>
        </section>
        <section class="card">
          <div class="card-head"><h3>Home Monitor Cards</h3><span class="pill">Pick what appears on Home</span></div>
          <div class="card-body"><div class="site-list" id="settingsSiteList">{skeleton_list()}</div></div>
        </section>
      </div>
    </section>
  </main>
</div>
<div class="toast-stack" id="toastStack"></div>
<script>
const page = "{page}";
let lastRefresh = 0;
let refreshTimer = null;
let latest = {{status: null, health: null, network: null, docker: null, sites: {{sites: []}}}};
let selectedSiteName = "";
let seenAlerts = new Set(JSON.parse(localStorage.getItem("meerkatSeenAlerts") || "[]"));
function esc(value) {{ return String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[ch])); }}
function pct(value) {{ return Math.round(Number(value || 0)); }}
function statusLabel(value) {{ return value === true ? "up" : value === false ? "down" : "unknown"; }}
function latencyText(value) {{ return value === null || value === undefined ? "--" : `${{Math.round(value)}}ms`; }}
function siteHistory(site) {{ return Array.isArray(site?.history) ? site.history : []; }}
function avgLatency(site) {{ const s = siteHistory(site).map(x => x.latency_ms).filter(x => x !== null && x !== undefined); return s.length ? s.reduce((a,b) => a + Number(b), 0) / s.length : null; }}
function uptime(site) {{ const s = siteHistory(site); if (!s.length) return site?.up ? 100 : 0; return Math.round(s.filter(x => x.up === true).length / s.length * 1000) / 10; }}
function prefs() {{ return JSON.parse(localStorage.getItem("meerkatUiPrefs") || "{{}}"); }}
function selectedSite() {{ const sites = latest.sites.sites || []; return sites.find(site => site.name === selectedSiteName) || sites[0] || null; }}
function statusMini(site) {{ return `<span class="status-mini ${{site?.up ? "" : "down"}}">${{site?.up ? "UP" : "DN"}}</span>`; }}
function bars(site, count = 24) {{
  const samples = siteHistory(site).slice(-count);
  const padded = Array(Math.max(0, count - samples.length)).fill(null).concat(samples);
  return padded.map(s => !s ? "<span class='empty'></span>" : `<span class="${{s.up ? "" : "down"}}"></span>`).join("");
}}
function metric(name, value, suffix = "%") {{
  const v = pct(value), cls = v >= 90 ? "bad" : v >= 75 ? "warn" : "";
  return `<div class="metric-row"><div class="muted">${{esc(name)}}</div><div class="track"><div class="fill ${{cls}}" style="width:${{Math.max(0, Math.min(100, v))}}%"></div></div><div>${{v}}${{suffix}}</div></div>`;
}}
function applyPreferences() {{
  const p = prefs();
  document.body.dataset.theme = p.theme || "light";
  const theme = document.getElementById("themeSelect");
  const refresh = document.getElementById("refreshSelect");
  const token = document.getElementById("tokenInput");
  const popup = document.getElementById("popupToggle");
  if (theme) theme.value = p.theme || "light";
  if (refresh) refresh.value = String(p.refresh || 10000);
  if (token) token.value = localStorage.getItem("meerkatActionToken") || "";
  if (popup) popup.checked = p.popups !== false;
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, Number(p.refresh || 10000));
}}
function savePreferences() {{
  const p = prefs();
  const theme = document.getElementById("themeSelect");
  const refresh = document.getElementById("refreshSelect");
  const popup = document.getElementById("popupToggle");
  localStorage.setItem("meerkatUiPrefs", JSON.stringify({{theme: theme ? theme.value : p.theme || "light", refresh: refresh ? Number(refresh.value) : p.refresh || 10000, popups: popup ? popup.checked : p.popups !== false}}));
  applyPreferences();
}}
function saveActionToken() {{
  const token = document.getElementById("tokenInput").value.trim();
  if (token) localStorage.setItem("meerkatActionToken", token); else localStorage.removeItem("meerkatActionToken");
  showToast("Action token", token ? "Saved for this browser." : "Cleared.");
}}
function pinnedNames() {{ return new Set(JSON.parse(localStorage.getItem("meerkatPinnedSites") || "[]")); }}
function togglePin(name) {{
  const pins = pinnedNames();
  if (pins.has(name)) pins.delete(name); else pins.add(name);
  localStorage.setItem("meerkatPinnedSites", JSON.stringify([...pins]));
  renderAll();
}}
function enableNotifications() {{
  if (!("Notification" in window)) {{ showToast("Notifications unavailable", "This browser does not support desktop notifications."); return; }}
  Notification.requestPermission().then(result => showToast("Desktop notifications", result));
}}
function showToast(title, body) {{
  const node = document.createElement("div");
  node.className = "toast";
  node.innerHTML = `<div class="event-title">${{esc(title)}}</div><div class="event-body">${{esc(body)}}</div>`;
  document.getElementById("toastStack").appendChild(node);
  setTimeout(() => node.remove(), 8000);
}}
function alertUser(event) {{
  const p = prefs();
  if (p.popups !== false) showToast(event.title || "Alert", event.body || event.severity || "");
  if ("Notification" in window && Notification.permission === "granted") new Notification(event.title || "Meerkat alert", {{body: event.body || event.severity || ""}});
}}
function drawLoadingChart() {{
  const svg = document.getElementById("latencyChart"); if (!svg) return;
  svg.setAttribute("viewBox", "0 0 720 240");
  svg.innerHTML = `<rect width="720" height="240" rx="8" fill="var(--soft)"></rect><polyline points="46,172 140,146 232,156 326,112 420,128 514,82 612,104 704,72" fill="none" stroke="var(--brand)" stroke-width="3" stroke-linecap="round" opacity=".35"></polyline><text x="24" y="34" fill="var(--muted)" font-size="12">Loading response data...</text>`;
}}
function drawChart(samples) {{
  const svg = document.getElementById("latencyChart"); if (!svg) return;
  const values = samples.filter(s => s.latency_ms !== null && s.latency_ms !== undefined).slice(-60);
  if (!values.length) {{ drawLoadingChart(); return; }}
  const width = 720, height = 240, pad = {{left:44,right:16,top:16,bottom:34}};
  const max = Math.max(...values.map(s => Number(s.latency_ms)), 10), min = Math.min(...values.map(s => Number(s.latency_ms)), 0), range = Math.max(1, max - min);
  const step = values.length > 1 ? (width - pad.left - pad.right) / (values.length - 1) : 0;
  const points = values.map((s,i) => `${{(pad.left + i * step).toFixed(1)}},${{(height - pad.bottom - ((Number(s.latency_ms) - min) / range) * (height - pad.top - pad.bottom)).toFixed(1)}}`).join(" ");
  svg.setAttribute("viewBox", `0 0 ${{width}} ${{height}}`);
  svg.innerHTML = `<polyline points="${{pad.left}},${{height-pad.bottom}} ${{points}} ${{width-pad.right}},${{height-pad.bottom}}" fill="rgba(22,185,112,.12)" stroke="none"></polyline><polyline points="${{points}}" fill="none" stroke="var(--brand)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline><text x="8" y="24" fill="var(--muted)" font-size="11">${{Math.round(max)}}ms</text><text x="${{pad.left}}" y="${{height-9}}" fill="var(--muted)" font-size="11">older</text><text x="${{width-pad.right-34}}" y="${{height-9}}" fill="var(--muted)" font-size="11">now</text>`;
}}
function renderSiteCard(site, opts = {{pin: false}}) {{
  return `<button class="site-row" onclick="selectSite('${{esc(site.name)}}')"><div><div class="site-name">${{esc(site.name)}}</div><div class="site-url">${{esc(site.url)}}</div><div class="bars">${{bars(site, 18)}}</div></div>${{statusMini(site)}}</button>${{opts.pin ? `<label class="check-row"><input type="checkbox" ${{pinnedNames().has(site.name) ? "checked" : ""}} onchange="togglePin('${{esc(site.name)}}')"> Show on Home</label>` : ""}}`;
}}
function renderAll() {{
  const status = latest.status, health = latest.health, docker = latest.docker, sites = latest.sites;
  if (!status || !health || !docker || !sites) return;
  const running = docker.containers.filter(c => c.status === "running").length;
  const summary = document.getElementById("summary");
  if (summary) summary.innerHTML = [
    ["Internet", statusLabel(status.internet_up), status.internet_up ? "Reachable" : "Probe failing"],
    ["Alerts", status.active_alerts.length, status.alerts_silenced ? "Silenced" : "Active"],
    ["Containers", `${{running}}/${{docker.containers.length}}`, docker.available ? "Docker connected" : "Docker unavailable"],
    ["Sites", `${{sites.up}}/${{sites.total}}`, sites.down ? `${{sites.down}} down` : "All up"]
  ].map(x => `<section class="card stat"><div class="label">${{esc(x[0])}}</div><div class="value">${{esc(x[1])}}</div><div class="subvalue">${{esc(x[2])}}</div></section>`).join("");
  const allSites = sites.sites || [];
  if (!selectedSiteName && allSites.length) selectedSiteName = allSites[0].name;
  const pins = pinnedNames();
  const pinned = allSites.filter(site => pins.has(site.name)).slice(0, 4);
  const pinnedNode = document.getElementById("pinnedSites");
  if (pinnedNode) pinnedNode.innerHTML = (pinned.length ? pinned : allSites.slice(0, 2)).map(renderSiteCard).join("") || "<div class='empty'>No monitors yet.</div>";
  const siteList = document.getElementById("siteList");
  if (siteList) siteList.innerHTML = allSites.map(renderSiteCard).join("") || "<div class='empty'>No monitors yet.</div>";
  const settingsList = document.getElementById("settingsSiteList");
  if (settingsList) settingsList.innerHTML = allSites.map(site => renderSiteCard(site, {{pin: true}})).join("") || "<div class='empty'>Add monitors from the Monitoring page.</div>";
  renderSelectedSite();
  const metrics = document.getElementById("metrics");
  if (metrics) metrics.innerHTML = [metric("CPU", health.cpu_percent), metric("RAM", health.ram_percent), metric("Disk", health.disk_percent), health.cpu_temperature == null ? "" : metric("Temp", health.cpu_temperature, "C")].join("");
  const tempState = document.getElementById("tempState");
  if (tempState) tempState.textContent = `CPU ${{health.cpu_temperature == null ? "unavailable" : Math.round(health.cpu_temperature) + "C"}}`;
  const activeAlerts = document.getElementById("activeAlerts");
  if (activeAlerts) activeAlerts.innerHTML = status.active_alerts.length ? status.active_alerts.map(a => `<div class="event critical"><div class="event-title">${{esc(a)}}</div><div class="event-meta">currently active</div></div>`).join("") : "<div class='empty'>No active alerts.</div>";
  const alertMode = document.getElementById("alertMode");
  if (alertMode) alertMode.textContent = status.alerts_silenced ? "Silenced" : "Active";
}}
function renderSelectedSite() {{
  const site = selectedSite();
  if (!site || page !== "monitoring") return;
  document.getElementById("selectedSiteName").textContent = site.name;
  const url = document.getElementById("selectedSiteUrl");
  url.textContent = site.url;
  url.href = site.url;
  const badge = document.getElementById("selectedSiteStatus");
  badge.textContent = site.up ? "UP" : "DN";
  badge.className = `status-mini ${{site.up ? "" : "down"}}`;
  document.getElementById("uptimeBars").innerHTML = bars(site, window.matchMedia("(max-width: 560px)").matches ? 18 : 24);
  document.getElementById("checkCaption").textContent = `HTTP ${{site.status_code ?? "none"}} - ${{site.error || "latest check completed"}}`;
  document.getElementById("currentLatency").textContent = latencyText(site.latency_ms);
  document.getElementById("averageLatency").textContent = latencyText(avgLatency(site));
  document.getElementById("uptimePercent").textContent = `${{uptime(site)}}%`;
  drawChart(siteHistory(site));
}}
function selectSite(name) {{
  selectedSiteName = name;
  if (page !== "monitoring") location.href = `/monitoring?site=${{encodeURIComponent(name)}}`;
  else renderSelectedSite();
}}
async function refresh() {{
  try {{
    const [status, health, network, docker, sites] = await Promise.all([fetch("/api/status").then(r => r.json()), fetch("/api/health").then(r => r.json()), fetch("/api/network").then(r => r.json()), fetch("/api/docker").then(r => r.json()), fetch("/api/sites").then(r => r.json())]);
    latest = {{status, health, network, docker, sites}};
    lastRefresh = Date.now();
    document.getElementById("liveDot").className = "dot good";
    document.getElementById("liveText").textContent = "Live";
    const params = new URLSearchParams(location.search);
    if (params.get("site")) selectedSiteName = params.get("site");
    renderAll();
    status.recent_events.filter(e => ["critical", "emergency", "warning"].includes(e.severity)).slice(0, 5).forEach(e => {{
      const key = `${{e.ts}}:${{e.alert_id}}:${{e.status}}`;
      if (!seenAlerts.has(key)) {{ seenAlerts.add(key); alertUser(e); }}
    }});
    localStorage.setItem("meerkatSeenAlerts", JSON.stringify([...seenAlerts].slice(-100)));
  }} catch (error) {{
    document.getElementById("liveDot").className = "dot bad";
    document.getElementById("liveText").textContent = "Offline";
  }}
}}
async function postJson(url, body = {{}}) {{
  const token = localStorage.getItem("meerkatActionToken");
  const headers = {{"Content-Type": "application/json"}};
  if (token) headers["X-Meerkat-Action-Token"] = token;
  const response = await fetch(url, {{method: "POST", headers, body: JSON.stringify(body)}});
  const payload = await response.json();
  if (!payload.ok) showToast("Action failed", payload.error || "Request failed");
  else showToast("Done", payload.message || "Action completed");
  await refresh();
}}
async function addSite() {{
  const name = document.getElementById("siteNameInput").value.trim();
  const url = document.getElementById("siteUrlInput").value.trim();
  if (!name || !url) {{ showToast("Missing site details", "Name and URL are required."); return; }}
  await postJson("/api/actions/sites/add", {{name, url}});
  selectedSiteName = name;
  document.getElementById("siteNameInput").value = "";
  document.getElementById("siteUrlInput").value = "";
}}
async function removeSelectedSite() {{
  const site = selectedSite();
  if (!site) {{ showToast("No site selected", "Select a runtime site first."); return; }}
  await postJson("/api/actions/sites/remove", {{name: site.name}});
  selectedSiteName = "";
}}
applyPreferences();
drawLoadingChart();
refresh();
setInterval(() => {{ document.getElementById("refreshAge").textContent = lastRefresh ? `${{Math.floor((Date.now() - lastRefresh) / 1000)}}s` : "--s"; }}, 1000);
window.addEventListener("resize", renderSelectedSite);
</script>
</body>
</html>"""


def skeleton_stats() -> str:
    return "".join(
        "<section class='card stat'><div class='skeleton sk-line short'></div><div class='skeleton sk-value'></div><div class='skeleton sk-line medium'></div></section>"
        for _ in range(4)
    )


def skeleton_list() -> str:
    return "".join("<div class='skeleton' style='height:68px'></div>" for _ in range(3))


def empty_bars(count: int) -> str:
    return "".join("<span class='empty'></span>" for _ in range(count))


def dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Meerkat Monitor</title>
  <style>
    :root {
      --bg: #eef3f8;
      --surface: #ffffff;
      --surface-2: #f7fafc;
      --ink: #111827;
      --muted: #667085;
      --line: #d8e0ea;
      --brand: #15b86f;
      --brand-dark: #0b8f55;
      --blue: #2563eb;
      --warn: #d98b16;
      --bad: #dd3f3f;
      --shadow: 0 12px 30px rgba(17, 24, 39, .08);
      --radius: 8px;
    }
    body[data-theme="dark"] {
      --bg: #0d1218;
      --surface: #151d27;
      --surface-2: #101720;
      --ink: #edf2f7;
      --muted: #9aa6b2;
      --line: #273342;
      --shadow: 0 16px 34px rgba(0, 0, 0, .28);
    }
    * { box-sizing: border-box; }
    html, body { min-width: 0; overflow-x: hidden; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    button, input, select {
      width: 100%;
      min-width: 0;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
    }
    button { cursor: pointer; font-weight: 750; }
    button:hover { border-color: var(--brand); }
    .primary { border-color: var(--brand); background: var(--brand); color: #ffffff; }
    .danger { color: var(--bad); border-color: rgba(221, 63, 63, .45); }
    code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; }
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 292px minmax(0, 1fr);
    }
    .sidebar {
      min-width: 0;
      background: var(--surface);
      border-right: 1px solid var(--line);
      padding: 18px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }
    .brand {
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      margin-bottom: 16px;
    }
    .logo {
      width: 44px;
      height: 44px;
      border-radius: 8px;
      box-shadow: 0 10px 24px rgba(21, 184, 111, .25);
      overflow: hidden;
      flex: 0 0 auto;
    }
    h1, h2, h3 { margin: 0; }
    h1 { font-size: 20px; line-height: 1.1; font-weight: 850; }
    h2 { font-size: 20px; line-height: 1.2; font-weight: 850; }
    h3 { font-size: 14px; line-height: 1.2; font-weight: 850; }
    .subtitle, .muted { color: var(--muted); }
    .subtitle { margin-top: 3px; font-size: 12px; overflow-wrap: anywhere; }
    .live-row { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0 14px; }
    .pill {
      display: inline-flex;
      width: auto;
      max-width: 100%;
      align-items: center;
      gap: 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: var(--surface-2);
      color: var(--muted);
      font-size: 12px;
      white-space: normal;
    }
    .dot { width: 8px; height: 8px; border-radius: 999px; background: var(--muted); flex: 0 0 auto; }
    .dot.good { background: var(--brand); }
    .dot.bad { background: var(--bad); }
    .dot.warn { background: var(--warn); }
    .monitor-search { margin-bottom: 10px; }
    .monitor-list { display: grid; gap: 8px; }
    .monitor-row {
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      min-height: 58px;
      padding: 10px;
      text-align: left;
      background: transparent;
      border: 1px solid transparent;
    }
    .monitor-row.active { background: rgba(21, 184, 111, .12); border-color: rgba(21, 184, 111, .35); }
    .monitor-name { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 850; }
    .mini-bars { display: grid; grid-auto-flow: column; gap: 3px; align-items: end; }
    .bar-ok, .bar-bad, .bar-empty { width: 4px; height: 18px; border-radius: 99px; background: var(--brand); }
    .bar-bad { background: var(--bad); }
    .bar-empty { background: var(--line); }
    .content { min-width: 0; padding: 20px; }
    .topbar {
      display: flex;
      gap: 12px;
      align-items: flex-start;
      justify-content: space-between;
      margin-bottom: 14px;
    }
    .top-actions { display: flex; width: auto; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    .top-actions button { width: auto; }
    .grid { display: grid; gap: 14px; min-width: 0; }
    .summary { grid-template-columns: repeat(5, minmax(0, 1fr)); margin-bottom: 14px; }
    .card {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .stat { padding: 15px; min-height: 98px; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
    .value { font-size: 25px; line-height: 1.05; font-weight: 850; overflow-wrap: anywhere; }
    .subvalue { color: var(--muted); font-size: 12px; margin-top: 8px; overflow-wrap: anywhere; }
    .section-head {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .section-body { padding: 16px; min-width: 0; }
    .detail-layout { grid-template-columns: minmax(0, 1.35fr) minmax(290px, .65fr); align-items: start; }
    .site-hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: start;
    }
    .site-title { min-width: 0; font-size: 28px; line-height: 1.1; font-weight: 900; overflow-wrap: anywhere; }
    .site-url { color: var(--brand-dark); display: inline-block; margin-top: 7px; max-width: 100%; overflow-wrap: anywhere; }
    .big-status {
      min-width: 72px;
      min-height: 46px;
      display: grid;
      place-items: center;
      border-radius: 999px;
      background: var(--brand);
      color: #ffffff;
      font-size: 17px;
      font-weight: 900;
    }
    .big-status.down { background: var(--bad); }
    .uptime-bars {
      display: grid;
      grid-template-columns: repeat(40, minmax(0, 1fr));
      gap: 4px;
      margin-top: 18px;
      min-height: 34px;
    }
    .uptime-bars span { min-width: 0; border-radius: 999px; background: var(--brand); }
    .uptime-bars span.down { background: var(--bad); }
    .uptime-bars span.empty { background: var(--line); opacity: .75; }
    .metric-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
    .metric-card { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface-2); padding: 13px; text-align: center; }
    .metric-card strong { display: block; font-size: 22px; margin-top: 6px; overflow-wrap: anywhere; }
    .chart-wrap { height: 250px; }
    #latencyChart { width: 100%; height: 100%; display: block; }
    .metrics { display: grid; gap: 13px; }
    .metric-row { display: grid; grid-template-columns: 76px minmax(0, 1fr) 64px; gap: 10px; align-items: center; }
    .track { height: 10px; border-radius: 999px; background: var(--line); overflow: hidden; }
    .fill { width: 0%; height: 100%; border-radius: inherit; background: var(--brand); }
    .fill.warn { background: var(--warn); }
    .fill.bad { background: var(--bad); }
    .metric-value { text-align: right; font-variant-numeric: tabular-nums; }
    .form-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr) 100px 108px; gap: 10px; align-items: start; }
    .table-scroll { max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: middle; }
    th { color: var(--muted); font-size: 12px; font-weight: 800; }
    tr:last-child td { border-bottom: 0; }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 4px 8px;
      background: var(--surface-2);
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }
    .status.good { color: var(--brand-dark); }
    .status.bad { color: var(--bad); }
    .status.warn { color: var(--warn); }
    .events { display: grid; gap: 10px; }
    .event { border-left: 3px solid var(--line); padding-left: 10px; min-width: 0; }
    .event.warning { border-left-color: var(--warn); }
    .event.critical, .event.emergency { border-left-color: var(--bad); }
    .event.info { border-left-color: var(--blue); }
    .event-title { font-weight: 850; overflow-wrap: anywhere; }
    .event-meta { color: var(--muted); font-size: 12px; margin: 3px 0 5px; overflow-wrap: anywhere; }
    .event-body { white-space: pre-wrap; line-height: 1.4; overflow-wrap: anywhere; }
    .empty { color: var(--muted); padding: 10px 0; }
    .settings {
      display: none;
      position: fixed;
      inset: 0;
      z-index: 20;
      background: rgba(13, 18, 24, .45);
      padding: 16px;
    }
    .settings.open { display: grid; place-items: center; }
    .settings-card {
      width: min(720px, 100%);
      max-height: calc(100vh - 32px);
      overflow: auto;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .setting-field { display: grid; gap: 6px; min-width: 0; }
    .setting-field label { color: var(--muted); font-size: 12px; font-weight: 800; }
    .actions-stack { display: grid; gap: 10px; }
    @media (max-width: 1180px) {
      .app { grid-template-columns: 260px minmax(0, 1fr); }
      .summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .detail-layout { grid-template-columns: 1fr; }
    }
    @media (max-width: 820px) {
      .app { display: block; }
      .sidebar {
        position: relative;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--line);
        padding: 14px;
      }
      .monitor-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .content { padding: 14px; }
      .topbar { display: grid; }
      .top-actions { width: 100%; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .top-actions button { width: 100%; }
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .form-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      body { font-size: 13px; }
      .sidebar, .content { padding: 12px; }
      .brand { grid-template-columns: 40px minmax(0, 1fr); }
      .logo { width: 40px; height: 40px; }
      h2 { font-size: 18px; }
      .monitor-list, .summary, .metric-cards, .settings-grid { grid-template-columns: 1fr; }
      .card { box-shadow: none; }
      .stat { min-height: 86px; padding: 13px; }
      .value { font-size: 23px; }
      .section-head { align-items: flex-start; flex-direction: column; padding: 13px; }
      .section-body { padding: 13px; }
      .site-hero { grid-template-columns: 1fr; }
      .site-title { font-size: 23px; }
      .big-status { width: 68px; min-width: 68px; min-height: 40px; font-size: 15px; }
      .uptime-bars { grid-template-columns: repeat(24, minmax(0, 1fr)); gap: 3px; min-height: 28px; }
      .chart-wrap { height: 210px; }
      .metric-row { grid-template-columns: 62px minmax(0, 1fr) 54px; gap: 8px; }
      .actions-stack { grid-template-columns: 1fr; }
      .table-scroll { overflow: visible; }
      table, thead, tbody, tr, th, td { display: block; width: 100%; }
      thead { display: none; }
      tr {
        border: 1px solid var(--line);
        border-radius: var(--radius);
        background: var(--surface-2);
        padding: 9px;
        margin-bottom: 10px;
      }
      td {
        border-bottom: 0;
        padding: 5px 0;
        white-space: normal;
        overflow-wrap: anywhere;
      }
      td[data-label] {
        display: grid;
        grid-template-columns: 82px minmax(0, 1fr);
        gap: 8px;
      }
      td[data-label]::before {
        content: attr(data-label);
        color: var(--muted);
        font-size: 12px;
        font-weight: 800;
      }
    }
  </style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <svg class="logo" viewBox="0 0 64 64" role="img" aria-label="Meerkat logo">
        <defs>
          <linearGradient id="g" x1="9" y1="7" x2="56" y2="58" gradientUnits="userSpaceOnUse">
            <stop stop-color="#17d985"/>
            <stop offset="1" stop-color="#2563eb"/>
          </linearGradient>
        </defs>
        <rect width="64" height="64" rx="12" fill="url(#g)"/>
        <path d="M18 44V22c0-4 3-7 7-7h14c4 0 7 3 7 7v22h-8V25l-6 16h-5l-6-16v19h-8Z" fill="#fff"/>
        <circle cx="24" cy="18" r="3" fill="#0d1218" opacity=".28"/>
        <circle cx="40" cy="18" r="3" fill="#0d1218" opacity=".28"/>
      </svg>
      <div>
        <h1>Meerkat</h1>
        <div class="subtitle">Uptime and homelab monitoring</div>
      </div>
    </div>
    <div class="live-row">
      <span class="pill"><span id="liveDot" class="dot"></span><span id="liveText">Loading</span></span>
      <span class="pill"><span id="refreshAge">--</span>s ago</span>
    </div>
    <input id="siteSearch" class="monitor-search" placeholder="Search monitors" oninput="renderSiteList()">
    <div id="monitorList" class="monitor-list">
      <div class="skeleton sk-monitor"></div>
      <div class="skeleton sk-monitor"></div>
      <div class="skeleton sk-monitor"></div>
    </div>
  </aside>

  <main class="content">
    <div class="topbar">
      <div>
        <h2>Operations</h2>
        <div class="subtitle">HTTP uptime, response time, system health, Docker, and network state.</div>
      </div>
      <div class="top-actions">
        <button onclick="toggleSettings()">Settings</button>
        <button onclick="refresh()">Refresh</button>
      </div>
    </div>

    <div class="grid summary" id="summary"></div>

    <div class="grid detail-layout">
      <div class="grid">
        <section class="card">
          <div class="section-body">
            <div class="site-hero">
              <div>
                <div class="site-title" id="selectedSiteName">No monitor selected</div>
                <a class="site-url" id="selectedSiteUrl" href="#" target="_blank" rel="noreferrer"></a>
              </div>
              <div class="big-status" id="selectedSiteStatus">--</div>
            </div>
            <div class="uptime-bars" id="uptimeBars"></div>
            <div class="subtitle" id="checkCaption">Waiting for site checks.</div>
            <div class="metric-cards">
              <div class="metric-card"><span class="muted">Response</span><strong id="currentLatency">--</strong></div>
              <div class="metric-card"><span class="muted">Average</span><strong id="averageLatency">--</strong></div>
              <div class="metric-card"><span class="muted">Uptime</span><strong id="uptimePercent">--</strong></div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="section-head"><h3>Response Time</h3><span class="pill">Recent samples</span></div>
          <div class="section-body chart-wrap"><svg id="latencyChart" role="img" aria-label="HTTP response time chart"></svg></div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Website Monitors</h3>
            <button class="danger" onclick="removeSelectedSite()">Remove selected</button>
          </div>
          <div class="section-body">
            <div class="form-grid">
              <input id="siteNameInput" placeholder="Name">
              <input id="siteUrlInput" placeholder="https://example.com">
              <button class="primary" onclick="addSiteInline()">Add site</button>
              <button onclick="refresh()">Refresh</button>
            </div>
            <div class="table-scroll"><table id="sitesTable"><tbody><tr><td><div class="skeleton sk-line"></div><div class="skeleton sk-line medium"></div></td></tr></tbody></table></div>
          </div>
        </section>

        <section class="card">
          <div class="section-head"><h3>System Health</h3><span class="pill" id="tempState">Temperature --</span></div>
          <div class="section-body"><div class="metrics" id="metrics"></div></div>
        </section>
      </div>

      <div class="grid">
        <section class="card">
          <div class="section-head"><h3>Quick Actions</h3><span class="pill" id="actionState">Ready</span></div>
          <div class="section-body">
            <div class="actions-stack">
              <button onclick="clearCache()">Clear RAM cache</button>
              <select id="restartSelect"></select>
              <button onclick="restartSelected()">Restart container</button>
            </div>
          </div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Network</h3><span class="pill" id="routeState">Route unknown</span></div>
          <div class="section-body table-scroll"><table id="networkTable"></table></div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Docker Containers</h3><span class="pill" id="dockerCount">0 containers</span></div>
          <div class="section-body table-scroll"><table id="dockerTable"></table></div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Active Alerts</h3><span class="pill" id="alertMode">Alerts active</span></div>
          <div class="section-body" id="activeAlerts"></div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Recent Events</h3><button onclick="clearEvents()">Clear</button></div>
          <div class="section-body"><div class="events" id="events"></div></div>
        </section>
      </div>
    </div>
  </main>
</div>

<div class="settings" id="settingsPanel" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
  <section class="settings-card">
    <div class="section-head">
      <div>
        <h3 id="settingsTitle">Settings</h3>
        <div class="subtitle">Display and action preferences for this browser.</div>
      </div>
      <button onclick="toggleSettings()">Close</button>
    </div>
    <div class="section-body">
      <div class="settings-grid">
        <div class="setting-field">
          <label for="themeSelect">Theme</label>
          <select id="themeSelect" onchange="savePreferences()">
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </div>
        <div class="setting-field">
          <label for="refreshSelect">Refresh interval</label>
          <select id="refreshSelect" onchange="savePreferences()">
            <option value="5000">5 seconds</option>
            <option value="10000">10 seconds</option>
            <option value="30000">30 seconds</option>
          </select>
        </div>
        <div class="setting-field">
          <label for="tokenInput">Action token</label>
          <input id="tokenInput" placeholder="Only needed if configured">
        </div>
        <div class="setting-field">
          <label>&nbsp;</label>
          <button onclick="saveActionToken()">Save token</button>
        </div>
      </div>
    </div>
  </section>
</div>

<script>
let lastRefresh = 0;
let refreshTimer = null;
let latest = {status: null, health: null, network: null, docker: null, sites: {sites: []}};
let selectedSiteName = "";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}
function pct(value) { return Math.round(Number(value || 0)); }
function statusClass(value) { return value === true ? "good" : value === false ? "bad" : "warn"; }
function statusLabel(value) { return value === true ? "up" : value === false ? "down" : "unknown"; }
function tone(value) { return value >= 90 ? "bad" : value >= 75 ? "warn" : "good"; }
function latencyText(value) { return value === null || value === undefined ? "--" : `${Math.round(value)}ms`; }
function siteHistory(site) { return Array.isArray(site?.history) ? site.history : []; }
function averageLatency(site) {
  const samples = siteHistory(site).map(s => s.latency_ms).filter(v => v !== null && v !== undefined);
  if (!samples.length) return null;
  return samples.reduce((sum, value) => sum + Number(value), 0) / samples.length;
}
function uptime(site) {
  const samples = siteHistory(site);
  if (!samples.length) return site?.up ? 100 : 0;
  return Math.round(samples.filter(s => s.up === true).length / samples.length * 1000) / 10;
}
function tempLabel(value) {
  if (value === null || value === undefined) return "unavailable";
  if (value >= 85) return "very hot";
  if (value >= 75) return "hot";
  if (value >= 60) return "warm";
  return "normal";
}
function statusPill(value, text) {
  const cls = statusClass(value);
  return `<span class="status ${cls}"><span class="dot ${cls}"></span>${esc(text || statusLabel(value))}</span>`;
}
function metric(name, value, suffix = "%") {
  const rounded = pct(value);
  const cls = tone(rounded);
  return `<div class="metric-row">
    <div class="muted">${esc(name)}</div>
    <div class="track"><div class="fill ${cls}" style="width:${Math.max(0, Math.min(100, rounded))}%"></div></div>
    <div class="metric-value">${rounded}${suffix}</div>
  </div>`;
}
function miniBars(site) {
  const samples = siteHistory(site).slice(-14);
  const padded = Array(Math.max(0, 14 - samples.length)).fill(null).concat(samples);
  return padded.map(sample => {
    if (!sample) return "<span class='bar-empty'></span>";
    return `<span class="${sample.up ? "bar-ok" : "bar-bad"}"></span>`;
  }).join("");
}
function renderSiteList() {
  const query = document.getElementById("siteSearch").value.trim().toLowerCase();
  const sites = latest.sites.sites || [];
  const filtered = sites.filter(site => `${site.name} ${site.url}`.toLowerCase().includes(query));
  document.getElementById("monitorList").innerHTML = filtered.length ? filtered.map(site => `
    <button class="monitor-row ${site.name === selectedSiteName ? "active" : ""}" onclick="selectSite('${esc(site.name)}')">
      <span>
        <span class="monitor-name">${esc(site.name)}</span>
        <span class="subtitle">${uptime(site)}% uptime</span>
      </span>
      <span class="mini-bars">${miniBars(site)}</span>
    </button>
  `).join("") : "<div class='empty'>No site monitors found.</div>";
}
function renderBars(site) {
  const samples = siteHistory(site).slice(-40);
  const mobile = window.matchMedia("(max-width: 560px)").matches;
  const count = mobile ? 24 : 40;
  const visible = samples.slice(-count);
  const padded = Array(Math.max(0, count - visible.length)).fill(null).concat(visible);
  document.getElementById("uptimeBars").innerHTML = padded.map(sample => {
    if (!sample) return "<span class='empty'></span>";
    return `<span class="${sample.up ? "" : "down"}"></span>`;
  }).join("");
}
function selectSite(name) {
  selectedSiteName = name;
  renderSelectedSite();
  renderSiteList();
}
function selectedSite() {
  const sites = latest.sites.sites || [];
  return sites.find(site => site.name === selectedSiteName) || sites[0] || null;
}
function renderSelectedSite() {
  const site = selectedSite();
  if (!site) {
    document.getElementById("selectedSiteName").textContent = "No monitor selected";
    document.getElementById("selectedSiteUrl").textContent = "";
    document.getElementById("selectedSiteStatus").textContent = "--";
    document.getElementById("selectedSiteStatus").className = "big-status";
    document.getElementById("uptimeBars").innerHTML = "";
    document.getElementById("checkCaption").textContent = "Add an HTTP monitor to start collecting response data.";
    document.getElementById("currentLatency").textContent = "--";
    document.getElementById("averageLatency").textContent = "--";
    document.getElementById("uptimePercent").textContent = "--";
    drawChart([]);
    return;
  }
  selectedSiteName = site.name;
  const statusNode = document.getElementById("selectedSiteStatus");
  document.getElementById("selectedSiteName").textContent = site.name;
  document.getElementById("selectedSiteUrl").textContent = site.url;
  document.getElementById("selectedSiteUrl").href = site.url;
  statusNode.textContent = site.up ? "Up" : "Down";
  statusNode.className = `big-status ${site.up ? "" : "down"}`;
  renderBars(site);
  document.getElementById("checkCaption").textContent = `HTTP ${site.status_code ?? "none"} - ${site.error || "latest check completed"}`;
  document.getElementById("currentLatency").textContent = latencyText(site.latency_ms);
  document.getElementById("averageLatency").textContent = latencyText(averageLatency(site));
  document.getElementById("uptimePercent").textContent = `${uptime(site)}%`;
  drawChart(siteHistory(site));
}
function drawChart(samples) {
  const svg = document.getElementById("latencyChart");
  const values = samples.filter(s => s.latency_ms !== null && s.latency_ms !== undefined).slice(-60);
  if (!values.length) {
    svg.innerHTML = `<text x="20" y="38" fill="var(--muted)">No response samples yet.</text>`;
    return;
  }
  const width = 720;
  const height = 240;
  const pad = {left: 44, right: 16, top: 16, bottom: 34};
  const max = Math.max(...values.map(s => Number(s.latency_ms)), 10);
  const min = Math.min(...values.map(s => Number(s.latency_ms)), 0);
  const range = Math.max(1, max - min);
  const xStep = values.length > 1 ? (width - pad.left - pad.right) / (values.length - 1) : 0;
  const points = values.map((sample, index) => {
    const x = pad.left + index * xStep;
    const y = height - pad.bottom - ((Number(sample.latency_ms) - min) / range) * (height - pad.top - pad.bottom);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const area = `${pad.left},${height - pad.bottom} ${points} ${width - pad.right},${height - pad.bottom}`;
  const grid = [0, .25, .5, .75, 1].map(part => {
    const y = pad.top + part * (height - pad.top - pad.bottom);
    const label = Math.round(max - part * range);
    return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="var(--line)" />
      <text x="6" y="${y + 4}" fill="var(--muted)" font-size="11">${label}</text>`;
  }).join("");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `${grid}
    <polyline points="${area}" fill="rgba(21,184,111,.12)" stroke="none"></polyline>
    <polyline points="${points}" fill="none" stroke="var(--brand)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
    <text x="${pad.left}" y="${height - 9}" fill="var(--muted)" font-size="11">older</text>
    <text x="${width - pad.right - 34}" y="${height - 9}" fill="var(--muted)" font-size="11">now</text>`;
}
function applyPreferences() {
  const prefs = JSON.parse(localStorage.getItem("meerkatUiPrefs") || "{}");
  const theme = prefs.theme || "light";
  const refreshMs = String(prefs.refresh || 10000);
  document.body.dataset.theme = theme;
  document.getElementById("themeSelect").value = theme;
  document.getElementById("refreshSelect").value = refreshMs;
  document.getElementById("tokenInput").value = localStorage.getItem("meerkatActionToken") || "";
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, Number(refreshMs));
}
function drawLoadingChart() {
  const svg = document.getElementById("latencyChart");
  svg.setAttribute("viewBox", "0 0 720 240");
  svg.innerHTML = `
    <rect x="0" y="0" width="720" height="240" rx="8" fill="var(--panel-2)"></rect>
    <line x1="46" y1="48" x2="704" y2="48" stroke="var(--line)"></line>
    <line x1="46" y1="96" x2="704" y2="96" stroke="var(--line)"></line>
    <line x1="46" y1="144" x2="704" y2="144" stroke="var(--line)"></line>
    <polyline points="46,172 140,146 232,156 326,112 420,128 514,82 612,104 704,72" fill="none" stroke="var(--brand)" stroke-width="3" stroke-linecap="round" opacity=".35"></polyline>
    <text x="24" y="34" fill="var(--muted)" font-size="12">Loading response data...</text>`;
}
function savePreferences() {
  localStorage.setItem("meerkatUiPrefs", JSON.stringify({
    theme: document.getElementById("themeSelect").value,
    refresh: Number(document.getElementById("refreshSelect").value)
  }));
  applyPreferences();
}
function saveActionToken() {
  const token = document.getElementById("tokenInput").value.trim();
  if (token) localStorage.setItem("meerkatActionToken", token);
  else localStorage.removeItem("meerkatActionToken");
  document.getElementById("actionState").textContent = token ? "Action token saved" : "Action token cleared";
}
async function refresh() {
  try {
    const [status, health, network, docker, sites] = await Promise.all([
      fetch("/api/status").then(r => r.json()),
      fetch("/api/health").then(r => r.json()),
      fetch("/api/network").then(r => r.json()),
      fetch("/api/docker").then(r => r.json()),
      fetch("/api/sites").then(r => r.json())
    ]);
    latest = {status, health, network, docker, sites};
    lastRefresh = Date.now();
    document.getElementById("liveDot").className = "dot good";
    document.getElementById("liveText").textContent = "Live";

    const running = docker.containers.filter(c => c.status === "running").length;
    document.getElementById("summary").innerHTML = [
      ["Internet", statusLabel(status.internet_up), status.internet_up ? "Reachable" : "Probe failing"],
      ["Alerts", status.active_alerts.length, status.alerts_silenced ? "Silenced" : "Active"],
      ["Containers", `${running}/${docker.containers.length}`, docker.available ? "Docker connected" : "Docker unavailable"],
      ["Sites", `${sites.up}/${sites.total}`, sites.down ? `${sites.down} down` : "All monitored sites up"],
      ["Route", status.default_route_label || "unknown", "Outbound interface"]
    ].map(item => `<section class="card stat"><div class="label">${esc(item[0])}</div><div class="value">${esc(item[1])}</div><div class="subvalue">${esc(item[2])}</div></section>`).join("");

    const temp = health.cpu_temperature;
    document.getElementById("metrics").innerHTML = [
      metric("CPU", health.cpu_percent),
      metric("RAM", health.ram_percent),
      metric("Disk", health.disk_percent),
      temp === null || temp === undefined ? "" : metric("Temp", temp, "C")
    ].join("");
    document.getElementById("tempState").textContent = `CPU ${temp === null || temp === undefined ? "unavailable" : Math.round(temp) + "C (" + tempLabel(temp) + ")"}`;

    document.getElementById("routeState").textContent = `Route ${network.default_route_label || "unknown"}`;
    const interfaces = Object.entries(network.interfaces);
    document.getElementById("networkTable").innerHTML = interfaces.length ? `<thead><tr><th>Type</th><th>Interface</th><th>Status</th><th>Carrier</th><th>IPv4</th></tr></thead><tbody>${interfaces.map(([type, item]) => `<tr><td data-label="Type">${esc(type)}</td><td data-label="Interface"><code>${esc(item.name)}</code></td><td data-label="Status">${statusPill(item.up)}</td><td data-label="Carrier">${esc(item.carrier)}</td><td data-label="IPv4">${esc(item.has_ip)}</td></tr>`).join("")}</tbody>` : "<tbody><tr><td class='empty'>No interfaces configured.</td></tr></tbody>";

    document.getElementById("dockerCount").textContent = `${docker.containers.length} containers`;
    document.getElementById("restartSelect").innerHTML = docker.containers.length ? docker.containers.map(c => `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join("") : "<option value=''>No containers</option>";
    document.getElementById("dockerTable").innerHTML = docker.available && docker.containers.length
      ? `<thead><tr><th>Name</th><th>Status</th><th>Image</th></tr></thead><tbody>${docker.containers.map(c => `<tr><td data-label="Name"><code>${esc(c.name)}</code></td><td data-label="Status">${statusPill(c.status === "running", c.status)}</td><td data-label="Image">${esc(c.image || "untagged")}</td></tr>`).join("")}</tbody>`
      : `<tbody><tr><td class="empty">${docker.available ? "No containers found." : esc(docker.error)}</td></tr></tbody>`;

    if (!selectedSiteName && sites.sites.length) selectedSiteName = sites.sites[0].name;
    document.getElementById("sitesTable").innerHTML = sites.sites.length
      ? `<thead><tr><th></th><th>Name</th><th>Status</th><th>Latency</th><th>HTTP</th><th>URL</th></tr></thead><tbody>${sites.sites.map(s => `<tr><td data-label="Pick"><input type="radio" name="selectedSite" value="${esc(s.name)}" ${s.name === selectedSiteName ? "checked" : ""} onchange="selectSite('${esc(s.name)}')"></td><td data-label="Name">${esc(s.name)}</td><td data-label="Status">${statusPill(s.up)}</td><td data-label="Latency">${latencyText(s.latency_ms)}</td><td data-label="HTTP">${esc(s.status_code ?? "none")}</td><td data-label="URL"><code>${esc(s.url)}</code></td></tr>`).join("")}</tbody>`
      : "<tbody><tr><td class='empty'>No website monitors configured.</td></tr></tbody>";
    renderSiteList();
    renderSelectedSite();

    document.getElementById("alertMode").textContent = status.alerts_silenced ? "Alerts silenced" : "Alerts active";
    document.getElementById("activeAlerts").innerHTML = status.active_alerts.length
      ? status.active_alerts.map(a => `<div class="event critical"><div class="event-title">${esc(a)}</div><div class="event-meta">currently active</div></div>`).join("")
      : "<div class='empty'>No active alerts.</div>";
    document.getElementById("events").innerHTML = status.recent_events.length
      ? status.recent_events.map(e => `<div class="event ${esc(e.severity)}"><div class="event-title">${esc(e.title)}</div><div class="event-meta">${esc(e.ts)} - ${esc(e.severity)} - ${esc(e.source)}</div><div class="event-body">${esc(e.body)}</div></div>`).join("")
      : "<div class='empty'>No events yet.</div>";
  } catch (error) {
    document.getElementById("liveDot").className = "dot bad";
    document.getElementById("liveText").textContent = "Disconnected";
  }
}
async function postJson(url, body = {}) {
  let token = localStorage.getItem("meerkatActionToken");
  const headers = {"Content-Type": "application/json"};
  if (token) headers["X-Meerkat-Action-Token"] = token;
  let response = await fetch(url, {method: "POST", headers, body: JSON.stringify(body)});
  if (response.status === 403) {
    token = prompt("Action token");
    if (!token) return;
    localStorage.setItem("meerkatActionToken", token);
    response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Meerkat-Action-Token": token},
      body: JSON.stringify(body)
    });
  }
  const payload = await response.json();
  if (response.status === 403) localStorage.removeItem("meerkatActionToken");
  document.getElementById("actionState").textContent = payload.ok ? payload.message : payload.error;
  await refresh();
}
async function clearCache() { await postJson("/api/actions/clear-ram-cache"); }
async function restartSelected() {
  const container = document.getElementById("restartSelect").value;
  if (container) await postJson("/api/actions/docker/restart", {container});
}
async function addSite() {
  const name = document.getElementById("siteNameInput").value.trim();
  const url = document.getElementById("siteUrlInput").value.trim();
  if (!name || !url) {
    document.getElementById("actionState").textContent = "Site name and URL are required";
    return;
  }
  await postJson("/api/actions/sites/add", {name, url});
  selectedSiteName = name;
  document.getElementById("siteNameInput").value = "";
  document.getElementById("siteUrlInput").value = "";
}
async function removeSelectedSite() {
  const site = selectedSite();
  if (!site) {
    document.getElementById("actionState").textContent = "Select a runtime site first";
    return;
  }
  await postJson("/api/actions/sites/remove", {name: site.name});
  selectedSiteName = "";
}
async function clearEvents() { await postJson("/api/actions/events/clear"); }
function toggleSettings() { document.getElementById("settingsPanel").classList.toggle("open"); }

applyPreferences();
refresh();
setInterval(() => {
  document.getElementById("refreshAge").textContent = lastRefresh ? Math.floor((Date.now() - lastRefresh) / 1000) : "--";
}, 1000);
window.addEventListener("resize", renderSelectedSite);
</script>
</body>
</html>"""


def dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Meerkat Monitor</title>
  <style>
    :root {
      --bg: #f4f7fb;
      --panel: #ffffff;
      --panel-2: #f8fafc;
      --ink: #152033;
      --muted: #667085;
      --line: #dce3ee;
      --brand: #20c977;
      --brand-2: #0fa968;
      --bad: #e04444;
      --warn: #e59a2f;
      --blue: #2563eb;
      --shadow: 0 16px 40px rgba(21, 32, 51, .08);
    }
    body[data-theme="dark"] {
      --bg: #0e131b;
      --panel: #151c26;
      --panel-2: #101720;
      --ink: #edf2f7;
      --muted: #98a2b3;
      --line: #263241;
      --shadow: 0 16px 40px rgba(0, 0, 0, .28);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    button, input, select {
      width: 100%;
      min-width: 0;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
    }
    button { cursor: pointer; font-weight: 750; }
    button:hover { border-color: var(--brand); }
    .primary { border-color: var(--brand); background: var(--brand); color: #ffffff; }
    .danger { border-color: var(--bad); color: var(--bad); }
    input[type="radio"] { width: auto; min-height: 0; }
    code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; white-space: normal; }
    .shell { min-width: 0; min-height: 100vh; display: grid; grid-template-columns: 300px minmax(0, 1fr); }
    .sidebar {
      min-width: 0;
      position: sticky;
      top: 0;
      height: 100vh;
      border-right: 1px solid var(--line);
      background: var(--panel);
      padding: 18px;
      overflow: auto;
    }
    .brand { display: grid; grid-template-columns: 42px minmax(0, 1fr); align-items: center; gap: 12px; margin-bottom: 18px; }
    .mark {
      width: 42px;
      height: 42px;
      border-radius: 8px;
      box-shadow: 0 10px 24px rgba(32, 201, 119, .22);
      overflow: hidden;
    }
    h1, h2, h3 { margin: 0; }
    h1 { font-size: 20px; line-height: 1.1; }
    h2 { font-size: 16px; }
    h3 { font-size: 14px; }
    .subtitle, .muted { color: var(--muted); }
    .subtitle { margin-top: 3px; font-size: 12px; }
    .topline { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      color: var(--muted);
      background: var(--panel-2);
      font-size: 12px;
      white-space: nowrap;
    }
    .dot { width: 8px; height: 8px; border-radius: 999px; background: var(--muted); flex: 0 0 auto; }
    .dot.good { background: var(--brand); }
    .dot.bad { background: var(--bad); }
    .dot.warn { background: var(--warn); }
    .search { width: 100%; margin-bottom: 12px; }
    .monitor-list { display: grid; gap: 8px; }
    .monitor-row {
      width: 100%;
      min-height: 58px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      border: 1px solid transparent;
      border-radius: 8px;
      background: transparent;
      padding: 10px;
      text-align: left;
    }
    .monitor-row.active { background: rgba(32, 201, 119, .12); border-color: rgba(32, 201, 119, .28); }
    .monitor-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 800; }
    .mini-bars { display: grid; grid-auto-flow: column; gap: 3px; align-items: end; }
    .bar-ok, .bar-bad, .bar-empty {
      width: 4px;
      height: 18px;
      border-radius: 99px;
      background: var(--brand);
    }
    .bar-bad { background: var(--bad); }
    .bar-empty { background: var(--line); }
    .content { min-width: 0; max-width: 100%; padding: 22px; }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 16px;
    }
    .toolbar-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    .toolbar-actions button { width: auto; }
    .grid { display: grid; gap: 14px; min-width: 0; }
    .summary { grid-template-columns: repeat(5, minmax(0, 1fr)); margin-bottom: 14px; }
    .card {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .stat { padding: 15px; min-height: 100px; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
    .value { font-size: 26px; line-height: 1.05; font-weight: 850; overflow-wrap: anywhere; }
    .subvalue { color: var(--muted); font-size: 12px; margin-top: 9px; }
    .section-head {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .section-body { padding: 16px; }
    .detail-layout { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(280px, .65fr); gap: 14px; align-items: start; }
    .site-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
    .site-title { font-size: 28px; line-height: 1.1; font-weight: 850; overflow-wrap: anywhere; }
    .site-url { display: inline-block; color: var(--brand-2); margin-top: 7px; overflow-wrap: anywhere; }
    .big-status {
      min-width: 74px;
      min-height: 48px;
      display: grid;
      place-items: center;
      border-radius: 999px;
      background: var(--brand);
      color: #ffffff;
      font-size: 18px;
      font-weight: 900;
    }
    .big-status.down { background: var(--bad); }
    .kuma-bars {
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: minmax(5px, 1fr);
      gap: 5px;
      align-items: stretch;
      margin-top: 18px;
      min-height: 34px;
    }
    .kuma-bars span { border-radius: 999px; background: var(--brand); }
    .kuma-bars span.down { background: var(--bad); }
    .kuma-bars span.empty { background: var(--line); opacity: .7; }
    .metric-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
    .metric-card { min-width: 0; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-2); padding: 14px; text-align: center; }
    .metric-card strong { display: block; font-size: 22px; margin-top: 6px; }
    .chart-wrap { height: 260px; }
    #latencyChart { width: 100%; height: 100%; display: block; }
    .metrics { display: grid; gap: 13px; }
    .metric-row { display: grid; grid-template-columns: 86px minmax(0, 1fr) 70px; gap: 10px; align-items: center; }
    .track { height: 10px; border-radius: 999px; background: var(--line); overflow: hidden; }
    .fill { width: 0%; height: 100%; border-radius: inherit; background: var(--brand); }
    .fill.warn { background: var(--warn); }
    .fill.bad { background: var(--bad); }
    .metric-value { text-align: right; font-variant-numeric: tabular-nums; }
    .form-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.5fr) 96px 96px; gap: 10px; }
    .table-scroll { max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: middle; }
    th { color: var(--muted); font-size: 12px; font-weight: 750; }
    tr:last-child td { border-bottom: 0; }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 4px 8px;
      background: var(--panel-2);
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      white-space: nowrap;
    }
    .status.good { color: var(--brand-2); }
    .status.bad { color: var(--bad); }
    .status.warn { color: var(--warn); }
    .events { display: grid; gap: 10px; }
    .event { border-left: 3px solid var(--line); padding-left: 10px; }
    .event.warning { border-left-color: var(--warn); }
    .event.critical, .event.emergency { border-left-color: var(--bad); }
    .event.info { border-left-color: var(--blue); }
    .event-title { font-weight: 800; }
    .event-meta { color: var(--muted); font-size: 12px; margin: 3px 0 5px; }
    .event-body { white-space: pre-wrap; line-height: 1.4; }
    .empty { color: var(--muted); padding: 10px 0; }
    .settings { display: none; margin-bottom: 14px; }
    .settings.open { display: block; }
    .settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .setting-field { display: grid; gap: 6px; min-width: 0; }
    .setting-field label { color: var(--muted); font-size: 12px; font-weight: 800; }
    .skeleton {
      position: relative;
      overflow: hidden;
      background: linear-gradient(90deg, var(--panel-2), rgba(32, 201, 119, .10), var(--panel-2));
      background-size: 220% 100%;
      animation: shimmer 1.35s ease-in-out infinite;
      border-radius: 8px;
      color: transparent;
    }
    .sk-line { height: 12px; width: 100%; margin-bottom: 10px; }
    .sk-line.short { width: 48%; }
    .sk-line.medium { width: 70%; }
    .sk-value { height: 30px; width: 62%; margin: 8px 0 12px; }
    .sk-card { min-height: 100px; padding: 15px; }
    .sk-monitor { height: 58px; }
    .sk-chart { height: 210px; }
    @keyframes shimmer {
      0% { background-position: 120% 0; }
      100% { background-position: -120% 0; }
    }
    @media (max-width: 1180px) {
      .shell { grid-template-columns: 280px minmax(0, 1fr); }
      .summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .detail-layout { grid-template-columns: 1fr; }
    }
    @media (max-width: 820px) {
      .shell { display: block; }
      .sidebar { position: relative; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
      .monitor-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .content { padding: 14px; }
      .toolbar { align-items: flex-start; flex-direction: column; }
      .toolbar-actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; justify-content: flex-start; }
      .toolbar-actions button { width: 100%; }
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .form-grid, .settings-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      body { font-size: 13px; }
      .sidebar { padding: 14px; }
      .monitor-list, .summary, .metric-cards { grid-template-columns: 1fr; }
      .site-hero { flex-direction: column; }
      .site-title { font-size: 24px; }
      .big-status { min-width: 64px; min-height: 42px; font-size: 16px; }
      .section-head { align-items: flex-start; flex-direction: column; }
      .metric-row { grid-template-columns: 70px minmax(0, 1fr) 58px; }
      .table-scroll { overflow: visible; }
      table, thead, tbody, tr, th, td { display: block; width: 100%; }
      thead { display: none; }
      tr {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel-2);
        padding: 9px;
        margin-bottom: 10px;
      }
      td {
        border-bottom: 0;
        padding: 5px 0;
        white-space: normal;
        overflow-wrap: anywhere;
      }
      td[data-label] {
        display: grid;
        grid-template-columns: 82px minmax(0, 1fr);
        gap: 8px;
      }
      td[data-label]::before {
        content: attr(data-label);
        color: var(--muted);
        font-size: 12px;
        font-weight: 800;
      }
    }
  </style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand">
      <svg class="mark" viewBox="0 0 64 64" role="img" aria-label="Meerkat logo">
        <defs><linearGradient id="logoGradient" x1="8" y1="7" x2="56" y2="58" gradientUnits="userSpaceOnUse"><stop stop-color="#20c977"/><stop offset="1" stop-color="#2563eb"/></linearGradient></defs>
        <rect width="64" height="64" rx="12" fill="url(#logoGradient)"/>
        <path d="M18 45V22c0-4 3-7 7-7h14c4 0 7 3 7 7v23h-8V25l-6 17h-5l-6-17v20h-8Z" fill="#fff"/>
        <circle cx="24" cy="18" r="3" fill="#0e131b" opacity=".26"/>
        <circle cx="40" cy="18" r="3" fill="#0e131b" opacity=".26"/>
      </svg>
      <div>
        <h1>Meerkat</h1>
        <div class="subtitle">Product-grade homelab monitoring</div>
      </div>
    </div>
    <div class="topline">
      <span class="pill"><span id="liveDot" class="dot"></span><span id="liveText">Loading</span></span>
      <span class="pill">Updated <span id="refreshAge">--</span>s ago</span>
    </div>
    <input id="siteSearch" class="search" placeholder="Search monitors" oninput="renderSiteList()">
    <div id="monitorList" class="monitor-list"></div>
  </aside>
  <main class="content">
    <div class="toolbar">
      <div>
        <h2>Operations Dashboard</h2>
        <div class="subtitle">HTTP uptime, response time, containers, network, and host health.</div>
      </div>
      <div class="toolbar-actions">
        <button onclick="toggleSettings()">Settings</button>
        <button onclick="refresh()">Refresh</button>
      </div>
    </div>

    <section class="card settings" id="settingsPanel">
      <div class="section-head"><h3>Display</h3><span class="pill">Stored in this browser</span></div>
      <div class="section-body">
        <div class="settings-grid">
          <div class="setting-field">
            <label for="themeSelect">Theme</label>
            <select id="themeSelect" onchange="savePreferences()">
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
          <div class="setting-field">
            <label for="refreshSelect">Refresh interval</label>
            <select id="refreshSelect" onchange="savePreferences()">
              <option value="5000">5 seconds</option>
              <option value="10000">10 seconds</option>
              <option value="30000">30 seconds</option>
            </select>
          </div>
          <div class="setting-field">
            <label for="tokenInput">Action token</label>
            <input id="tokenInput" placeholder="Only needed if configured">
          </div>
          <div class="setting-field">
            <label>&nbsp;</label>
            <button onclick="saveActionToken()">Save token</button>
          </div>
        </div>
      </div>
    </section>

    <div class="grid summary" id="summary">
      <section class="card sk-card"><div class="skeleton sk-line short"></div><div class="skeleton sk-value"></div><div class="skeleton sk-line medium"></div></section>
      <section class="card sk-card"><div class="skeleton sk-line short"></div><div class="skeleton sk-value"></div><div class="skeleton sk-line medium"></div></section>
      <section class="card sk-card"><div class="skeleton sk-line short"></div><div class="skeleton sk-value"></div><div class="skeleton sk-line medium"></div></section>
      <section class="card sk-card"><div class="skeleton sk-line short"></div><div class="skeleton sk-value"></div><div class="skeleton sk-line medium"></div></section>
      <section class="card sk-card"><div class="skeleton sk-line short"></div><div class="skeleton sk-value"></div><div class="skeleton sk-line medium"></div></section>
    </div>

    <div class="detail-layout">
      <div class="grid">
        <section class="card">
          <div class="section-body">
            <div class="site-hero">
              <div>
                <div class="site-title" id="selectedSiteName"><span class="skeleton sk-line medium" style="display:block;height:30px"></span></div>
                <a class="site-url" id="selectedSiteUrl" href="#" target="_blank" rel="noreferrer"></a>
              </div>
              <div class="big-status skeleton" id="selectedSiteStatus">--</div>
            </div>
            <div class="kuma-bars" id="uptimeBars"><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span><span class="empty"></span></div>
            <div class="subtitle" id="checkCaption">Loading monitor data...</div>
            <div class="metric-cards">
              <div class="metric-card"><span class="muted">Response</span><strong id="currentLatency"><span class="skeleton sk-line"></span></strong></div>
              <div class="metric-card"><span class="muted">Avg. Response</span><strong id="averageLatency"><span class="skeleton sk-line"></span></strong></div>
              <div class="metric-card"><span class="muted">Uptime</span><strong id="uptimePercent"><span class="skeleton sk-line"></span></strong></div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="section-head"><h3>Response Time</h3><span class="pill">Last samples</span></div>
          <div class="section-body chart-wrap"><svg id="latencyChart" role="img" aria-label="HTTP response time chart"></svg></div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Website Monitors</h3>
            <div class="toolbar-actions">
              <button class="primary" onclick="addSite()">Add site</button>
              <button class="danger" onclick="removeSelectedSite()">Remove selected</button>
            </div>
          </div>
          <div class="section-body">
            <div class="form-grid">
              <input id="siteNameInputInline" placeholder="Name">
              <input id="siteUrlInputInline" placeholder="https://example.com">
              <button class="primary" onclick="addSiteInline()">Add</button>
              <button onclick="refresh()">Refresh</button>
            </div>
            <div class="table-scroll"><table id="sitesTable"></table></div>
          </div>
        </section>

        <section class="card">
          <div class="section-head"><h3>System Health</h3><span class="pill" id="tempState">Temperature --</span></div>
          <div class="section-body"><div class="metrics" id="metrics"><div class="skeleton sk-line"></div><div class="skeleton sk-line"></div><div class="skeleton sk-line"></div></div></div>
        </section>
      </div>

      <div class="grid">
        <section class="card">
          <div class="section-head"><h3>Quick Actions</h3><span class="pill" id="actionState">Ready</span></div>
          <div class="section-body">
            <div class="grid">
              <button onclick="clearCache()">Clear RAM cache</button>
              <select id="restartSelect"></select>
              <button onclick="restartSelected()">Restart container</button>
            </div>
          </div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Network</h3><span class="pill" id="routeState">Route unknown</span></div>
          <div class="section-body table-scroll"><table id="networkTable"><tbody><tr><td><div class="skeleton sk-line"></div><div class="skeleton sk-line medium"></div></td></tr></tbody></table></div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Docker Containers</h3><span class="pill" id="dockerCount">0 containers</span></div>
          <div class="section-body table-scroll"><table id="dockerTable"><tbody><tr><td><div class="skeleton sk-line"></div><div class="skeleton sk-line medium"></div></td></tr></tbody></table></div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Active Alerts</h3><span class="pill" id="alertMode">Alerts active</span></div>
          <div class="section-body" id="activeAlerts"><div class="skeleton sk-line"></div><div class="skeleton sk-line medium"></div></div>
        </section>
        <section class="card">
          <div class="section-head"><h3>Recent Events</h3><button onclick="clearEvents()">Clear</button></div>
          <div class="section-body"><div class="events" id="events"><div class="skeleton sk-line"></div><div class="skeleton sk-line medium"></div><div class="skeleton sk-line short"></div></div></div>
        </section>
      </div>
    </div>
  </main>
</div>
<script>
let lastRefresh = 0;
let refreshTimer = null;
let latest = {status: null, health: null, network: null, docker: null, sites: {sites: []}};
let selectedSiteName = "";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}
function pct(value) { return Math.round(Number(value || 0)); }
function statusClass(value) { return value === true ? "good" : value === false ? "bad" : "warn"; }
function statusLabel(value) { return value === true ? "up" : value === false ? "down" : "unknown"; }
function tone(value) { return value >= 90 ? "bad" : value >= 75 ? "warn" : "good"; }
function tempLabel(value) {
  if (value === null || value === undefined) return "unavailable";
  if (value >= 85) return "very hot";
  if (value >= 75) return "hot";
  if (value >= 60) return "warm";
  return "normal";
}
function statusPill(value, text) {
  const cls = statusClass(value);
  return `<span class="status ${cls}"><span class="dot ${cls}"></span>${esc(text || statusLabel(value))}</span>`;
}
function metric(name, value, suffix = "%") {
  const rounded = pct(value);
  const cls = tone(rounded);
  return `<div class="metric-row">
    <div class="muted">${esc(name)}</div>
    <div class="track"><div class="fill ${cls}" style="width:${Math.max(0, Math.min(100, rounded))}%"></div></div>
    <div class="metric-value">${rounded}${suffix}</div>
  </div>`;
}
function latencyText(value) {
  return value === null || value === undefined ? "--" : `${Math.round(value)}ms`;
}
function siteHistory(site) {
  return Array.isArray(site?.history) ? site.history : [];
}
function averageLatency(site) {
  const samples = siteHistory(site).map(s => s.latency_ms).filter(v => v !== null && v !== undefined);
  if (!samples.length) return null;
  return samples.reduce((sum, value) => sum + Number(value), 0) / samples.length;
}
function uptime(site) {
  const samples = siteHistory(site);
  if (!samples.length) return site?.up ? 100 : 0;
  return Math.round(samples.filter(s => s.up === true).length / samples.length * 1000) / 10;
}
function renderBars(site, targetId, count) {
  const samples = siteHistory(site).slice(-count);
  const padded = Array(Math.max(0, count - samples.length)).fill(null).concat(samples);
  document.getElementById(targetId).innerHTML = padded.map(sample => {
    if (!sample) return "<span class='empty'></span>";
    return `<span class="${sample.up ? "" : "down"}"></span>`;
  }).join("");
}
function renderSiteList() {
  const query = document.getElementById("siteSearch").value.trim().toLowerCase();
  const sites = latest.sites.sites || [];
  const filtered = sites.filter(site => `${site.name} ${site.url}`.toLowerCase().includes(query));
  document.getElementById("monitorList").innerHTML = filtered.length ? filtered.map(site => `
    <button class="monitor-row ${site.name === selectedSiteName ? "active" : ""}" onclick="selectSite('${esc(site.name)}')">
      <span>
        <span class="monitor-name">${esc(site.name)}</span>
        <span class="subtitle">${uptime(site)}% uptime</span>
      </span>
      <span class="mini-bars">${miniBars(site)}</span>
    </button>
  `).join("") : "<div class='empty'>No site monitors found.</div>";
}
function miniBars(site) {
  const samples = siteHistory(site).slice(-14);
  const padded = Array(Math.max(0, 14 - samples.length)).fill(null).concat(samples);
  return padded.map(sample => {
    if (!sample) return "<span class='bar-empty'></span>";
    return `<span class="${sample.up ? "bar-ok" : "bar-bad"}"></span>`;
  }).join("");
}
function selectSite(name) {
  selectedSiteName = name;
  renderSelectedSite();
  renderSiteList();
}
function selectedSite() {
  const sites = latest.sites.sites || [];
  return sites.find(site => site.name === selectedSiteName) || sites[0] || null;
}
function renderSelectedSite() {
  const site = selectedSite();
  if (!site) {
    document.getElementById("selectedSiteName").textContent = "No monitor selected";
    document.getElementById("selectedSiteUrl").textContent = "";
    document.getElementById("selectedSiteStatus").textContent = "--";
    document.getElementById("selectedSiteStatus").className = "big-status";
    document.getElementById("uptimeBars").innerHTML = "";
    document.getElementById("checkCaption").textContent = "Add an HTTP monitor to start collecting response data.";
    document.getElementById("currentLatency").textContent = "--";
    document.getElementById("averageLatency").textContent = "--";
    document.getElementById("uptimePercent").textContent = "--";
    drawChart([]);
    return;
  }
  selectedSiteName = site.name;
  const statusNode = document.getElementById("selectedSiteStatus");
  document.getElementById("selectedSiteName").textContent = site.name;
  document.getElementById("selectedSiteUrl").textContent = site.url;
  document.getElementById("selectedSiteUrl").href = site.url;
  statusNode.textContent = site.up ? "Up" : "Down";
  statusNode.className = `big-status ${site.up ? "" : "down"}`;
  renderBars(site, "uptimeBars", 40);
  document.getElementById("checkCaption").textContent = `HTTP ${site.status_code ?? "none"} - ${site.error || "latest check completed"}`;
  document.getElementById("currentLatency").textContent = latencyText(site.latency_ms);
  document.getElementById("averageLatency").textContent = latencyText(averageLatency(site));
  document.getElementById("uptimePercent").textContent = `${uptime(site)}%`;
  drawChart(siteHistory(site));
}
function drawChart(samples) {
  const svg = document.getElementById("latencyChart");
  const values = samples.filter(s => s.latency_ms !== null && s.latency_ms !== undefined).slice(-60);
  if (!values.length) {
    svg.innerHTML = `<text x="24" y="42" fill="var(--muted)">No response samples yet.</text>`;
    return;
  }
  const width = 720;
  const height = 240;
  const pad = {left: 46, right: 16, top: 16, bottom: 34};
  const max = Math.max(...values.map(s => Number(s.latency_ms)), 10);
  const min = Math.min(...values.map(s => Number(s.latency_ms)), 0);
  const range = Math.max(1, max - min);
  const xStep = values.length > 1 ? (width - pad.left - pad.right) / (values.length - 1) : 0;
  const points = values.map((sample, index) => {
    const x = pad.left + index * xStep;
    const y = height - pad.bottom - ((Number(sample.latency_ms) - min) / range) * (height - pad.top - pad.bottom);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const area = `${pad.left},${height - pad.bottom} ${points} ${width - pad.right},${height - pad.bottom}`;
  const grid = [0, .25, .5, .75, 1].map(part => {
    const y = pad.top + part * (height - pad.top - pad.bottom);
    const label = Math.round(max - part * range);
    return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="var(--line)" />
      <text x="8" y="${y + 4}" fill="var(--muted)" font-size="11">${label}</text>`;
  }).join("");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `${grid}
    <polyline points="${area}" fill="rgba(32,201,119,.12)" stroke="none"></polyline>
    <polyline points="${points}" fill="none" stroke="var(--brand)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
    <text x="${pad.left}" y="${height - 9}" fill="var(--muted)" font-size="11">older</text>
    <text x="${width - pad.right - 34}" y="${height - 9}" fill="var(--muted)" font-size="11">now</text>`;
}
function applyPreferences() {
  const prefs = JSON.parse(localStorage.getItem("meerkatUiPrefs") || "{}");
  const theme = prefs.theme || "light";
  const refreshMs = String(prefs.refresh || 10000);
  document.body.dataset.theme = theme;
  document.getElementById("themeSelect").value = theme;
  document.getElementById("refreshSelect").value = refreshMs;
  document.getElementById("tokenInput").value = localStorage.getItem("meerkatActionToken") || "";
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, Number(refreshMs));
}
function savePreferences() {
  localStorage.setItem("meerkatUiPrefs", JSON.stringify({
    theme: document.getElementById("themeSelect").value,
    refresh: Number(document.getElementById("refreshSelect").value)
  }));
  applyPreferences();
}
function saveActionToken() {
  const token = document.getElementById("tokenInput").value.trim();
  if (token) localStorage.setItem("meerkatActionToken", token);
  else localStorage.removeItem("meerkatActionToken");
  document.getElementById("actionState").textContent = token ? "Action token saved" : "Action token cleared";
}
async function refresh() {
  try {
    const [status, health, network, docker, sites] = await Promise.all([
      fetch("/api/status").then(r => r.json()),
      fetch("/api/health").then(r => r.json()),
      fetch("/api/network").then(r => r.json()),
      fetch("/api/docker").then(r => r.json()),
      fetch("/api/sites").then(r => r.json())
    ]);
    latest = {status, health, network, docker, sites};
    lastRefresh = Date.now();
    document.getElementById("liveDot").className = "dot good";
    document.getElementById("liveText").textContent = "Live";

    const running = docker.containers.filter(c => c.status === "running").length;
    document.getElementById("summary").innerHTML = [
      ["Internet", statusLabel(status.internet_up), status.internet_up ? "Reachable" : "Probe failing"],
      ["Alerts", status.active_alerts.length, status.alerts_silenced ? "Silenced" : "Notifications active"],
      ["Containers", `${running}/${docker.containers.length}`, docker.available ? "Docker connected" : "Docker unavailable"],
      ["Sites", `${sites.up}/${sites.total}`, sites.down ? `${sites.down} down` : "All monitored sites up"],
      ["Route", status.default_route_label || "unknown", "Outbound interface"]
    ].map(item => `<section class="card stat"><div class="label">${esc(item[0])}</div><div class="value">${esc(item[1])}</div><div class="subvalue">${esc(item[2])}</div></section>`).join("");

    const temp = health.cpu_temperature;
    document.getElementById("metrics").innerHTML = [
      metric("CPU", health.cpu_percent),
      metric("RAM", health.ram_percent),
      metric("Disk", health.disk_percent),
      temp === null || temp === undefined ? "" : metric("Temp", temp, "C")
    ].join("");
    document.getElementById("tempState").textContent = `CPU ${temp === null || temp === undefined ? "unavailable" : Math.round(temp) + "C (" + tempLabel(temp) + ")"}`;

    document.getElementById("routeState").textContent = `Route ${network.default_route_label || "unknown"}`;
    const interfaces = Object.entries(network.interfaces);
    document.getElementById("networkTable").innerHTML = interfaces.length ? `<thead><tr><th>Type</th><th>Interface</th><th>Status</th><th>Carrier</th><th>IPv4</th></tr></thead><tbody>${interfaces.map(([type, item]) => `<tr><td data-label="Type">${esc(type)}</td><td data-label="Interface"><code>${esc(item.name)}</code></td><td data-label="Status">${statusPill(item.up)}</td><td data-label="Carrier">${esc(item.carrier)}</td><td data-label="IPv4">${esc(item.has_ip)}</td></tr>`).join("")}</tbody>` : "<tbody><tr><td class='empty'>No interfaces configured.</td></tr></tbody>";

    document.getElementById("dockerCount").textContent = `${docker.containers.length} containers`;
    document.getElementById("restartSelect").innerHTML = docker.containers.length ? docker.containers.map(c => `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join("") : "<option value=''>No containers</option>";
    document.getElementById("dockerTable").innerHTML = docker.available && docker.containers.length
      ? `<thead><tr><th>Name</th><th>Status</th><th>Image</th></tr></thead><tbody>${docker.containers.map(c => `<tr><td data-label="Name"><code>${esc(c.name)}</code></td><td data-label="Status">${statusPill(c.status === "running", c.status)}</td><td data-label="Image">${esc(c.image || "untagged")}</td></tr>`).join("")}</tbody>`
      : `<tbody><tr><td class="empty">${docker.available ? "No containers found." : esc(docker.error)}</td></tr></tbody>`;

    if (!selectedSiteName && sites.sites.length) selectedSiteName = sites.sites[0].name;
    document.getElementById("sitesTable").innerHTML = sites.sites.length
      ? `<thead><tr><th></th><th>Name</th><th>Status</th><th>Latency</th><th>HTTP</th><th>URL</th></tr></thead><tbody>${sites.sites.map(s => `<tr><td data-label="Pick"><input type="radio" name="selectedSite" value="${esc(s.name)}" ${s.name === selectedSiteName ? "checked" : ""} onchange="selectSite('${esc(s.name)}')"></td><td data-label="Name">${esc(s.name)}</td><td data-label="Status">${statusPill(s.up)}</td><td data-label="Latency">${latencyText(s.latency_ms)}</td><td data-label="HTTP">${esc(s.status_code ?? "none")}</td><td data-label="URL"><code>${esc(s.url)}</code></td></tr>`).join("")}</tbody>`
      : "<tbody><tr><td class='empty'>No website monitors configured.</td></tr></tbody>";
    renderSiteList();
    renderSelectedSite();

    document.getElementById("alertMode").textContent = status.alerts_silenced ? "Alerts silenced" : "Alerts active";
    document.getElementById("activeAlerts").innerHTML = status.active_alerts.length
      ? status.active_alerts.map(a => `<div class="event critical"><div class="event-title">${esc(a)}</div><div class="event-meta">currently active</div></div>`).join("")
      : "<div class='empty'>No active alerts.</div>";
    document.getElementById("events").innerHTML = status.recent_events.length
      ? status.recent_events.map(e => `<div class="event ${esc(e.severity)}"><div class="event-title">${esc(e.title)}</div><div class="event-meta">${esc(e.ts)} - ${esc(e.severity)} - ${esc(e.source)}</div><div class="event-body">${esc(e.body)}</div></div>`).join("")
      : "<div class='empty'>No events yet.</div>";
  } catch (error) {
    document.getElementById("liveDot").className = "dot bad";
    document.getElementById("liveText").textContent = "Disconnected";
  }
}
async function postJson(url, body = {}) {
  let token = localStorage.getItem("meerkatActionToken");
  const headers = {"Content-Type": "application/json"};
  if (token) headers["X-Meerkat-Action-Token"] = token;
  let response = await fetch(url, {method: "POST", headers, body: JSON.stringify(body)});
  if (response.status === 403) {
    token = prompt("Action token");
    if (!token) return;
    localStorage.setItem("meerkatActionToken", token);
    response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Meerkat-Action-Token": token},
      body: JSON.stringify(body)
    });
  }
  const payload = await response.json();
  if (response.status === 403) localStorage.removeItem("meerkatActionToken");
  document.getElementById("actionState").textContent = payload.ok ? payload.message : payload.error;
  await refresh();
}
async function clearCache() { await postJson("/api/actions/clear-ram-cache"); }
async function restartSelected() {
  const container = document.getElementById("restartSelect").value;
  if (container) await postJson("/api/actions/docker/restart", {container});
}
async function addSiteFrom(nameId, urlId) {
  const name = document.getElementById(nameId).value.trim();
  const url = document.getElementById(urlId).value.trim();
  if (!name || !url) {
    document.getElementById("actionState").textContent = "Site name and URL are required";
    return;
  }
  await postJson("/api/actions/sites/add", {name, url});
  selectedSiteName = name;
  document.getElementById(nameId).value = "";
  document.getElementById(urlId).value = "";
}
async function addSite() { await addSiteFrom("siteNameInput", "siteUrlInput"); }
async function addSiteInline() { await addSiteFrom("siteNameInputInline", "siteUrlInputInline"); }
async function removeSelectedSite() {
  const site = selectedSite();
  if (!site) {
    document.getElementById("actionState").textContent = "Select a runtime site first";
    return;
  }
  await postJson("/api/actions/sites/remove", {name: site.name});
  selectedSiteName = "";
}
async function clearEvents() { await postJson("/api/actions/events/clear"); }
function toggleSettings() { document.getElementById("settingsPanel").classList.toggle("open"); }

applyPreferences();
drawLoadingChart();
refresh();
setInterval(() => {
  document.getElementById("refreshAge").textContent = lastRefresh ? Math.floor((Date.now() - lastRefresh) / 1000) : "--";
}, 1000);
</script>
</body>
</html>"""
