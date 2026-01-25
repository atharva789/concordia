import argparse
import asyncio

from .client import run_client
from .config import ensure_gemini_key_interactive
from .server import run_server
from .utils import default_username, fetch_public_ip, generate_token, guess_public_host, parse_invite


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="concordia", description="Multi-user Claude Code prompt party")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--create-party", action="store_true", help="Create a new party")
    g.add_argument("--join", metavar="INVITE_CODE", help="Join a party with invite code")

    p.add_argument("--user", default=default_username(), help="Your display name")

    p.add_argument("--host", default="0.0.0.0", help="Host to bind (create-party)")
    p.add_argument("--port", type=int, default=8765, help="Port to bind (create-party)")
    p.add_argument("--public-host", default=None, help="Public host/IP for invite code")
    p.add_argument("--claude-command", default="cat {prompt_file} | claude")
    p.add_argument("--dedupe-window", type=float, default=3.0, help="Seconds to wait before dedupe")
    p.add_argument("--min-prompts", type=int, default=1, help="Minimum prompts before run")
    p.add_argument("--no-local-repl", action="store_true", help="Disable local REPL for creator")

    return p


def _ws_uri(host: str, port: int) -> str:
    return f"ws://{host}:{port}"


async def _run_create_party(args: argparse.Namespace) -> None:
    key = ensure_gemini_key_interactive()
    if not key:
        raise SystemExit("Missing GEMINI_API_KEY")
    public_host = args.public_host or fetch_public_ip() or guess_public_host()
    token = generate_token(16)
    server_task = asyncio.create_task(
        run_server(
            creator=args.user,
            host=args.host,
            port=args.port,
            public_host=public_host,
            claude_command=args.claude_command,
            dedupe_window=args.dedupe_window,
            min_prompts=args.min_prompts,
            token=token,
        )
    )

    if not args.no_local_repl:
        local_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        await asyncio.sleep(0.5)
        for _ in range(10):
            try:
                await run_client(_ws_uri(local_host, args.port), token=token, user=args.user)
                break
            except Exception:
                await asyncio.sleep(0.5)

    await server_task


async def _run_join(args: argparse.Namespace) -> None:
    invite = parse_invite(args.join)
    uri = _ws_uri(invite.host, invite.port)
    await run_client(uri, invite.token, args.user)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.create_party:
        asyncio.run(_run_create_party(args))
    else:
        asyncio.run(_run_join(args))


if __name__ == "__main__":
    main()
