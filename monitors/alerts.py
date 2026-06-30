from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}


@dataclass(frozen=True)
class Alert:
    id: str
    source: str
    severity: str
    status: str
    title: str
    body: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_duration(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().lower()
    if not text:
        return default

    units = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hour": 3600,
        "hours": 3600,
    }

    for suffix, multiplier in units.items():
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            return int(float(number) * multiplier)

    return int(float(text))


class AlertManager:
    def __init__(self, config: dict[str, Any], state: Any, notifier: Any, history: Any) -> None:
        self.config = config
        self.state = state
        self.notifier = notifier
        self.history = history
        alerting_config = config.get("alerting", {}) or {}
        self.default_duration = parse_duration(alerting_config.get("duration"), 0)
        self.default_cooldown = parse_duration(alerting_config.get("cooldown"), 900)

    def condition(
        self,
        alert_id: str,
        source: str,
        active: bool,
        severity: str,
        title: str,
        alert_body: str,
        recovery_body: str,
        duration: Any = None,
        cooldown: Any = None,
    ) -> None:
        now = datetime.now(timezone.utc).timestamp()
        duration_seconds = parse_duration(duration, self.default_duration)
        cooldown_seconds = parse_duration(cooldown, self.default_cooldown)
        base_key = f"alerts.{alert_id}"
        active_since_key = f"{base_key}.active_since"
        notified_key = f"{base_key}.notified"
        last_sent_key = f"{base_key}.last_sent"

        if active:
            active_since = self.state.get(active_since_key)
            if active_since is None:
                active_since = now
                self.state.set(active_since_key, now)

            notified = self.state.get(notified_key, False)
            last_sent = self.state.get(last_sent_key, 0)
            stable_enough = now - float(active_since) >= duration_seconds
            cooled_down = now - float(last_sent or 0) >= cooldown_seconds
            if stable_enough and (not notified or cooled_down):
                self.state.set(notified_key, True)
                self.state.set(last_sent_key, now)
                self._emit(alert_id, source, severity, "active", title, alert_body)
            return

        was_notified = self.state.get(notified_key, False)
        self.state.set(active_since_key, None)
        if was_notified:
            self.state.set(notified_key, False)
            self.state.set(last_sent_key, now)
            self._emit(alert_id, source, "info", "recovered", f"{title} recovered", recovery_body)

    def state_change(
        self,
        alert_id: str,
        source: str,
        current: Any,
        title: str,
        body: str,
        severity: str = "warning",
        notify_initial: bool = False,
    ) -> None:
        key = f"changes.{alert_id}.value"
        previous = self.state.get(key)
        if previous is None and not notify_initial:
            self.state.set(key, current)
            return
        if previous != current:
            self.state.set(key, current)
            self._emit(alert_id, source, severity, "changed", title, body)

    def event(
        self,
        alert_id: str,
        source: str,
        severity: str,
        title: str,
        body: str,
        dedupe_id: str | None = None,
        force: bool = False,
    ) -> None:
        if dedupe_id:
            key = f"events.{alert_id}.last"
            if self.state.get(key) == dedupe_id:
                return
            self.state.set(key, dedupe_id)
        self._emit(alert_id, source, severity, "event", title, body, force=force)

    def _emit(
        self,
        alert_id: str,
        source: str,
        severity: str,
        status: str,
        title: str,
        body: str,
        force: bool = False,
    ) -> None:
        alert = Alert(
            id=alert_id,
            source=source,
            severity=severity,
            status=status,
            title=title,
            body=body,
            timestamp=utc_now(),
        )
        self.history.record(alert)
        self.notifier.send_alert(alert, force=force)
