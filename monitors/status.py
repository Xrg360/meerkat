from typing import Any

import docker
import psutil

from monitors.network import get_default_route, get_interface_status
from monitors.temp import read_cpu_temperature


class StatusService:
    def __init__(self, config: dict[str, Any], state: Any, history: Any) -> None:
        self.config = config
        self.state = state
        self.history = history

    def status(self) -> dict[str, Any]:
        return {
            "alerts_silenced": self.state.get("alerts.silenced", False),
            "internet_up": self.state.get("internet.up"),
            "ethernet_up": self.state.get("changes.network.ethernet.value"),
            "wifi_up": self.state.get("changes.network.wifi.value"),
            "default_route": self.state.get("changes.network.route.value"),
            "active_alerts": self.active_alerts(),
            "recent_events": self.history.recent(20),
        }

    def health(self) -> dict[str, Any]:
        disk_path = ((self.config.get("disk", {}) or {}).get("paths") or ["/"])[0]
        return {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "ram_percent": psutil.virtual_memory().percent,
            "disk_path": disk_path,
            "disk_percent": psutil.disk_usage(disk_path).percent,
            "cpu_temperature": read_cpu_temperature(),
            "load_average": psutil.getloadavg() if hasattr(psutil, "getloadavg") else None,
        }

    def network(self) -> dict[str, Any]:
        network_config = self.config.get("network", {}) or {}
        interfaces = {}
        for label in ("ethernet", "wifi"):
            name = network_config.get(label)
            if not name:
                continue
            status = get_interface_status(name)
            interfaces[label] = {
                "name": status.name,
                "operstate": status.operstate,
                "carrier": status.carrier,
                "has_ip": status.has_ip,
                "up": status.up,
            }
        return {
            "interfaces": interfaces,
            "default_route": get_default_route(),
            "internet_up": self.state.get("internet.up"),
        }

    def docker(self) -> dict[str, Any]:
        try:
            client = docker.from_env()
            containers = [
                {"name": container.name, "status": container.status, "image": ",".join(container.image.tags)}
                for container in client.containers.list(all=True)
            ]
        except Exception as exc:
            return {"available": False, "error": str(exc), "containers": []}
        return {"available": True, "containers": containers}

    def active_alerts(self) -> list[str]:
        prefix = "alerts."
        suffix = ".notified"
        active = []
        for key, value in self.state.snapshot().items():
            if key.startswith(prefix) and key.endswith(suffix) and value is True:
                active.append(key.removeprefix(prefix).removesuffix(suffix))
        return sorted(active)
