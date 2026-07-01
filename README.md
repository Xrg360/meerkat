# Meerkat

A lightweight infrastructure monitoring and alerting agent for Docker homelabs.

Meerkat watches network state, internet reachability, Docker container events, and basic system health. It sends Telegram alerts only when something changes state, so restarts and steady-state checks do not produce repeated noise.

## Why Meerkat?

Meerkats are natural lookouts: one watches the horizon, warns the group early, and stays useful by being alert without creating constant panic. That maps well to this project’s goal: a small homelab agent that keeps watch over infrastructure, sends early state-change alerts, and avoids noisy repeated notifications.

## Features

- Ethernet disconnected/restored
- Wi-Fi disconnected/restored
- Active default route changes
- Internet lost/restored using `1.1.1.1` and `8.8.8.8`
- Docker container created, started, stopped, restarted, died, and removed events
- Website uptime monitoring with HTTP status, latency, redirects, and optional keyword checks
- CPU, RAM, disk, and CPU temperature threshold alerts
- Persistent state in `state/state.json`
- SQLite event history in `state/history.db`
- Alert duration and cooldown controls
- Telegram commands for status, health, network, Docker, quick actions, silence, and resume
- REST API and Prometheus metrics from the Python monitor API
- Next.js web app with Home, Monitoring, and Settings pages
- In-app alert popups and optional browser desktop notifications
- Browser-local settings for theme, refresh interval, action token, and pinned Home monitors

## Server Defaults

The default config is already set for your server:

- Ethernet: `enp2s0`
- Wi-Fi: `wlp1s0`
- Timezone: `Asia/Kolkata`

## Docker Compose

Create a project directory:

```bash
mkdir -p /opt/meerkat/config /opt/meerkat/state
cd /opt/meerkat
```

Create `compose.yml`:

```yaml
services:
  meerkat:
    image: ghcr.io/xrg360/meerkat:latest
    container_name: meerkat
    restart: unless-stopped
    privileged: true
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config:/app/config
      - ./state:/app/state
    environment:
      TZ: Asia/Kolkata
      PORT: 8710
      MEERKAT_API_PORT: 8711
      MEERKAT_API_BASE: http://127.0.0.1:8711
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
      MEERKAT_ACTION_TOKEN: ${MEERKAT_ACTION_TOKEN}
```

Create `.env`:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
MEERKAT_ACTION_TOKEN=change-this-long-random-token
```

Create `config/config.yml` using the configuration example below, then start Meerkat:

```bash
docker compose up -d
```

Web app and API:

```text
http://<server-ip>:8710/
http://<server-ip>:8710/monitoring
http://<server-ip>:8710/settings
http://<server-ip>:8711/api/status
http://<server-ip>:8711/metrics
```

The Next.js web app is served on port `8710`. The Python monitor API runs internally on `8711`, and the web app proxies backend requests through `/api/meerkat/*` using `MEERKAT_API_BASE=http://127.0.0.1:8711`.

View logs:

```bash
docker logs -f meerkat
```

Stop:

```bash
docker compose down
```

Update:

```bash
docker compose pull
docker compose up -d
```

Backup state:

```bash
tar -czf meerkat-state-backup.tar.gz state
```

## Docker CLI

```bash
docker run -d \
  --name meerkat \
  --restart unless-stopped \
  --privileged \
  --network host \
  -e TZ=Asia/Kolkata \
  -e TELEGRAM_BOT_TOKEN=your-token \
  -e TELEGRAM_CHAT_ID=your-chat-id \
  -e MEERKAT_ACTION_TOKEN=change-this-long-random-token \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/state:/app/state \
  ghcr.io/xrg360/meerkat:latest
```

Build locally from source:

```bash
git clone https://github.com/xrg360/meerkat.git
cd meerkat
docker compose up -d --build
```

## Local Development

Run the Python monitor API on the internal API port:

```bash
MEERKAT_API_PORT=8711 python app.py
```

PowerShell:

```powershell
$env:MEERKAT_API_PORT = "8711"
python app.py
```

In another terminal, run the Next.js web app:

```bash
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:8710/
```

If the Python API is not running, the Next.js app still loads and shows a backend-unavailable state.

## Telegram BotFather Commands

Meerkat reads these Telegram commands from your configured `TELEGRAM_CHAT_ID`:

```text
start - Show Meerkat bot info
status - Show current monitor state
health - Show CPU RAM disk and temperature
network - Show interface and internet state
docker - Show Docker containers
sites - Show website monitors
addsite - Add a website monitor. Usage: /addsite name https://example.com
removesite - Remove a runtime website monitor. Usage: /removesite name
restart - Restart a Docker container. Usage: /restart container_name
clearcache - Clear Linux RAM caches
silence - Pause monitor alerts
resume - Resume monitor alerts
help - Show available commands
```

In BotFather:

```text
/setcommands
```

Select your Meerkat bot, then paste the command list above.

## Telegram Troubleshooting

Set either env naming style:

```env
TELEGRAM_BOT_TOKEN=123456:abc...
TELEGRAM_CHAT_ID=123456789
```

or:

```env
MEERKAT_TELEGRAM_BOT_TOKEN=123456:abc...
MEERKAT_TELEGRAM_CHAT_ID=123456789
```

After `docker compose up -d --build`, check:

```bash
docker logs meerkat | grep Telegram
```

Expected when configured:

```text
Telegram enabled for chat_id=...
Telegram command listener connected as @...
Telegram command listener started
```

If you see `Telegram is disabled`, the container did not receive the token/chat id. If messages send but commands do not respond, send `/start` to the bot once from the configured chat and confirm the `TELEGRAM_CHAT_ID` matches that chat.

## Configuration

```yaml
telegram:
  bot_token:
  chat_id:

network:
  ethernet: enp2s0
  wifi: wlp1s0

cpu:
  threshold: 90

ram:
  threshold: 90

disk:
  threshold: 90
  paths:
    - /

temperature:
  threshold: 80

internet:
  hosts:
    - 1.1.1.1
    - 8.8.8.8
  timeout: 2
  severity: critical

alerting:
  duration: 0
  cooldown: 15m

api:
  enabled: true
  host: 0.0.0.0
  port: 8710

actions:
  enabled: true
  token:
  blocked_containers:
    - meerkat

sites:
  - name: Example
    url: https://example.com
    expected_status:
      - 200
    timeout: 10
    follow_redirects: true
    severity: critical
    duration: 30s
    cooldown: 15m

interval: 30
```

## Website Monitoring

Meerkat can monitor public websites or internal services in addition to the host it runs on:

```yaml
sites:
  - name: Blog
    url: https://blog.example.com
    expected_status:
      - 200
    timeout: 10
    follow_redirects: true
    keyword: "Welcome"
    severity: critical
    duration: 1m
    cooldown: 15m
```

If `keyword` is set, the site is considered up only when the HTTP status matches and the response body contains that text.

Runtime site monitors can also be added from Telegram or the web app Monitoring page:

```text
/addsite blog https://blog.example.com
/removesite blog
```

Runtime-added sites are stored in `state/state.json`. YAML-defined sites remain the recommended option for infrastructure-as-code deployments.

## Alert Behavior

Meerkat stores alert state independently from alert history:

- `state/state.json` keeps current state and dedupe markers.
- `state/history.db` keeps alert, recovery, and Docker event history.
- `alerting.duration` requires a condition to stay bad before alerting.
- `alerting.cooldown` prevents repeated messages for the same active alert.
- Recovery messages are sent only if an active alert was previously sent.

Per-monitor overrides are supported:

```yaml
cpu:
  threshold: 90
  severity: warning
  duration: 2m
  cooldown: 30m
```

## API

```text
Python API:

GET  /health
GET  /status
GET  /api/status
GET  /api/health
GET  /api/network
GET  /api/docker
GET  /api/sites
GET  /api/events
GET  /metrics
POST /clearRamCache
POST /api/actions/clear-ram-cache
POST /api/actions/docker/restart
POST /api/actions/sites/add
POST /api/actions/sites/remove
POST /api/actions/events/clear

Next.js proxy:

GET  /api/meerkat/status
GET  /api/meerkat/health
GET  /api/meerkat/docker
GET  /api/meerkat/sites
POST /api/meerkat/actions/sites/add
POST /api/meerkat/actions/sites/remove
POST /api/meerkat/actions/docker/restart
POST /api/meerkat/actions/clear-ram-cache
```

`/metrics` is Prometheus-compatible.

Action endpoints require `X-Meerkat-Action-Token`. Set it with `MEERKAT_ACTION_TOKEN` or `actions.token`.

`POST /api/actions/docker/restart` expects JSON:

```json
{
  "container": "cloudflared"
}
```

Example:

```bash
curl -X POST http://127.0.0.1:8710/api/meerkat/actions/docker/restart \
  -H "Content-Type: application/json" \
  -H "X-Meerkat-Action-Token: change-this-long-random-token" \
  -d '{"container":"cloudflared"}'
```

For security, action endpoints are intended for trusted LAN deployments or reverse proxies with authentication. The Meerkat container blocks restarting itself by default through `actions.blocked_containers`.

## State Behavior

On first run Meerkat records current monitor state without sending fake recovery alerts. It does send one boot message when the Meerkat Docker container starts. After that it only sends messages when a monitor changes state:

- down
- up
- restored
- threshold exceeded
- back to normal
- Docker lifecycle event

The state file lives at `state/state.json`.
