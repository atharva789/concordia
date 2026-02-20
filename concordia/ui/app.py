import asyncio
from typing import Dict, List

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText, HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_formatted_text

from ..client import ClientTransport, _run_shell_command


async def run_tui(transport: ClientTransport) -> None:
    session: PromptSession = PromptSession()
    invite_code = ""
    main_user = ""
    users: List[str] = []
    last_deduped = ""
    connected = False

    def _toolbar() -> FormattedText:
        parts = []
        if invite_code:
            parts.append(("bg:#1a1a2e #7c8da6", f" Invite: {invite_code} "))
            parts.append(("", " "))
        if main_user:
            parts.append(("bg:#1a1a2e #7c8da6", f" Host: {main_user} "))
            parts.append(("", " "))
        if users:
            parts.append(("bg:#1a1a2e #7c8da6", f" Users: {', '.join(users)} "))
        if not parts:
            parts.append(("bg:#1a1a2e #555", " Connecting... "))
        return FormattedText(parts)

    def _print_system(text: str) -> None:
        print_formatted_text(HTML(f"<style fg='#9ca3af'>{text}</style>"))

    def _print_error(text: str) -> None:
        print_formatted_text(HTML(f"<style fg='#fca5a5'>{text}</style>"))

    def _print_output(text: str) -> None:
        print_formatted_text(HTML(f"<b>{text}</b>"))

    def _print_plain(text: str) -> None:
        print_formatted_text(text)

    def _show_deduped_block(text: str) -> None:
        print_formatted_text(HTML("<style fg='#9ca3af'>[deduped prompt ready]</style>"))
        print_formatted_text(HTML("<style fg='#9ca3af'>----------------------------------------</style>"))
        if text.strip():
            for line in text.splitlines():
                _print_plain(line)
        else:
            _print_system("(empty)")
        print_formatted_text(HTML("<style fg='#9ca3af'>----------------------------------------</style>"))
        _print_system("Press Enter to send this prompt, or type to edit/override.")

    async def _receiver() -> None:
        nonlocal invite_code, main_user, users, last_deduped, connected
        try:
            async for msg in transport.iter_messages():
                mtype = msg.get("type")
                if mtype == "output":
                    _print_output(msg.get("text", ""))
                elif mtype == "system":
                    _print_system(f"[system] {msg.get('message', '')}")
                elif mtype == "invite":
                    invite_code = msg.get("code", "")
                    _print_system(f"[invite] {invite_code}")
                    if session.app:
                        session.app.invalidate()
                elif mtype == "participants":
                    main_user = msg.get("main_user", "")
                    users = list(msg.get("users", []))
                    if session.app:
                        session.app.invalidate()
                elif mtype == "error":
                    _print_error(f"[error] {msg.get('message', '')}")
                elif mtype == "deduped_prompt":
                    last_deduped = msg.get("text", "")
                    _show_deduped_block(last_deduped)
                elif mtype == "pong":
                    pass
                else:
                    _print_system(f"[info] {msg}")
        except Exception:
            pass
        finally:
            connected = False
            _print_system("[system] disconnected")

    async def _sender() -> None:
        nonlocal last_deduped
        with patch_stdout():
            while connected:
                try:
                    text = await session.prompt_async(
                        "> ",
                        bottom_toolbar=_toolbar,
                    )
                except (EOFError, KeyboardInterrupt):
                    break
                text = (text or "").strip()
                if not text:
                    if last_deduped:
                        text = last_deduped
                    else:
                        continue
                if text in ("/quit", "/exit"):
                    break
                if text.startswith("/shell "):
                    output = await _run_shell_command(text[7:])
                    if output:
                        _print_plain(output)
                    continue
                if not connected:
                    _print_error("[error] not connected")
                    continue
                await transport.send_prompt(text)

    await transport.connect()
    connected = True
    _print_system("[system] connected")
    _print_system("Type a prompt and press Enter. /quit to exit. /shell <cmd> for local shell.")

    recv_task = asyncio.create_task(_receiver())
    try:
        await _sender()
    finally:
        recv_task.cancel()
        await transport.close()
