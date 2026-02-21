import asyncio
import base64
import os
import sys
import termios
import tty
from typing import List

from ..client import ClientTransport


def _stderr_line(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


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

    async def _receiver() -> None:
        nonlocal connected, invite_code, main_user, users
        out_fd = sys.stdout.fileno()
        async for msg in transport.iter_messages():
            mtype = msg.get("type")
            if mtype == "output_bytes":
                data_b64 = msg.get("data_b64", "")
                try:
                    raw = base64.b64decode(data_b64)
                except Exception:
                    continue
                if raw:
                    os.write(out_fd, raw)
            elif mtype == "invite":
                invite_code = msg.get("code", "")
                _stderr_line(f"[invite] {invite_code}")
            elif mtype == "participants":
                main_user = msg.get("main_user", "")
                users = list(msg.get("users", []))
                _stderr_line(f"[party] host={main_user} users={', '.join(users)}")
            elif mtype == "system":
                _stderr_line(f"[system] {msg.get('message', '')}")
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
            _stderr_line("[system] shared terminal mode active (Ctrl-] to disconnect local client)")
            while connected:
                chunk = await loop.run_in_executor(None, os.read, fd, 1024)
                if not chunk:
                    break
                if chunk == b"\x1d":
                    break
                await transport.send_input_bytes(chunk)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)

    await transport.connect()
    connected = True
    _stderr_line("[system] connected")

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
