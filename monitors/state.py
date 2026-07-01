import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.lock = Lock()
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.data = {}
            return

        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logging.warning("Could not read state file %s: %s", self.path, exc)
            self.data = {}

    def save(self) -> None:
        payload = json.dumps(self.data, indent=2, sort_keys=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(payload, encoding="utf-8")
        try:
            temp_path.replace(self.path)
        except PermissionError:
            self.path.write_text(payload, encoding="utf-8")
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return self.data.get(key, default)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.data)

    def set(self, key: str, value: Any) -> bool:
        with self.lock:
            if self.data.get(key) == value:
                return False
            self.data[key] = value
            self.save()
            return True

    def emit_on_change(self, key: str, value: Any, message: str, notifier: Any) -> bool:
        changed = self.set(key, value)
        if changed:
            notifier.send(message)
        return changed
