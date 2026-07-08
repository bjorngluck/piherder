"""
SSH service using paramiko.

- Keypair generation
- In-memory decrypt + connect (never store plaintext key on disk except very short-lived temp files for rsync)
- Helpers matching legacy bash SSH_OPTS

Onboarding (deploy key, rotate, least-priv user) lives in ``ssh_onboarding.py``.
"""
import paramiko
from io import StringIO
from typing import Tuple
import tempfile
import os
from contextlib import contextmanager
from ..models import Server
from ..security import encryption


SSH_OPTS = {
    "timeout": 15,
    "banner_timeout": 15,
    "auth_timeout": 15,
}

LEGACY_SSH_OPTS_STR = "-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"


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
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # accept-new equivalent in spirit

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
        return client
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
