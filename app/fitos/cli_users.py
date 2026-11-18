"""`fitos users list|add|password|disable|enable|set-chat|adopt-legacy`: accounts from the command line.

Passwords are read from stdin (never from argv, which other processes can see): `make user-add` and the
admin page are the friendly ways in.
"""

from __future__ import annotations

import getpass
import sys


def _read_password(args) -> str:
    if getattr(args, "password_stdin", False) or not sys.stdin.isatty():
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("password: ")
        if getpass.getpass("again: ") != pw:
            raise SystemExit("passwords don't match")
    from .core.accounts import MIN_PASSWORD, password_problem

    if password_problem(pw):
        raise SystemExit(f"the password needs at least {MIN_PASSWORD} characters")
    return pw


def _list(_args) -> int:
    from .core import accounts, tenancy

    accounts.init()
    for a in accounts.all_accounts():
        has_db = "db" if tenancy.db_path(a.username).is_file() else "NO DB"
        print(f"{a.username}\t{a.role}\t{'active' if a.active else 'disabled'}\t{has_db}\t"
              f"{'whatsapp' if a.wa_chat_id else 'web only'}\t{a.display_name}")
    return 0


def _add(args) -> int:
    from .core import accounts
    from .users import create_user

    accounts.init()
    pw = _read_password(args)
    try:
        acc = create_user(args.username, args.name or args.username, pw, role=args.role)
    except ValueError as e:
        raise SystemExit(f"users add: {e}") from e
    print(f"users add: {acc.username} ({acc.role}) with their own database")
    return 0


def _password(args) -> int:
    from .core import accounts

    accounts.init()
    if accounts.get(args.username) is None:
        raise SystemExit(f"no account {args.username!r}")
    accounts.set_password(args.username, _read_password(args))
    print(f"users password: {args.username} updated")
    return 0


def _active(args, value: bool) -> int:
    from .core import accounts

    accounts.init()
    try:
        accounts.update(args.username, active=value)
    except (KeyError, ValueError) as e:
        raise SystemExit(f"users: {e}") from e
    print(f"users: {args.username} {'enabled' if value else 'disabled (data kept)'}")
    return 0


def _set_chat(args) -> int:
    from .core import accounts

    accounts.init()
    try:
        accounts.update(args.username, wa_chat_id=args.chat_id or None)
    except KeyError as e:
        raise SystemExit(f"no account {args.username!r}") from e
    print(f"users set-chat: {args.username} → {args.chat_id or 'web only'}")
    return 0


def _adopt(args) -> int:
    from .users import adopt_legacy

    name = adopt_legacy(args.as_username or None, args.name or None)
    print(f"adopt-legacy: the old database now belongs to {name} (admin)" if name else "adopt-legacy: nothing to adopt")
    return 0


def register(sub) -> None:
    u = sub.add_parser("users", help="accounts: who can sign in").add_subparsers(dest="users_cmd", required=True)
    u.add_parser("list").set_defaults(fn=_list, scope="none")
    a = u.add_parser("add", help="new account with its own database (password from stdin or prompt)")
    a.add_argument("username")
    a.add_argument("--name", default="", help="display name")
    a.add_argument("--role", choices=("user", "admin"), default="user")
    a.add_argument("--password-stdin", action="store_true")
    a.set_defaults(fn=_add, scope="none")
    pw = u.add_parser("password", help="set a new password (stdin or prompt)")
    pw.add_argument("username")
    pw.add_argument("--password-stdin", action="store_true")
    pw.set_defaults(fn=_password, scope="none")
    for name, value in (("disable", False), ("enable", True)):
        d = u.add_parser(name)
        d.add_argument("username")
        d.set_defaults(fn=lambda args, v=value: _active(args, v), scope="none")
    c = u.add_parser("set-chat", help="WhatsApp group of an account (empty = web only)")
    c.add_argument("username")
    c.add_argument("chat_id", nargs="?", default="")
    c.set_defaults(fn=_set_chat, scope="none")
    ad = u.add_parser("adopt-legacy", help="make the single-person database (db/fitos.sqlite) the first admin's")
    ad.add_argument("--as", dest="as_username", default="", help="username (default: the one in the old users file)")
    ad.add_argument("--name", default="", help="display name")
    ad.set_defaults(fn=_adopt, scope="none")
