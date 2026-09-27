"""Small durable ledger for memories, incoming events, and notification delivery."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
                recipe TEXT NOT NULL, markdown TEXT NOT NULL, compiled TEXT NOT NULL,
                revision TEXT NOT NULL, sources TEXT NOT NULL, active INTEGER NOT NULL,
                updated_at TEXT NOT NULL, error TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, memory_revision TEXT NOT NULL,
                event_id TEXT, kind TEXT NOT NULL, text TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT,
                delivery_id TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                title TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            UPDATE events SET status='queued' WHERE status IN ('processing','writing');
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(events)")}
        if "attempts" not in columns:
            self.db.execute("ALTER TABLE events ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "retry_at" not in columns:
            self.db.execute("ALTER TABLE events ADD COLUMN retry_at TEXT")
        notification_columns = {row[1] for row in self.db.execute("PRAGMA table_info(notifications)")}
        if "delivery_id" not in notification_columns:
            self.db.execute("ALTER TABLE notifications ADD COLUMN delivery_id TEXT")
            if "telegram_message_id" in notification_columns:
                self.db.execute("UPDATE notifications SET delivery_id=telegram_message_id")
        if "attempts" not in notification_columns:
            self.db.execute("ALTER TABLE notifications ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "retry_at" not in notification_columns:
            self.db.execute("ALTER TABLE notifications ADD COLUMN retry_at TEXT")
        if "delivery_subject" not in notification_columns:
            self.db.execute("ALTER TABLE notifications ADD COLUMN delivery_subject TEXT")
        if "delivery_text" not in notification_columns:
            self.db.execute("ALTER TABLE notifications ADD COLUMN delivery_text TEXT")
        self.db.commit()
        path.chmod(0o600)

    def close(self) -> None:
        self.db.close()

    def setting(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, encode(value)))

    def enable_email_delivery(self) -> str:
        """Opt in once without sending the backlog accumulated before email."""
        cutoff = self.setting("email_enabled_since")
        if cutoff is None:
            cutoff = stamp()
            with self.db:
                self.db.execute("INSERT INTO settings VALUES (?,?)", ("email_enabled_since", encode(cutoff)))
                self.db.execute(
                    "UPDATE notifications SET status='local' WHERE status='pending' AND created_at<?",
                    (cutoff,),
                )
        return cutoff

    def trace(self, kind: str, title: str, detail: Any = None) -> None:
        with self.db:
            self.db.execute("INSERT INTO traces(kind,title,detail,created_at) VALUES(?,?,?,?)", (kind, title, encode(detail or {}), stamp()))

    def refresh_memory_markdown(self, memory_id: str, markdown: str, *, recipe: str | None = None) -> None:
        """Keep canonical metadata edits without resetting unchanged behavior."""
        with self.db:
            self.db.execute("UPDATE memories SET markdown=?, recipe=COALESCE(?,recipe), updated_at=? WHERE id=?", (markdown, recipe, stamp(), memory_id))

    def save_memory(self, memory: dict, *, preserve_event_id: str | None = None) -> None:
        with self.db:
            self.db.execute("""INSERT OR REPLACE INTO memories
                (id,title,body,recipe,markdown,compiled,revision,sources,active,updated_at,error)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                    memory["id"], memory["title"], memory["body"], memory["recipe"],
                    memory["markdown"], encode(memory["compiled"]), memory["revision"],
                    encode(memory.get("sources", [])), int(memory.get("active", True)),
                    stamp(), memory.get("error"),
                ))
            if preserve_event_id is not None:
                # The alert and rewrite are outcomes of the same observed
                # event. Keep that alert deliverable after its own rewrite.
                self.db.execute(
                    """UPDATE notifications SET memory_revision=?
                    WHERE memory_id=? AND event_id=? AND status='pending'""",
                    (memory["revision"], memory["id"], preserve_event_id),
                )
            self.db.execute("""UPDATE notifications SET status='canceled'
                WHERE memory_id=? AND memory_revision<>? AND status='pending'""",
                (memory["id"], memory["revision"]))

    @staticmethod
    def memory_row(row) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        for key in ("compiled", "sources"):
            item[key] = json.loads(item[key])
        item["active"] = bool(item["active"])
        return item

    def memory(self, memory_id: str) -> dict | None:
        return self.memory_row(self.db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone())

    def memories(self, active_only=False) -> list[dict]:
        where = "WHERE active=1 AND error IS NULL" if active_only else ""
        return [self.memory_row(r) for r in self.db.execute(f"SELECT * FROM memories {where} ORDER BY updated_at DESC")]

    def deactivate(self, memory_id: str, *, error: str | None = None) -> None:
        with self.db:
            self.db.execute("UPDATE memories SET active=0,error=? WHERE id=?", (error, memory_id))
            self.db.execute("UPDATE notifications SET status='canceled' WHERE memory_id=? AND status='pending'", (memory_id,))

    def add_event(self, event_id: str, source: str, payload: dict) -> bool:
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO events(id,source,payload,created_at) VALUES(?,?,?,?)", (event_id, source, encode(payload), stamp()))
            return bool(cursor.rowcount)

    def set_event_status(self, event_id: str, status: str, error: str | None = None) -> None:
        with self.db:
            retry_at = None
            if status == "failed":
                row = self.db.execute("SELECT attempts FROM events WHERE id=?", (event_id,)).fetchone()
                attempt = row[0] if row else 1
                delay = min(300, 5 * 2 ** min(max(attempt - 1, 0), 6))
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            self.db.execute(
                "UPDATE events SET status=?,error=?,retry_at=?,attempts=attempts+? WHERE id=?",
                (status, error, retry_at, int(status == "processing"), event_id),
            )

    def event(self, event_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def ready_events(self, *, now: str | None = None, limit=100) -> list[dict]:
        rows = self.db.execute(
            """SELECT * FROM events WHERE status='queued'
            OR (status='failed' AND retry_at<=?) ORDER BY created_at LIMIT ?""",
            (now or stamp(), limit),
        )
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def events(self, status: str | None = None, limit=100) -> list[dict]:
        rows = self.db.execute("SELECT * FROM events " + ("WHERE status=? " if status else "") + "ORDER BY created_at DESC LIMIT ?", ((status, limit) if status else (limit,)))
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def enqueue(self, *, notification_id: str, memory: dict, kind: str, text: str, event_id: str | None = None) -> bool:
        cutoff = self.setting("email_enabled_since")
        event = self.event(event_id) if event_id else None
        historical_event = bool(cutoff and event and event["created_at"] < cutoff)
        status = "local" if kind == "surface" or historical_event else "pending"
        with self.db:
            result = self.db.execute("""INSERT OR IGNORE INTO notifications
                (id,memory_id,memory_revision,event_id,kind,text,status,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (notification_id, memory["id"], memory["revision"], event_id, kind, text, status, stamp()))
            if result.rowcount:
                return True
            # An edit cancels the old pending row. If the same reminder still
            # exists, reattach it to the new revision instead of losing it.
            revived = self.db.execute(
                """UPDATE notifications SET status=?,memory_revision=?,error=NULL,retry_at=NULL
                WHERE id=? AND status='canceled' AND memory_id=?""",
                (status, memory["revision"], notification_id, memory["id"]),
            )
            return bool(revived.rowcount)

    def notifications(self, pending_only=False, limit=100) -> list[dict]:
        where = "WHERE status='pending'" if pending_only else ""
        return [dict(r) for r in self.db.execute(f"SELECT * FROM notifications {where} ORDER BY created_at DESC LIMIT ?", (limit,))]

    def notification(self, notification_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()
        return dict(row) if row else None

    def ready_notifications(self, *, now: str | None = None, limit=100) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            """SELECT * FROM notifications WHERE status='pending'
            AND (retry_at IS NULL OR retry_at<=?) ORDER BY created_at LIMIT ?""",
            (now or stamp(), limit),
        )]

    def delivery_attempt(self, notification_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE notifications SET attempts=attempts+1 WHERE id=?", (notification_id,))

    def prepare_delivery(self, notification_id: str, *, subject: str, text: str) -> dict:
        """Freeze the send payload once; retries must preserve its idempotency key."""
        with self.db:
            self.db.execute(
                """UPDATE notifications SET
                delivery_subject=COALESCE(delivery_subject,?),
                delivery_text=COALESCE(delivery_text,?) WHERE id=?""",
                (subject, text, notification_id),
            )
        return self.notification(notification_id)

    def sent_delivery(self, delivery_id: str | None) -> bool:
        if not delivery_id:
            return False
        return self.db.execute(
            "SELECT 1 FROM notifications WHERE delivery_id=? AND status='delivered' LIMIT 1",
            (delivery_id,),
        ).fetchone() is not None

    def notification_status(self, notification_id: str, status: str, *, error=None, delivery_id=None) -> None:
        with self.db:
            retry_at = None
            if status == "pending" and error:
                row = self.db.execute("SELECT attempts FROM notifications WHERE id=?", (notification_id,)).fetchone()
                attempt = row[0] if row else 1
                delay = min(900, 30 * 2 ** min(max(attempt - 1, 0), 5))
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            self.db.execute("UPDATE notifications SET status=?,error=?,delivery_id=COALESCE(?,delivery_id),delivered_at=?,retry_at=? WHERE id=?", (status, error, delivery_id, stamp() if status == "delivered" else None, retry_at, notification_id))

    def traces(self, limit=100) -> list[dict]:
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in self.db.execute("SELECT * FROM traces ORDER BY id DESC LIMIT ?", (limit,))]
