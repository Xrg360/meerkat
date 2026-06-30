import logging
import threading
from typing import Any

import requests


HELP_TEXT = """Meerkat commands:

/status - Show current monitor state
/health - Show CPU RAM disk and temperature
/network - Show interface and internet state
/docker - Show Docker containers
/silence - Pause monitor alerts
/resume - Resume monitor alerts
/help - Show this help"""


class TelegramCommandMonitor:
    def __init__(self, config: dict[str, Any], state: Any, notifier: Any, status_service: Any) -> None:
        self.config = config
        self.state = state
        self.notifier = notifier
        self.status_service = status_service
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def start(self) -> None:
        if not self.notifier.enabled:
            return
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

    def _poll_once(self) -> None:
        offset = self.state.get("telegram.update_offset")
        params: dict[str, Any] = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset

        url = f"https://api.telegram.org/bot{self.notifier.bot_token}/getUpdates"
        response = requests.get(url, params=params, timeout=35)
        response.raise_for_status()

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
            logging.info("Ignoring Telegram command from unauthorized chat %s", chat_id)
            return

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        command = text.split()[0].split("@")[0].lower()
        handlers = {
            "/start": self._help,
            "/help": self._help,
            "/status": self._status,
            "/health": self._health,
            "/network": self._network,
            "/docker": self._docker,
            "/silence": self._silence,
            "/resume": self._resume,
        }

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
                "Meerkat status",
                "",
                f"Alerts: {'silenced' if status['alerts_silenced'] else 'active'}",
                f"Active alerts: {len(status['active_alerts'])}",
                f"Internet: {self._state_label(status['internet_up'])}",
                f"Ethernet: {self._state_label(status['ethernet_up'])}",
                f"Wi-Fi: {self._state_label(status['wifi_up'])}",
                f"Default route: {status['default_route'] or 'unknown'}",
            ]
        )

    def _health(self) -> str:
        health = self.status_service.health()
        temp = health["cpu_temperature"]
        temp_text = f"{temp:.0f} C" if temp is not None else "unavailable"

        return "\n".join(
            [
                "System health",
                "",
                f"CPU: {health['cpu_percent']:.0f}%",
                f"RAM: {health['ram_percent']:.0f}%",
                f"Disk {health['disk_path']}: {health['disk_percent']:.0f}%",
                f"CPU temp: {temp_text}",
            ]
        )

    def _network(self) -> str:
        network = self.status_service.network()
        lines = ["Network", ""]

        for label, status in network["interfaces"].items():
            lines.append(
                f"{label.title()} {status['name']}: {'up' if status['up'] else 'down'} "
                f"(state={status['operstate']}, carrier={status['carrier']}, ip={status['has_ip']})"
            )

        lines.append(f"Default route: {network['default_route'] or 'unknown'}")
        lines.append(f"Internet: {self._state_label(network['internet_up'])}")
        return "\n".join(lines)

    def _docker(self) -> str:
        docker_status = self.status_service.docker()
        if not docker_status["available"]:
            return f"Docker unavailable: {docker_status['error']}"

        containers = docker_status["containers"]
        if not containers:
            return "Docker\n\nNo containers found."

        lines = ["Docker", ""]
        for container in containers[:20]:
            lines.append(f"{container['name']}: {container['status']}")
        if len(containers) > 20:
            lines.append(f"...and {len(containers) - 20} more")
        return "\n".join(lines)

    def _silence(self) -> str:
        self.state.set("alerts.silenced", True)
        return "Alerts silenced. Use /resume to enable alerts again."

    def _resume(self) -> str:
        self.state.set("alerts.silenced", False)
        return "Alerts resumed."

    @staticmethod
    def _state_label(value: Any) -> str:
        if value is True:
            return "up"
        if value is False:
            return "down"
        return "unknown"
