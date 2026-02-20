# Concordia

Concordia is a multi-user prompt party for Claude Code. Users join with an invite code, submit prompts, and a Gemini-powered deduplication agent merges related prompts into a single multi-step prompt that is executed by the party creator's Claude Code CLI. Output is broadcast to all participants.

## Quickstart

1) Run the smart installation script:

```bash
bash install.sh
```

This will:
- Detect your Python installation
- Find and use `pipx` (or fall back to `pip`)
- Install the concordia package
- Prompt you for your Gemini API key (host only)
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

**"Gemini API key issues"**
- Get a free key: https://ai.google.dev/
- Re-run setup: Edit `~/.config/concordia/.env` and add your key

## Requirements

- Python 3.9+ on all machines
- Claude Code CLI on the party creator's machine (`claude` on PATH)
- Gemini API key on the host only
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

Host setup: first run prompts for your Gemini key and stores it in `~/.config/concordia/.env`.
You can edit that file later if needed.

## Host vs client installs

- Host installs and runs `concordia_host` (prompts for `GEMINI_API_KEY` on first run).
- Clients install and run `concordia_client` (no API key needed).
- Both host and client default to a Codex-style full-screen TUI.

## How it works

- The creator runs the server and is the main user; all Claude Code commands are executed locally by the creator.
- Participants submit prompts; the Gemini deduper merges them into a single multi-step prompt.
- On startup, Concordia creates a Claude session and stores the returned `session_id`.
- For each deduped prompt batch, Concordia resumes that Claude session (`--resume <session_id>`) in the configured project directory.
- Claude output is broadcast to every participant's REPL.

## Options

```bash
concordia_host --port 9000 --project-dir ~/my-project --dedupe-window 6 --min-prompts 2
concordia_client concordia://<ngrok-host>:<ngrok-port>/abc123 --user bob
```

## Notes

- ngrok is required for hosting; invite host/port are always derived from the ngrok tunnel.
- `--project-dir` controls the working directory used for Claude session start/resume commands.
- `--plain` forces the legacy non-TUI client UI.
- Set `--no-local-repl` to run a server without the creator's local REPL.
- Claude execution uses session start + `--resume` internally; `--claude-command` is currently not used by runtime execution.
- `--public-host` and `--ngrok` flags are deprecated and ignored.

## Docker

Build and run a party container (creator/main user):

```bash
docker build -t concordia .
docker run --rm -it \\
  -p 8765:8765 \\
  --env GEMINI_API_KEY=YOUR_KEY \\
  --env NGROK_AUTHTOKEN=YOUR_NGROK_TOKEN \\
  concordia \\
  concordia --create-party --host 0.0.0.0
```

Or with docker-compose (uses `.env`):

```bash
docker compose up --build
```

Set `GEMINI_API_KEY` and `NGROK_AUTHTOKEN` in `.env`.

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
