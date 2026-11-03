"""Local management CLI (works directly on the SQLite DB, no admin key needed).

    python -m gateway.cli create-key --name client-a [--rpm 60] [--max-concurrent 2]
        [--daily-tokens 100000] [--monthly-tokens 2000000] [--expires 2026-12-31]
        [--models gemma3:4b,qwen3.5:4b] [--priority 1]
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
from .quota import parse_expiry

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
            policy = {"daily_token_limit": args.daily_tokens, "monthly_token_limit": args.monthly_tokens,
                      "priority": args.priority, "log_prompts": False if args.no_prompt_log else None,
                      "expires_at": parse_expiry(args.expires) if args.expires else None,
                      "allowed_models": [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None}
            if policy["allowed_models"] is not None:
                unknown = set(policy["allowed_models"]) - {m["name"] for m in await db.list_models()}
                if unknown or not policy["allowed_models"]:
                    raise SystemExit(f"unknown or empty --models: {', '.join(sorted(unknown)) or '(empty)'}")
            key = generate_key()
            row = await db.create_key(args.name, hash_key(key), display_prefix(key), args.rpm, args.max_concurrent,
                                      **policy)
            audit.info("key created via CLI id=%s name=%r prefix=%s rpm=%s max_concurrent=%s policy=%s",
                       row["id"], row["name"], row["prefix"], row["rpm_limit"], row["max_concurrent"],
                       {k: v for k, v in policy.items() if v})
            print(f"Created key id={row['id']} name={row['name']}")
            print(f"API key (shown once, store it now): {key}")
        elif args.cmd == "list-keys":
            rows = [{**r, "allowed_models": ",".join(r["allowed_models"]) if r["allowed_models"] else None}
                    for r in await db.list_keys()]
            _table(rows, ["id", "name", "prefix", "rpm_limit", "max_concurrent", "daily_token_limit",
                          "monthly_token_limit", "expires_at", "allowed_models", "priority", "log_prompts",
                          "last_used_at", "revoked_at"])
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
    p.add_argument("--daily-tokens", type=int, default=None, help="token quota per local day (default: none)")
    p.add_argument("--monthly-tokens", type=int, default=None, help="token quota per local month (default: none)")
    p.add_argument("--expires", default=None, help="last valid local day YYYY-MM-DD, or an ISO datetime")
    p.add_argument("--models", default=None, help="comma-separated models this key may use (default: all)")
    p.add_argument("--priority", type=int, default=0, help="queue priority 0-9 (default 0)")
    p.add_argument("--no-prompt-log", action="store_true", help="do not store this key's prompts and answers")

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
    if args.cmd == "create-key":
        if args.expires:
            try:
                parse_expiry(args.expires)
            except ValueError:
                parser.error("--expires must be a date YYYY-MM-DD (or ISO datetime) between 2000 and 2100")
        if not 0 <= args.priority <= 9:
            parser.error("--priority must be between 0 and 9")
        for flag in ("rpm", "max_concurrent", "daily_tokens", "monthly_tokens"):
            if getattr(args, flag) is not None and getattr(args, flag) < 1:
                parser.error(f"--{flag.replace('_', '-')} must be at least 1")
    if args.cmd == "gen-admin-key":
        print("adm-" + secrets.token_urlsafe(32))
        return
    _setup_audit_log()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
