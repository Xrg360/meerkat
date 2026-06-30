import logging
import socket
import subprocess
from dataclasses import dataclass
from typing import Any

import psutil


@dataclass(frozen=True)
class InterfaceStatus:
    name: str
    operstate: str
    carrier: bool
    has_ip: bool
    up: bool


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            return file.read().strip()
    except OSError:
        return None


def _has_ipv4_address(interface: str) -> bool:
    return any(address.family == socket.AF_INET for address in psutil.net_if_addrs().get(interface, []))


def get_interface_status(interface: str) -> InterfaceStatus:
    operstate = _read_text(f"/sys/class/net/{interface}/operstate") or "missing"
    carrier_text = _read_text(f"/sys/class/net/{interface}/carrier")
    carrier = carrier_text == "1"
    has_ip = _has_ipv4_address(interface)
    up = operstate == "up" and carrier and has_ip
    return InterfaceStatus(interface, operstate, carrier, has_ip, up)


def get_default_route() -> str | None:
    try:
        output = subprocess.check_output(["ip", "route", "show", "default"], text=True, timeout=5)
    except (subprocess.SubprocessError, OSError) as exc:
        logging.warning("Could not read default route: %s", exc)
        return None

    for line in output.splitlines():
        parts = line.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    return None


def check_network(config: dict[str, Any], state: Any, notifier: Any) -> None:
    network_config = config.get("network", {}) or {}
    interfaces = {
        "ethernet": network_config.get("ethernet"),
        "wifi": network_config.get("wifi"),
    }

    current_status: dict[str, bool] = {}
    status_by_label: dict[str, InterfaceStatus] = {}

    for label, interface in interfaces.items():
        if not interface:
            continue

        status = get_interface_status(interface)
        current_status[label] = status.up
        status_by_label[label] = status

    for label, status in status_by_label.items():
        interface = status.name
        fallback = "No fallback network detected"
        if label == "ethernet" and current_status.get("wifi"):
            fallback = "Using Wi-Fi"
        elif label == "wifi" and current_status.get("ethernet"):
            fallback = "Using Ethernet"

        notifier.state_change(
            alert_id=f"network.{label}",
            source="network",
            current=status.up,
            severity="info" if status.up else "warning",
            title=f"{label.title()} {'restored' if status.up else 'disconnected'}",
            body=f"Interface: {interface}\nState: {status.operstate}\nCarrier: {status.carrier}\nIPv4: {status.has_ip}\n{fallback if not status.up else ''}".strip(),
        )

    route = get_default_route()
    notifier.state_change(
        alert_id="network.route",
        source="network",
        current=route,
        severity="info",
        title="Active route changed",
        body=f"Current default route: {route or 'none'}",
    )
