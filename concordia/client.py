import asyncio
import subprocess
import sys
from typing import AsyncIterator, Dict, Optional

import websockets

from .protocol import decode, encode


async def _read_input(prompt: str) -> Optional[str]:
    return await asyncio.to_thread(lambda: input(prompt))


class ClientTransport:
    def __init__(self, uri: str, token: str, user: str):
        self.uri = uri
        self.token = token
        self.user = user
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None

    async def connect(self) -> None:
        self._websocket = await websockets.connect(self.uri)
        await self._websocket.send(encode({"type": "hello", "user": self.user, "token": self.token}))

    async def close(self) -> None:
        if self._websocket:
            await self._websocket.close()
            self._websocket = None

    async def send_prompt(self, text: str) -> None:
        if not self._websocket:
            raise RuntimeError("WebSocket is not connected")
        await self._websocket.send(encode({"type": "prompt", "text": text}))

    @property
    def is_connected(self) -> bool:
        return self._websocket is not None

    async def iter_messages(self) -> AsyncIterator[Dict]:
        if not self._websocket:
            raise RuntimeError("WebSocket is not connected")
        async for raw in self._websocket:
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


async def run_client_plain(uri: str, token: str, user: str) -> None:
    transport = ClientTransport(uri=uri, token=token, user=user)
    await transport.connect()

    async def receiver() -> None:
        async for msg in transport.iter_messages():
            mtype = msg.get("type")
            if mtype == "output":
                print(msg.get("text", ""))
            elif mtype == "system":
                print(f"[system] {msg.get('message', '')}")
            elif mtype == "participants":
                users = ", ".join(msg.get("users", []))
                main_user = msg.get("main_user", "")
                print(f"[party] main={main_user} users={users}")
            elif mtype == "error":
                print(f"[error] {msg.get('message', '')}")
            elif mtype == "deduped_prompt":
                print("[deduped]")
                print(msg.get("text", ""))
            elif mtype == "invite":
                print(f"[invite] {msg.get('code', '')}")
            else:
                print(f"[info] {msg}")

    async def sender() -> None:
        print("type a prompt and press enter.")
        print("special commands: /quit (exit) | /shell <cmd> (run shell command)")
        while True:
            text = await _read_input("> ")
            if text is None:
                continue
            text = text.strip()
            if not text:
                continue
            if text in ("/quit", "/exit"):
                await transport.close()
                return
            if text.startswith("/shell "):
                shell_output = await _run_shell_command(text[7:])
                if shell_output:
                    print(shell_output)
                continue
            await transport.send_prompt(text)

    try:
        await asyncio.gather(receiver(), sender())
    finally:
        await transport.close()


async def run_client_tui(uri: str, token: str, user: str) -> None:
    from .ui.app import run_tui

    await run_tui(transport=ClientTransport(uri=uri, token=token, user=user))


async def run_client(uri: str, token: str, user: str, plain: bool = False) -> None:
    if plain:
        await run_client_plain(uri=uri, token=token, user=user)
        return
    try:
        await run_client_tui(uri=uri, token=token, user=user)
    except Exception as exc:
        print(f"[warn] TUI failed, falling back to plain mode: {exc}", file=sys.stderr)
        await run_client_plain(uri=uri, token=token, user=user)
