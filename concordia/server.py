import asyncio
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import websockets

from .dedupe import build_deduped_prompt
from .protocol import decode, encode
from .config import load_env
from .debug import debug_print
from .utils import Invite, format_invite, generate_token


@dataclass
class PromptItem:
    user: str
    text: str
    ts: float


@dataclass
class PartyState:
    invite: Invite
    creator: str
    claude_command: str
    dedupe_window: float
    min_prompts: int
    pending: List[PromptItem] = field(default_factory=list)
    connections: Dict[str, websockets.WebSocketServerProtocol] = field(default_factory=dict)
    claude_process: Optional[asyncio.subprocess.Process] = None
    claude_ready: asyncio.Event = field(default_factory=asyncio.Event)


class PartyServer:
    def __init__(self, state: PartyState):
        self.state = state
        self._last_prompt_ts: Optional[float] = None
        self._lock = asyncio.Lock()
        self._pump_tasks: List[asyncio.Task] = []

    async def start(self, host: str, port: int) -> None:
        async with websockets.serve(self._handler, host, port):
            debug_print("party created")
            debug_print(f"invite code: {format_invite(self.state.invite.host, self.state.invite.port, self.state.invite.token)}")
            try:
                res = await self._start_claude()
                if not res:
                    debug_print("[CMD] Failed to run claude")
                    await self.shutdown()
                await self._dedupe_loop()
            finally:
                debug_print("[CMD] Failed to run claude")
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
            name = msg.get("user") or "user"
            self.state.connections[name] = websocket
            await self._broadcast({"type": "system", "message": f"{name} joined"})
            await self._broadcast_participants()
            async for raw in websocket:
                msg = decode(raw)
                if msg.get("type") == "prompt":
                    await self._enqueue_prompt(name, msg.get("text", ""))
                elif msg.get("type") == "ping":
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

    async def _broadcast_participants(self) -> None:
        await self._broadcast(
            {
                "type": "participants",
                "main_user": self.state.creator,
                "users": sorted(self.state.connections.keys()),
            }
        )

    async def _enqueue_prompt(self, user: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        async with self._lock:
            self.state.pending.append(PromptItem(user=user, text=text, ts=time.time()))
            self._last_prompt_ts = time.time()
        await self._broadcast({"type": "system", "message": f"received prompt from {user}"})

    async def _dedupe_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if not self.state.pending:
                continue
            if self._last_prompt_ts is None:
                continue
            if time.time() - self._last_prompt_ts < self.state.dedupe_window:
                continue
            if not self.state.claude_ready.is_set():
                continue
            async with self._lock:
                batch = list(self.state.pending)
                self.state.pending.clear()
                self._last_prompt_ts = None
            if len(batch) < self.state.min_prompts:
                continue
            await self._process_batch(batch)

    async def _process_batch(self, batch: List[PromptItem]) -> None:
        await self._broadcast({"type": "system", "message": f"deduping {len(batch)} prompts"})
        prompts = [{"user": item.user, "text": item.text} for item in batch]
        api_key = os.environ.get("GEMINI_API_KEY", "")
        try:
            combined = await asyncio.to_thread(build_deduped_prompt, prompts, api_key)
        except Exception as exc:
            # If API error (bad key), invalidate it for next run
            if "Gemini API error" in str(exc):
                os.environ["GEMINI_API_KEY"] = ""
                from .config import env_path
                env_path().write_text("", encoding="utf-8")
                await self._broadcast({"type": "error", "message": f"API key invalid: {exc}. Restart to re-enter."})
            else:
                await self._broadcast({"type": "error", "message": f"dedupe failed: {exc}"})
            return
        await self._broadcast({"type": "system", "message": "running claude"})
        self.state.claude_ready.clear()
        await self._write_prompt_to_claude(combined)

    async def _run_claude(self, prompt: str) -> None:
        if not prompt.strip():
            await self._broadcast({"type": "error", "message": "empty prompt"})
            return
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".prompt", encoding="utf-8") as f:
            f.write(prompt)
            prompt_path = f.name
        cmd = self.state.claude_command.format(prompt_file=prompt_path)
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def pump(stream, label: str) -> None:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                await self._broadcast({"type": "output", "stream": label, "text": text})
                debug_print(text)

        await asyncio.gather(pump(process.stdout, "stdout"), pump(process.stderr, "stderr"))
        code = await process.wait()
        await self._broadcast({"type": "system", "message": f"claude exited {code}"})

    async def _start_claude(self) -> bool:
        """Start Claude Code in interactive mode. Returns True if successful."""
        cmd = self.state.claude_command.replace("{prompt_file}", "-")
        try:
            debug_print("[CLAUDE-CODE-CONCORDIA] attempting claude run")
            self.state.claude_process = await asyncio.create_subprocess_shell(
                cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await self._broadcast({"type": "system", "message": "claude started (interactive mode)"})
            self._pump_tasks.append(asyncio.create_task(self._pump_claude_stdout()))
            self._pump_tasks.append(asyncio.create_task(self._pump_claude_stderr()))
            return True
        except Exception as exc:
            debug_print(f"[ERROR] failed to start claude: {exc}", file=sys.stderr)
            await self._broadcast({"type": "error", "message": f"failed to start claude: {exc}"})
            return False

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self.state.claude_process:
            self.state.claude_process.terminate()
            try:
                await asyncio.wait_for(self.state.claude_process.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                self.state.claude_process.kill()
        for task in getattr(self, '_pump_tasks', []):
            task.cancel()

    async def _write_prompt_to_claude(self, prompt: str) -> None:
        """Write prompt to Claude stdin."""
        if not prompt.strip():
            await self._broadcast({"type": "error", "message": "Cannot send empty prompt"})
            return
        if not self.state.claude_process or not self.state.claude_process.stdin:
            await self._broadcast({"type": "error", "message": "Claude not running"})
            self.state.claude_ready.set()
            return
        try:
            async with self._lock:
                self.state.claude_process.stdin.write(prompt.encode() + b"\n")
                await asyncio.wait_for(self.state.claude_process.stdin.drain(), timeout=5.0)
        except asyncio.TimeoutError:
            await self._broadcast({"type": "error", "message": "Prompt delivery timeout"})
            self.state.claude_ready.set()
        except Exception as exc:
            await self._broadcast({"type": "error", "message": f"Failed to send prompt: {exc}"})
            self.state.claude_ready.set()

    async def _pump_claude_stdout(self) -> None:
        """Stream Claude stdout to all clients."""
        if not self.state.claude_process or not self.state.claude_process.stdout:
            return
        try:
            while True:
                line = await self.state.claude_process.stdout.readline()
                if not line:
                    self.state.claude_ready.set()
                    await self._broadcast({"type": "system", "message": "claude disconnected"})
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                await self._broadcast({"type": "output", "text": text})
                debug_print(text)
                if text == ">" or text.endswith(">>> "):
                    self.state.claude_ready.set()
                    await self._broadcast({"type": "system", "message": "claude ready for next prompt"})
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            debug_print(f"Error in stdout pump: {exc}", file=sys.stderr)
            await self._broadcast({"type": "error", "message": f"stdout pump error: {exc}"})

    async def _pump_claude_stderr(self) -> None:
        """Stream Claude stderr to all clients."""
        if not self.state.claude_process or not self.state.claude_process.stderr:
            return
        try:
            while True:
                line = await self.state.claude_process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                await self._broadcast({"type": "output", "text": text})
                debug_print(text, file=sys.stderr)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            debug_print(f"Error in stderr pump: {exc}", file=sys.stderr)
            await self._broadcast({"type": "error", "message": f"stderr pump error: {exc}"})


def create_party_state(
    creator: str,
    host: str,
    port: int,
    public_host: str,
    claude_command: str,
    dedupe_window: float,
    min_prompts: int,
    token: Optional[str] = None,
) -> PartyState:
    token = token or generate_token(16)
    invite = Invite(host=public_host, port=port, token=token)
    return PartyState(
        invite=invite,
        creator=creator,
        claude_command=claude_command,
        dedupe_window=dedupe_window,
        min_prompts=min_prompts,
    )


async def run_server(
    creator: str,
    host: str,
    port: int,
    public_host: str,
    claude_command: str,
    dedupe_window: float,
    min_prompts: int,
    token: Optional[str] = None,
) -> None:
    load_env()
    state = create_party_state(
        creator, host, port, public_host, claude_command, dedupe_window, min_prompts, token=token
    )
    server = PartyServer(state)
    await server.start(host, port)
