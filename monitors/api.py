import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ApiServer:
    def __init__(self, config: dict[str, Any], status_service: Any) -> None:
        api_config = config.get("api", {}) or {}
        self.enabled = bool(api_config.get("enabled", True))
        self.host = str(api_config.get("host", "0.0.0.0"))
        self.port = int(api_config.get("port", 8710))
        self.status_service = status_service
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return

        status_service = self.status_service

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                routes = {
                    "/health": status_service.status,
                    "/status": status_service.status,
                    "/api/status": status_service.status,
                    "/api/health": status_service.health,
                    "/api/network": status_service.network,
                    "/api/docker": status_service.docker,
                    "/api/events": lambda: {"events": status_service.history.recent(100)},
                    "/metrics": lambda: metrics_payload(status_service),
                    "/": lambda: dashboard_html(),
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

            def log_message(self, format: str, *args: Any) -> None:
                logging.debug("api: " + format, *args)

            def _write(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

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
    ]
    for label, details in network.get("interfaces", {}).items():
        lines.append(f'meerkat_network_interface_up{{interface="{details["name"]}",type="{label}"}} {1 if details["up"] else 0}')
    return "\n".join(lines) + "\n"


def dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Meerkat</title>
  <style>
    body { margin: 0; font-family: Arial, sans-serif; background: #0f172a; color: #e5e7eb; }
    main { max-width: 1120px; margin: 0 auto; padding: 24px; }
    h1 { font-size: 28px; margin: 0 0 16px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
    .card { border: 1px solid #334155; border-radius: 8px; padding: 14px; background: #111827; }
    .label { color: #94a3b8; font-size: 13px; }
    .value { font-size: 24px; margin-top: 6px; }
    pre { white-space: pre-wrap; background: #020617; border: 1px solid #334155; border-radius: 8px; padding: 12px; overflow: auto; }
  </style>
</head>
<body>
<main>
  <h1>Meerkat</h1>
  <div class="grid" id="cards"></div>
  <h2>Recent Events</h2>
  <pre id="events">Loading...</pre>
</main>
<script>
async function refresh() {
  const status = await fetch('/api/status').then(r => r.json());
  const health = await fetch('/api/health').then(r => r.json());
  const cards = [
    ['Alerts', status.active_alerts.length],
    ['Internet', status.internet_up ? 'up' : 'down'],
    ['CPU', Math.round(health.cpu_percent) + '%'],
    ['RAM', Math.round(health.ram_percent) + '%'],
    ['Disk', Math.round(health.disk_percent) + '%'],
    ['Route', status.default_route || 'unknown']
  ];
  document.getElementById('cards').innerHTML = cards.map(c => `<div class="card"><div class="label">${c[0]}</div><div class="value">${c[1]}</div></div>`).join('');
  document.getElementById('events').textContent = status.recent_events.map(e => `${e.ts} ${e.severity} ${e.title}\\n${e.body}`).join('\\n\\n') || 'No events yet.';
}
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""
