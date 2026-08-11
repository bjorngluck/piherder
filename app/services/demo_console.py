"""Demo-only simulated console PTY (Stream D5).

No Paramiko, no TCP, no live host access. Mimics a minimal shell so the
public demo can show xterm UI without becoming a jump box.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Optional


_ANSI_RESET = "\x1b[0m"
_ANSI_DIM = "\x1b[2m"
_ANSI_CYAN = "\x1b[36m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_GREEN = "\x1b[32m"


class DemoShellChannel:
    """Paramiko-channel-shaped object for the console WebSocket pump."""

    def __init__(
        self,
        *,
        host_label: str,
        username: str = "demo",
        cwd: str = "~",
    ):
        self._lock = threading.Lock()
        self._out = bytearray()
        self._line = bytearray()
        self._closed = False
        self._exit_ready = False
        self.timeout = 0.0
        self.closed = False
        self._host = (host_label or "lab").strip() or "lab"
        self._user = (username or "demo").strip() or "demo"
        self._cwd = cwd or "~"
        self._history: list[str] = []
        self._hist_i = 0
        banner = (
            f"\r\n{_ANSI_YELLOW}PiHerder demo console{_ANSI_RESET} — "
            f"{_ANSI_DIM}simulated shell · no network · no real SSH{_ANSI_RESET}\r\n"
            f"Host: {_ANSI_CYAN}{self._host}{_ANSI_RESET}  "
            f"user: {self._user}\r\n"
            f"Try: {_ANSI_GREEN}help{_ANSI_RESET}, {_ANSI_GREEN}ls{_ANSI_RESET}, "
            f"{_ANSI_GREEN}uname -a{_ANSI_RESET}, {_ANSI_GREEN}whoami{_ANSI_RESET}, "
            f"{_ANSI_GREEN}exit{_ANSI_RESET}\r\n\r\n"
        )
        self._push(banner)
        self._push_prompt()

    def _push(self, text: str) -> None:
        if not text:
            return
        data = text.encode("utf-8", errors="replace")
        with self._lock:
            self._out.extend(data)

    def _prompt(self) -> str:
        return f"{_ANSI_GREEN}{self._user}@{self._host}{_ANSI_RESET}:{_ANSI_CYAN}{self._cwd}{_ANSI_RESET}$ "

    def _push_prompt(self) -> None:
        self._push(self._prompt())

    def recv_ready(self) -> bool:
        with self._lock:
            return (not self._closed) and len(self._out) > 0

    def recv_stderr_ready(self) -> bool:
        return False

    def recv(self, n: int = 8192) -> bytes:
        n = max(1, int(n or 8192))
        with self._lock:
            if not self._out:
                return b""
            chunk = bytes(self._out[:n])
            del self._out[:n]
            return chunk

    def recv_stderr(self, n: int = 4096) -> bytes:
        del n
        return b""

    def exit_status_ready(self) -> bool:
        return self._exit_ready or self._closed

    def send(self, data: Any) -> int:
        if self._closed:
            return 0
        if isinstance(data, str):
            raw = data.encode("utf-8", errors="replace")
        else:
            raw = bytes(data or b"")
        if not raw:
            return 0
        # Process byte-wise for basic line editing
        for b in raw:
            self._handle_byte(b)
        return len(raw)

    def _handle_byte(self, b: int) -> None:
        if self._closed:
            return
        # Ctrl-C
        if b == 0x03:
            self._line.clear()
            self._push("^C\r\n")
            self._push_prompt()
            return
        # Ctrl-D / EOF on empty line
        if b == 0x04:
            if not self._line:
                self._push("logout\r\n")
                self._close_shell()
            return
        # Enter
        if b in (0x0D, 0x0A):
            line = self._line.decode("utf-8", errors="replace")
            self._line.clear()
            self._push("\r\n")
            self._run_command(line)
            return
        # Backspace
        if b in (0x7F, 0x08):
            if self._line:
                self._line.pop()
                self._push("\b \b")
            return
        # Ignore other controls
        if b < 0x20:
            return
        self._line.append(b)
        self._push(bytes([b]).decode("latin-1"))

    def _run_command(self, line: str) -> None:
        cmd = (line or "").strip()
        if cmd:
            self._history.append(cmd)
            self._hist_i = len(self._history)
        if not cmd:
            self._push_prompt()
            return
        low = cmd.lower()
        if low in ("exit", "logout", "quit"):
            self._push("logout\r\n")
            self._close_shell()
            return
        if low in ("help", "?"):
            self._push(
                "Demo commands (simulated):\r\n"
                "  help, whoami, hostname, pwd, ls, uname [-a], date, id, clear, exit\r\n"
                "This is not a real shell — no network, no files, no sudo.\r\n"
            )
        elif low == "whoami":
            self._push(f"{self._user}\r\n")
        elif low in ("hostname", "hostname -f"):
            self._push(f"{self._host}\r\n")
        elif low == "pwd":
            path = "/home/demo" if self._cwd == "~" else self._cwd
            self._push(f"{path}\r\n")
        elif low in ("ls", "ls -la", "ls -l"):
            self._push(
                "docker/  backups/  README.md  .ssh/\r\n"
                f"{_ANSI_DIM}(demo listing){_ANSI_RESET}\r\n"
            )
        elif low.startswith("uname"):
            self._push(
                "Linux lab-demo 6.1.0-demo #1 SMP PREEMPT_DYNAMIC "
                "x86_64 GNU/Linux\r\n"
            )
        elif low == "date":
            self._push(time.strftime("%a %b %d %H:%M:%S UTC %Y", time.gmtime()) + "\r\n")
        elif low == "id":
            self._push(
                f"uid=1000({self._user}) gid=1000({self._user}) "
                f"groups=1000({self._user}),27(sudo)\r\n"
            )
        elif low == "clear":
            self._push("\x1b[2J\x1b[H")
        elif low.startswith("cd"):
            # Cosmetics only
            parts = cmd.split(maxsplit=1)
            if len(parts) == 1 or parts[1] in ("~", "/home/demo"):
                self._cwd = "~"
            else:
                arg = parts[1].strip().strip("'\"")
                if arg.startswith("/"):
                    self._cwd = arg.rstrip("/") or "/"
                else:
                    self._cwd = f"~/{arg}" if self._cwd == "~" else f"{self._cwd}/{arg}"
        elif re.match(r"^(sudo|ssh|nmap|curl|wget|rm|dd)\b", low):
            self._push(
                f"{_ANSI_YELLOW}demo:{_ANSI_RESET} command blocked "
                f"(no live access)\r\n"
            )
        else:
            first = cmd.split()[0]
            self._push(f"bash: {first}: command not found (demo)\r\n")
        if not self._closed:
            self._push_prompt()

    def _close_shell(self) -> None:
        self._exit_ready = True
        self._closed = True
        self.closed = True

    def resize_pty(self, width: int = 80, height: int = 24, width_pixels: int = 0, height_pixels: int = 0) -> None:
        del width, height, width_pixels, height_pixels

    def close(self) -> None:
        self._closed = True
        self.closed = True
        self._exit_ready = True


class DemoShellClient:
    """Stand-in for paramiko.SSHClient — only close() is used by hold logic."""

    def __init__(self, channel: DemoShellChannel):
        self._channel = channel

    def close(self) -> None:
        try:
            self._channel.close()
        except Exception:
            pass


def open_demo_shell(
    *,
    host_label: str,
    username: str = "demo",
) -> tuple[DemoShellClient, DemoShellChannel]:
    """Return (client, channel) for the console bridge — never opens a socket."""
    ch = DemoShellChannel(host_label=host_label, username=username or "demo")
    return DemoShellClient(ch), ch


def is_simulated_console() -> bool:
    """True when the herder must never open live SSH for console."""
    from .demo import demo_mode

    return bool(demo_mode())
