"""SQLite-Speicher: Snapshots, Änderungsverlauf, Push-Abos, Einstellungen.

Die App hat genau einen Nutzer und wenige Schreibvorgänge pro Viertelstunde,
deshalb reicht eine einzige Verbindung hinter einem Lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    module     TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    category   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    url        TEXT,
    ref_date   TEXT,
    data       TEXT,
    pushed     INTEGER NOT NULL DEFAULT 0,
    seen       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_created ON events(created_at DESC);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint     TEXT PRIMARY KEY,
    p256dh       TEXT NOT NULL,
    auth         TEXT NOT NULL,
    user_agent   TEXT,
    created_at   TEXT NOT NULL,
    last_success TEXT,
    failures     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS homework_done (
    id      TEXT PRIMARY KEY,
    done_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER,
    error       TEXT,
    changes     INTEGER NOT NULL DEFAULT 0,
    trigger     TEXT
);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "briefing_enabled": True,
    "briefing_time": "06:30",
    "evening_enabled": False,
    "evening_time": "19:00",
    "reminder_enabled": True,
    "reminder_time": "17:00",
    "reminder_days": [7, 3, 1],
    "notify_lessons": True,
    "notify_homework": True,
    "notify_exams": True,
    "notify_grades": True,
    "notify_letters": True,
    "notify_messages": True,
    "notify_calendar": True,
    "grade_values_in_push": False,
    "poll_interval": 15,
    "poll_start": "06:00",
    "poll_end": "21:30",
    "weekend_poll_interval": 60,
}

# Grenzen für Werte, die über die Einstellungen-Seite kommen
SETTING_LIMITS = {
    "poll_interval": (10, 120),
    "weekend_poll_interval": (30, 240),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # ── Key/Value ────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def delete(self, key: str) -> None:
        self.execute("DELETE FROM kv WHERE key = ?", (key,))

    # ── Einstellungen ────────────────────────────────────────────────

    def settings(self) -> dict[str, Any]:
        stored = self.get("settings", {}) or {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        return merged

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        for key, value in patch.items():
            if key not in DEFAULT_SETTINGS:
                continue
            default = DEFAULT_SETTINGS[key]
            if isinstance(default, bool):
                current[key] = bool(value)
            elif isinstance(default, int):
                number = int(value)
                low, high = SETTING_LIMITS.get(key, (number, number))
                current[key] = max(low, min(high, number))
            elif isinstance(default, list):
                days = sorted({int(v) for v in value if 0 < int(v) <= 60}, reverse=True)
                current[key] = days
            elif key.endswith("_time") or key in {"poll_start", "poll_end"}:
                current[key] = _validate_time(str(value))
            else:
                current[key] = value
        self.set("settings", current)
        return current

    # ── Snapshots ────────────────────────────────────────────────────

    def snapshot(self, module: str) -> Any | None:
        row = self.query_one("SELECT data FROM snapshots WHERE module = ?", (module,))
        return json.loads(row["data"]) if row else None

    def snapshot_time(self, module: str) -> str | None:
        row = self.query_one("SELECT fetched_at FROM snapshots WHERE module = ?", (module,))
        return row["fetched_at"] if row else None

    def save_snapshot(self, module: str, data: Any) -> None:
        self.execute(
            "INSERT INTO snapshots(module, data, fetched_at) VALUES(?, ?, ?) "
            "ON CONFLICT(module) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at",
            (module, json.dumps(data, ensure_ascii=False), now_iso()),
        )

    # ── Verlauf ──────────────────────────────────────────────────────

    def add_event(
        self,
        category: str,
        kind: str,
        title: str,
        body: str,
        url: str | None = None,
        ref_date: str | None = None,
        data: Any = None,
    ) -> int:
        cur = self.execute(
            "INSERT INTO events(created_at, category, kind, title, body, url, ref_date, data) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now_iso(),
                category,
                kind,
                title,
                body,
                url,
                ref_date,
                json.dumps(data, ensure_ascii=False) if data is not None else None,
            ),
        )
        return int(cur.lastrowid)

    def mark_pushed(self, event_id: int) -> None:
        self.execute("UPDATE events SET pushed = 1 WHERE id = ?", (event_id,))

    def events(self, limit: int = 50, before_id: int | None = None) -> list[dict[str, Any]]:
        if before_id:
            rows = self.query(
                "SELECT * FROM events WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit)
            )
        else:
            rows = self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"]) if item["data"] else None
            item["pushed"] = bool(item["pushed"])
            item["seen"] = bool(item["seen"])
            out.append(item)
        return out

    def unseen_count(self) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM events WHERE seen = 0 AND category != 'briefing'"
        )
        return int(row["n"]) if row else 0

    def mark_all_seen(self) -> None:
        self.execute("UPDATE events SET seen = 1 WHERE seen = 0")

    def prune_events(self, keep_days: int = 180) -> None:
        self.execute(
            "DELETE FROM events WHERE created_at < datetime('now', ?)", (f"-{keep_days} days",)
        )

    # ── Hausaufgaben erledigt (nur lokal) ────────────────────────────

    def homework_done_ids(self) -> set[str]:
        return {row["id"] for row in self.query("SELECT id FROM homework_done")}

    def set_homework_done(self, hw_id: str, done: bool) -> None:
        if done:
            self.execute(
                "INSERT OR REPLACE INTO homework_done(id, done_at) VALUES(?, ?)", (hw_id, now_iso())
            )
        else:
            self.execute("DELETE FROM homework_done WHERE id = ?", (hw_id,))

    # ── Sync-Protokoll ───────────────────────────────────────────────

    def sync_started(self, trigger: str) -> int:
        cur = self.execute(
            "INSERT INTO sync_log(started_at, trigger) VALUES(?, ?)", (now_iso(), trigger)
        )
        return int(cur.lastrowid)

    def sync_finished(self, sync_id: int, ok: bool, changes: int = 0, error: str | None = None) -> None:
        self.execute(
            "UPDATE sync_log SET finished_at = ?, ok = ?, changes = ?, error = ? WHERE id = ?",
            (now_iso(), int(ok), changes, error, sync_id),
        )
        self.execute(
            "DELETE FROM sync_log WHERE id NOT IN (SELECT id FROM sync_log ORDER BY id DESC LIMIT 500)"
        )

    def last_syncs(self, limit: int = 10) -> list[dict[str, Any]]:
        return [dict(r) for r in self.query("SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,))]

    def last_successful_sync(self) -> str | None:
        row = self.query_one(
            "SELECT finished_at FROM sync_log WHERE ok = 1 ORDER BY id DESC LIMIT 1"
        )
        return row["finished_at"] if row else None


def _validate_time(value: str) -> str:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Ungültige Uhrzeit: {value}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"Ungültige Uhrzeit: {value}")
    return f"{hour:02d}:{minute:02d}"
