import platform
import subprocess
from typing import Any


def ping(host: str, timeout: int) -> bool:
    if platform.system().lower() == "windows":
        command = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
    else:
        command = ["ping", "-c", "1", "-W", str(timeout), host]

    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout + 2)
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def check_internet(config: dict[str, Any], state: Any, notifier: Any) -> None:
    internet_config = config.get("internet", {}) or {}
    hosts = internet_config.get("hosts") or ["1.1.1.1", "8.8.8.8"]
    timeout = int(internet_config.get("timeout", 2))

    reachable = any(ping(host, timeout) for host in hosts)
    state.set("internet.up", reachable)
    notifier.condition(
        alert_id="internet.down",
        source="internet",
        active=not reachable,
        severity=internet_config.get("severity", "critical"),
        title="Internet lost",
        alert_body=f"All probes failed: {', '.join(hosts)}",
        recovery_body=f"At least one probe is reachable: {', '.join(hosts)}",
        duration=internet_config.get("duration", 0),
        cooldown=internet_config.get("cooldown"),
    )
