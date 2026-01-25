import asyncio
import sys
from typing import Optional

import websockets

from .protocol import decode, encode


async def _read_input(prompt: str) -> Optional[str]:
    return await asyncio.to_thread(lambda: input(prompt))


async def run_client(uri: str, token: str, user: str) -> None:
    async with websockets.connect(uri) as websocket:
        await websocket.send(encode({"type": "hello", "user": user, "token": token}))

        async def receiver() -> None:
            async for raw in websocket:
                msg = decode(raw)
                mtype = msg.get("type")
                if mtype == "output":
                    text = msg.get("text", "")
                    print(text)
                elif mtype == "system":
                    print(f"[system] {msg.get('message', '')}")
                elif mtype == "participants":
                    users = ", ".join(msg.get("users", []))
                    main_user = msg.get("main_user", "")
                    print(f"[party] main={main_user} users={users}")
                elif mtype == "error":
                    print(f"[error] {msg.get('message', '')}")
                else:
                    print(f"[info] {msg}")

        async def sender() -> None:
            print("type a prompt and press enter. /quit to exit.")
            while True:
                text = await _read_input("> ")
                if text is None:
                    continue
                text = text.strip()
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    await websocket.close()
                    return
                await websocket.send(encode({"type": "prompt", "text": text}))

        await asyncio.gather(receiver(), sender())
