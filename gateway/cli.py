"""Local management CLI (works directly on the SQLite DB, no admin key needed).

    python -m gateway.cli create-key --name client-a [--rpm 60] [--max-concurrent 2]
    python -m gateway.cli list-keys
    python -m gateway.cli revoke-key 3
    python -m gateway.cli list-models | enable-model NAME | disable-model NAME
    python -m gateway.cli usage [--days 7] [--group-by day|key|model]
    python -m gateway.cli gen-admin-key

Changes to keys take effect in a running gateway within 30 seconds (auth cache TTL).
"""

import argparse
import asyncio
import json
import logging
import secrets

from .auth import display_prefix, generate_key, hash_key
from .config import get_settings
from .db import Database, days_ago
from .logs import TEXT_FORMAT, DailyFileHandler

audit = logging.getLogger("gateway.cli")


def _setup_audit_log() -> None:
    """Record CLI changes in the same gateway-*.log the service writes."""
    settings = get_settings()
    try:
        handler = DailyFileHandler(settings.log_dir, "gateway", settings.log_retention_days)
    except OSError:
        return
    handler.setFormatter(logging.Formatter(TEXT_FORMAT))
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    audit.propagate = False


def _table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c) if r.get(c) is not None else "-")) for r in rows)) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c) if r.get(c) is not None else "-").ljust(widths[c]) for c in columns))


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    try:
        await db.seed_models(settings.seed_model_list)
        if args.cmd == "create-key":
            key = generate_key()
            row = await db.create_key(args.name, hash_key(key), display_prefix(key), args.rpm, args.max_concurrent)
            audit.info("key created via CLI id=%s name=%r prefix=%s rpm=%s max_concurrent=%s",
                       row["id"], row["name"], row["prefix"], row["rpm_limit"], row["max_concurrent"])
            print(f"Created key id={row['id']} name={row['name']}")
            print(f"API key (shown once, store it now): {key}")
        elif args.cmd == "list-keys":
            _table(await db.list_keys(), ["id", "name", "prefix", "rpm_limit", "max_concurrent",
                                          "created_at", "last_used_at", "revoked_at"])
        elif args.cmd == "revoke-key":
            revoked = await db.revoke_key(args.id)
            if revoked:
                audit.info("key revoked via CLI id=%s", args.id)
            print("revoked" if revoked else "not found or already revoked")
        elif args.cmd == "list-models":
            _table(await db.list_models(), ["name", "enabled", "added_at"])
        elif args.cmd in ("enable-model", "disable-model"):
            await db.set_model_enabled(args.name, args.cmd == "enable-model")
            state = "enabled" if args.cmd == "enable-model" else "disabled"
            audit.info("model %s %s via CLI", args.name, state)
            print(f"{args.name}: {state}")
        elif args.cmd == "usage":
            rows = await db.usage_summary(days_ago(args.days), group_by=args.group_by)
            cols = [args.group_by] + (["key_name"] if args.group_by == "key" else []) + [
                "requests", "errors", "prompt_tokens", "completion_tokens", "total_tokens", "avg_latency_ms"]
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                _table(rows, cols)
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m gateway.cli", description="ollama-provider management")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-key", help="create an API key")
    p.add_argument("--name", required=True)
    p.add_argument("--rpm", type=int, default=None, help="requests per minute (default from .env)")
    p.add_argument("--max-concurrent", type=int, default=None, help="in-flight requests (default from .env)")

    sub.add_parser("list-keys", help="list API keys")
    p = sub.add_parser("revoke-key", help="revoke an API key")
    p.add_argument("id", type=int)

    sub.add_parser("list-models", help="list the model allowlist")
    for cmd in ("enable-model", "disable-model"):
        p = sub.add_parser(cmd, help=f"{cmd.split('-')[0]} a model in the allowlist")
        p.add_argument("name")

    p = sub.add_parser("usage", help="usage summary")
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--group-by", choices=["day", "key", "model"], default="day")
    p.add_argument("--json", action="store_true")

    sub.add_parser("gen-admin-key", help="print a random value for ADMIN_KEY")

    args = parser.parse_args()
    if args.cmd == "gen-admin-key":
        print("adm-" + secrets.token_urlsafe(32))
        return
    _setup_audit_log()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
