from typing import Any

import psutil


def read_cpu_temperature() -> float | None:
    try:
        sensors = psutil.sensors_temperatures(fahrenheit=False)
    except (AttributeError, OSError):
        return None

    preferred_names = ("coretemp", "k10temp", "cpu_thermal", "acpitz")
    readings = []

    for name in preferred_names:
        for entry in sensors.get(name, []):
            if entry.current is not None:
                readings.append(float(entry.current))

    if not readings:
        for entries in sensors.values():
            for entry in entries:
                if entry.current is not None:
                    readings.append(float(entry.current))

    return max(readings) if readings else None


def check_temperature(config: dict[str, Any], state: Any, notifier: Any) -> None:
    temp_config = config.get("temperature", {}) or {}
    threshold = float(temp_config.get("threshold", 80))
    temperature = read_cpu_temperature()
    if temperature is None:
        state.set("temperature.available", False)
        return

    state.set("temperature.available", True)
    state.set("metrics.temperature.cpu", temperature)
    notifier.condition(
        alert_id="temperature.cpu.high",
        source="temperature",
        active=temperature >= threshold,
        severity=temp_config.get("severity", "critical"),
        title="CPU temperature high",
        alert_body=f"Temperature: {temperature:.0f} C\nThreshold: {threshold:.0f} C",
        recovery_body=f"Temperature: {temperature:.0f} C\nThreshold: {threshold:.0f} C",
        duration=temp_config.get("duration"),
        cooldown=temp_config.get("cooldown"),
    )
