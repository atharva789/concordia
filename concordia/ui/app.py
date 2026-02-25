import asyncio
import base64
import os
import select
import shutil
import subprocess
import sys
import termios
import tty
from typing import List, Optional

from ..client import ClientTransport


def _stderr_line(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


ESC = "\x1b"
RESET = f"{ESC}[0m"
HUD_BG = f"{ESC}[48;5;238m"
HUD_FG = f"{ESC}[38;5;252m"
ACCENT = f"{ESC}[38;5;39m"
_ALT_SCREEN_SEQUENCES = (
    b"\x1b[?1049h",
    b"\x1b[?1049l",
    b"\x1b[?1047h",
    b"\x1b[?1047l",
    b"\x1b[?47h",
    b"\x1b[?47l",
)


def _copy_to_clipboard(text: str) -> bool:
    if not text:
        return False
    commands = [
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["clip"],
    ]
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, input=text, text=True, capture_output=True)
        except (FileNotFoundError, OSError):
            continue
        if proc.returncode == 0:
            return True
    return False


def _sanitize_stream_bytes(raw: bytes) -> bytes:
    clean = raw
    for seq in _ALT_SCREEN_SEQUENCES:
        clean = clean.replace(seq, b"")
    return clean


def _read_stdin_chunk(fd: int, size: int = 1024, timeout_sec: float = 0.2) -> bytes:
    ready, _, _ = select.select([fd], [], [], timeout_sec)
    if not ready:
        return b""
    return os.read(fd, size)


def _trim_for_cols(text: str, cols: int) -> str:
    if cols <= 0:
        return ""
    if len(text) <= cols:
        return text
    if cols == 1:
        return text[:1]
    return text[: cols - 1] + "…"


def _pad_line(text: str, cols: int) -> str:
    visible = _trim_for_cols(text, cols)
    return visible + (" " * max(cols - len(visible), 0))


def _draw_hud(
    out_fd: int,
    invite_code: str,
    main_user: str,
    users: List[str],
    clipboard_ok: Optional[bool],
) -> None:
    cols = shutil.get_terminal_size((100, 30)).columns
    users_str = ", ".join(users) if users else "-"
    host_str = main_user or "-"
    invite_label = invite_code if invite_code else "(waiting...)"
    if clipboard_ok is None:
        clip_status = "pending"
    else:
        clip_status = "copied" if clipboard_ok else "copy failed"

    line1 = _pad_line(" CONCORDIA  Ctrl-] disconnect local client ", cols)
    line2 = _pad_line(f" invite: {invite_label}  [{clip_status}] ", cols)
    line3 = _pad_line(f" host: {host_str}  users: {users_str} ", cols)

    frame = [
        f"{HUD_BG}{HUD_FG}{line1}{RESET}",
        f"{HUD_BG}{ACCENT}{line2}{RESET}",
        f"{HUD_BG}{HUD_FG}{line3}{RESET}",
    ]

    seq = [f"{ESC}7"]
    for idx, line in enumerate(frame, start=1):
        seq.append(f"{ESC}[{idx};1H{ESC}[2K{line}")
    seq.append(f"{ESC}8")
    os.write(out_fd, "".join(seq).encode("utf-8", errors="ignore"))


def _render_intro(out_fd: int) -> None:
    os.write(out_fd, f"{ESC}[2J{ESC}[H".encode())


async def run_tui(transport: ClientTransport) -> None:
    """Shared terminal mode: stream stdin bytes to host and render host PTY bytes locally.

    This intentionally avoids a virtual text UI renderer so ANSI/PTY control sequences
    are interpreted by the user's real terminal.
    """

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("TUI shared-terminal mode requires a TTY")

    connected = False
    invite_code = ""
    main_user = ""
    users: List[str] = []
    invite_copied: Optional[bool] = None
    async def _receiver() -> None:
        nonlocal connected, invite_code, main_user, users, invite_copied
        out_fd = sys.stdout.fileno()
        async for msg in transport.iter_messages():
            mtype = msg.get("type")
            if mtype == "output_raw":
                raw = msg.get("data", b"")
                if raw:
                    os.write(out_fd, _sanitize_stream_bytes(raw))
                    _draw_hud(out_fd, invite_code, main_user, users, invite_copied)
            elif mtype == "output_bytes":
                data_b64 = msg.get("data_b64", "")
                try:
                    raw = base64.b64decode(data_b64)
                except Exception:
                    continue
                if raw:
                    os.write(out_fd, _sanitize_stream_bytes(raw))
                    _draw_hud(out_fd, invite_code, main_user, users, invite_copied)
            elif mtype == "invite":
                invite_code = msg.get("code", "")
                if invite_code:
                    invite_copied = _copy_to_clipboard(invite_code)
                    _draw_hud(out_fd, invite_code, main_user, users, invite_copied)
            elif mtype == "participants":
                main_user = msg.get("main_user", "")
                users = list(msg.get("users", []))
                _draw_hud(out_fd, invite_code, main_user, users, invite_copied)
            elif mtype == "system":
                # Ignore routine system chatter to keep terminal rendering clean.
                pass
            elif mtype == "error":
                _stderr_line(f"[error] {msg.get('message', '')}")

        connected = False

    async def _sender_raw() -> None:
        nonlocal connected
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        loop = asyncio.get_running_loop()
        try:
            tty.setraw(fd)
            while connected:
                chunk = await loop.run_in_executor(None, _read_stdin_chunk, fd, 1024, 0.2)
                if not chunk:
                    continue
                ctrl_idx = chunk.find(b"\x1d")
                if ctrl_idx >= 0:
                    lead = chunk[:ctrl_idx]
                    if lead:
                        await transport.send_input_bytes(lead)
                    break
                await transport.send_input_bytes(chunk)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)

    out_fd = sys.stdout.fileno()
    _render_intro(out_fd)
    await transport.connect()
    connected = True
    _draw_hud(out_fd, invite_code, main_user, users, invite_copied)

    recv_task = asyncio.create_task(_receiver())
    send_task = asyncio.create_task(_sender_raw())

    done, pending = await asyncio.wait(
        {recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    for task in done:
        exc = task.exception()
        if exc is not None:
            raise exc

    await transport.close()
    os.write(out_fd, f"{ESC}[2J{ESC}[H".encode())
