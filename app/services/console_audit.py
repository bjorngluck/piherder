"""Opt-in web-console command audit (v1.3 W-audit).

Server-side PTY tap (option A). Default off. Demo never persists.
Body is Fernet-encrypted; AuditLog details stay metadata-only.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlmodel import Session, select

logger = logging.getLogger("piherder.console_audit")

MODE_OFF = "off"
MODE_COMMANDS = "commands"
MODE_COMMANDS_OUTPUT = "commands_output"
AUDIT_MODES = (MODE_OFF, MODE_COMMANDS, MODE_COMMANDS_OUTPUT)

CMD_LINE_MAX = 4 * 1024
OUT_PER_CMD_MAX = 2 * 1024
EVENTS_MAX = 2000
BODY_MAX_COMMANDS = 256 * 1024
BODY_MAX_OUTPUT = 1024 * 1024
FLUSH_DEBOUNCE_SEC = 5.0
RETENTION_MIN, RETENTION_MAX, RETENTION_DEFAULT = 1, 90, 14

_PASSWORD_PROMPT = re.compile(
    r"(?i)(?:\[sudo\]\s+)?(?:password|passphrase|passcode)(?:\s+for\s+[^\r\n:]*)?:\s*$"
)
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
)
_TOKEN_RES = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
)
_CSI = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CHARSET = re.compile(r"\x1b[()][0-9A-Za-z]")
_SIMPLE_ESC = re.compile(r"\x1b[@-Z\\-_]")
_C0_KEEP = frozenset((0x08, 0x09, 0x0A, 0x0D, 0x03, 0x7F))


def clamp_mode(raw: Any) -> str:
    s = str(raw or "").strip().lower().replace("-", "_")
    if s in ("cmd", "command"):
        s = MODE_COMMANDS
    if s in ("commands_out", "output", "cmds_output", "full"):
        s = MODE_COMMANDS_OUTPUT
    return s if s in AUDIT_MODES else MODE_OFF


def clamp_retention_days(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = RETENTION_DEFAULT
    return max(RETENTION_MIN, min(RETENTION_MAX, n))


def body_cap_for(mode: str) -> int:
    return BODY_MAX_OUTPUT if mode == MODE_COMMANDS_OUTPUT else BODY_MAX_COMMANDS


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = _PEM_BLOCK.sub("[redacted-pem]", text)
    for rx in _TOKEN_RES:
        out = rx.sub("[redacted-token]", out)
    return out


def strip_ansi(text: str) -> str:
    if not text or "\x1b" not in text:
        return text.replace("\x0e", "").replace("\x0f", "")
    out = _OSC.sub("", text)
    out = _CSI.sub("", out)
    out = _CHARSET.sub("", out)
    out = _SIMPLE_ESC.sub("", out)
    return out.replace("\x0e", "").replace("\x0f", "")


def looks_like_password_prompt(text: str) -> bool:
    s = strip_ansi(text or "").replace("\r", "").rstrip()
    if not s:
        return False
    line = s.split("\n")[-1].rstrip()
    return bool(_PASSWORD_PROMPT.search(line))


def _to_bytes(data: Any) -> bytes:
    if data is None:
        return b""
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8", errors="replace")
    try:
        return bytes(data)
    except Exception:
        return b""


def _split_incomplete_esc(text: str) -> tuple[str, str]:
    idx = text.rfind("\x1b")
    if idx < 0:
        return text, ""
    tail = text[idx:]
    if len(tail) > 32:
        return text, ""
    if _CSI.fullmatch(tail) or _OSC.fullmatch(tail) or _CHARSET.fullmatch(tail) or _SIMPLE_ESC.fullmatch(
        tail
    ):
        return text, ""
    return text[:idx], tail


class _EchoLine:
    """Best-effort visible line (host echo + Tab completion)."""

    __slots__ = ("chars", "cur")

    def __init__(self) -> None:
        self.chars: list[str] = []
        self.cur = 0

    def put(self, ch: str) -> None:
        if self.cur < len(self.chars):
            self.chars[self.cur] = ch
            self.cur += 1
            return
        self.chars.append(ch)
        self.cur = len(self.chars)

    def bs(self) -> None:
        if self.cur <= 0:
            return
        self.cur -= 1
        del self.chars[self.cur]

    def cr(self) -> None:
        self.cur = 0

    def clear_to_end(self) -> None:
        del self.chars[self.cur :]

    def clear(self) -> None:
        self.chars.clear()
        self.cur = 0

    def text(self) -> str:
        return "".join(self.chars).rstrip()


class SessionRecorder:
    """In-process command boundary detector. No I/O in feed_*."""

    def __init__(self, mode: str = MODE_COMMANDS) -> None:
        self.mode = clamp_mode(mode)
        if self.mode == MODE_OFF:
            self.mode = MODE_COMMANDS
        self.session_key = ""
        self.transcript_id: Optional[int] = None
        self.audit_open_id: Optional[int] = None
        self.user_id: Optional[int] = None
        self.server_id: Optional[int] = None
        self.identity_role: Optional[str] = None
        self.identity_username: Optional[str] = None
        self.truncated = False
        self.finalized = False
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._in_line: list[str] = []
        self._in_rest = b""
        self._in_esc = b""
        self._echo = _EchoLine()
        self._out_rest = ""
        self._out_hold = ""
        self._password = False
        self._capturing_out = False
        self._out_buf = ""
        self._out_trunc = False
        self._last_flush = 0.0
        self._body_len = 0

    @property
    def command_count(self) -> int:
        return sum(1 for e in self._events if e.get("kind") == "cmd")

    @property
    def byte_count(self) -> int:
        return self._body_len or len(self.dumps())

    def dumps(self) -> str:
        with self._lock:
            payload = {"v": 1, "events": list(self._events)}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def feed_stdin(self, data: Any) -> None:
        if self.finalized or self.truncated:
            return
        raw = _to_bytes(data)
        if not raw:
            return
        with self._lock:
            buf = self._in_rest + self._in_esc + raw
            self._in_esc = b""
            i = 0
            n = len(buf)
            while i < n:
                b = buf[i]
                if b == 0x1B:
                    j = i + 1
                    if j >= n:
                        self._in_esc = buf[i:]
                        break
                    if buf[j : j + 1] == b"[":
                        j += 1
                        while j < n and not (0x40 <= buf[j] <= 0x7E):
                            j += 1
                        if j >= n:
                            self._in_esc = buf[i:]
                            break
                        i = j + 1
                        continue
                    if buf[j : j + 1] == b"]":
                        while j < n and buf[j] not in (0x07,):
                            if buf[j] == 0x1B and j + 1 < n and buf[j + 1] == 0x5C:
                                j += 2
                                break
                            j += 1
                        else:
                            if j >= n:
                                self._in_esc = buf[i:]
                                break
                            j += 1
                        i = j
                        continue
                    i += 2
                    continue
                if b in (0x0D, 0x0A):
                    if b == 0x0D and i + 1 < n and buf[i + 1] == 0x0A:
                        i += 1
                    self._commit_locked()
                    i += 1
                    continue
                if b in (0x08, 0x7F):
                    if self._in_line:
                        self._in_line.pop()
                    i += 1
                    continue
                if b == 0x03:
                    self._in_line.clear()
                    self._echo.clear()
                    self._password = False
                    self._capturing_out = False
                    self._out_buf = ""
                    i += 1
                    continue
                if b == 0x09:
                    self._in_line.append("\t")
                    i += 1
                    continue
                if b < 0x20:
                    i += 1
                    continue
                # UTF-8
                if b < 0x80:
                    self._in_line.append(chr(b))
                    i += 1
                    continue
                need = 2 if b < 0xE0 else 3 if b < 0xF0 else 4
                if i + need > n:
                    self._in_rest = buf[i:]
                    return
                try:
                    ch = buf[i : i + need].decode("utf-8")
                except UnicodeDecodeError:
                    ch = "\ufffd"
                self._in_line.append(ch)
                i += need
            self._in_rest = b""
            if len(self._in_line) > CMD_LINE_MAX:
                self._in_line = self._in_line[:CMD_LINE_MAX]

    def feed_stdout(self, data: Any) -> None:
        if self.finalized or self.truncated:
            return
        raw = _to_bytes(data)
        if not raw:
            return
        try:
            chunk = raw.decode("utf-8", errors="replace")
        except Exception:
            chunk = raw.decode("latin-1", errors="replace")
        with self._lock:
            text = self._out_hold + chunk
            ready, hold = _split_incomplete_esc(text)
            self._out_hold = hold
            stripped = strip_ansi(ready)
            if looks_like_password_prompt(stripped):
                self._password = True
                self._capturing_out = False
            self._apply_echo_locked(stripped)
            if self.mode == MODE_COMMANDS_OUTPUT and self._capturing_out and not self._password:
                room = OUT_PER_CMD_MAX - len(self._out_buf)
                if room > 0:
                    add = stripped[:room]
                    self._out_buf += add
                    if len(stripped) > room:
                        self._out_trunc = True
                else:
                    self._out_trunc = True

    def _apply_echo_locked(self, stripped: str) -> None:
        for ch in stripped:
            if ch == "\r":
                self._echo.cr()
            elif ch == "\n":
                self._echo.clear()
            elif ch in ("\x08", "\x7f"):
                self._echo.bs()
            elif ch == "\t":
                self._echo.put(" ")
            elif ch >= " ":
                self._echo.put(ch)

    def _commit_locked(self) -> None:
        echo = self._echo.text()
        typed = "".join(self._in_line).replace("\t", "").rstrip()
        self._in_line.clear()
        self._echo.clear()
        cmd = (echo or typed).strip()
        if not cmd and not self._password:
            self._flush_out_locked()
            return
        if self._password:
            evt = {
                "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "kind": "cmd",
                "text": "[redacted]",
                "reason": "password_prompt",
            }
            self._password = False
            self._capturing_out = False
            self._out_buf = ""
            self._append_locked(evt)
            return
        cmd = redact_secrets(cmd)[:CMD_LINE_MAX]
        self._flush_out_locked()
        evt = {
            "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "cmd",
            "text": cmd,
        }
        self._append_locked(evt)
        if self.mode == MODE_COMMANDS_OUTPUT:
            self._capturing_out = True
            self._out_buf = ""
            self._out_trunc = False

    def _flush_out_locked(self) -> None:
        if self.mode != MODE_COMMANDS_OUTPUT:
            self._out_buf = ""
            self._capturing_out = False
            return
        buf = redact_secrets(self._out_buf)
        self._out_buf = ""
        capturing = self._capturing_out
        self._capturing_out = False
        if not capturing or not buf.strip():
            return
        evt: dict[str, Any] = {
            "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "out",
            "text": buf[:OUT_PER_CMD_MAX],
        }
        if getattr(self, "_out_trunc", False) or len(buf) >= OUT_PER_CMD_MAX:
            evt["truncated"] = True
        self._out_trunc = False
        self._append_locked(evt)

    def _append_locked(self, evt: dict[str, Any]) -> None:
        if self.truncated:
            return
        if len(self._events) >= EVENTS_MAX:
            self.truncated = True
            return
        extra = len(json.dumps(evt, ensure_ascii=False, separators=(",", ":")))
        cap = body_cap_for(self.mode)
        if self._body_len + extra > cap:
            self.truncated = True
            return
        self._events.append(evt)
        self._body_len += extra

    def should_flush(self, *, finalize: bool = False) -> bool:
        if self.finalized:
            return False
        if finalize:
            return True
        now = time.monotonic()
        if now - self._last_flush >= FLUSH_DEBOUNCE_SEC:
            return True
        return False

    def mark_flushed(self) -> None:
        self._last_flush = time.monotonic()


def _model():
    from ..models import ConsoleTranscript

    return ConsoleTranscript


def start_session(
    session: Session,
    *,
    session_key: str,
    user_id: Optional[int],
    server_id: Optional[int],
    identity_role: Optional[str] = None,
    identity_username: Optional[str] = None,
    audit_open_id: Optional[int] = None,
    mode: Optional[str] = None,
) -> Optional[SessionRecorder]:
    """Create an in-memory recorder + stub DB row. None when off or demo."""
    from . import ssh_console as cons

    if cons.is_demo_console():
        return None
    use_mode = clamp_mode(mode) if mode else cons.audit_mode()
    if use_mode == MODE_OFF:
        return None
    rec = SessionRecorder(mode=use_mode)
    rec.session_key = (session_key or "")[:64]
    rec.user_id = int(user_id) if user_id else None
    rec.server_id = int(server_id) if server_id else None
    rec.identity_role = (identity_role or "")[:16] or None
    rec.identity_username = (identity_username or "")[:64] or None
    rec.audit_open_id = audit_open_id
    ConsoleTranscript = _model()
    row = ConsoleTranscript(
        session_key=rec.session_key,
        audit_open_id=audit_open_id,
        user_id=rec.user_id,
        server_id=rec.server_id,
        identity_role=rec.identity_role,
        identity_username=rec.identity_username,
        mode=rec.mode,
        command_count=0,
        byte_count=0,
        truncated=False,
        body_encrypted=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    rec.transcript_id = int(row.id) if row.id else None
    rec.mark_flushed()
    return rec


def flush_recorder(
    session: Session,
    rec: Optional[SessionRecorder],
    *,
    finalize: bool = False,
) -> Optional[int]:
    """Encrypt current events onto the stub row. Returns transcript id."""
    if rec is None or rec.finalized:
        return rec.transcript_id if rec else None
    if rec.mode == MODE_COMMANDS_OUTPUT and finalize:
        with rec._lock:
            rec._flush_out_locked()
    if not rec.should_flush(finalize=finalize) and rec.transcript_id:
        return rec.transcript_id
    from ..security.encryption import encrypt_str

    ConsoleTranscript = _model()
    payload = rec.dumps()
    try:
        enc = encrypt_str(payload) if payload else ""
    except Exception:
        logger.exception("console transcript encrypt failed")
        raise
    row = None
    if rec.transcript_id:
        row = session.get(ConsoleTranscript, rec.transcript_id)
    if row is None and rec.session_key:
        row = session.exec(
            select(ConsoleTranscript).where(ConsoleTranscript.session_key == rec.session_key)
        ).first()
    now = datetime.utcnow()
    if row is None:
        row = ConsoleTranscript(
            session_key=rec.session_key or f"anon-{int(time.time())}",
            created_at=now,
        )
        session.add(row)
    row.audit_open_id = rec.audit_open_id
    row.user_id = rec.user_id
    row.server_id = rec.server_id
    row.identity_role = rec.identity_role
    row.identity_username = rec.identity_username
    row.mode = rec.mode
    row.command_count = rec.command_count
    row.byte_count = rec.byte_count
    row.truncated = bool(rec.truncated)
    if not row.purged_at:
        row.body_encrypted = enc
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    rec.transcript_id = int(row.id) if row.id else rec.transcript_id
    rec.mark_flushed()
    if finalize:
        rec.finalized = True
    return rec.transcript_id


def close_details(rec: Optional[SessionRecorder], base: str = "") -> str:
    """Metadata-only details suffix for ssh_console_close."""
    parts = [base.strip()] if base and base.strip() else []
    if rec is None or not rec.transcript_id:
        return " ".join(parts)
    parts.append(f"transcript_id={rec.transcript_id}")
    parts.append(f"cmds={rec.command_count}")
    parts.append(f"bytes={rec.byte_count}")
    parts.append(f"mode={rec.mode}")
    parts.append(f"truncated={1 if rec.truncated else 0}")
    return " ".join(p for p in parts if p)


def parse_kv_details(details: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (details or "").split():
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k] = v
    return out


def load_transcript(session: Session, transcript_id: int):
    ConsoleTranscript = _model()
    return session.get(ConsoleTranscript, int(transcript_id))


def decrypt_events(row) -> list[dict[str, Any]]:
    if row is None:
        return []
    if getattr(row, "purged_at", None) or not getattr(row, "body_encrypted", None):
        return []
    from ..security.encryption import decrypt_str

    raw = decrypt_str(row.body_encrypted)
    try:
        data = json.loads(raw)
    except Exception:
        return []
    ev = data.get("events") if isinstance(data, dict) else None
    return list(ev) if isinstance(ev, list) else []


def events_as_text(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for e in events:
        kind = e.get("kind") or ""
        ts = e.get("ts") or ""
        text = e.get("text") or ""
        if kind == "cmd":
            reason = e.get("reason")
            extra = f"  # {reason}" if reason else ""
            lines.append(f"{ts} $ {text}{extra}")
        elif kind == "out":
            trunc = " [truncated]" if e.get("truncated") else ""
            for ln in str(text).splitlines() or [text]:
                lines.append(f"{ts} | {ln}{trunc}")
                trunc = ""
        else:
            lines.append(f"{ts} {kind} {text}")
    return "\n".join(lines) + ("\n" if lines else "")


def purge_transcript_bodies(
    session: Session,
    *,
    older_than_days: Optional[int] = None,
    server_id: Optional[int] = None,
) -> int:
    """Clear Fernet bodies; keep counts. Returns rows purged."""
    ConsoleTranscript = _model()
    q = select(ConsoleTranscript)
    if server_id is not None:
        q = q.where(ConsoleTranscript.server_id == int(server_id))
    rows = list(session.exec(q).all())
    cut = None
    if older_than_days is not None:
        days = clamp_retention_days(older_than_days)
        cut = datetime.utcnow() - timedelta(days=days)
    n = 0
    now = datetime.utcnow()
    for row in rows:
        if row.purged_at or not row.body_encrypted:
            continue
        if cut is not None:
            ts = row.updated_at or row.created_at
            if ts and ts >= cut:
                continue
        row.body_encrypted = ""
        row.purged_at = now
        row.updated_at = now
        session.add(row)
        n += 1
    if n:
        session.commit()
    return n


def unlink_server(session: Session, server_id: int) -> int:
    """On host delete: purge bodies and drop server_id."""
    ConsoleTranscript = _model()
    rows = list(
        session.exec(
            select(ConsoleTranscript).where(ConsoleTranscript.server_id == int(server_id))
        ).all()
    )
    now = datetime.utcnow()
    for row in rows:
        if row.body_encrypted:
            row.body_encrypted = ""
            row.purged_at = row.purged_at or now
        row.server_id = None
        row.updated_at = now
        session.add(row)
    if rows:
        session.commit()
    return len(rows)


def purge_expired_now(session: Optional[Session] = None) -> int:
    """Retention pass using the Settings/env knob."""
    from . import ssh_console as cons
    from ..database import engine

    days = cons.audit_retention_days()
    own = session is None
    if own:
        session = Session(engine)
    try:
        return purge_transcript_bodies(session, older_than_days=days)
    finally:
        if own and session is not None:
            session.close()


def public_meta(row, *, readable: bool) -> dict[str, Any]:
    if row is None:
        return {}
    purged = bool(row.purged_at) or not row.body_encrypted
    return {
        "id": row.id,
        "mode": row.mode,
        "command_count": int(row.command_count or 0),
        "byte_count": int(row.byte_count or 0),
        "truncated": bool(row.truncated),
        "purged": purged,
        "identity_role": row.identity_role,
        "identity_username": row.identity_username,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "readable": bool(readable) and not purged,
    }
