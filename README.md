# Concordia

Concordia is a live, multi-user terminal sharing tool for interactive TUI apps.

Think of it like Google Docs for terminal sessions: one host runs a program, other users join with an invite link, and everyone sees the same live screen and can collaborate in real time.

## Why Concordia instead of tmux/ssh

- Internet-first collaboration with invite codes (no manual SSH account setup per guest).
- One host-managed runtime for quick pair/mob sessions.
- Shared live view for all participants, including the host's own terminal stream.
- Better for ad-hoc collaborative sessions than managing remote shell access and key distribution.

Use `tmux`/`ssh` when you want full server administration and long-lived personal shells. Use Concordia when you want fast collaborative live sessions.

## Quickstart

1. Install:

```bash
bash install.sh
```

2. Set ngrok auth token on the host (required):

```bash
export NGROK_AUTHTOKEN=YOUR_NGROK_TOKEN
```

3. Start a party and choose the shared program:

```bash
concordia_host --program "bash"
```

4. Share the invite code printed by host.

5. Join from another machine:

```bash
concordia_client <paste-invite-code> --user alice
```

## What can be shared

Any interactive terminal program that runs on the host, for example:

- `bash`, `zsh`, `fish`
- `python3 -q`, `ipython`
- `htop`, `btop`
- `vim`, `nvim`
- `lazygit`
- custom internal TUIs

## Common commands

```bash
# Share a shell
concordia_host --program "bash"

# Share a Python REPL in a specific project
concordia_host --project-dir ~/my-project --program "python3 -q"

# Share a TUI app
concordia_host --program "lazygit"

# Join
concordia_client concordia://<ngrok-host>:<ngrok-port>/<token> --user bob
```

## How it works

- Host starts a websocket server and launches the chosen program inside a PTY.
- Clients connect with invite token.
- Client input bytes are forwarded to host PTY stdin.
- PTY output bytes are broadcast to all connected clients.
- Control messages (`invite`, `participants`, `system`, `error`) are sent as JSON.

## Requirements

- Python 3.9+ on host and clients
- ngrok auth token on host (`NGROK_AUTHTOKEN`)
- The shared program installed on host and available on `PATH`

## Install (global)

```bash
pipx install .
```

If you do not use `pipx`:

```bash
python3 -m pip install --user .
```

## Host and client apps

- Host command: `concordia_host`
- Client command: `concordia_client`
- Use `--plain` for legacy non-TUI mode.

## Permissions model (default-safe)

- Default mode is safety-first for remote participants.
- For shell programs (`bash`, `zsh`, `sh`, `fish`), remote input is filtered:
  - destructive commands are blocked
  - directory-changing commands are blocked
  - paths that escape `--project-dir` are blocked
- For non-shell programs, remote participants are view-only by default.
- Host can disable these guards with `--dangerously-skip-permissions`.
- When that dangerous flag is used, Concordia shows an explicit risk confirmation prompt before startup.

## Notes

- `--program` is required when creating a party.
- `--project-dir` sets the working directory for the shared program process.
- `--no-local-repl` runs server without auto-connecting host client.
- ngrok is required for hosting; invite host/port come from the ngrok tunnel.
- `--public-host` and `--ngrok` are deprecated and ignored.

## Docker

```bash
docker build -t concordia .
docker run --rm -it \
  -p 8765:8765 \
  --env NGROK_AUTHTOKEN=YOUR_NGROK_TOKEN \
  concordia \
  concordia --create-party --host 0.0.0.0 --program "bash"
```

Or:

```bash
docker compose up --build
```

Join from another machine:

```bash
concordia_client concordia://<ngrok-host>:<ngrok-port>/TOKEN --user alice
```

## Bundled install (standalone)

```bash
./scripts/build_bundle.sh
./dist/concordia.pyz --create-party --program "bash"
```

## Releases

```bash
./scripts/build_release.sh
```

Upload artifacts in `dist/` to GitHub Releases.
