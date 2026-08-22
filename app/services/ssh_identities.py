"""Host SSH identities (v1.3 W-id): fleet + optional privileged.

Invariants this freeze:
  * Exactly one fleet row per server (created on ensure)
  * At most one privileged row
  * Fleet cannot be deleted or disabled
  * Privileged is console + Files (never jobs)
  * Server.ssh_username / ssh_private_key_encrypted / ssh_public_key dual-write the fleet cache
"""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ..models import Server, ServerSshIdentity
from ..security import encryption
from . import ssh as ssh_service

ROLE_FLEET = "fleet"
ROLE_PRIVILEGED = "privileged"
ROLES = (ROLE_FLEET, ROLE_PRIVILEGED)

LABEL_MAX = 32
DEFAULT_LABELS = {ROLE_FLEET: "Fleet", ROLE_PRIVILEGED: "Privileged"}
DEFAULT_PRIVILEGED_USER = "piherder-admin"


class IdentityError(Exception):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.code = code


def _now() -> datetime:
    return datetime.utcnow()


def fingerprint_public(public_key: Optional[str]) -> Optional[str]:
    """OpenSSH SHA256 fingerprint of a user public key (not the host key)."""
    if not public_key or not str(public_key).strip():
        return None
    parts = str(public_key).strip().split()
    if len(parts) < 2 or not parts[0].startswith("ssh-"):
        return None
    try:
        raw = base64.b64decode(parts[1], validate=False)
        if not raw:
            return None
        digest = hashlib.sha256(raw).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
    except Exception:
        return None


def _clean_label(raw: Optional[str], role: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "").strip())[:LABEL_MAX]
    return s or DEFAULT_LABELS.get(role, role.title())


def _clean_username(raw: Optional[str], *, default: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]", "", (raw or "").strip())
    return s or default


def public_view(ident: ServerSshIdentity) -> dict[str, Any]:
    """Safe dict for templates / JSON — never includes PEM or ciphertext."""
    from .ssh_onboarding import is_real_public_key

    pub = ident.public_key if is_real_public_key(ident.public_key) else None
    return {
        "id": ident.id,
        "role": ident.role,
        "label": ident.label or DEFAULT_LABELS.get(ident.role, ident.role),
        "username": ident.username,
        "fingerprint": ident.key_fingerprint,
        "has_key": bool(ident.private_key_encrypted),
        "enabled": bool(ident.enabled),
        "has_real_public_key": bool(pub),
        "public_key": pub,
    }


def get_by_id(
    session: Session, server_id: int, identity_id: int
) -> Optional[ServerSshIdentity]:
    row = session.get(ServerSshIdentity, int(identity_id))
    if not row or int(row.server_id) != int(server_id):
        return None
    return row


def get_by_role(
    session: Session, server_id: int, role: str
) -> Optional[ServerSshIdentity]:
    return session.exec(
        select(ServerSshIdentity).where(
            ServerSshIdentity.server_id == int(server_id),
            ServerSshIdentity.role == role,
        )
    ).first()


def list_for_server(session: Session, server_id: int) -> list[ServerSshIdentity]:
    rows = list(
        session.exec(
            select(ServerSshIdentity)
            .where(ServerSshIdentity.server_id == int(server_id))
            .order_by(ServerSshIdentity.role)
        ).all()
    )
    # fleet first
    rows.sort(key=lambda r: 0 if r.role == ROLE_FLEET else 1)
    return rows


def apply_material(
    ident: ServerSshIdentity,
    *,
    public_key: Optional[str] = None,
    private_plain: Optional[str] = None,
    private_encrypted: Optional[str] = None,
) -> None:
    if private_plain:
        ident.private_key_encrypted = encryption.encrypt_str(private_plain)
        if not public_key:
            from .ssh_onboarding import public_key_from_private

            try:
                public_key = public_key_from_private(
                    private_plain, comment=f"piherder-{ident.role}"
                )
            except Exception:
                public_key = ident.public_key
    elif private_encrypted is not None:
        ident.private_key_encrypted = private_encrypted
    if public_key is not None:
        ident.public_key = public_key
    ident.key_fingerprint = fingerprint_public(ident.public_key)
    ident.updated_at = _now()


def apply_fleet_to_server(server: Server, ident: ServerSshIdentity) -> None:
    """Dual-write fleet cache onto Server columns."""
    if ident.role != ROLE_FLEET:
        return
    server.ssh_username = ident.username
    server.ssh_private_key_encrypted = ident.private_key_encrypted
    server.ssh_public_key = ident.public_key


def ensure_fleet_identity(session: Session, server: Server) -> ServerSshIdentity:
    """Create or sync the fleet row from Server.ssh_* (source of truth for jobs)."""
    if not getattr(server, "id", None):
        session.flush()
    username = (server.ssh_username or "pi").strip() or "pi"
    row = get_by_role(session, int(server.id), ROLE_FLEET)
    if row is None:
        row = ServerSshIdentity(
            server_id=int(server.id),
            role=ROLE_FLEET,
            label=DEFAULT_LABELS[ROLE_FLEET],
            username=username,
            private_key_encrypted=server.ssh_private_key_encrypted,
            public_key=server.ssh_public_key,
            key_fingerprint=fingerprint_public(server.ssh_public_key),
            enabled=True,
        )
        session.add(row)
        session.flush()
        return row
    dirty = False
    if row.username != username:
        row.username = username
        dirty = True
    if (row.private_key_encrypted or None) != (server.ssh_private_key_encrypted or None):
        row.private_key_encrypted = server.ssh_private_key_encrypted
        dirty = True
    if (row.public_key or None) != (server.ssh_public_key or None):
        row.public_key = server.ssh_public_key
        row.key_fingerprint = fingerprint_public(server.ssh_public_key)
        dirty = True
    if not row.enabled:
        row.enabled = True
        dirty = True
    if dirty:
        row.updated_at = _now()
        session.add(row)
    return row


def add_privileged(
    session: Session,
    server: Server,
    *,
    username: str,
    label: Optional[str] = None,
    private_plain: Optional[str] = None,
    generate: bool = False,
) -> ServerSshIdentity:
    if get_by_role(session, int(server.id), ROLE_PRIVILEGED):
        raise IdentityError("This host already has a privileged identity", "exists")
    user = _clean_username(username, default=DEFAULT_PRIVILEGED_USER)
    if user.lower() in {"daemon", "nobody"}:
        raise IdentityError("That username is not allowed", "username")
    pub = None
    priv = None
    if generate:
        comment = f"piherder-privileged@{server.hostname or server.name or 'host'}"
        pub, priv = ssh_service.generate_keypair(comment=comment)
    elif private_plain and private_plain.strip():
        priv = private_plain.strip()
        from .ssh_onboarding import public_key_from_private

        try:
            pub = public_key_from_private(
                priv, comment=f"piherder-privileged@{server.hostname or server.name}"
            )
        except Exception as e:
            raise IdentityError(f"Could not parse private key: {e}", "bad_key") from e
    else:
        raise IdentityError("Generate a keypair or paste a private key", "need_key")
    row = ServerSshIdentity(
        server_id=int(server.id),
        role=ROLE_PRIVILEGED,
        label=_clean_label(label, ROLE_PRIVILEGED),
        username=user,
        enabled=True,
    )
    apply_material(row, public_key=pub, private_plain=priv)
    session.add(row)
    session.flush()
    return row


def update_privileged_username(
    session: Session, ident: ServerSshIdentity, username: str
) -> ServerSshIdentity:
    if ident.role != ROLE_PRIVILEGED:
        raise IdentityError("Only the privileged identity username is edited here", "role")
    user = _clean_username(username, default=ident.username)
    if not user:
        raise IdentityError("Username required", "username")
    ident.username = user
    ident.updated_at = _now()
    session.add(ident)
    return ident


def rotate_identity_material(
    ident: ServerSshIdentity,
    *,
    public_key: str,
    private_plain: str,
) -> None:
    apply_material(ident, public_key=public_key, private_plain=private_plain)


def remove_privileged(session: Session, ident: ServerSshIdentity) -> None:
    if ident.role != ROLE_PRIVILEGED:
        raise IdentityError("The fleet identity cannot be removed", "fleet_required")
    session.delete(ident)


def purge_for_server(session: Session, server_id: int) -> int:
    rows = list(
        session.exec(
            select(ServerSshIdentity).where(
                ServerSshIdentity.server_id == int(server_id)
            )
        ).all()
    )
    n = 0
    for row in rows:
        session.delete(row)
        n += 1
    return n


def console_identities(
    session: Session, server: Server, *, demo: bool = False
) -> list[dict[str, Any]]:
    """Picker payload. Demo never exposes privileged."""
    ensure_fleet_identity(session, server)
    out = []
    for row in list_for_server(session, int(server.id)):
        if row.role == ROLE_PRIVILEGED and (demo or not row.enabled):
            continue
        if row.role == ROLE_PRIVILEGED and not row.private_key_encrypted:
            continue
        view = public_view(row)
        out.append(view)
    return out


def overlay_server_for_identity(server: Server, ident: Optional[ServerSshIdentity]):
    """SimpleNamespace-ready field overlay for get_ssh_client / console PTY."""
    from types import SimpleNamespace

    privileged = ident is not None and ident.role == ROLE_PRIVILEGED
    username = ident.username if ident is not None else server.ssh_username
    key_enc = (
        ident.private_key_encrypted if ident is not None else server.ssh_private_key_encrypted
    )
    pw_enc = None if privileged else server.ssh_password_encrypted
    return SimpleNamespace(
        id=server.id,
        name=server.name,
        hostname=server.hostname,
        ip_address=getattr(server, "ip_address", None),
        ssh_port=server.ssh_port,
        ssh_username=username,
        ssh_private_key_encrypted=key_enc,
        ssh_password_encrypted=pw_enc,
        ssh_hostkey_type=getattr(server, "ssh_hostkey_type", None),
        ssh_hostkey_b64=getattr(server, "ssh_hostkey_b64", None),
        ssh_hostkey_fp=getattr(server, "ssh_hostkey_fp", None),
    )
