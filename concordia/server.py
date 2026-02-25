import asyncio
import base64
import contextlib
import os
import pty
import shlex
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

import websockets

from .config import load_env
from .debug import debug_print
from .protocol import decode, encode
from .utils import Invite, format_invite, generate_token


@dataclass
class PartyState:
    invite: Invite
    creator: str
    program_command: str
    project_dir: str
    dangerously_skip_permissions: bool = False
    claude_start_cmd: str = ""
    claude_master_fd: int = -1
    claude_process: Optional[asyncio.subprocess.Process] = None
    claude_stdout: Optional[asyncio.StreamReader] = None
    claude_stderr: Optional[asyncio.StreamReader] = None
    env: Dict[str, str] = field(default_factory=dict)
    connections: Dict[str, websockets.WebSocketServerProtocol] = field(default_factory=dict)


class PartyServer:
    SHELL_NAMES: Set[str] = {"bash", "zsh", "sh", "fish"}
    BLOCKED_COMMANDS: Set[str] = {
        "rm",
        "rmdir",
        "mv",
        "chmod",
        "chown",
        "chgrp",
        "unlink",
        "truncate",
        "dd",
        "mkfs",
        "mount",
        "umount",
        "sudo",
        "su",
        "reboot",
        "shutdown",
        "poweroff",
        "init",
        "kill",
        "killall",
        "pkill",
    }
    BLOCKED_INTERPRETERS: Set[str] = {
        "bash",
        "zsh",
        "sh",
        "fish",
        "python",
        "python3",
        "node",
        "ruby",
        "perl",
        "lua",
        "pwsh",
        "powershell",
    }
    SAFE_GIT_SUBCOMMANDS: Set[str] = {"status", "diff", "log", "show", "branch", "rev-parse", "fetch", "remote"}

    def __init__(self, state: PartyState):
        self.state = state
        self.start_cmd: str = (state.program_command or "").strip() or "bash"
        self.state.claude_start_cmd = self.start_cmd
        self._write_lock = asyncio.Lock()
        self._claude_reader_task: Optional[asyncio.Task] = None
        self._permission_notice_sent: Set[str] = set()
        self._remote_line_buffers: Dict[str, bytearray] = {}
        self._project_root = Path(self.state.project_dir).resolve()
        self._program_name = self._extract_program_name(self.start_cmd)
        self._program_is_shell = self._program_name in self.SHELL_NAMES

    def _reserve_connection_name(self, requested: str) -> str:
        base = (requested or "user").strip() or "user"
        if base not in self.state.connections:
            return base
        idx = 2
        while True:
            candidate = f"{base}-{idx}"
            if candidate not in self.state.connections:
                return candidate
            idx += 1

    async def start(self, host: str, port: int) -> None:
        async with websockets.serve(self._handler, host, port, compression=None, max_queue=256):
            debug_print("party created")
            debug_print(
                f"invite code: {format_invite(self.state.invite.host, self.state.invite.port, self.state.invite.token)}"
            )
            try:
                if not await self._start_program():
                    debug_print("[CMD] Failed to run program")
                    return
                if not self.state.claude_process:
                    await self._broadcast({"type": "error", "message": "Program process missing"})
                    return
                if not self.state.dangerously_skip_permissions:
                    if self._program_is_shell:
                        await self._broadcast(
                            {
                                "type": "system",
                                "message": (
                                    "safe permissions ON: remote shell input filtered; "
                                    f"root scope is {self._project_root}"
                                ),
                            }
                        )
                    else:
                        await self._broadcast(
                            {
                                "type": "system",
                                "message": (
                                    "safe permissions ON: remote participants are view-only "
                                    "for non-shell programs. Use --dangerously-skip-permissions to bypass."
                                ),
                            }
                        )
                await self.state.claude_process.wait()
                await self._broadcast({"type": "system", "message": "program process exited"})
            finally:
                await self.shutdown()

    async def _handler(self, websocket: websockets.WebSocketServerProtocol) -> None:
        name = None
        try:
            raw = await websocket.recv()
            msg = decode(raw)
            if msg.get("type") != "hello":
                await websocket.send(encode({"type": "error", "message": "missing hello"}))
                return
            if msg.get("token") != self.state.invite.token:
                await websocket.send(encode({"type": "error", "message": "invalid invite"}))
                return

            requested_name = msg.get("user") or "user"
            name = self._reserve_connection_name(requested_name)
            self.state.connections[name] = websocket
            if name != requested_name:
                await websocket.send(
                    encode(
                        {
                            "type": "system",
                            "message": f"name '{requested_name}' already in use; joined as '{name}'",
                        }
                    )
                )

            await websocket.send(
                encode(
                    {
                        "type": "invite",
                        "code": format_invite(
                            self.state.invite.host,
                            self.state.invite.port,
                            self.state.invite.token,
                        ),
                    }
                )
            )
            await self._broadcast({"type": "system", "message": f"{name} joined"})
            await self._broadcast_participants()

            async for raw in websocket:
                if isinstance(raw, (bytes, bytearray)):
                    await self._handle_client_input(name, websocket, bytes(raw))
                    continue
                msg = decode(raw)
                mtype = msg.get("type")
                if mtype == "input_bytes":
                    data_b64 = msg.get("data_b64", "")
                    try:
                        chunk = base64.b64decode(data_b64)
                    except Exception:
                        await websocket.send(encode({"type": "error", "message": "invalid input_bytes payload"}))
                        continue
                    await self._handle_client_input(name, websocket, chunk)
                elif mtype == "ping":
                    await websocket.send(encode({"type": "pong"}))
        except websockets.ConnectionClosed:
            pass
        finally:
            if name and name in self.state.connections:
                self.state.connections.pop(name, None)
                self._remote_line_buffers.pop(name, None)
                self._permission_notice_sent.discard(name)
                await self._broadcast({"type": "system", "message": f"{name} left"})
                await self._broadcast_participants()

    def _extract_program_name(self, command: str) -> str:
        try:
            parts = shlex.split(command)
        except ValueError:
            return ""
        if not parts:
            return ""
        return Path(parts[0]).name.lower()

    def _path_within_project_root(self, token: str) -> bool:
        if not token:
            return True
        if token.startswith("~"):
            return False
        if token.startswith(".."):
            return False
        p = Path(token)
        resolved = p.resolve() if p.is_absolute() else (self._project_root / p).resolve()
        return resolved == self._project_root or self._project_root in resolved.parents

    def _validate_remote_shell_line(self, line: str) -> Tuple[bool, str]:
        stripped = line.strip()
        if not stripped:
            return True, ""
        if any(op in stripped for op in ("&&", "||", ";", "|", "`", "$(", ">", "<")):
            return False, "command chaining and subshells are blocked in safe mode"
        try:
            parts = shlex.split(stripped)
        except ValueError:
            return False, "unable to parse command"
        if not parts:
            return True, ""
        cmd = parts[0].lower()
        if cmd in {"cd", "pushd", "popd"}:
            return False, "changing directories is blocked in safe mode"
        if cmd in self.BLOCKED_COMMANDS:
            return False, f"'{cmd}' is blocked in safe mode"
        if cmd in self.BLOCKED_INTERPRETERS:
            return False, f"spawning interpreter '{cmd}' is blocked in safe mode"
        if cmd == "git":
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub and sub not in self.SAFE_GIT_SUBCOMMANDS:
                return False, f"git subcommand '{sub}' is blocked in safe mode"
        for tok in parts[1:]:
            if tok.startswith("-"):
                continue
            if "/" in tok or tok.startswith("."):
                if not self._path_within_project_root(tok):
                    return False, f"path '{tok}' escapes project root"
        return True, ""

    async def _handle_remote_shell_input(
        self,
        user: str,
        websocket: websockets.WebSocketServerProtocol,
        chunk: bytes,
    ) -> None:
        if not chunk:
            return
        buf = self._remote_line_buffers.setdefault(user, bytearray())
        out = bytearray()
        for b in chunk:
            if b in (0x08, 0x7F):  # backspace
                if buf:
                    buf.pop()
                out.append(b)
                continue
            if b in (0x0D, 0x0A):  # newline / carriage return
                line = buf.decode("utf-8", errors="ignore")
                allowed, reason = self._validate_remote_shell_line(line)
                if allowed:
                    out.append(0x0A)
                else:
                    await websocket.send(encode({"type": "error", "message": f"blocked by safe permissions: {reason}"}))
                    out.append(0x03)  # Ctrl-C to clear current line at PTY
                buf.clear()
                continue
            if b == 0x09 or (0x20 <= b <= 0x7E):  # tab or printable ascii
                buf.append(b)
                out.append(b)
                continue
            # Drop other control bytes in safe mode.
        if out:
            await self._write_input_bytes(bytes(out))

    async def _handle_client_input(
        self,
        user: str,
        websocket: websockets.WebSocketServerProtocol,
        chunk: bytes,
    ) -> None:
        if not chunk:
            return
        if user == self.state.creator or self.state.dangerously_skip_permissions:
            await self._write_input_bytes(chunk)
            return
        if not self._program_is_shell:
            if user not in self._permission_notice_sent:
                self._permission_notice_sent.add(user)
                await websocket.send(
                    encode(
                        {
                            "type": "error",
                            "message": (
                                "safe permissions enabled: remote input is disabled for non-shell programs. "
                                "Host can use --dangerously-skip-permissions to bypass."
                            ),
                        }
                    )
                )
            return
        await self._handle_remote_shell_input(user, websocket, chunk)

    async def _broadcast(self, message: Dict) -> None:
        if not self.state.connections:
            return
        raw = encode(message)
        items = list(self.state.connections.items())
        results = await asyncio.gather(
            *(ws.send(raw) for _, ws in items),
            return_exceptions=True,
        )
        for (name, _), res in zip(items, results):
            if isinstance(res, websockets.ConnectionClosed):
                self.state.connections.pop(name, None)

        mtype = message.get("type")
        if mtype in {"system", "error"}:
            text = message.get("message", "")
            if text:
                debug_print(f"[{mtype}] {text}")

    async def _broadcast_raw(self, chunk: bytes) -> None:
        if not self.state.connections or not chunk:
            return
        items = list(self.state.connections.items())
        results = await asyncio.gather(
            *(ws.send(chunk) for _, ws in items),
            return_exceptions=True,
        )
        for (name, _), res in zip(items, results):
            if isinstance(res, websockets.ConnectionClosed):
                self.state.connections.pop(name, None)

    async def _broadcast_participants(self) -> None:
        await self._broadcast(
            {
                "type": "participants",
                "main_user": self.state.creator,
                "users": sorted(self.state.connections.keys()),
            }
        )

    async def _start_program(self) -> bool:
        self.state.env = os.environ.copy()

        debug_print(f"Running program command: {self.start_cmd}")
        try:
            master_fd, slave_fd = pty.openpty()
            process = await asyncio.create_subprocess_shell(
                self.start_cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.state.project_dir if self.state.project_dir else None,
                env=self.state.env,
            )
            os.close(slave_fd)

            self.state.claude_master_fd = master_fd
            self.state.claude_process = process
            self.state.claude_stdout = process.stdout
            self.state.claude_stderr = process.stderr

            self._claude_reader_task = asyncio.create_task(self._read_claude_and_broadcast())
            await self._broadcast({"type": "system", "message": "program started (interactive mode)"})
            return True
        except Exception as exc:
            debug_print(f"[ERROR] failed to start program: {exc}", file=sys.stderr)
            await self._broadcast({"type": "error", "message": f"failed to start program: {exc}"})
            return False

    async def _write_input_bytes(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self.state.claude_master_fd < 0:
            await self._broadcast({"type": "error", "message": "Program PTY not initialized"})
            return
        try:
            async with self._write_lock:
                os.write(self.state.claude_master_fd, chunk)
        except Exception as exc:
            await self._broadcast({"type": "error", "message": f"Failed to write to program PTY: {exc}"})

    async def _read_claude_and_broadcast(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if self.state.claude_master_fd < 0:
                return
            try:
                data = await loop.run_in_executor(None, os.read, self.state.claude_master_fd, 4096)
            except OSError:
                return
            if not data:
                return
            await self._broadcast_raw(data)

    async def shutdown(self) -> None:
        if self._claude_reader_task:
            self._claude_reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._claude_reader_task
            self._claude_reader_task = None

        if self.state.claude_master_fd >= 0:
            try:
                os.close(self.state.claude_master_fd)
            except OSError:
                pass
            self.state.claude_master_fd = -1

        if self.state.claude_process:
            self.state.claude_process.terminate()
            try:
                await asyncio.wait_for(self.state.claude_process.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                self.state.claude_process.kill()
            self.state.claude_process = None
            self.state.claude_stdout = None
            self.state.claude_stderr = None


def create_party_state(
    creator: str,
    public_host: str,
    invite_port: int,
    project_dir: str,
    program_command: str,
    dangerously_skip_permissions: bool,
    token: Optional[str] = None,
) -> PartyState:
    token = token or generate_token(16)
    invite = Invite(host=public_host, port=invite_port, token=token)
    return PartyState(
        invite=invite,
        creator=creator,
        program_command=program_command,
        project_dir=project_dir,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )


async def run_server(
    creator: str,
    host: str,
    port: int,
    public_host: str,
    invite_port: int,
    project_dir: str,
    program_command: str,
    dangerously_skip_permissions: bool,
    token: Optional[str] = None,
) -> None:
    load_env()
    state = create_party_state(
        creator,
        public_host,
        invite_port,
        project_dir,
        program_command,
        dangerously_skip_permissions,
        token=token,
    )
    server = PartyServer(state)
    await server.start(host, port)
