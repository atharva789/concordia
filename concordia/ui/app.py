import asyncio
import base64
import os
import shutil
import sys
import termios
import tty
from typing import List

from ..client import ClientTransport

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
FG_TITLE = "\033[38;5;81m"
FG_TEXT = "\033[38;5;252m"
FG_DIM = "\033[38;5;245m"
FG_OK = "\033[38;5;114m"
FG_WARN = "\033[38;5;221m"
FG_ERR = "\033[38;5;203m"


def _stderr_line(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _meta_line(kind: str, text: str) -> None:
    if kind == "system":
        color = FG_OK
    elif kind == "invite":
        color = FG_WARN
    elif kind == "party":
        color = FG_DIM
    elif kind == "error":
        color = FG_ERR
    else:
        color = FG_TEXT
    _stderr_line(f"{DIM}[{kind}]{RESET} {color}{text}{RESET}")


def _render_intro() -> None:
    cols = max(60, min(shutil.get_terminal_size((100, 30)).columns, 120))
    inner = cols - 4

    def row(text: str = "") -> str:
        trimmed = text[:inner]
        return f"| {trimmed.ljust(inner)} |"

    top = "+" + "-" * (cols - 2) + "+"
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write(f"{FG_DIM}{top}{RESET}\n")
    sys.stdout.write(f"{FG_DIM}{row()} {RESET}\n")
    sys.stdout.write(f"{FG_DIM}| {FG_TITLE}{BOLD}{'CONCORDIA'.ljust(inner)}{RESET}{FG_DIM} |\n")
    sys.stdout.write(f"{FG_DIM}{row('Shared Claude terminal')}{RESET}\n")
    sys.stdout.write(f"{FG_DIM}{row()}{RESET}\n")
    sys.stdout.write(f"{FG_DIM}{row('Controls: Ctrl-] disconnects local client')}{RESET}\n")
    sys.stdout.write(f"{FG_DIM}{row('Waiting for session bootstrap...')}{RESET}\n")
    sys.stdout.write(f"{FG_DIM}{row()}{RESET}\n")
    sys.stdout.write(f"{FG_DIM}{top}{RESET}\n\n")
    sys.stdout.write(f"{FG_TEXT}Startup log{RESET}\n")
    sys.stdout.write(f"{FG_DIM}{'-' * 22}{RESET}\n")
    sys.stdout.flush()


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
    printed_invite = False
    printed_party = False

    async def _receiver() -> None:
        nonlocal connected, invite_code, main_user, users, printed_invite, printed_party
        out_fd = sys.stdout.fileno()
        async for msg in transport.iter_messages():
            mtype = msg.get("type")
            if mtype == "output_raw":
                raw = msg.get("data", b"")
                if raw:
                    os.write(out_fd, raw)
            elif mtype == "output_bytes":
                data_b64 = msg.get("data_b64", "")
                try:
                    raw = base64.b64decode(data_b64)
                except Exception:
                    continue
                if raw:
                    os.write(out_fd, raw)
            elif mtype == "invite":
                invite_code = msg.get("code", "")
                if invite_code and not printed_invite:
                    _meta_line("invite", invite_code)
                    printed_invite = True
            elif mtype == "participants":
                main_user = msg.get("main_user", "")
                users = list(msg.get("users", []))
                if users and not printed_party:
                    _meta_line("party", f"host={main_user} users={', '.join(users)}")
                    printed_party = True
            elif mtype == "system":
                # Ignore routine system chatter to keep terminal rendering clean.
                pass
            elif mtype == "error":
                _meta_line("error", msg.get("message", ""))

        connected = False

    async def _sender_raw() -> None:
        nonlocal connected
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        loop = asyncio.get_running_loop()
        try:
            tty.setraw(fd)
            while connected:
                chunk = await loop.run_in_executor(None, os.read, fd, 1024)
                if not chunk:
                    break
                if chunk == b"\x1d":
                    break
                await transport.send_input_bytes(chunk)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)

    _render_intro()
    await transport.connect()
    connected = True
    _meta_line("system", "connected")

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
