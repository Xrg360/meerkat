import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Any


class HistoryStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self.memory_only = False
        self.memory_events: list[dict[str, Any]] = []
        try:
            self._init_db()
        except (OSError, sqlite3.DatabaseError):
            try:
                self._quarantine_broken_db()
                self._init_db()
            except (OSError, sqlite3.DatabaseError):
                self.memory_only = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self.lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    alert_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_ts
                ON events (ts DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_alert
                ON events (alert_id, ts DESC)
                """
            )

    def _quarantine_broken_db(self) -> None:
        suffix = f".broken-{int(time.time())}"
        for path in [
            self.path,
            self.path.with_name(f"{self.path.name}-journal"),
            self.path.with_name(f"{self.path.name}-wal"),
            self.path.with_name(f"{self.path.name}-shm"),
        ]:
            if path.exists():
                path.replace(path.with_name(f"{path.name}{suffix}"))

    def record(self, alert: Any) -> None:
        if self.memory_only:
            self.memory_events.append(
                {
                    "ts": alert.timestamp,
                    "alert_id": alert.id,
                    "source": alert.source,
                    "severity": alert.severity,
                    "status": alert.status,
                    "title": alert.title,
                    "body": alert.body,
                }
            )
            self.memory_events = self.memory_events[-200:]
            return

        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events (ts, alert_id, source, severity, status, title, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.timestamp,
                    alert.id,
                    alert.source,
                    alert.severity,
                    alert.status,
                    alert.title,
                    alert.body,
                ),
            )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if self.memory_only:
            return list(reversed(self.memory_events[-limit:]))

        with self.lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT ts, alert_id, source, severity, status, title, body
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear(self) -> None:
        if self.memory_only:
            self.memory_events = []
            return

        with self.lock, self._connect() as connection:
            connection.execute("DELETE FROM events")
