import logging
import threading
from datetime import datetime, timezone
from typing import Any

import docker

from monitors.alerts import parse_duration
from monitors.network import get_interface_status


class AutoHealMonitor:
    def __init__(self, config: dict[str, Any], state: Any, alerts: Any, actions: Any) -> None:
        auto_config = config.get("auto_heal", {}) or {}
        self.enabled = bool(auto_config.get("enabled", True))
        self.interval = parse_duration(auto_config.get("interval"), 300)
        self.repair_cooldown = parse_duration(auto_config.get("repair_cooldown"), 300)
        self.containers_enabled = bool((auto_config.get("containers", {}) or {}).get("enabled", True))
        self.network_enabled = bool((auto_config.get("network", {}) or {}).get("enabled", True))
        self.state = state
        self.alerts = alerts
        self.actions = actions
        self.config = config
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def start(self) -> None:
        if not self.enabled:
            logging.info("Auto-heal monitor disabled")
            return
        self.thread = threading.Thread(target=self._run, name="auto-heal", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        logging.info("Auto-heal monitor started with %ss interval", self.interval)
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                logging.exception("Auto-heal monitor failed")
            self.stop_event.wait(max(5, self.interval))

    def run_once(self) -> None:
        if self.containers_enabled:
            self._heal_containers()
        if self.network_enabled:
            self._heal_network()

    def _heal_containers(self) -> None:
        try:
            client = docker.from_env()
            containers = client.containers.list(all=True)
        except Exception as exc:
            logging.warning("Auto-heal could not inspect Docker containers: %s", exc)
            return

        active_names = set(self.state.get("auto_heal.containers.active", []))
        blocked = set(getattr(self.actions, "blocked_containers", set()))

        for container in containers:
            name = container.name
            if name in blocked:
                continue
            if container.status == "running":
                active_names.add(name)
                continue
            if name not in active_names:
                continue
            if not self._cooldown_elapsed(f"auto_heal.containers.{name}.last_repair"):
                continue

            result = self.actions.start_container(name)
            self._record_repair(
                ok=bool(result.get("ok")),
                alert_id=f"auto_heal.container.{name}",
                title="Container auto-heal",
                body=f"Container: {name}\nPrevious status: {container.status}\nResult: {result.get('message') or result.get('error')}",
            )

        self.state.set("auto_heal.containers.active", sorted(active_names))

    def _heal_network(self) -> None:
        network_config = self.config.get("network", {}) or {}
        active_interfaces = set(self.state.get("auto_heal.network.active", []))

        for label in ("ethernet", "wifi"):
            interface = network_config.get(label)
            if not interface:
                continue

            status = get_interface_status(interface)
            if status.up:
                active_interfaces.add(interface)
                continue
            if interface not in active_interfaces:
                continue
            if not self._cooldown_elapsed(f"auto_heal.network.{interface}.last_repair"):
                continue

            result = self.actions.restart_network_interface(interface)
            self._record_repair(
                ok=bool(result.get("ok")),
                alert_id=f"auto_heal.network.{interface}",
                title=f"{label.title()} auto-heal",
                body=(
                    f"Interface: {interface}\n"
                    f"State: {status.operstate}\n"
                    f"Carrier: {status.carrier}\n"
                    f"IPv4: {status.has_ip}\n"
                    f"Result: {result.get('message') or result.get('error')}"
                ),
            )

        self.state.set("auto_heal.network.active", sorted(active_interfaces))

    def _cooldown_elapsed(self, key: str) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        last_repair = float(self.state.get(key, 0) or 0)
        if now - last_repair < self.repair_cooldown:
            return False
        self.state.set(key, now)
        return True

    def _record_repair(self, ok: bool, alert_id: str, title: str, body: str) -> None:
        self.alerts.event(
            alert_id=alert_id,
            source="auto_heal",
            severity="info" if ok else "warning",
            title=title if ok else f"{title} failed",
            body=body,
            force=True,
        )
