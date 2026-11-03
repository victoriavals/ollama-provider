"""SQLite storage: API keys, model allowlist, usage logs."""

import asyncio
import json
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

-- Prompt & response audit log (admin only, never written to text log files). ts = when the
-- request finished, so it grows with id; rows older than PROMPT_LOG_RETENTION_DAYS are pruned.
CREATE TABLE IF NOT EXISTS prompt_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    request_id      TEXT,
    key_id          INTEGER,
    key_name        TEXT,
    client_ip       TEXT,
    end_user        TEXT,
    endpoint        TEXT NOT NULL,
    model           TEXT,
    stream          INTEGER NOT NULL DEFAULT 0,
    status_code     INTEGER NOT NULL,
    total_tokens    INTEGER,
    prompt          TEXT NOT NULL,
    prompt_preview  TEXT NOT NULL,
    prompt_chars    INTEGER NOT NULL,
    response        TEXT
);
CREATE INDEX IF NOT EXISTS idx_prompt_ts ON prompt_logs(ts);
CREATE INDEX IF NOT EXISTS idx_prompt_key_ts ON prompt_logs(key_id, ts);
"""

# Columns added after the first release. connect() adds the missing ones with ALTER TABLE, so an
# existing database keeps its rows (additive only: nullable or with a default).
KEY_MIGRATIONS = (
    ("daily_token_limit", "INTEGER"),
    ("monthly_token_limit", "INTEGER"),
    ("expires_at", "TEXT"),
    ("allowed_models", "TEXT"),  # JSON array of model names; NULL = every enabled model
    ("priority", "INTEGER NOT NULL DEFAULT 0"),
    ("log_prompts", "INTEGER NOT NULL DEFAULT 1"),  # 0 = this key's prompts are not stored
)
KEY_COLUMNS = ("id, name, prefix, rpm_limit, max_concurrent, created_at, last_used_at, revoked_at, "
               + ", ".join(name for name, _ in KEY_MIGRATIONS))
KEY_UPDATABLE = {"name", "rpm_limit", "max_concurrent", *(name for name, _ in KEY_MIGRATIONS)}
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


def _encode_key_fields(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    if "allowed_models" in out and out["allowed_models"] is not None:
        out["allowed_models"] = json.dumps(sorted(set(out["allowed_models"])))
    if "priority" in out and out["priority"] is None:
        out["priority"] = 0  # NOT NULL column: null means "back to normal"
    if "log_prompts" in out:
        out["log_prompts"] = 0 if out["log_prompts"] is False else 1  # null means "back to the default"
    return out


def _decode_key(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is not None and row.get("allowed_models") is not None:
        row["allowed_models"] = json.loads(row["allowed_models"])
    return row


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        # Read-only second connection (own thread) for heavy admin reads such as prompt-log searches.
        # With WAL it runs in parallel with the main connection, so a slow dashboard query never
        # delays the auth / allowlist / usage queries of API requests.
        self._reader: aiosqlite.Connection | None = None

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
        # Deleted rows (pruned prompt logs) are overwritten where it costs no extra I/O.
        await self._conn.execute("PRAGMA secure_delete=FAST")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()
        self._reader = await aiosqlite.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        self._reader.row_factory = aiosqlite.Row
        await self._reader.execute("PRAGMA busy_timeout=5000")
        # Memory-mapped reads roughly halve the time of a full-range text search (measured at 50k rows).
        await self._reader.execute("PRAGMA mmap_size=268435456")

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA table_info(api_keys)") as cur:
            existing = {row["name"] for row in await cur.fetchall()}
        for name, ddl in KEY_MIGRATIONS:
            if name not in existing:
                await self.conn.execute(f"ALTER TABLE api_keys ADD COLUMN {name} {ddl}")
                log.info("database migrated: api_keys.%s added", name)

    async def close(self) -> None:
        if self._reader is not None:
            await self._reader.close()
            self._reader = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _read_all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        """Like _all, on the read-only connection (never blocks the main one)."""
        if self._reader is None:
            raise RuntimeError("database not connected")
        async with self._reader.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def _all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def _one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ---- API keys ----------------------------------------------------------
    # allowed_models is a list[str] | None in Python and JSON text in the table.

    async def create_key(
        self, name: str, key_hash: str, prefix: str, rpm_limit: int | None, max_concurrent: int | None,
        **policy: Any,
    ) -> dict[str, Any]:
        """policy: optional daily_token_limit, monthly_token_limit, expires_at, allowed_models, priority."""
        extra = _encode_key_fields({k: v for k, v in policy.items() if k in KEY_UPDATABLE and v is not None})
        cols = ["name", "key_hash", "prefix", "rpm_limit", "max_concurrent", "created_at", *extra]
        values = [name, key_hash, prefix, rpm_limit, max_concurrent, utcnow(), *extra.values()]
        cur = await self.conn.execute(
            f"INSERT INTO api_keys ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
        await self.conn.commit()
        return await self.get_key(cur.lastrowid)

    async def get_key(self, key_id: int) -> dict[str, Any] | None:
        return _decode_key(await self._one(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id = ?", (key_id,)))

    async def get_active_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        return _decode_key(await self._one(
            f"SELECT {KEY_COLUMNS} FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", (key_hash,)
        ))

    async def list_keys(self, include_revoked: bool = True) -> list[dict[str, Any]]:
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        return [_decode_key(r) for r in await self._all(f"SELECT {KEY_COLUMNS} FROM api_keys {where} ORDER BY id")]

    async def update_key(self, key_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = _encode_key_fields({k: v for k, v in fields.items() if k in KEY_UPDATABLE})
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

    async def token_usage_since(self, key_id: int, since: str) -> int:
        row = await self._one("SELECT COALESCE(SUM(total_tokens), 0) AS t FROM usage_logs"
                              " WHERE key_id = ? AND ts >= ?", (key_id, since))
        return row["t"]

    async def token_usage_by_key(self, day_start: str, month_start: str) -> dict[int, dict[str, int]]:
        """Tokens per key since the start of the local day and month (both UTC ISO strings)."""
        rows = await self._all(
            "SELECT key_id, COALESCE(SUM(CASE WHEN ts >= ? THEN total_tokens END), 0) AS day,"
            " COALESCE(SUM(total_tokens), 0) AS month FROM usage_logs"
            " WHERE ts >= ? AND key_id IS NOT NULL GROUP BY key_id", (day_start, month_start))
        return {r["key_id"]: {"day": r["day"], "month": r["month"]} for r in rows}

    async def recent_usage(self, limit: int = 50, key_id: int | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE key_id = ?", [key_id]) if key_id is not None else ("", [])
        return await self._all(f"SELECT * FROM usage_logs {where} ORDER BY id DESC LIMIT ?", [*params, limit])

    async def prune_usage(self, older_than_days: int) -> int:
        cur = await self.conn.execute("DELETE FROM usage_logs WHERE ts < ?", (days_ago(older_than_days),))
        await self.conn.commit()
        return cur.rowcount

    # ---- Prompt log ------------------------------------------------------------

    async def insert_prompts(self, records: list["PromptRecord"]) -> None:
        await self.conn.executemany(
            "INSERT INTO prompt_logs (ts, request_id, key_id, key_name, client_ip, end_user, endpoint, model, stream,"
            " status_code, total_tokens, prompt, prompt_preview, prompt_chars, response)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [r.as_row() for r in records],
        )
        await self.conn.commit()

    async def list_prompts(self, since: str, until: str | None = None, key_id: int | None = None,
                           model: str | None = None, failed: bool | None = None, query: str | None = None,
                           before: tuple[str, int] | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first, previews only (the full text is fetched per row with get_prompt).

        Always bounded by a time range (idx_prompt_ts) and LIMIT, so the dashboard stays fast.
        """
        where, params = ["ts >= ?"], [since]
        if until:
            where.append("ts < ?"); params.append(until)
        if key_id is not None:
            where.append("key_id = ?"); params.append(key_id)
        if model:
            where.append("model = ?"); params.append(model)
        if failed is not None:
            where.append("status_code >= 400" if failed else "status_code < 400")
        if before is not None:
            # Same as (ts < c OR (ts = c AND id < c_id)), written so SQLite keeps one idx_prompt_ts range
            # scan: the plain OR form turns into a multi-index OR plus a temp B-tree sort (~200 ms at 50k rows).
            where.append("ts <= ? AND (ts < ? OR id < ?)"); params += [before[0], before[0], before[1]]
        if query:
            def like(text: str) -> str:
                return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            # prompt is stored as JSON: search for the query the way json.dumps would write it
            # (quotes and newlines escaped), the other columns hold plain text.
            in_json = json.dumps(query, ensure_ascii=False)[1:-1]
            where.append("(prompt LIKE ? ESCAPE '\\' OR response LIKE ? ESCAPE '\\' OR end_user LIKE ? ESCAPE '\\')")
            params += [like(in_json), like(query), like(query)]
        sql = ("SELECT id, ts, request_id, key_id, key_name, client_ip, end_user, endpoint, model, stream, status_code,"
               " total_tokens, prompt_preview, prompt_chars, substr(response, 1, 200) AS response_preview,"
               " length(response) AS response_chars"
               f" FROM prompt_logs WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT ?")
        return await self._read_all(sql, (*params, limit))

    async def get_prompt(self, prompt_id: int) -> dict[str, Any] | None:
        rows = await self._read_all("SELECT * FROM prompt_logs WHERE id = ?", (prompt_id,))
        if not rows:
            return None
        row = rows[0]
        row["prompt"] = json.loads(row["prompt"])
        return row

    async def prune_prompts(self, older_than_days: int) -> int:
        cur = await self.conn.execute("DELETE FROM prompt_logs WHERE ts < ?", (days_ago(older_than_days),))
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


@dataclass
class PromptRecord:
    """One prompt-log row; built in apps/public.py, completed and submitted by proxy.Finalizer."""
    key_id: int
    key_name: str
    endpoint: str
    model: str | None
    prompt: list[dict[str, str]]  # [{"role", "content"}] already capped and masked (gateway/promptlog.py)
    prompt_preview: str
    prompt_chars: int
    request_id: str | None = None
    client_ip: str | None = None
    end_user: str | None = None
    stream: bool = False
    status_code: int = 200
    total_tokens: int | None = None
    response: str | None = None
    ts: str = field(default_factory=utcnow)

    def as_row(self) -> tuple:
        return (
            self.ts, self.request_id, self.key_id, self.key_name, self.client_ip, self.end_user, self.endpoint,
            self.model, int(self.stream), self.status_code, self.total_tokens,
            json.dumps(self.prompt, ensure_ascii=False), self.prompt_preview, self.prompt_chars, self.response,
        )


class UsageWriter:
    """Batches usage and prompt-log rows off the request path so logging never slows a response."""

    def __init__(self, db: Database, batch_size: int = 200):
        self.db = db
        self.batch_size = batch_size
        self._queue: asyncio.Queue[UsageRecord | PromptRecord | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="usage-writer")

    def submit(self, record: "UsageRecord | PromptRecord") -> None:
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
            batch: list[UsageRecord | PromptRecord] = []
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
            usage = [r for r in batch if isinstance(r, UsageRecord)]
            prompts = [r for r in batch if isinstance(r, PromptRecord)]
            if usage:
                try:
                    await self.db.insert_usage(usage)
                except Exception:
                    log.exception("failed to write %d usage rows", len(usage))
            if prompts:
                try:
                    await self.db.insert_prompts(prompts)
                except Exception:
                    log.exception("failed to write %d prompt-log rows", len(prompts))
