import asyncio
import base64
import hashlib
import subprocess
import sys
import time
from typing import AsyncIterator, Dict, Optional

import websockets

from .protocol import decode, encode


async def _read_input(prompt: str) -> Optional[str]:
    return await asyncio.to_thread(lambda: input(prompt))


class ClientTransport:
    def __init__(self, uri: str, token: str, user: str, client_verification: Optional[Dict] = None):
        self.uri = uri
        self.token = token
        self.user = user
        self.client_verification = client_verification
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None

    async def connect(self) -> None:
        self._websocket = await websockets.connect(self.uri, compression=None, max_queue=256)
        hello = {"type": "hello", "user": self.user, "token": self.token}
        if self.client_verification is not None:
            hello["client_verification"] = self.client_verification
        await self._websocket.send(encode(hello))

    async def close(self) -> None:
        if self._websocket:
            await self._websocket.close()
            self._websocket = None

    async def send_input_bytes(self, raw: bytes) -> None:
        if not self._websocket:
            raise RuntimeError("WebSocket is not connected")
        await self._websocket.send(raw)

    @property
    def is_connected(self) -> bool:
        return self._websocket is not None

    async def iter_messages(self) -> AsyncIterator[Dict]:
        if not self._websocket:
            raise RuntimeError("WebSocket is not connected")
        async for raw in self._websocket:
            if isinstance(raw, (bytes, bytearray)):
                yield {"type": "output_raw", "data": bytes(raw)}
            else:
                yield decode(raw)


async def _run_shell_command(cmd: str) -> str:
    def _exec() -> str:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            out = []
            if result.stdout:
                out.append(result.stdout.rstrip())
            if result.stderr:
                out.append(result.stderr.rstrip())
            return "\n".join(part for part in out if part).strip()
        except Exception as exc:
            return f"shell error: {exc}"

    return await asyncio.to_thread(_exec)


async def run_client_verification_probe(command: str, timeout_sec: float) -> Dict:
    started = time.time()
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {
                "method": "local_claude_probe_v1",
                "ok": False,
                "checked_at": time.time(),
                "duration_ms": int((time.time() - started) * 1000),
                "command": command,
                "error": "timeout",
            }
        out = stdout or b""
        err = stderr or b""
        return {
            "method": "local_claude_probe_v1",
            "ok": process.returncode == 0,
            "checked_at": time.time(),
            "duration_ms": int((time.time() - started) * 1000),
            "command": command,
            "returncode": process.returncode,
            "stdout_sha256": hashlib.sha256(out).hexdigest() if out else "",
            "stderr_sha256": hashlib.sha256(err).hexdigest() if err else "",
        }
    except Exception as exc:
        return {
            "method": "local_claude_probe_v1",
            "ok": False,
            "checked_at": time.time(),
            "duration_ms": int((time.time() - started) * 1000),
            "command": command,
            "error": str(exc),
        }


async def run_client_plain(uri: str, token: str, user: str, client_verification: Optional[Dict] = None) -> None:
    transport = ClientTransport(uri=uri, token=token, user=user, client_verification=client_verification)
    await transport.connect()

    async def receiver() -> None:
        async for msg in transport.iter_messages():
            mtype = msg.get("type")
            if mtype == "output_raw":
                raw = msg.get("data", b"")
                if raw:
                    sys.stdout.buffer.write(raw)
                    sys.stdout.buffer.flush()
            elif mtype == "output_bytes":
                data_b64 = msg.get("data_b64", "")
                stream = msg.get("stream", "stdout")
                try:
                    raw = base64.b64decode(data_b64)
                except Exception:
                    continue
                text = raw.decode("utf-8", errors="replace")
                if stream == "stderr":
                    sys.stderr.write(text)
                    sys.stderr.flush()
                else:
                    sys.stdout.write(text)
                    sys.stdout.flush()
            elif mtype == "system":
                print(f"[system] {msg.get('message', '')}")
            elif mtype == "participants":
                users = ", ".join(msg.get("users", []))
                main_user = msg.get("main_user", "")
                print(f"[party] main={main_user} users={users}")
            elif mtype == "error":
                print(f"[error] {msg.get('message', '')}")
            elif mtype == "invite":
                print(f"[invite] {msg.get('code', '')}")
            else:
                print(f"[info] {msg}")

    async def sender() -> None:
        print("type and press enter (plain mode sends one line at a time).")
        print("special commands: /quit (exit) | /shell <cmd> (run shell command)")
        while True:
            text = await _read_input("> ")
            if text is None:
                continue
            raw_text = text
            text = (text or "").strip()
            if not raw_text:
                continue
            if text in ("/quit", "/exit"):
                await transport.close()
                return
            if text.startswith("/shell "):
                shell_output = await _run_shell_command(text[7:])
                if shell_output:
                    print(shell_output)
                continue
            await transport.send_input_bytes((raw_text + "\n").encode("utf-8", errors="replace"))

    try:
        await asyncio.gather(receiver(), sender())
    finally:
        await transport.close()


async def run_client_tui(uri: str, token: str, user: str, client_verification: Optional[Dict] = None) -> None:
    from .ui.app import run_tui

    await run_tui(transport=ClientTransport(uri=uri, token=token, user=user, client_verification=client_verification))


async def run_client(
    uri: str,
    token: str,
    user: str,
    plain: bool = False,
    verify_claude_subscription: bool = False,
    claude_check_command: str = "claude -p 'Reply exactly: CONCORDIA_SUB_OK' --dangerously-skip-permissions",
    claude_check_timeout: float = 20.0,
    require_probe_success: bool = False,
) -> None:
    verification: Optional[Dict] = None
    if verify_claude_subscription:
        verification = await run_client_verification_probe(claude_check_command, claude_check_timeout)
        if require_probe_success and not verification.get("ok", False):
            raise RuntimeError(
                f"Claude subscription verification failed for this client: {verification.get('error') or verification.get('returncode')}"
            )

    if plain:
        await run_client_plain(uri=uri, token=token, user=user, client_verification=verification)
        return
    try:
        await run_client_tui(uri=uri, token=token, user=user, client_verification=verification)
    except Exception as exc:
        print(f"[warn] TUI failed, falling back to plain mode: {exc}", file=sys.stderr)
        await run_client_plain(uri=uri, token=token, user=user, client_verification=verification)
