"""Host-side credential recovery for sole-admin lockout.

Run inside the PiHerder web container (has DATABASE_URL + app code)::

    docker compose exec -T web python -m app.cli.recover_admin list
    docker compose exec -T web python -m app.cli.recover_admin reset-access \\
        --email you@example.com --generate --yes

Or from the compose project root with the helper script::

    ./scripts/recover-admin.sh list
    ./scripts/recover-admin.sh reset-access --email you@example.com --generate --yes

Prefer the UI **Users → Recover…** when another admin can still sign in.
This CLI is for host operators who have Docker access but no working session.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime
from typing import Callable, Optional

from sqlmodel import Session, select

from app.database import engine
from app.models import AuditLog, User
from app.services.password_policy import generate_password, policy_rules_text, validate_password
from app.services.user_admin import (
    admin_clear_2fa_only,
    admin_reset_password,
    admin_sign_out_sessions,
    detach_and_delete_user,
)


ACTIONS_NEEDING_PASSWORD = frozenset({"reset-password", "reset-access"})
DESTRUCTIVE_ACTIONS = frozenset(
    {"reset-password", "clear-2fa", "reset-access", "sign-out", "delete-user"}
)


def list_users(session: Session) -> list[User]:
    return list(session.exec(select(User).order_by(User.id)).all())


def find_user(session: Session, email: str) -> Optional[User]:
    email_n = (email or "").strip().lower()
    if not email_n:
        return None
    # Case-insensitive match (emails stored as typed at register)
    for u in session.exec(select(User)).all():
        if (u.email or "").strip().lower() == email_n:
            return u
    return None


def _audit(
    session: Session,
    *,
    action: str,
    target: User,
    details: str,
    status: str = "success",
) -> None:
    """Record host recovery in Audit (no interactive actor; user_id = target)."""
    session.add(
        AuditLog(
            user_id=int(target.id) if target.id is not None else None,
            action=action,
            status=status,
            details=details,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            client_ip=None,
        )
    )


def resolve_password(
    *,
    password: Optional[str],
    generate: bool,
    prompt: bool,
) -> str:
    if generate and password:
        raise SystemExit("Use either --password or --generate, not both.")
    if generate:
        return generate_password(16)
    if password:
        plain = password
    elif prompt and sys.stdin.isatty():
        a = getpass.getpass("New temporary password: ")
        b = getpass.getpass("Confirm password: ")
        if a != b:
            raise SystemExit("Passwords do not match.")
        plain = a
    else:
        raise SystemExit(
            "Password required: pass --password PATH, --generate, or run interactively."
        )
    ok, err = validate_password(plain)
    if not ok:
        raise SystemExit(f"Password policy: {err}\n({policy_rules_text()})")
    return plain


def cmd_list(session: Session, _args: argparse.Namespace) -> int:
    users = list_users(session)
    if not users:
        print("No users in database. Open the app URL and Register the first admin.")
        return 0
    print(f"{'ID':>4}  {'ROLE':<10}  {'ACTIVE':<6}  {'2FA':<4}  {'MCPW':<5}  EMAIL")
    for u in users:
        print(
            f"{int(u.id):>4}  "
            f"{(u.role or '?'):<10}  "
            f"{'yes' if u.is_active else 'no':<6}  "
            f"{'yes' if u.totp_enabled else 'no':<4}  "
            f"{'yes' if u.must_change_password else 'no':<5}  "
            f"{u.email}"
        )
    print(
        "\nMCPW = must_change_password. "
        "Use reset-access if you lost password and 2FA."
    )
    return 0


def cmd_reset_password(session: Session, args: argparse.Namespace) -> int:
    user = _require_user(session, args.email)
    plain = resolve_password(
        password=args.password, generate=args.generate, prompt=True
    )
    _confirm(args, f"Set temporary password for {user.email} and revoke sessions?")
    admin_reset_password(session, user, plain, clear_2fa=False)
    _audit(
        session,
        action="host_password_reset",
        target=user,
        details=f"Host CLI: temporary password set for {user.email}; sessions revoked",
    )
    session.commit()
    _print_credentials(user.email, plain, kind="reset-password")
    return 0


def cmd_clear_2fa(session: Session, args: argparse.Namespace) -> int:
    user = _require_user(session, args.email)
    _confirm(args, f"Clear 2FA + trusted devices for {user.email} and revoke sessions?")
    admin_clear_2fa_only(session, user)
    _audit(
        session,
        action="host_2fa_cleared",
        target=user,
        details=f"Host CLI: 2FA cleared for {user.email}; sessions revoked",
    )
    session.commit()
    print(f"OK: 2FA cleared for {user.email}. Password unchanged. Sessions revoked.")
    return 0


def cmd_reset_access(session: Session, args: argparse.Namespace) -> int:
    user = _require_user(session, args.email)
    plain = resolve_password(
        password=args.password, generate=args.generate, prompt=True
    )
    _confirm(
        args,
        f"FULL reset for {user.email}: new temp password + clear 2FA + revoke sessions?",
    )
    admin_reset_password(session, user, plain, clear_2fa=True)
    _audit(
        session,
        action="host_access_reset",
        target=user,
        details=(
            f"Host CLI: full access reset for {user.email} "
            f"(password + 2FA + sessions)"
        ),
    )
    session.commit()
    _print_credentials(user.email, plain, kind="reset-access")
    return 0


def cmd_sign_out(session: Session, args: argparse.Namespace) -> int:
    user = _require_user(session, args.email)
    _confirm(args, f"Revoke all sessions + trusted devices for {user.email}?")
    v = admin_sign_out_sessions(session, user)
    _audit(
        session,
        action="host_sessions_revoked",
        target=user,
        details=f"Host CLI: sessions revoked for {user.email} (session_version={v})",
    )
    session.commit()
    print(f"OK: sessions revoked for {user.email} (session_version={v}).")
    return 0


def cmd_delete_user(session: Session, args: argparse.Namespace) -> int:
    user = _require_user(session, args.email)
    remaining = len(list_users(session)) - 1
    note = (
        "After this delete, the database has no users — "
        "open Register to create a new first admin."
        if remaining <= 0
        else f"{remaining} other user(s) remain; registration stays closed."
    )
    _confirm(
        args,
        f"DELETE user {user.email} (id={user.id})? {note}",
        force_yes=True,
    )
    email = detach_and_delete_user(session, user)
    # Audit without user_id (row gone) — store email in details only
    session.add(
        AuditLog(
            user_id=None,
            action="host_user_deleted",
            status="success",
            details=f"Host CLI: deleted user {email}. {note}",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
    )
    session.commit()
    print(f"OK: deleted {email}. {note}")
    return 0


def _require_user(session: Session, email: str) -> User:
    user = find_user(session, email)
    if not user:
        raise SystemExit(
            f"No user with email {email!r}. Run: python -m app.cli.recover_admin list"
        )
    return user


def _confirm(args: argparse.Namespace, message: str, *, force_yes: bool = False) -> None:
    if getattr(args, "yes", False):
        return
    if force_yes or not sys.stdin.isatty():
        raise SystemExit(
            f"Refusing without --yes (non-interactive or high-impact).\n  {message}"
        )
    print(message)
    ans = input("Type YES to continue: ").strip()
    if ans != "YES":
        raise SystemExit("Aborted.")


def _print_credentials(email: str, password: str, *, kind: str) -> None:
    print("─" * 48)
    print(f"OK ({kind})")
    print(f"  Email:             {email}")
    print(f"  Temporary password: {password}")
    print("  Next: sign in → change password (forced).")
    print("  If force-2FA is on, enrol authenticator after password change.")
    print("─" * 48)
    print("Copy the temporary password now; it is not stored in plain text.")


COMMANDS: dict[str, Callable[[Session, argparse.Namespace], int]] = {
    "list": cmd_list,
    "reset-password": cmd_reset_password,
    "clear-2fa": cmd_clear_2fa,
    "reset-access": cmd_reset_access,
    "sign-out": cmd_sign_out,
    "delete-user": cmd_delete_user,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.cli.recover_admin",
        description=(
            "Host-side credential recovery (Docker exec). "
            "Use when no admin can sign in to the Users UI."
        ),
        epilog=f"Password policy: {policy_rules_text()}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List users (id, role, 2FA, email)")

    def _email(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--email", required=True, help="Target user email")

    def _yes(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation (required for non-interactive / delete-user)",
        )

    def _pw(sp: argparse.ArgumentParser) -> None:
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--password", help="Temporary password (meets policy)")
        g.add_argument(
            "--generate",
            action="store_true",
            help="Generate a strong temporary password and print it",
        )

    sp = sub.add_parser(
        "reset-password",
        help="Temp password + must_change_password + revoke sessions (keep 2FA)",
    )
    _email(sp)
    _pw(sp)
    _yes(sp)

    sp = sub.add_parser(
        "clear-2fa",
        help="Wipe TOTP + backup codes + trusted devices + revoke sessions",
    )
    _email(sp)
    _yes(sp)

    sp = sub.add_parser(
        "reset-access",
        help="Full lockout recovery: temp password + clear 2FA + revoke sessions",
    )
    _email(sp)
    _pw(sp)
    _yes(sp)

    sp = sub.add_parser(
        "sign-out",
        help="Revoke all JWTs (session_version) + trusted devices only",
    )
    _email(sp)
    _yes(sp)

    sp = sub.add_parser(
        "delete-user",
        help="Delete a user (if last user, Register re-opens for first admin)",
    )
    _email(sp)
    _yes(sp)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = COMMANDS.get(args.command)
    if not cmd:
        parser.error(f"unknown command: {args.command}")
    try:
        with Session(engine) as session:
            return cmd(session, args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
