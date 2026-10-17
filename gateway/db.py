"""SQLite storage: API keys, model allowlist, usage logs."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger("gateway.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    key_hash        TEXT NOT NULL UNIQUE,
    prefix          TEXT NOT NULL,
    rpm_limit       INTEGER,
    max_concurrent  INTEGER,
    created_at      TEXT NOT NULL,
    last_used_at    TEXT,
    revoked_at      TEXT
);

CREATE TABLE IF NOT EXISTS models (
    name      TEXT PRIMARY KEY,
    enabled   INTEGER NOT NULL DEFAULT 1,
    added_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_logs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id             INTEGER,
    ts                 TEXT NOT NULL,
    request_id         TEXT,
    endpoint           TEXT NOT NULL,
    model              TEXT,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    total_tokens       INTEGER,
    latency_ms         INTEGER,
    status_code        INTEGER NOT NULL,
    stream             INTEGER NOT NULL DEFAULT 0,
    client_ip          TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_logs(ts);
CREATE INDEX IF NOT EXISTS idx_usage_key_ts ON usage_logs(key_id, ts);
"""

KEY_COLUMNS = "id, name, prefix, rpm_limit, max_concurrent, created_at, last_used_at, revoked_at"
GROUP_BY = {
    "hour": "substr(u.ts, 1, 13)",
    "day": "substr(u.ts, 1, 10)",
    "key": "u.key_id",
    "model": "u.model",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database not connected")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def _one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ---- API keys ----------------------------------------------------------

    async def create_key(
        self, name: str, key_hash: str, prefix: str, rpm_limit: int | None, max_concurrent: int | None
    ) -> dict[str, Any]:
        cur = await self.conn.execute(
            "INSERT INTO api_keys (name, key_hash, prefix, rpm_limit, max_concurrent, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, key_hash, prefix, rpm_limit, max_concurrent, utcnow()),
        )
        await self.conn.commit()
        return await self.get_key(cur.lastrowid)

    async def get_key(self, key_id: int) -> dict[str, Any] | None:
        return await self._one(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id = ?", (key_id,))

    async def get_active_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        return await self._one(
            f"SELECT {KEY_COLUMNS} FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", (key_hash,)
        )

    async def list_keys(self, include_revoked: bool = True) -> list[dict[str, Any]]:
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        return await self._all(f"SELECT {KEY_COLUMNS} FROM api_keys {where} ORDER BY id")

    async def update_key(self, key_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {k: v for k, v in fields.items() if k in {"name", "rpm_limit", "max_concurrent"}}
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            await self.conn.execute(f"UPDATE api_keys SET {sets} WHERE id = ?", (*allowed.values(), key_id))
            await self.conn.commit()
        return await self.get_key(key_id)

    async def revoke_key(self, key_id: int) -> bool:
        cur = await self.conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (utcnow(), key_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---- Model allowlist ---------------------------------------------------

    async def seed_models(self, names: list[str]) -> None:
        await self.conn.executemany(
            "INSERT OR IGNORE INTO models (name, enabled, added_at) VALUES (?, 1, ?)",
            [(n, utcnow()) for n in names],
        )
        await self.conn.commit()

    async def list_models(self) -> list[dict[str, Any]]:
        return await self._all("SELECT name, enabled, added_at FROM models ORDER BY name")

    async def enabled_models(self) -> list[dict[str, Any]]:
        return await self._all("SELECT name, added_at FROM models WHERE enabled = 1 ORDER BY name")

    async def is_model_enabled(self, name: str) -> bool:
        return await self._one("SELECT 1 FROM models WHERE name = ? AND enabled = 1", (name,)) is not None

    async def set_model_enabled(self, name: str, enabled: bool) -> None:
        await self.conn.execute(
            "INSERT INTO models (name, enabled, added_at) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled",
            (name, int(enabled), utcnow()),
        )
        await self.conn.commit()

    async def delete_model(self, name: str) -> None:
        await self.conn.execute("DELETE FROM models WHERE name = ?", (name,))
        await self.conn.commit()

    # ---- Usage -------------------------------------------------------------

    async def insert_usage(self, records: list["UsageRecord"]) -> None:
        await self.conn.executemany(
            "INSERT INTO usage_logs (key_id, ts, request_id, endpoint, model, prompt_tokens,"
            " completion_tokens, total_tokens, latency_ms, status_code, stream, client_ip)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [r.as_row() for r in records],
        )
        last_used: dict[int, str] = {}
        for r in records:
            if r.key_id is not None:
                last_used[r.key_id] = max(r.ts, last_used.get(r.key_id, ""))
        await self.conn.executemany(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?", [(ts, kid) for kid, ts in last_used.items()]
        )
        await self.conn.commit()

    async def usage_summary(
        self, since: str, until: str | None = None, key_id: int | None = None, group_by: str = "day"
    ) -> list[dict[str, Any]]:
        group_expr = GROUP_BY[group_by]
        where, params = ["u.ts >= ?"], [since]
        if until:
            where.append("u.ts < ?")
            params.append(until)
        if key_id is not None:
            where.append("u.key_id = ?")
            params.append(key_id)
        return await self._all(
            f"SELECT {group_expr} AS {group_by},"
            + (" k.name AS key_name," if group_by == "key" else "")
            + " COUNT(*) AS requests,"
            " SUM(CASE WHEN u.status_code >= 400 THEN 1 ELSE 0 END) AS errors,"
            " COALESCE(SUM(u.prompt_tokens), 0) AS prompt_tokens,"
            " COALESCE(SUM(u.completion_tokens), 0) AS completion_tokens,"
            " COALESCE(SUM(u.total_tokens), 0) AS total_tokens,"
            " CAST(AVG(u.latency_ms) AS INTEGER) AS avg_latency_ms"
            " FROM usage_logs u LEFT JOIN api_keys k ON k.id = u.key_id"
            f" WHERE {' AND '.join(where)} GROUP BY {group_expr} ORDER BY {group_expr}",
            params,
        )

    async def recent_usage(self, limit: int = 50, key_id: int | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE key_id = ?", [key_id]) if key_id is not None else ("", [])
        return await self._all(f"SELECT * FROM usage_logs {where} ORDER BY id DESC LIMIT ?", [*params, limit])

    async def prune_usage(self, older_than_days: int) -> int:
        cur = await self.conn.execute("DELETE FROM usage_logs WHERE ts < ?", (days_ago(older_than_days),))
        await self.conn.commit()
        return cur.rowcount

    async def backup(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        await self.conn.execute("VACUUM INTO ?", (str(dest),))


@dataclass
class UsageRecord:
    key_id: int | None
    endpoint: str
    model: str | None
    status_code: int
    request_id: str | None = None
    stream: bool = False
    client_ip: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    ts: str = field(default_factory=utcnow)

    def set_usage(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        self.prompt_tokens = usage.get("prompt_tokens")
        self.completion_tokens = usage.get("completion_tokens")
        self.total_tokens = usage.get("total_tokens")

    def as_row(self) -> tuple:
        return (
            self.key_id, self.ts, self.request_id, self.endpoint, self.model, self.prompt_tokens,
            self.completion_tokens, self.total_tokens, self.latency_ms, self.status_code,
            int(self.stream), self.client_ip,
        )


class UsageWriter:
    """Batches usage rows off the request path so logging never slows a response."""

    def __init__(self, db: Database, batch_size: int = 200):
        self.db = db
        self.batch_size = batch_size
        self._queue: asyncio.Queue[UsageRecord | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="usage-writer")

    def submit(self, record: UsageRecord) -> None:
        self._queue.put_nowait(record)

    async def stop(self) -> None:
        if self._task is not None:
            self._queue.put_nowait(None)
            await self._task
            self._task = None

    async def _run(self) -> None:
        stopping = False
        while not stopping:
            item = await self._queue.get()
            batch: list[UsageRecord] = []
            if item is None:
                stopping = True
            else:
                batch.append(item)
            while len(batch) < self.batch_size and not self._queue.empty():
                nxt = self._queue.get_nowait()
                if nxt is None:
                    stopping = True
                else:
                    batch.append(nxt)
            if batch:
                try:
                    await self.db.insert_usage(batch)
                except Exception:
                    log.exception("failed to write %d usage rows", len(batch))
