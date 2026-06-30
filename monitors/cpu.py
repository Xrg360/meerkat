from typing import Any

import psutil


def check_cpu(config: dict[str, Any], state: Any, notifier: Any) -> None:
    cpu_config = config.get("cpu", {}) or {}
    threshold = float(cpu_config.get("threshold", 90))
    usage = psutil.cpu_percent(interval=1)
    state.set("metrics.cpu.percent", usage)
    notifier.condition(
        alert_id="cpu.high",
        source="cpu",
        active=usage >= threshold,
        severity=cpu_config.get("severity", "warning"),
        title="CPU usage high",
        alert_body=f"Usage: {usage:.0f}%\nThreshold: {threshold:.0f}%",
        recovery_body=f"Usage: {usage:.0f}%\nThreshold: {threshold:.0f}%",
        duration=cpu_config.get("duration"),
        cooldown=cpu_config.get("cooldown"),
    )
