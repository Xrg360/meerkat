from typing import Any


class ConfigError(ValueError):
    pass


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    interval = config.get("interval", 30)
    if not isinstance(interval, int) or interval < 5:
        errors.append("interval must be an integer >= 5")

    for section in ("cpu", "ram", "disk", "temperature"):
        threshold = (config.get(section, {}) or {}).get("threshold")
        if threshold is None:
            errors.append(f"{section}.threshold is required")
            continue
        try:
            value = float(threshold)
            if value <= 0:
                errors.append(f"{section}.threshold must be > 0")
        except (TypeError, ValueError):
            errors.append(f"{section}.threshold must be numeric")

    network = config.get("network", {}) or {}
    if not network.get("ethernet") and not network.get("wifi"):
        errors.append("at least one network interface must be configured")

    internet = config.get("internet", {}) or {}
    hosts = internet.get("hosts") or []
    if not isinstance(hosts, list) or not hosts:
        errors.append("internet.hosts must contain at least one host")

    for index, site in enumerate(config.get("sites") or []):
        if not isinstance(site, dict):
            errors.append(f"sites[{index}] must be an object")
            continue
        if not site.get("name"):
            errors.append(f"sites[{index}].name is required")
        url = str(site.get("url") or "")
        if not url.startswith(("http://", "https://")):
            errors.append(f"sites[{index}].url must start with http:// or https://")

    if errors:
        raise ConfigError("; ".join(errors))

    return config
