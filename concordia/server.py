import asyncio
import base64
import contextlib
import hashlib
import json
import os
import pty
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, Optional, Set, Tuple

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
    claude_start_cmd: str = ""
    claude_master_fd: int = -1
    claude_process: Optional[asyncio.subprocess.Process] = None
    claude_stdout: Optional[asyncio.StreamReader] = None
    claude_stderr: Optional[asyncio.StreamReader] = None
    env: Dict[str, str] = field(default_factory=dict)
    connections: Dict[str, websockets.WebSocketServerProtocol] = field(default_factory=dict)
    compliance_mode: str = "strict"
    allow_remote_input: bool = False
    audit_log_path: Optional[str] = None
    require_client_claude_check: bool = False
    client_claude_check_max_age_sec: float = 600.0
    estimate_token_usage: bool = False
    usage_estimate_window_sec: float = 8.0
    usage_estimate_path: Optional[str] = None


class PartyServer:
    MAX_INPUT_CHUNK_BYTES = 8192
    MAX_INPUT_BYTES_PER_SEC = 32768

    def __init__(self, state: PartyState):
        self.state = state
        self.start_cmd: str = (state.program_command or "").strip() or "claude --dangerously-skip-permissions"
        self.state.claude_start_cmd = self.start_cmd
        self._write_lock = asyncio.Lock()
        self._claude_reader_task: Optional[asyncio.Task] = None
        self._policy_notice_sent: Set[str] = set()
        self._input_windows: Dict[str, Deque[Tuple[float, int]]] = defaultdict(deque)
        self._usage_input_events: Deque[Tuple[float, str, int, int]] = deque()
        self._usage_input_bytes_by_user: Dict[str, int] = defaultdict(int)
        self._usage_prompt_estimate_by_user: Dict[str, int] = defaultdict(int)
        self._usage_output_estimate_by_user: Dict[str, float] = defaultdict(float)
        self._usage_output_total_bytes: int = 0
        self._usage_unattributed_output_bytes: float = 0.0

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

    def _input_allowed_for_user(self, user: str) -> bool:
        if user == self.state.creator:
            return True
        return self.state.allow_remote_input

    def _within_input_rate_limit(self, user: str, chunk_len: int) -> bool:
        now = time.time()
        window = self._input_windows[user]
        while window and now - window[0][0] > 1.0:
            window.popleft()
        in_window = sum(size for _, size in window)
        if in_window + chunk_len > self.MAX_INPUT_BYTES_PER_SEC:
            return False
        window.append((now, chunk_len))
        return True

    def _append_audit_log(self, user: str, chunk: bytes, accepted: bool, reason: str) -> None:
        path = self.state.audit_log_path
        if not path:
            return
        record = {
            "ts": time.time(),
            "user": user,
            "accepted": accepted,
            "reason": reason,
            "bytes": len(chunk),
            "sha256": hashlib.sha256(chunk).hexdigest(),
            "compliance_mode": self.state.compliance_mode,
        }
        self._append_audit_record(record)

    def _append_audit_record(self, record: Dict) -> None:
        path = self.state.audit_log_path
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
        except OSError:
            pass

    def _usage_prune_events(self, now_ts: float) -> None:
        window = max(float(self.state.usage_estimate_window_sec), 0.1)
        while self._usage_input_events and now_ts - self._usage_input_events[0][0] > window:
            self._usage_input_events.popleft()

    def _record_usage_input(self, user: str, chunk: bytes) -> None:
        if not self.state.estimate_token_usage:
            return
        size = len(chunk)
        if size <= 0:
            return
        # PTY enter key can appear as CR, LF, or CRLF depending on terminal mode.
        cr_count = chunk.count(b"\r")
        lf_count = chunk.count(b"\n")
        if cr_count and lf_count:
            prompts = max(cr_count, lf_count)
        else:
            prompts = cr_count + lf_count
        now_ts = time.time()
        self._usage_input_bytes_by_user[user] += size
        self._usage_prompt_estimate_by_user[user] += prompts
        self._usage_input_events.append((now_ts, user, size, prompts))
        self._usage_prune_events(now_ts)

    def _estimate_output_attribution(self, chunk_size: int) -> None:
        if not self.state.estimate_token_usage or chunk_size <= 0:
            return
        now_ts = time.time()
        self._usage_prune_events(now_ts)
        self._usage_output_total_bytes += chunk_size
        if not self._usage_input_events:
            self._usage_unattributed_output_bytes += float(chunk_size)
            return
        weights: Dict[str, float] = defaultdict(float)
        for _, user, byte_count, prompt_count in self._usage_input_events:
            # Prompt boundaries (newline submissions) get a small boost over raw keypress bytes.
            weights[user] += float(byte_count) + float(prompt_count) * 64.0
        total_weight = sum(weights.values())
        if total_weight <= 0:
            self._usage_unattributed_output_bytes += float(chunk_size)
            return
        for user, weight in weights.items():
            self._usage_output_estimate_by_user[user] += float(chunk_size) * (weight / total_weight)

    def _write_usage_estimate_report(self) -> None:
        if not self.state.estimate_token_usage:
            return
        output_path = self.state.usage_estimate_path or str(Path.cwd() / "concordia-usage-estimate.json")
        users = sorted(
            set(self._usage_input_bytes_by_user.keys())
            | set(self._usage_prompt_estimate_by_user.keys())
            | set(self._usage_output_estimate_by_user.keys())
        )
        by_user = []
        for user in users:
            by_user.append(
                {
                    "user": user,
                    "input_bytes": int(self._usage_input_bytes_by_user.get(user, 0)),
                    "prompt_count_estimate": int(self._usage_prompt_estimate_by_user.get(user, 0)),
                    "output_bytes_attributed_estimate": round(float(self._usage_output_estimate_by_user.get(user, 0.0)), 2),
                }
            )
        report = {
            "generated_at": time.time(),
            "method": "active_writer_window_v1",
            "estimated": True,
            "disclaimer": (
                "This report is an approximation for shared PTY sessions and is not exact token accounting. "
                "Use API-native usage fields for exact per-request token usage."
            ),
            "window_sec": float(self.state.usage_estimate_window_sec),
            "totals": {
                "output_bytes_total": int(self._usage_output_total_bytes),
                "output_bytes_unattributed_estimate": round(float(self._usage_unattributed_output_bytes), 2),
            },
            "by_user": by_user,
        }
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=True)
        except OSError:
            return
        self._append_audit_record(
            {
                "ts": time.time(),
                "event": "usage_estimate_written",
                "path": output_path,
                "output_bytes_total": int(self._usage_output_total_bytes),
                "window_sec": float(self.state.usage_estimate_window_sec),
            }
        )

    def _validate_client_verification(self, user: str, payload: Dict) -> Tuple[bool, str]:
        if user == self.state.creator:
            return True, "host_user_exempt"
        if not self.state.require_client_claude_check:
            return True, "probe_not_required"
        if not isinstance(payload, dict):
            return False, "missing_probe"
        if payload.get("method") != "local_claude_probe_v1":
            return False, "invalid_probe_method"
        if not payload.get("ok", False):
            return False, "probe_not_ok"
        checked_at = payload.get("checked_at")
        if not isinstance(checked_at, (int, float)):
            return False, "invalid_checked_at"
        now = time.time()
        if checked_at > now + 5:
            return False, "probe_time_in_future"
        if now - float(checked_at) > float(self.state.client_claude_check_max_age_sec):
            return False, "probe_too_old"
        cmd = str(payload.get("command", "")).strip().lower()
        if "claude" not in cmd:
            return False, "probe_command_not_claude"
        return True, "probe_ok"

    async def _handle_client_input(self, user: str, websocket: websockets.WebSocketServerProtocol, chunk: bytes) -> None:
        if not chunk:
            return
        if len(chunk) > self.MAX_INPUT_CHUNK_BYTES:
            self._append_audit_log(user, chunk, accepted=False, reason="chunk_too_large")
            await websocket.send(encode({"type": "error", "message": "input chunk too large"}))
            return
        if not self._input_allowed_for_user(user):
            self._append_audit_log(user, chunk, accepted=False, reason="remote_input_disabled")
            if user not in self._policy_notice_sent:
                self._policy_notice_sent.add(user)
                await websocket.send(
                    encode(
                        {
                            "type": "error",
                            "message": "host compliance policy: remote input disabled (view-only mode)",
                        }
                    )
                )
            return
        if not self._within_input_rate_limit(user, len(chunk)):
            self._append_audit_log(user, chunk, accepted=False, reason="rate_limited")
            await websocket.send(encode({"type": "error", "message": "input rate limited"}))
            return

        self._append_audit_log(user, chunk, accepted=True, reason="accepted")
        self._record_usage_input(user, chunk)
        await self._write_input_bytes(chunk)

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
                await self._broadcast(
                    {
                        "type": "system",
                        "message": (
                            f"compliance={self.state.compliance_mode} "
                            f"remote_input={'enabled' if self.state.allow_remote_input else 'disabled'} "
                            f"client_probe={'required' if self.state.require_client_claude_check else 'optional'}"
                        ),
                    }
                )
                if not self.state.claude_process:
                    await self._broadcast({"type": "error", "message": "Program process missing"})
                    return
                await self.state.claude_process.wait()
                await self._broadcast({"type": "system", "message": "program process exited"})
            finally:
                await self.shutdown()

    async def _handler(self, websocket: websockets.WebSocketServerProtocol) -> None:
        name = None
        try:
            raw = await websocket.recv()
            if isinstance(raw, (bytes, bytearray)):
                await websocket.send(encode({"type": "error", "message": "missing hello"}))
                return
            msg = decode(raw)
            if msg.get("type") != "hello":
                await websocket.send(encode({"type": "error", "message": "missing hello"}))
                return
            if msg.get("token") != self.state.invite.token:
                await websocket.send(encode({"type": "error", "message": "invalid invite"}))
                return

            requested_name = msg.get("user") or "user"
            name = self._reserve_connection_name(requested_name)
            verification_payload = msg.get("client_verification", {})
            verification_ok, verification_reason = self._validate_client_verification(name, verification_payload)
            self._append_audit_record(
                {
                    "ts": time.time(),
                    "event": "client_join_verification",
                    "user": name,
                    "accepted": verification_ok,
                    "reason": verification_reason,
                    "compliance_mode": self.state.compliance_mode,
                }
            )
            if not verification_ok:
                await websocket.send(
                    encode(
                        {
                            "type": "error",
                            "message": (
                                "client verification failed: "
                                f"{verification_reason}. run with local Claude check enabled."
                            ),
                        }
                    )
                )
                return
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
            if not self._input_allowed_for_user(name):
                await websocket.send(
                    encode(
                        {
                            "type": "system",
                            "message": "compliance mode active: this client is view-only (remote input disabled)",
                        }
                    )
                )

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
                self._input_windows.pop(name, None)
                self._policy_notice_sent.discard(name)
                await self._broadcast({"type": "system", "message": f"{name} left"})
                await self._broadcast_participants()

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
            if isinstance(res, Exception):
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
            if isinstance(res, Exception):
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
        self.state.env.pop("ANTROPIC_API_KEY", None)
        self.state.env.pop("ANTHROPIC_API_KEY", None)
        if not self.state.audit_log_path:
            self.state.audit_log_path = str(Path.cwd() / "concordia-audit.log")

        debug_print(f"Running claude command: {self.start_cmd}")
        debug_print(
            f"Compliance mode: {self.state.compliance_mode}, "
            f"allow_remote_input={self.state.allow_remote_input}"
        )
        debug_print(f"Audit log: {self.state.audit_log_path}")
        if self.state.estimate_token_usage:
            debug_print(
                f"Usage estimation enabled: window_sec={self.state.usage_estimate_window_sec}, "
                f"path={self.state.usage_estimate_path}"
            )

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
            self._estimate_output_attribution(len(data))
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
        self._write_usage_estimate_report()


def create_party_state(
    creator: str,
    public_host: str,
    invite_port: int,
    project_dir: str,
    claude_command: str,
    compliance_mode: str,
    allow_remote_input: bool,
    audit_log_path: str,
    require_client_claude_check: bool,
    client_claude_check_max_age_sec: float,
    estimate_token_usage: bool,
    usage_estimate_window_sec: float,
    usage_estimate_path: str,
    token: Optional[str] = None,
) -> PartyState:
    token = token or generate_token(16)
    invite = Invite(host=public_host, port=invite_port, token=token)
    return PartyState(
        invite=invite,
        creator=creator,
        program_command=program_command,
        project_dir=project_dir,
        compliance_mode=compliance_mode,
        allow_remote_input=allow_remote_input,
        audit_log_path=audit_log_path,
        require_client_claude_check=require_client_claude_check,
        client_claude_check_max_age_sec=client_claude_check_max_age_sec,
        estimate_token_usage=estimate_token_usage,
        usage_estimate_window_sec=usage_estimate_window_sec,
        usage_estimate_path=usage_estimate_path,
    )


async def run_server(
    creator: str,
    host: str,
    port: int,
    public_host: str,
    invite_port: int,
    project_dir: str,
    claude_command: str,
    compliance_mode: str,
    allow_remote_input: bool,
    audit_log_path: str,
    require_client_claude_check: bool,
    client_claude_check_max_age_sec: float,
    estimate_token_usage: bool,
    usage_estimate_window_sec: float,
    usage_estimate_path: str,
    token: Optional[str] = None,
) -> None:
    load_env()
    state = create_party_state(
        creator,
        public_host,
        invite_port,
        project_dir,
        claude_command,
        compliance_mode,
        allow_remote_input,
        audit_log_path,
        require_client_claude_check,
        client_claude_check_max_age_sec,
        estimate_token_usage,
        usage_estimate_window_sec,
        usage_estimate_path,
        token=token,
    )
    server = PartyServer(state)
    await server.start(host, port)
