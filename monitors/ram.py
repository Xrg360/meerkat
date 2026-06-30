from typing import Any

import psutil


def check_ram(config: dict[str, Any], state: Any, notifier: Any) -> None:
    ram_config = config.get("ram", {}) or {}
    threshold = float(ram_config.get("threshold", 90))
    usage = psutil.virtual_memory().percent
    state.set("metrics.ram.percent", usage)
    notifier.condition(
        alert_id="ram.high",
        source="ram",
        active=usage >= threshold,
        severity=ram_config.get("severity", "warning"),
        title="RAM usage high",
        alert_body=f"Usage: {usage:.0f}%\nThreshold: {threshold:.0f}%",
        recovery_body=f"Usage: {usage:.0f}%\nThreshold: {threshold:.0f}%",
        duration=ram_config.get("duration"),
        cooldown=ram_config.get("cooldown"),
    )
