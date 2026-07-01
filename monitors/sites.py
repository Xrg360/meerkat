import time
from typing import Any

import requests


def configured_sites(config: dict[str, Any], state: Any) -> list[dict[str, Any]]:
    config_sites = config.get("sites") or []
    runtime_sites = state.get("sites.custom", [])
    merged: dict[str, dict[str, Any]] = {}

    for site in config_sites + runtime_sites:
        if not isinstance(site, dict):
            continue
        key = str(site.get("name") or site.get("url") or "").lower()
        if key:
            merged[key] = site

    return list(merged.values())


def check_sites(config: dict[str, Any], state: Any, alerts: Any) -> None:
    sites = configured_sites(config, state)
    if not sites:
        state.set("sites.status", [])
        return

    results = []
    for site in sites:
        name = str(site.get("name") or site.get("url") or "unnamed")
        url = str(site.get("url") or "")
        if not url:
            continue

        timeout = float(site.get("timeout", 10))
        expected_status = site.get("expected_status", [200])
        if isinstance(expected_status, int):
            expected_status = [expected_status]
        keyword = site.get("keyword")
        severity = site.get("severity", "critical")
        duration = site.get("duration", site.get("down_duration", 0))
        cooldown = site.get("cooldown")
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_") or "site"

        started = time.perf_counter()
        status_code = None
        latency_ms = None
        error = None
        up = False

        try:
            response = requests.get(
                url,
                timeout=timeout,
                allow_redirects=bool(site.get("follow_redirects", True)),
                headers={"User-Agent": "Meerkat/1.0"},
            )
            latency_ms = round((time.perf_counter() - started) * 1000)
            status_code = response.status_code
            up = status_code in expected_status
            if keyword:
                up = up and str(keyword) in response.text
        except requests.RequestException as exc:
            latency_ms = round((time.perf_counter() - started) * 1000)
            error = str(exc)

        result = {
            "name": name,
            "url": url,
            "up": up,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "error": error,
            "expected_status": expected_status,
        }
        history_key = f"metrics.sites.{safe_name}.history"
        history = list(state.get(history_key, []))
        history.append(
            {
                "ts": int(time.time()),
                "up": up,
                "status_code": status_code,
                "latency_ms": latency_ms,
            }
        )
        history = history[-80:]
        result["history"] = history
        results.append(result)
        state.set(f"metrics.sites.{safe_name}.up", up)
        state.set(f"metrics.sites.{safe_name}.latency_ms", latency_ms)
        state.set(f"metrics.sites.{safe_name}.status_code", status_code)
        state.set(history_key, history)

        detail = [
            f"Site: {name}",
            f"URL: {url}",
            f"Expected: {', '.join(str(code) for code in expected_status)}",
            f"Status: {status_code if status_code is not None else 'none'}",
            f"Latency: {latency_ms if latency_ms is not None else 'unknown'}ms",
        ]
        if keyword:
            detail.append(f"Keyword: {keyword}")
        if error:
            detail.append(f"Error: {error}")

        alerts.condition(
            alert_id=f"site.{safe_name}.down",
            source="site",
            active=not up,
            severity=severity,
            title=f"Site down: {name}",
            alert_body="\n".join(detail),
            recovery_body="\n".join(detail),
            duration=duration,
            cooldown=cooldown,
        )

    state.set("sites.status", results)
