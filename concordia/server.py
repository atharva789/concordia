import asyncio
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import websockets

from .dedupe import build_deduped_prompt, build_session_summary
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
    project_dir: str
    claude_start_cmd: str = ""
    claude_session_id: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    pending: List[PromptItem] = field(default_factory=list)
    connections: Dict[str, websockets.WebSocketServerProtocol] = field(default_factory=dict)
    prompt_log_path: Optional[str] = None
    deduped_prompts: List[str] = field(default_factory=list)
    context_written: bool = False


class PartyServer:
    def __init__(self, state: PartyState):
        self.state = state
        self.start_cmd: str = """claude -p "understand the codebase" --dangerously-skip-permissions --output-format json | jq -r '.session_id'"""
        self.state.claude_start_cmd = self.start_cmd
        self._last_prompt_ts: Optional[float] = None
        self._lock = asyncio.Lock()

    async def start(self, host: str, port: int) -> None:
        async with websockets.serve(self._handler, host, port):
            debug_print("party created")
            debug_print(f"invite code: {format_invite(self.state.invite.host, self.state.invite.port, self.state.invite.token)}")
            try:
                res = await self._start_claude()
                if not res:
                    debug_print("[CMD] Failed to run claude")
                    await self.shutdown()
                    return
                await self._dedupe_loop()
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
            async with self._lock:
                batch = list(self.state.pending)
                self.state.pending.clear()
                self._last_prompt_ts = None
            if len(batch) < self.state.min_prompts:
                continue
            debug_print("[_dedupe_loop() prompt deduped!]")
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
        self.state.deduped_prompts.append(combined)
        await self._write_prompt_to_claude(combined)

    async def _start_claude(self) -> bool:
        """Start Claude once and capture a resumable session id."""
        prompt_file = tempfile.NamedTemporaryFile("w", delete=False, suffix=".prompt", encoding="utf-8")
        prompt_file.close()
        self.state.prompt_log_path = prompt_file.name
        self.state.env = os.environ.copy()
        self.state.env.pop("ANTROPIC_API_KEY", None)
        self.state.env.pop("ANTHROPIC_API_KEY", None)
        cmd = self.start_cmd
        debug_print(f"Running claude command: {cmd}")
        debug_print(f"Session prompt log: {self.state.prompt_log_path}")
        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.state.project_dir if self.state.project_dir else None,
                env=self.state.env,
            )
            stdout, stderr = await process.communicate()
            stderr_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
            if stderr_text:
                debug_print(f"[start_claude()] stderr: {stderr_text}", file=sys.stderr)

            self.state.claude_session_id = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
            if process.returncode != 0 or not self.state.claude_session_id:
                debug_print("[start_claude()] Failed to get session id", file=sys.stderr)
                return False

            await self._broadcast({"type": "system", "message": "claude started (interactive mode)"})
            debug_print(f"[start_claude()] Session ID: {self.state.claude_session_id}")
            return True
        except Exception as exc:
            debug_print(f"[ERROR] failed to start claude: {exc}", file=sys.stderr)
            await self._broadcast({"type": "error", "message": f"failed to start claude: {exc}"})
            return False

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        await self._write_context_file()
        if self.state.prompt_log_path:
            try:
                os.unlink(self.state.prompt_log_path)
            except OSError:
                pass
            self.state.prompt_log_path = None

    async def _write_prompt_to_claude(self, prompt: str) -> None:
        """Resume Claude session with prompt and stream result to participants."""
        if not prompt.strip():
            await self._broadcast({"type": "error", "message": "Cannot send empty prompt"})
            return
        if not self.state.claude_session_id:
            await self._broadcast({"type": "error", "message": "Claude session not initialized"})
            return
        try:
            async with self._lock:
                if self.state.prompt_log_path:
                    with open(self.state.prompt_log_path, "a", encoding="utf-8") as f:
                        f.write(prompt.rstrip() + "\n\n")
                cmd = (
                    f"claude -p {shlex.quote(prompt)} "
                    f"--resume {shlex.quote(self.state.claude_session_id)} "
                    "--dangerously-skip-permissions"
                )
                process = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.state.project_dir if self.state.project_dir else None,
                    env=self.state.env,
                )
                stdout, stderr = await process.communicate()
                stdout_text = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
                stderr_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""

                if process.returncode != 0:
                    if stdout_text:
                        for line in stdout_text.splitlines():
                            await self._broadcast({"type": "output", "text": line})
                    if stderr_text:
                        for line in stderr_text.splitlines():
                            await self._broadcast({"type": "output", "text": line})
                    await self._broadcast(
                        {
                            "type": "error",
                            "message": f"claude command failed (code={process.returncode})",
                        }
                    )
                    return

                if stderr_text:
                    for line in stderr_text.splitlines():
                        await self._broadcast({"type": "output", "text": line})
                if stdout_text:
                    for line in stdout_text.splitlines():
                        await self._broadcast({"type": "output", "text": line})
        except Exception as exc:
            await self._broadcast({"type": "error", "message": f"Failed to send prompt: {exc}"})

    async def _write_context_file(self) -> None:
        if self.state.context_written:
            return
        self.state.context_written = True

        output_path = Path.cwd() / "concordia-context.md"
        prompts = [p.strip() for p in self.state.deduped_prompts if p.strip()]
        if not prompts:
            content = "# Concordia Context\n\nNo deduped prompts were processed in this session.\n"
            output_path.write_text(content, encoding="utf-8")
            debug_print(f"Wrote {output_path}")
            return

        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        try:
            summary = await asyncio.to_thread(build_session_summary, prompts, api_key)
        except Exception as exc:
            debug_print(f"Context summary generation failed: {exc}", file=sys.stderr)
            summary = self._fallback_context_summary(prompts)

        body = summary.strip() if summary.strip() else self._fallback_context_summary(prompts)
        content = f"# Concordia Context\n\n{body}\n"
        output_path.write_text(content, encoding="utf-8")
        debug_print(f"Wrote {output_path}")

    def _fallback_context_summary(self, prompts: List[str]) -> str:
        lines = ["## Deduped Prompts", ""]
        for idx, prompt in enumerate(prompts, start=1):
            lines.append(f"### Prompt {idx}")
            lines.append(prompt)
            lines.append("")
        return "\n".join(lines).rstrip()


def create_party_state(
    creator: str,
    host: str,
    port: int,
    public_host: str,
    invite_port: int,
    project_dir: str,
    claude_command: str,
    dedupe_window: float,
    min_prompts: int,
    token: Optional[str] = None,
) -> PartyState:
    token = token or generate_token(16)
    invite = Invite(host=public_host, port=invite_port, token=token)
    return PartyState(
        invite=invite,
        creator=creator,
        claude_command=claude_command,
        project_dir=project_dir,
        dedupe_window=dedupe_window,
        min_prompts=min_prompts,
    )


async def run_server(
    creator: str,
    host: str,
    port: int,
    public_host: str,
    invite_port: int,
    project_dir: str,
    claude_command: str,
    dedupe_window: float,
    min_prompts: int,
    token: Optional[str] = None,
) -> None:
    load_env()
    state = create_party_state(
        creator,
        host,
        port,
        public_host,
        invite_port,
        project_dir,
        claude_command,
        dedupe_window,
        min_prompts,
        token=token,
    )
    server = PartyServer(state)
    await server.start(host, port)
