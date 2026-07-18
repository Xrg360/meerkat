import logging
import signal
import sys
from pathlib import Path
from threading import Event
from typing import Any

import yaml

from monitors.actions import ActionService
from monitors.alerts import AlertManager
from monitors.api import ApiServer
from monitors.autofix import AutoHealMonitor
from monitors.commands import TelegramCommandMonitor
from monitors.config import validate_config
from monitors.cpu import check_cpu
from monitors.disk import check_disk
from monitors.docker import DockerEventMonitor
from monitors.history import HistoryStore
from monitors.internet import check_internet
from monitors.network import check_network
from monitors.ram import check_ram
from monitors.sites import check_sites
from monitors.state import StateStore
from monitors.status import StatusService
from monitors.telegram import TelegramNotifier
from monitors.temp import check_temperature


CONFIG_PATH = Path("config/config.yml")
STATE_PATH = Path("state/state.json")
HISTORY_PATH = Path("state/history.db")


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing configuration file: {CONFIG_PATH}")

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def run_check(name: str, callback: Any, config: dict[str, Any], state: StateStore, alerts: AlertManager) -> None:
    try:
        callback(config, state, alerts)
    except Exception:
        logging.exception("%s check failed", name)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = load_config()
    validate_config(config)
    interval = int(config.get("interval", 30))
    state = StateStore(str(STATE_PATH))
    history = HistoryStore(str(HISTORY_PATH))
    notifier = TelegramNotifier(config, state)
    alerts = AlertManager(config, state, notifier, history)
    action_service = ActionService(config, state, history)
    status_service = StatusService(config, state, history)
    stopped = Event()

    auto_heal_monitor = AutoHealMonitor(config, state, alerts, action_service)
    auto_heal_monitor.start()
    docker_monitor = DockerEventMonitor(state, alerts)
    docker_monitor.start()
    command_monitor = TelegramCommandMonitor(config, state, notifier, status_service, action_service)
    command_monitor.start()
    api_server = ApiServer(config, status_service, action_service)
    api_server.start()

    def handle_signal(signum: int, _frame: Any) -> None:
        logging.info("Received signal %s, shutting down", signum)
        stopped.set()
        auto_heal_monitor.stop()
        docker_monitor.stop()
        command_monitor.stop()
        api_server.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logging.info("Meerkat started with %ss interval", interval)
    alerts.event(
        alert_id="meerkat.boot",
        source="meerkat",
        severity="info",
        title="Meerkat booted",
        body="Docker container started and monitoring is active.",
        force=True,
    )

    checks = [
        ("network", check_network),
        ("internet", check_internet),
        ("cpu", check_cpu),
        ("ram", check_ram),
        ("disk", check_disk),
        ("temperature", check_temperature),
        ("sites", check_sites),
    ]

    while not stopped.is_set():
        for name, callback in checks:
            run_check(name, callback, config, state, alerts)

        stopped.wait(interval)

    logging.info("Meerkat stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
