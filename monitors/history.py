import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any


class HistoryStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self._init_db()

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

    def record(self, alert: Any) -> None:
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
