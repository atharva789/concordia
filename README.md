# Concordia

Concordia is a multi-user shared terminal for Claude Code. Users join with an invite code and collaborate in real time against the host's Claude PTY session. The host runs Claude locally; Concordia streams terminal input/output to connected participants.

## Quickstart

1) Run the smart installation script:

```bash
bash install.sh
```

This will:
- Detect your Python installation
- Find and use `pipx` (or fall back to `pip`)
- Install the concordia package
- Check for Claude Code CLI

Before hosting, set your ngrok auth token (required):

```bash
export NGROK_AUTHTOKEN=YOUR_NGROK_TOKEN
```

2) On the host (creator/main user), start a party:

```bash
concordia_host
```

Use `--plain` if you want the legacy line-by-line terminal mode.

Strict compliance mode is enabled by default. If your usage is permitted, attest explicitly:

```bash
concordia_host --attest-commercial-use-rights
```

3) Share the invite code printed in the host terminal.

4) Join from another machine:

```bash
concordia_client <paste-invite-from-host-terminal> --user alice
```

## Installation Troubleshooting

**"pipx not found"**
- Install pipx: `brew install pipx` (macOS) or `pip3 install --user pipx`
- Or let the script use pip instead

**"Claude CLI not found"**
- Install Claude Code: https://claude.com/claude-code

**"Python 3.9+ required"**
- Check version: `python3 --version`
- Update Python if needed

## Requirements

- Python 3.9+ on all machines
- Claude Code CLI on the party creator's machine (`claude` on PATH)
- ngrok auth token on the host (`NGROK_AUTHTOKEN`)

## Install (global)

```bash
pipx install .
```

If you don't use `pipx`:

```bash
python3 -m pip install --user .
```

For a fully bundled, standalone install, see “Bundled install (standalone)” below.

## Setup

Host setup: ensure Claude CLI is authenticated on the host machine and `NGROK_AUTHTOKEN` is set.

## Host vs client installs

- Host installs and runs `concordia_host`.
- Clients install and run `concordia_client` (no API key needed).
- Both host and client default to a Codex-style full-screen TUI.

## How it works

- The creator runs the server and is the main user; all Claude Code commands are executed locally by the creator.
- On startup, Concordia launches Claude in an interactive PTY in the configured project directory.
- Participants connect over websocket and receive streamed PTY output in real time.
- Input bytes are forwarded to the host PTY according to compliance policy.
- Claude output is broadcast to every participant terminal.

## Options

```bash
concordia_host --port 9000 --project-dir ~/my-project --attest-commercial-use-rights
concordia_host --compliance-mode strict --allow-remote-input --attest-commercial-use-rights
concordia_host --compliance-mode strict --attest-commercial-use-rights --require-client-claude-check
concordia_host --estimate-token-usage --usage-estimate-window-sec 8 --usage-estimate-path ./concordia-usage-estimate.json --attest-commercial-use-rights
concordia_client concordia://<ngrok-host>:<ngrok-port>/abc123 --user bob
```

## Compliance and safety

- `--compliance-mode` controls startup policy (`strict`, `warn`, `off`; default `strict`).
- `--attest-commercial-use-rights` is required in strict mode.
- Remote input is disabled by default in strict mode unless `--allow-remote-input` is set.
- `--require-client-claude-check` requires non-host clients to provide a fresh local Claude probe result.
- Clients run local probe by default; use `--skip-claude-subscription-check` to bypass (host may reject if probe is required).
- `--estimate-token-usage` writes an approximate per-client usage attribution report (`--usage-estimate-path`).
- Usage attribution is estimate-only in shared PTY mode (active writer window + input ownership), not exact token accounting.
- Append-only audit logging is enabled by default (`--audit-log-path`, default `./concordia-audit.log`).
- See `COMPLIANCE_CHECKLIST.md` for release and operational guidance.

## Notes

- ngrok is required for hosting; invite host/port are always derived from the ngrok tunnel.
- `--project-dir` controls the working directory used for the host Claude process.
- `--plain` forces the legacy non-TUI client UI.
- Set `--no-local-repl` to run a server without the creator's local REPL.
- `--claude-command` controls the Claude startup command for the host PTY.
- `--public-host` and `--ngrok` flags are deprecated and ignored.

## Docker

Build and run a party container (creator/main user):

```bash
docker build -t concordia .
docker run --rm -it \\
  -p 8765:8765 \\
  --env NGROK_AUTHTOKEN=YOUR_NGROK_TOKEN \\
  concordia \\
  concordia --create-party --host 0.0.0.0 --attest-commercial-use-rights
```

Or with docker-compose (uses `.env`):

```bash
docker compose up --build
```

Set `NGROK_AUTHTOKEN` in `.env`.

Join from another machine:

```bash
concordia_client concordia://<ngrok-host>:<ngrok-port>/TOKEN --user alice
```

Notes:
- The container runs the server; Claude Code must be available inside the container if you want execution there.
- If you want Claude Code to run on the host instead, run the server on the host and skip Docker.

## Bundled install (standalone)

To create a self-contained `concordia.pyz` that bundles dependencies:

```bash
./scripts/build_bundle.sh
```

Run it directly:

```bash
./dist/concordia.pyz --create-party
```

## GitHub releases

To publish downloadable packages (wheel, sdist, standalone .pyz):

```bash
./scripts/build_release.sh
```

Upload the files in `dist/` to a GitHub Release.

Or push a tag like `v0.1.0` and the GitHub Actions workflow will build and upload assets automatically.
