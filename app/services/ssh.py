"""
SSH service using paramiko.

- Keypair generation
- In-memory decrypt + connect (never store plaintext key on disk except very short-lived temp files for rsync)
- Helpers matching legacy bash SSH_OPTS
- Remote **host key** is pinned (TOFU on first success; later mismatch refuses)

Onboarding (deploy key, rotate, least-priv user) lives in ``ssh_onboarding.py``.
"""
import base64
import hashlib
import logging
import os
import tempfile
from contextlib import contextmanager
from io import StringIO
from typing import Optional, Tuple

import paramiko

from ..models import Server
from ..security import encryption

logger = logging.getLogger(__name__)


SSH_OPTS = {
    "timeout": 15,
    "banner_timeout": 15,
    "auth_timeout": 15,
}

LEGACY_SSH_OPTS_STR = "-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"


class HostKeyMismatch(RuntimeError):
    """Remote host key does not match the pin stored on the Server row."""

    def __init__(self, hostname: str, expected_fp: str, seen_fp: str):
        self.hostname = hostname
        self.expected_fp = expected_fp
        self.seen_fp = seen_fp
        super().__init__(
            f"SSH host key mismatch for {hostname}: pinned {expected_fp}, "
            f"remote presented {seen_fp}. If you rebuilt the machine, reset the "
            f"host key under SSH access."
        )


def host_key_fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH-style SHA256 fingerprint (no trailing '=')."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class HostKeyPinPolicy(paramiko.MissingHostKeyPolicy):
    """TOFU: first seen key is allowed (caller persists). Later: exact match only."""

    def __init__(
        self,
        expected_type: Optional[str],
        expected_b64: Optional[str],
        expected_fp: Optional[str] = None,
    ):
        self.expected_type = (expected_type or "").strip() or None
        self.expected_b64 = (expected_b64 or "").strip() or None
        self.expected_fp = (expected_fp or "").strip() or None
        self.seen_key: Optional[paramiko.PKey] = None

    def missing_host_key(self, client, hostname, key):  # noqa: ANN001
        self.seen_key = key
        if not self.expected_b64:
            return
        same_type = not self.expected_type or key.get_name() == self.expected_type
        if same_type and key.get_base64() == self.expected_b64:
            return
        raise HostKeyMismatch(
            hostname or "host",
            expected_fp=self.expected_fp
            or f"{self.expected_type or 'ssh'} {self.expected_b64[:16]}…",
            seen_fp=host_key_fingerprint(key),
        )


def persist_host_key_if_needed(server: Server, key: Optional[paramiko.PKey]) -> None:
    """Store the first-seen host key on the Server row (TOFU)."""
    if key is None or not getattr(server, "id", None):
        return
    if (getattr(server, "ssh_hostkey_b64", None) or "").strip():
        return
    try:
        from sqlmodel import Session

        from ..database import engine
    except Exception:
        return
    fp = host_key_fingerprint(key)
    typ = key.get_name()
    b64 = key.get_base64()
    try:
        with Session(engine) as session:
            row = session.get(Server, int(server.id))
            if not row or (getattr(row, "ssh_hostkey_b64", None) or "").strip():
                return
            row.ssh_hostkey_type = typ
            row.ssh_hostkey_b64 = b64
            row.ssh_hostkey_fp = fp
            session.add(row)
            session.commit()
        server.ssh_hostkey_type = typ
        server.ssh_hostkey_b64 = b64
        server.ssh_hostkey_fp = fp
        logger.info("Pinned SSH host key for server %s (%s)", server.id, fp)
    except Exception:
        logger.warning("Could not persist SSH host key pin for server %s", server.id, exc_info=True)


def clear_host_key_pin(server: Server) -> None:
    """Forget the stored pin (operator reset after a rebuild)."""
    server.ssh_hostkey_type = None
    server.ssh_hostkey_b64 = None
    server.ssh_hostkey_fp = None


def attach_host_key_policy(client: paramiko.SSHClient, server: Server) -> HostKeyPinPolicy:
    policy = HostKeyPinPolicy(
        getattr(server, "ssh_hostkey_type", None),
        getattr(server, "ssh_hostkey_b64", None),
        getattr(server, "ssh_hostkey_fp", None),
    )
    client.set_missing_host_key_policy(policy)
    return policy


def expand_remote_path(path: str, username: str) -> str:
    """
    Expand a remote path that may use ``~`` for the SSH user's home.

    Prefer absolute paths (e.g. ``/home/pi/docker``) when the SSH user is a
    least-priv account that must manage stacks still owned under another home.
    """
    if path is None:
        return ""
    p = str(path).strip()
    if not p:
        return p
    user = (username or "").strip() or "root"
    if p == "~":
        return f"/home/{user}" if user != "root" else "/root"
    if p.startswith("~/"):
        rest = p[2:]
        if user == "root":
            return f"/root/{rest}" if rest else "/root"
        return f"/home/{user}/{rest}" if rest else f"/home/{user}"
    return p


def docker_base_expanded(server) -> str:
    """Resolve ``server.docker_base_dir`` for remote shell/SFTP paths."""
    return expand_remote_path(server.docker_base_dir or "~/docker", server.ssh_username or "root")


def generate_keypair(comment: str = "piherder-generated") -> Tuple[str, str]:
    """Return (public_key_openssh, private_key_openssh)"""
    key = paramiko.RSAKey.generate(4096)
    pub = f"{key.get_name()} {key.get_base64()} {comment}"
    priv_buf = StringIO()
    key.write_private_key(priv_buf)
    priv = priv_buf.getvalue()
    return pub, priv


def get_private_key_plain(server: Server) -> str:
    """Decrypt private key. ONLY use inside job execution contexts."""
    if not server.ssh_private_key_encrypted:
        raise RuntimeError("No encrypted private key on server")
    return encryption.decrypt_str(server.ssh_private_key_encrypted)


@contextmanager
def temp_key_file(privkey_plain: str):
    """Write a short-lived 0600 keyfile for use with subprocess rsync -e ssh -i ..."""
    fd, path = tempfile.mkstemp(prefix="piherder_ssh_", suffix=".key")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(privkey_plain)
        os.chmod(path, 0o600)
        yield path
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def _load_pkey(priv: str) -> paramiko.PKey:
    """Load RSA / Ed25519 / ECDSA private key material."""
    buf = StringIO(priv)
    last_err: Exception | None = None
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            buf.seek(0)
            return cls.from_private_key(buf)
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Could not load private key: {last_err}")


def get_ssh_client(server: Server) -> paramiko.SSHClient:
    """Create and connect an SSHClient. Caller must .close() or use context."""
    client = paramiko.SSHClient()
    policy = attach_host_key_policy(client, server)

    pkey = None
    if server.ssh_private_key_encrypted:
        priv = get_private_key_plain(server)
        pkey = _load_pkey(priv)

    try:
        client.connect(
            hostname=server.hostname or server.ip_address,
            port=server.ssh_port,
            username=server.ssh_username,
            pkey=pkey,
            password=encryption.decrypt_str(server.ssh_password_encrypted) if server.ssh_password_encrypted else None,
            timeout=SSH_OPTS["timeout"],
            banner_timeout=SSH_OPTS["banner_timeout"],
            auth_timeout=SSH_OPTS["auth_timeout"],
            look_for_keys=False,
            allow_agent=False,
        )
        persist_host_key_if_needed(server, policy.seen_key)
        return client
    except HostKeyMismatch:
        client.close()
        raise
    except Exception as e:
        client.close()
        raise RuntimeError(f"SSH connect failed to {server.hostname}: {e}")


def run_command(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    """Run cmd, return (exit_status, stdout, stderr)"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    status = stdout.channel.recv_exit_status()
    return status, out, err


def test_connection(server: Server) -> bool:
    """Quick test used by the 'Test connection' flow."""
    try:
        client = get_ssh_client(server)
        status, out, err = run_command(client, "echo 'PiHerder SSH test OK' && hostname && date", timeout=20)
        client.close()
        return status == 0
    except Exception:
        return False
