import logging
import threading
from datetime import datetime
from typing import Any

import docker


WATCHED_EVENTS = {"start", "stop", "restart", "die", "destroy", "create"}
EVENT_LABELS = {
    "start": "started",
    "stop": "stopped",
    "restart": "restarted",
    "die": "died",
    "destroy": "removed",
    "create": "created",
}


class DockerEventMonitor:
    def __init__(self, state: Any, notifier: Any, ignored_containers: set[str] | None = None) -> None:
        self.state = state
        self.notifier = notifier
        self.ignored_containers = ignored_containers or {"meerkat"}
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="docker-events", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                client = docker.from_env()
                for event in client.events(decode=True, filters={"type": "container"}):
                    if self.stop_event.is_set():
                        break
                    self._handle_event(event)
            except Exception as exc:
                logging.error("Docker event monitor error: %s", exc)
                self.stop_event.wait(10)

    def _handle_event(self, event: dict[str, Any]) -> None:
        action = event.get("Action")
        if action not in WATCHED_EVENTS:
            return

        actor = event.get("Actor", {}) or {}
        attributes = actor.get("Attributes", {}) or {}
        container = attributes.get("name") or actor.get("ID") or "unknown"

        if container in self.ignored_containers:
            return

        event_id = f"{event.get('timeNano') or event.get('time')}-{actor.get('ID')}-{action}"
        event_time = datetime.fromtimestamp(event.get("time", 0)).strftime("%H:%M")
        label = EVENT_LABELS.get(action, action)
        severity = "critical" if action == "die" else "info"
        self.notifier.event(
            alert_id=f"docker.{container}.{action}",
            source="docker",
            severity=severity,
            title=f"Container {label}",
            body=f"Container: {container}\nEvent: {label}\nTime: {event_time}",
            dedupe_id=event_id,
        )
