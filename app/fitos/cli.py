"""Comando `fitos`: operación dentro del contenedor (y en local con el venv)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


def cmd_migrate(_args) -> int:
    from .users import migrate_all

    done = migrate_all()
    print(f"migrate: ok ({', '.join(done) or 'no accounts yet'})")
    return 0


def _backup_file(src_path: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(out))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    out.chmod(0o600)


def cmd_db_backup(args) -> int:
    """Consistent copies with SQLite's online backup API (works with the app running): `accounts.sqlite` and
    `users/<name>/fitos.sqlite` of every account, under the directory `out`."""
    from .core import accounts, tenancy

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    accounts.init()
    acc_path = accounts.path()
    if acc_path and acc_path.is_file():
        _backup_file(acc_path, out / "accounts.sqlite")
    n = 0
    for acc in accounts.all_accounts():
        p = tenancy.db_path(acc.username)
        if p.is_file():
            _backup_file(p, out / "users" / acc.username / "fitos.sqlite")
            n += 1
    print(f"db-backup: {out} ({n} users)")
    return 0


def cmd_wa_groups(args) -> int:
    from .wa.client import get_client

    groups = asyncio.run(get_client().groups())
    for g in groups:
        if not args.match or args.match.lower() in g["name"].lower():
            print(f"{g['id']}\t{g['name']}")
    return 0


def cmd_wa_session(_args) -> int:
    from .wa.client import get_client

    info = asyncio.run(get_client().session_info())
    cfg = info.get("config") or {}
    hooks = [{"url": h.get("url"), "events": h.get("events")} for h in cfg.get("webhooks") or [] if isinstance(h, dict)]
    print(json.dumps({"status": info.get("status"), "webhooks": hooks}, indent=1))
    return 0


def _resolve_user(args) -> str:
    """--user, else FITOS_USER, else the only active account. Never a guess between several people."""
    import os

    from .core import accounts

    accounts.init()
    name = (args.user or os.environ.get("FITOS_USER") or "").strip().lower()
    if name:
        acc = accounts.get(name)
        if acc is None:
            raise SystemExit(f"fitos: no account {name!r} (fitos users list)")
        return acc.username
    active = accounts.active_usernames()
    if len(active) == 1:
        return active[0]
    raise SystemExit("fitos: whose data? pass --user <name> (fitos users list)" if active else
                     "fitos: no accounts yet (fitos users add, or fitos users adopt-legacy)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fitos")
    p.add_argument("--user", default="", help="whose data (default: FITOS_USER, or the only account)")
    sub = p.add_subparsers(dest="cmd", required=True)
    # scope: "user" runs as one person (--user); "each" runs once per account unless --user; "none" no data
    sub.add_parser("migrate", help="accounts and every person's database").set_defaults(fn=cmd_migrate,
                                                                                        scope="none")
    b = sub.add_parser("db-backup", help="online copy of every database into a directory")
    b.add_argument("out")
    b.set_defaults(fn=cmd_db_backup, scope="none")
    wa = sub.add_parser("wa").add_subparsers(dest="wa_cmd", required=True)
    g = wa.add_parser("groups")
    g.add_argument("--match", default="")
    g.set_defaults(fn=cmd_wa_groups, scope="none")
    wa.add_parser("session").set_defaults(fn=cmd_wa_session, scope="none")

    from . import cli_domain, cli_users  # domain subcommands (catalog, labs, profile…) and accounts

    cli_users.register(sub)
    cli_domain.register(sub)
    args = p.parse_args(argv)
    scope = getattr(args, "scope", "user")
    if scope == "none":
        return args.fn(args)
    from .core import accounts, tenancy

    if scope == "each" and not args.user:
        accounts.init()
        rc = 0
        for name in accounts.active_usernames():
            print(f"── {name}")
            with tenancy.as_user(name):
                rc = args.fn(args) or rc
        return rc
    with tenancy.as_user(_resolve_user(args)):
        return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
