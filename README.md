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
- CPU, RAM, disk, and CPU temperature threshold alerts
- Persistent state in `state/state.json`
- SQLite event history in `state/history.db`
- Alert duration and cooldown controls
- Telegram commands for status, health, network, Docker, silence, and resume
- REST API, Prometheus metrics, and a built-in dashboard

## Server Defaults

The default config is already set for your server:

- Ethernet: `enp2s0`
- Wi-Fi: `wlp1s0`
- Timezone: `Asia/Kolkata`

## Setup

Create `config/config.yml` from the included defaults and add your Telegram credentials:

```yaml
telegram:
  bot_token: "123456:telegram-bot-token"
  chat_id: "123456789"
```

You can also keep secrets out of the config file by setting environment variables:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

If using environment variables with Compose, add `env_file: .env` to `compose.yml`.

## Run

```bash
docker compose up -d --build
```

Dashboard and API:

```text
http://<server-ip>:8710/
http://<server-ip>:8710/api/status
http://<server-ip>:8710/metrics
```

View logs:

```bash
docker compose logs -f meerkat
```

Stop:

```bash
docker compose down
```

## Update Existing Server Install

From this project folder on your local machine, copy the updated app files to your server:

```bash
scp -r app.py monitors Dockerfile compose.yml requirements.txt README.md config/config.yml xrg@<server-ip>:/srv/docker/meerkat/
```

Then rebuild and restart Meerkat on the server:

```bash
ssh xrg@<server-ip> "cd /srv/docker/meerkat && docker compose up -d --build"
```

Use your real server IP in place of `<server-ip>`. This keeps the existing `state/state.json` on the server, so Meerkat does not forget previous monitor states.

If you intentionally want to copy everything, including `.env`, config, and state placeholders:

```bash
scp -r . xrg@<server-ip>:/srv/docker/meerkat/
```

Be careful with the full copy because it can overwrite server-side secrets or state files.

If your server path is exactly `/srv/docker/meerkat`, this is the direct command shape:

```bash
scp -r app.py monitors tests Dockerfile compose.yml requirements.txt README.md config/config.yml xrg@pookie:/srv/docker/meerkat/
ssh xrg@pookie "cd /srv/docker/meerkat && docker compose up -d --build"
```

If you are migrating from the old `/srv/docker/sentinel` path:

```bash
ssh xrg@pookie "mkdir -p /srv/docker/meerkat && cp -a /srv/docker/sentinel/state /srv/docker/meerkat/ 2>/dev/null || true"
scp -r app.py monitors tests Dockerfile compose.yml requirements.txt README.md config/config.yml xrg@pookie:/srv/docker/meerkat/
ssh xrg@pookie "cd /srv/docker/sentinel && docker compose down || true; cd /srv/docker/meerkat && docker compose up -d --build"
```

## Telegram BotFather Commands

Meerkat reads these Telegram commands from your configured `TELEGRAM_CHAT_ID`:

```text
start - Show Meerkat bot info
status - Show current monitor state
health - Show CPU RAM disk and temperature
network - Show interface and internet state
docker - Show Docker containers
silence - Pause monitor alerts
resume - Resume monitor alerts
help - Show available commands
```

In BotFather:

```text
/setcommands
```

Select your Meerkat bot, then paste the command list above.

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

interval: 30
```

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
GET /health
GET /status
GET /api/status
GET /api/health
GET /api/network
GET /api/docker
GET /api/events
GET /metrics
```

`/metrics` is Prometheus-compatible.

## State Behavior

On first run Meerkat records current monitor state without sending fake recovery alerts. It does send one boot message when the Meerkat Docker container starts. After that it only sends messages when a monitor changes state:

- down
- up
- restored
- threshold exceeded
- back to normal
- Docker lifecycle event

The state file lives at `state/state.json`.
