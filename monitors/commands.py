import logging
import hashlib
import json
import threading
from typing import Any

import requests


HELP_TEXT = """Meerkat commands:

/status - Show current monitor state
/health - Show CPU RAM disk and temperature
/network - Show interface and internet state
/docker - Show Docker containers
/sites - Show website monitors
/addsite <name> <url> - Add a website monitor
/removesite <name> - Remove a runtime website monitor
/restart <container> - Restart a Docker container
/clearcache - Clear Linux RAM caches
/silence - Pause monitor alerts
/resume - Resume monitor alerts
/help - Show this help"""


def usage_bar(value: float, width: int = 10) -> str:
    filled = max(0, min(width, round(value / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def usage_label(value: float) -> str:
    if value >= 90:
        return "critical"
    if value >= 75:
        return "high"
    if value >= 50:
        return "busy"
    return "normal"


def temp_label(value: float | None) -> str:
    if value is None:
        return "unavailable"
    if value >= 85:
        return "very hot"
    if value >= 75:
        return "hot"
    if value >= 60:
        return "warm"
    return "normal"


def state_icon(value: Any) -> str:
    if value is True:
        return "✅"
    if value is False:
        return "❌"
    return "❔"


class TelegramCommandMonitor:
    def __init__(self, config: dict[str, Any], state: Any, notifier: Any, status_service: Any, action_service: Any) -> None:
        self.config = config
        self.state = state
        self.notifier = notifier
        self.status_service = status_service
        self.action_service = action_service
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def start(self) -> None:
        if not self.notifier.enabled:
            return
        self._reset_offset_if_bot_changed()
        self._prepare_polling()
        self.thread = threading.Thread(target=self._run, name="telegram-commands", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        logging.info("Telegram command listener started")
        while not self.stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logging.error("Telegram command listener error: %s", exc)
                self.stop_event.wait(5)

    def _prepare_polling(self) -> None:
        bot_url = f"https://api.telegram.org/bot{self.notifier.bot_token}/getMe"
        try:
            bot_response = requests.get(bot_url, timeout=10)
            bot_response.raise_for_status()
            bot_info = bot_response.json().get("result", {})
            logging.info("Telegram command listener connected as @%s", bot_info.get("username", "unknown"))
        except requests.RequestException as exc:
            response_text = getattr(getattr(exc, "response", None), "text", "")
            logging.warning("Could not verify Telegram bot identity: %s %s", exc, response_text)

        url = f"https://api.telegram.org/bot{self.notifier.bot_token}/deleteWebhook"
        try:
            response = requests.post(url, json={"drop_pending_updates": False}, timeout=10)
            response.raise_for_status()
            logging.info("Telegram webhook cleared for command polling")
        except requests.RequestException as exc:
            response_text = getattr(getattr(exc, "response", None), "text", "")
            logging.warning("Could not clear Telegram webhook before polling: %s %s", exc, response_text)

    def _reset_offset_if_bot_changed(self) -> None:
        fingerprint = hashlib.sha256(str(self.notifier.bot_token).encode("utf-8")).hexdigest()
        key = "telegram.bot_fingerprint"
        if self.state.get(key) != fingerprint:
            self.state.set("telegram.update_offset", None)
            self.state.set(key, fingerprint)
            logging.info("Telegram bot token changed or first seen; command polling offset reset")

    def _poll_once(self) -> None:
        offset = self.state.get("telegram.update_offset")
        params: dict[str, Any] = {"timeout": 25, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset

        url = f"https://api.telegram.org/bot{self.notifier.bot_token}/getUpdates"
        response = requests.get(url, params=params, timeout=35)
        if response.status_code == 409:
            logging.warning("Telegram polling conflict detected; clearing webhook and retrying")
            self._prepare_polling()
            self.stop_event.wait(2)
            return
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            response_text = getattr(exc.response, "text", "")
            logging.error("Telegram getUpdates failed: %s %s", exc, response_text)
            raise

        payload = response.json()
        if not payload.get("ok"):
            logging.warning("Telegram getUpdates returned non-ok response: %s", payload)
            self.stop_event.wait(5)
            return

        for update in payload.get("result", []):
            update_id = update.get("update_id")
            if update_id is not None:
                self.state.set("telegram.update_offset", update_id + 1)
            self._handle_update(update)

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id"))
        if chat_id != str(self.notifier.chat_id):
            logging.info("Ignoring Telegram command from unauthorized chat %s; configured chat is %s", chat_id, self.notifier.chat_id)
            return

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        parts = text.split()
        command = parts[0].split("@")[0].lower()
        logging.info("Handling Telegram command %s from chat %s", command, chat_id)
        handlers = {
            "/start": self._help,
            "/help": self._help,
            "/status": self._status,
            "/health": self._health,
            "/network": self._network,
            "/docker": self._docker,
            "/sites": self._sites,
            "/clearcache": self._clear_cache,
            "/silence": self._silence,
            "/resume": self._resume,
        }

        if command == "/restart":
            self.notifier.send(self._restart(parts[1] if len(parts) > 1 else ""), force=True)
            return
        if command == "/addsite":
            self.notifier.send(self._add_site(parts), force=True)
            return
        if command == "/removesite":
            self.notifier.send(self._remove_site(parts[1] if len(parts) > 1 else ""), force=True)
            return

        handler = handlers.get(command)
        if handler:
            self.notifier.send(handler(), force=True)
        else:
            self.notifier.send("Unknown command. Use /help.", force=True)

    def _help(self) -> str:
        return HELP_TEXT

    def _status(self) -> str:
        status = self.status_service.status()

        return "\n".join(
            [
                "🦫 Meerkat status",
                "",
                f"🔔 Alerts: {'silenced' if status['alerts_silenced'] else 'active'}",
                f"🚨 Active alerts: {len(status['active_alerts'])}",
                f"{state_icon(status['internet_up'])} Internet: {self._state_label(status['internet_up'])}",
                f"{state_icon(status['ethernet_up'])} Ethernet: {self._state_label(status['ethernet_up'])}",
                f"{state_icon(status['wifi_up'])} Wi-Fi: {self._state_label(status['wifi_up'])}",
                f"🧭 Default route: {status['default_route_label']}",
            ]
        )

    def _health(self) -> str:
        health = self.status_service.health()
        temp = health["cpu_temperature"]
        temp_text = f"{temp:.0f}degC ({temp_label(temp)})" if temp is not None else "unavailable"

        return "\n".join(
            [
                "📊 System health",
                "",
                f"🧠 CPU  {usage_bar(health['cpu_percent'])} {health['cpu_percent']:.0f}% ({usage_label(health['cpu_percent'])})",
                f"💾 RAM  {usage_bar(health['ram_percent'])} {health['ram_percent']:.0f}% ({usage_label(health['ram_percent'])})",
                f"🗄 Disk {usage_bar(health['disk_percent'])} {health['disk_percent']:.0f}% ({usage_label(health['disk_percent'])})",
                f"🌡 CPU temp: {temp_text}",
            ]
        )

    def _network(self) -> str:
        network = self.status_service.network()
        lines = ["🌐 Network", ""]

        for label, status in network["interfaces"].items():
            lines.append(
                f"{state_icon(status['up'])} {label.title()} {status['name']}: {'up' if status['up'] else 'down'} "
                f"(state={status['operstate']}, carrier={status['carrier']}, ip={status['has_ip']})"
            )

        lines.append(f"🧭 Default route: {network['default_route_label']}")
        lines.append(f"{state_icon(network['internet_up'])} Internet: {self._state_label(network['internet_up'])}")
        return "\n".join(lines)

    def _docker(self) -> str:
        docker_status = self.status_service.docker()
        if not docker_status["available"]:
            return f"🐳 Docker unavailable: {docker_status['error']}"

        containers = docker_status["containers"]
        if not containers:
            return "🐳 Docker\n\nNo containers found."

        lines = ["🐳 Docker", ""]
        for container in containers[:20]:
            icon = "✅" if container["status"] == "running" else "⚠️"
            lines.append(f"{icon} {container['name']}: {container['status']}")
        if len(containers) > 20:
            lines.append(f"...and {len(containers) - 20} more")
        return "\n".join(lines)

    def _sites(self) -> str:
        sites = self.status_service.sites()
        if not sites["sites"]:
            return "🌍 Sites\n\nNo website monitors configured."

        lines = [f"🌍 Sites ({sites['up']}/{sites['total']} up)", ""]
        for site in sites["sites"][:20]:
            icon = state_icon(site.get("up"))
            latency = site.get("latency_ms")
            status_code = site.get("status_code") or "none"
            lines.append(f"{icon} {site['name']} - {status_code} - {latency if latency is not None else 'unknown'}ms")
        if len(sites["sites"]) > 20:
            lines.append(f"...and {len(sites['sites']) - 20} more")
        return "\n".join(lines)

    def _add_site(self, parts: list[str]) -> str:
        if len(parts) < 3:
            return "Usage: /addsite name https://example.com"
        result = self.action_service.add_site({"name": parts[1], "url": parts[2]})
        if result["ok"]:
            return f"🌍 Site monitor added\n\n{result['message']}"
        return f"⚠️ Site monitor add failed\n\n{result['error']}"

    def _remove_site(self, name: str) -> str:
        result = self.action_service.remove_site(name)
        if result["ok"]:
            return f"🗑 Site monitor removed\n\n{result['message']}"
        return f"⚠️ Site monitor remove failed\n\n{result['error']}"

    def _clear_cache(self) -> str:
        result = self.action_service.clear_ram_cache()
        if result["ok"]:
            return f"🧹 RAM cache cleared\n\n{result['message']}"
        return f"⚠️ RAM cache clear failed\n\n{result['error']}"

    def _restart(self, container: str) -> str:
        result = self.action_service.restart_container(container)
        if result["ok"]:
            return f"🔁 Docker restart requested\n\n{result['message']}"
        return f"⚠️ Docker restart failed\n\n{result['error']}"

    def _silence(self) -> str:
        self.state.set("alerts.silenced", True)
        return "🔕 Alerts silenced. Use /resume to enable alerts again."

    def _resume(self) -> str:
        self.state.set("alerts.silenced", False)
        return "🔔 Alerts resumed."

    @staticmethod
    def _state_label(value: Any) -> str:
        if value is True:
            return "up"
        if value is False:
            return "down"
        return "unknown"
