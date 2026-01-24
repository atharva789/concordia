# Concordia

Concordia is a multi-user prompt party for Claude Code. Users join with an invite code, submit prompts, and a Gemini-powered deduplication agent merges related prompts into a single multi-step prompt that is executed by the party creator's Claude Code CLI. Output is broadcast to all participants.

## Quickstart

1) Install:

```bash
pipx install .
```

2) On the host (creator/main user), start a party:

```bash
concordia_host
```

3) Share the invite code printed in the host terminal.

4) Join from another machine:

```bash
concordia_client concordia://HOST:PORT/TOKEN --user alice
```

## Requirements

- Python 3.9+ on all machines
- Claude Code CLI on the party creator's machine (`claude` on PATH)
- Gemini API key on the host only

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

## How it works

- The creator runs the server and is the main user; all Claude Code commands are executed locally by the creator.
- Participants submit prompts; the Gemini deduper merges them into a single multi-step prompt.
- Claude Code output is broadcast to every participant's REPL.

## Options

```bash
concordia_host --public-host 203.0.113.10 --port 9000 --dedupe-window 6 --min-prompts 2
concordia_client concordia://203.0.113.10:9000/abc123 --user bob
```

## Notes

- Ensure your firewall allows inbound traffic on the chosen port.
- Set `--no-local-repl` to run a server without the creator's local REPL.
- The `--claude-command` flag can override how Claude Code is invoked (default: `claude --prompt-file {prompt_file}`).
- Public IP is auto-detected on host startup; override with `--public-host` if needed.

## Docker

Build and run a party container (creator/main user):

```bash
docker build -t concordia .
docker run --rm -it \\
  -p 8765:8765 \\
  --env GEMINI_API_KEY=YOUR_KEY \\
  concordia \\
  concordia --create-party --host 0.0.0.0 --public-host YOUR_PUBLIC_IP
```

Or with docker-compose (uses `.env`):

```bash
docker compose up --build
```

Set `PUBLIC_HOST` in `.env`.

Join from another machine:

```bash
concordia_client concordia://YOUR_PUBLIC_IP:8765/TOKEN --user alice
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
