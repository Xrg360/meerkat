from typing import Any

import psutil


def check_disk(config: dict[str, Any], state: Any, notifier: Any) -> None:
    disk_config = config.get("disk", {}) or {}
    threshold = float(disk_config.get("threshold", 90))
    paths = disk_config.get("paths") or ["/"]

    for path in paths:
        try:
            usage = psutil.disk_usage(path)
        except OSError:
            continue

        safe_path = path.replace("/", "root").replace("\\", "_").replace(":", "")
        state.set(f"metrics.disk.{safe_path}.percent", usage.percent)
        notifier.condition(
            alert_id=f"disk.{safe_path}.high",
            source="disk",
            active=usage.percent >= threshold,
            severity=disk_config.get("severity", "warning"),
            title="Disk usage high",
            alert_body=f"Filesystem: {path}\nUsage: {usage.percent:.0f}%\nThreshold: {threshold:.0f}%",
            recovery_body=f"Filesystem: {path}\nUsage: {usage.percent:.0f}%",
            duration=disk_config.get("duration"),
            cooldown=disk_config.get("cooldown"),
        )
