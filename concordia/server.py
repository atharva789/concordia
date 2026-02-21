import asyncio
import base64
import contextlib
import os
import pty
import sys
from dataclasses import dataclass, field
from typing import Dict, Optional

import websockets

from .config import load_env
from .debug import debug_print
from .protocol import decode, encode
from .utils import Invite, format_invite, generate_token


@dataclass
class PartyState:
    invite: Invite
    creator: str
    claude_command: str
    project_dir: str
    claude_start_cmd: str = ""
    claude_master_fd: int = -1
    claude_process: Optional[asyncio.subprocess.Process] = None
    claude_stdout: Optional[asyncio.StreamReader] = None
    claude_stderr: Optional[asyncio.StreamReader] = None
    env: Dict[str, str] = field(default_factory=dict)
    connections: Dict[str, websockets.WebSocketServerProtocol] = field(default_factory=dict)


class PartyServer:
    def __init__(self, state: PartyState):
        self.state = state
        self.start_cmd: str = state.claude_command or "claude --dangerously-skip-permissions"
        self.state.claude_start_cmd = self.start_cmd
        self._write_lock = asyncio.Lock()
        self._claude_reader_task: Optional[asyncio.Task] = None

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
        async with websockets.serve(self._handler, host, port):
            debug_print("party created")
            debug_print(
                f"invite code: {format_invite(self.state.invite.host, self.state.invite.port, self.state.invite.token)}"
            )
            try:
                if not await self._start_claude():
                    debug_print("[CMD] Failed to run claude")
                    return
                if not self.state.claude_process:
                    await self._broadcast({"type": "error", "message": "Claude process missing"})
                    return
                await self.state.claude_process.wait()
                await self._broadcast({"type": "system", "message": "claude process exited"})
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
                msg = decode(raw)
                mtype = msg.get("type")
                if mtype == "input_bytes":
                    data_b64 = msg.get("data_b64", "")
                    try:
                        chunk = base64.b64decode(data_b64)
                    except Exception:
                        await websocket.send(encode({"type": "error", "message": "invalid input_bytes payload"}))
                        continue
                    await self._write_input_bytes(chunk)
                elif mtype == "ping":
                    await websocket.send(encode({"type": "pong"}))
        except websockets.ConnectionClosed:
            pass
        finally:
            if name and name in self.state.connections:
                self.state.connections.pop(name, None)
                await self._broadcast({"type": "system", "message": f"{name} left"})
                await self._broadcast_participants()

    async def _broadcast(self, message: Dict) -> None:
        if not self.state.connections:
            return
        raw = encode(message)
        dead = []
        for name, ws in self.state.connections.items():
            try:
                await ws.send(raw)
            except websockets.ConnectionClosed:
                dead.append(name)
        for name in dead:
            self.state.connections.pop(name, None)

        mtype = message.get("type")
        if mtype in {"system", "error"}:
            text = message.get("message", "")
            if text:
                debug_print(f"[{mtype}] {text}")

    async def _broadcast_participants(self) -> None:
        await self._broadcast(
            {
                "type": "participants",
                "main_user": self.state.creator,
                "users": sorted(self.state.connections.keys()),
            }
        )

    async def _start_claude(self) -> bool:
        self.state.env = os.environ.copy()
        self.state.env.pop("ANTROPIC_API_KEY", None)
        self.state.env.pop("ANTHROPIC_API_KEY", None)

        debug_print(f"Running claude command: {self.start_cmd}")
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
            await self._broadcast({"type": "system", "message": "claude started (interactive mode)"})
            return True
        except Exception as exc:
            debug_print(f"[ERROR] failed to start claude: {exc}", file=sys.stderr)
            await self._broadcast({"type": "error", "message": f"failed to start claude: {exc}"})
            return False

    async def _write_input_bytes(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self.state.claude_master_fd < 0:
            await self._broadcast({"type": "error", "message": "Claude PTY not initialized"})
            return
        try:
            async with self._write_lock:
                os.write(self.state.claude_master_fd, chunk)
        except Exception as exc:
            await self._broadcast({"type": "error", "message": f"Failed to write to Claude PTY: {exc}"})

    async def _broadcast_output_bytes(self, chunk: bytes, stream: str) -> None:
        if not chunk:
            return
        payload = base64.b64encode(chunk).decode("ascii")
        await self._broadcast({"type": "output_bytes", "stream": stream, "data_b64": payload})

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
            await self._broadcast_output_bytes(data, "stdout")

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
    claude_command: str,
    token: Optional[str] = None,
) -> PartyState:
    token = token or generate_token(16)
    invite = Invite(host=public_host, port=invite_port, token=token)
    return PartyState(
        invite=invite,
        creator=creator,
        claude_command=claude_command,
        project_dir=project_dir,
    )


async def run_server(
    creator: str,
    host: str,
    port: int,
    public_host: str,
    invite_port: int,
    project_dir: str,
    claude_command: str,
    token: Optional[str] = None,
) -> None:
    load_env()
    state = create_party_state(
        creator,
        public_host,
        invite_port,
        project_dir,
        claude_command,
        token=token,
    )
    server = PartyServer(state)
    await server.start(host, port)
