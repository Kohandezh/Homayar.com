"""Operator CLI: ``python -m backend.operator_access <command>`` (HOM-129).

  disable-user --email E --reason R     refuse every token, key and login of the user
  enable-user --email E                 allow new logins again
  revoke-sessions --email E --reason R  end every session of the user
  mfa-reset --email E --reason R        remove the user's TOTP factor and recovery codes
  break-glass designate --email E       mark a local password account as break-glass
  break-glass undesignate --email E     make it an ordinary account again
  break-glass open --email E --minutes M --reason R
                                        open a time-boxed window for that account
  break-glass close --email E           close the window and end its sessions
  break-glass status                    designated accounts and open windows

Exit 0 on success (one line on stdout), 2 on a refusal (its code on stderr).
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.services import privileged_access_service as access
from backend.services.privileged_access_service import PrivilegedAccessError


def _actor_label() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.operator_access",
        description="Account cut-off and break-glass access, from the server host",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, needs_reason in (
        ("disable-user", True),
        ("enable-user", False),
        ("revoke-sessions", True),
        ("mfa-reset", True),
    ):
        command = commands.add_parser(name)
        command.add_argument("--email", required=True)
        if needs_reason:
            command.add_argument("--reason", required=True)

    break_glass = commands.add_parser("break-glass").add_subparsers(dest="action", required=True)
    for name in ("designate", "undesignate", "close"):
        break_glass.add_parser(name).add_argument("--email", required=True)
    opened = break_glass.add_parser("open")
    opened.add_argument("--email", required=True)
    opened.add_argument("--minutes", required=True, type=int)
    opened.add_argument("--reason", required=True)
    break_glass.add_parser("status")
    return parser


def _run(db: Session, args: argparse.Namespace, settings: Settings, actor: str) -> list[str]:
    if args.command == "disable-user":
        user = access.disable_user(
            db, email=args.email, reason=args.reason, actor_label=actor, settings=settings
        )
        return [f"disabled user={user.id}"]
    if args.command == "enable-user":
        user = access.enable_user(db, email=args.email, actor_label=actor, settings=settings)
        return [f"enabled user={user.id}"]
    if args.command == "revoke-sessions":
        count = access.revoke_user_sessions(
            db, email=args.email, reason=args.reason, actor_label=actor, settings=settings
        )
        return [f"revoked sessions={count}"]
    if args.command == "mfa-reset":
        user = access.reset_user_mfa(
            db, email=args.email, reason=args.reason, actor_label=actor, settings=settings
        )
        return [f"mfa reset user={user.id}"]

    if args.action == "designate":
        user = access.designate_break_glass(
            db, email=args.email, actor_label=actor, settings=settings
        )
        return [f"designated user={user.id}"]
    if args.action == "undesignate":
        user = access.undesignate_break_glass(
            db, email=args.email, actor_label=actor, settings=settings
        )
        return [f"undesignated user={user.id}"]
    if args.action == "open":
        window = access.open_break_glass_window(
            db,
            email=args.email,
            minutes=args.minutes,
            reason=args.reason,
            actor_label=actor,
            settings=settings,
        )
        return [f"opened window={window.id} expires_at={window.expires_at.isoformat()}"]
    if args.action == "close":
        window = access.close_break_glass_window(
            db, email=args.email, actor_label=actor, settings=settings
        )
        return [f"closed window={window.id if window is not None else '-'}"]

    designated, windows = access.list_break_glass_status(db, settings=settings)
    lines = [f"break_glass_enabled={str(settings.break_glass_enabled).lower()}"]
    lines += [f"designated user={user.id} email={user.email}" for user in designated]
    lines += [
        f"open window={window.id} email={user.email} expires_at={window.expires_at.isoformat()} "
        f"opened_by={window.opened_by}"
        for user, window in windows
    ]
    return lines


@contextmanager
def _logs_on_stderr() -> Iterator[None]:
    """Every log handler that writes to stdout writes to stderr meanwhile, so stdout holds
    only the command's result.  ``setup_logging`` points the root handler at the real
    stdout (``sys.__stdout__``); any other logger's handler is moved the same way."""
    loggers = [logging.getLogger()] + [
        item
        for item in logging.Logger.manager.loggerDict.values()
        if isinstance(item, logging.Logger)
    ]
    moved: list[tuple[logging.StreamHandler, object]] = []
    for item in loggers:
        for handler in item.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream in (
                sys.stdout,
                sys.__stdout__,
            ):
                moved.append((handler, handler.setStream(sys.__stderr__)))
    try:
        yield
    finally:
        for handler, stream in moved:
            handler.setStream(stream)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with _logs_on_stderr():
        settings = get_settings()
        with get_session_factory()() as db:
            try:
                lines = _run(db, args, settings, _actor_label())
                db.commit()
            except PrivilegedAccessError as exc:
                db.rollback()
                print(exc.code, file=sys.stderr)
                return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    sys.exit(main())
