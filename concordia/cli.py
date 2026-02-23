import argparse
import asyncio
import os
import sys
from pathlib import Path

from pyngrok import ngrok

from .client import run_client
from .compliance import evaluate_create_party_config
from .config import load_env
from .debug import debug_print
from .server import run_server
from .utils import default_username, generate_token, parse_invite


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="concordia", description="Multi-user shared Claude terminal")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--create-party", action="store_true", help="Create a new party")
    g.add_argument("--join", metavar="INVITE_CODE", help="Join a party with invite code")

    p.add_argument("--user", default=default_username(), help="Your display name")

    p.add_argument("--host", default="0.0.0.0", help="Host to bind (create-party)")
    p.add_argument("--port", type=int, default=8765, help="Port to bind (create-party)")
    p.add_argument("--public-host", default=None, help="Deprecated: ignored. Host is derived from ngrok tunnel.")
    p.add_argument("--claude-command", default="claude --dangerously-skip-permissions")
    p.add_argument("--project-dir", default=str(Path.cwd()), help="Project directory Claude should operate in")
    p.add_argument("--plain", action="store_true", help="Use legacy non-TUI client mode")
    p.add_argument("--no-local-repl", action="store_true", help="Disable local REPL for creator")
    p.add_argument("--ngrok", action="store_true", help="Deprecated: ngrok is always enabled for party creation.")
    p.add_argument(
        "--compliance-mode",
        choices=["strict", "warn", "off"],
        default="strict",
        help="Startup policy enforcement level for multi-user compliance safeguards.",
    )
    p.add_argument(
        "--attest-commercial-use-rights",
        action="store_true",
        help="Attest that your account/plan allows this multi-user usage mode.",
    )
    p.add_argument(
        "--allow-remote-input",
        action="store_true",
        help="Allow non-host participants to send input to the host Claude session.",
    )
    p.add_argument(
        "--audit-log-path",
        default=str(Path.cwd() / "concordia-audit.log"),
        help="Path for append-only compliance audit log.",
    )
    p.add_argument(
        "--require-client-claude-check",
        action="store_true",
        help="Require each non-host client to provide a fresh local Claude verification probe.",
    )
    p.add_argument(
        "--client-claude-check-max-age-sec",
        type=float,
        default=600.0,
        help="Maximum age (seconds) of client Claude verification proof.",
    )
    p.add_argument(
        "--skip-claude-subscription-check",
        action="store_true",
        help="Skip local Claude verification probe on this client before connecting.",
    )
    p.add_argument(
        "--claude-check-command",
        default="claude -p 'Reply exactly: CONCORDIA_SUB_OK' --dangerously-skip-permissions",
        help="Local probe command used by clients for Claude verification.",
    )
    p.add_argument(
        "--claude-check-timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for the local Claude verification probe command.",
    )
    p.add_argument(
        "--estimate-token-usage",
        action="store_true",
        help="Enable approximate per-client token usage attribution (estimates only).",
    )
    p.add_argument(
        "--usage-estimate-window-sec",
        type=float,
        default=8.0,
        help="Sliding window (seconds) used for approximate output attribution to active writers.",
    )
    p.add_argument(
        "--usage-estimate-path",
        default=str(Path.cwd() / "concordia-usage-estimate.json"),
        help="Output path for estimated per-client usage report.",
    )

    return p


def _ws_uri(host: str, port: int) -> str:
    return f"ws://{host}:{port}"


async def _run_create_party(args: argparse.Namespace) -> None:
    load_env()
    require_client_claude_check = args.require_client_claude_check or args.compliance_mode == "strict"

    report = evaluate_create_party_config(
        compliance_mode=args.compliance_mode,
        attest_commercial_use_rights=args.attest_commercial_use_rights,
        allow_remote_input=args.allow_remote_input,
        claude_command=args.claude_command,
    )
    for w in report.warnings:
        print(f"[compliance warning] {w}", file=sys.stderr)
    if not report.ok:
        for err in report.errors:
            print(f"[compliance error] {err}", file=sys.stderr)
        raise SystemExit("Refusing to start party due to strict compliance policy.")

    authtoken = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if not authtoken:
        raise SystemExit("Missing NGROK_AUTHTOKEN")

    ngrok.set_auth_token(authtoken)
    ngrok_tunnel = ngrok.connect(args.port, "tcp")
    endpoint = ngrok_tunnel.public_url.replace("tcp://", "", 1)
    if ":" not in endpoint:
        raise SystemExit(f"Invalid ngrok public URL: {ngrok_tunnel.public_url}")
    public_host, public_port_str = endpoint.rsplit(":", 1)
    public_port = int(public_port_str)
    debug_print(f"PUBLIC_HOST: {public_host}, PUBLIC_PORT: {public_port}")
    debug_print(f"ngrok tunnel created: {ngrok_tunnel.public_url}")

    token = generate_token(16)

    async def run_with_cleanup():
        try:
            await run_server(
                creator=args.user,
                host=args.host,
                port=args.port,
                public_host=public_host,
                invite_port=public_port,
                project_dir=os.path.expanduser(args.project_dir),
                claude_command=args.claude_command,
                compliance_mode=args.compliance_mode,
                allow_remote_input=args.allow_remote_input,
                audit_log_path=os.path.expanduser(args.audit_log_path),
                require_client_claude_check=require_client_claude_check,
                client_claude_check_max_age_sec=args.client_claude_check_max_age_sec,
                estimate_token_usage=args.estimate_token_usage,
                usage_estimate_window_sec=args.usage_estimate_window_sec,
                usage_estimate_path=os.path.expanduser(args.usage_estimate_path),
                token=token,
            )
        finally:
            ngrok.disconnect(ngrok_tunnel.public_url)

    server_task = asyncio.create_task(run_with_cleanup())

    if not args.no_local_repl:
        local_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        await asyncio.sleep(0.1)
        for _ in range(10):
            try:
                await run_client(
                    f"ws://{local_host}:{args.port}",
                    token=token,
                    user=args.user,
                    plain=args.plain,
                    verify_claude_subscription=not args.skip_claude_subscription_check,
                    claude_check_command=args.claude_check_command,
                    claude_check_timeout=args.claude_check_timeout,
                    require_probe_success=require_client_claude_check,
                )
                break
            except Exception:
                await asyncio.sleep(0.5)

    await server_task


async def _run_join(args: argparse.Namespace) -> None:
    invite = parse_invite(args.join)
    uri = _ws_uri(invite.host, invite.port)
    await run_client(
        uri,
        invite.token,
        args.user,
        plain=args.plain,
        verify_claude_subscription=not args.skip_claude_subscription_check,
        claude_check_command=args.claude_check_command,
        claude_check_timeout=args.claude_check_timeout,
        require_probe_success=not args.skip_claude_subscription_check,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.create_party:
        asyncio.run(_run_create_party(args))
    else:
        asyncio.run(_run_join(args))


if __name__ == "__main__":
    main()
