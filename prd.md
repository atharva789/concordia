# Concordia PRD

## Overview
Concordia is a multi-user “prompt party” for Claude Code. A host creates a party, participants join via an invite URL, submit prompts, and a Gemini-powered deduplication agent merges related prompts into a single multi-step prompt executed by the host’s Claude Code CLI. Output is broadcast to all participants in real time.

## Goals
- Enable multiple users to collaborate on prompt authoring with minimal friction.
- Reduce redundant prompts via automated deduplication and grouping.
- Keep execution on the host’s machine for privacy and control.
- Provide a fast, low-setup join flow for participants.

## Non-Goals
- Replacing Claude Code CLI or running prompts on participant machines.
- Full document or project management.
- Fine-grained permissioning beyond host control.

## Core Functionality
1) **Party creation (host)**
- Host runs a CLI server to create a party.
- Server prints an invite code/URL for participants.
- Host must have Claude Code CLI available on PATH.
- Host provides `GEMINI_API_KEY` (prompted on first run and stored locally).

2) **Party joining (clients)**
- Participants join with a `concordia://HOST:PORT/TOKEN` URL.
- Participants choose a username (e.g., `--user alice`).
- Clients can submit prompts and receive broadcast output.

3) **Prompt submission**
- Participants submit prompts into a shared queue.
- Prompts are collected within a dedupe window.

4) **Deduplication and merge (Gemini)**
- Gemini agent groups related prompts and merges them into a single multi-step prompt.
- Merge aims to reduce redundancy and improve execution order.

5) **Execution (Claude Code)**
- Host’s Claude Code CLI executes the merged prompt.
- Output is streamed/broadcast to all participants.

6) **Broadcast and visibility**
- Participants see the merged prompt output in their client REPL.
- Host can optionally run without a local REPL (`--no-local-repl`).

## Current Logic Flow
1) **Host startup**
- Host runs `concordia_host`.
- If `GEMINI_API_KEY` not present, prompt and store at `~/.config/concordia/.env`.
- Server binds to a port and prints invite URL/token.
- Optional flags configure `--public-host`, `--port`, `--dedupe-window`, `--min-prompts`, `--no-local-repl`, `--claude-command`.

2) **Client join**
- Client runs `concordia_client concordia://HOST:PORT/TOKEN --user <name>`.
- Client connects to host and joins the session.

3) **Prompt collection**
- Prompts from all clients are collected into a time window (dedupe window).
- Optional minimum prompt threshold (e.g., `--min-prompts`) gates merging.

4) **Deduplication merge**
- Gemini deduper processes prompts and produces a multi-step consolidated prompt.

5) **Execution**
- Host invokes Claude Code CLI with the merged prompt (`claude --prompt-file {prompt_file}` by default).
- Execution occurs locally on the host machine.

6) **Broadcast**
- Host streams Claude Code output to all participants.
- Participants receive output in their REPLs.

## Intended UX
### Host (creator)
- **First-run setup**: Clear prompt for `GEMINI_API_KEY` with local storage and ability to edit later.
- **Simple start**: One command to create a party (`concordia_host`).
- **Share invite**: Host sees a copyable invite URL/token immediately.
- **Control knobs**: Optional flags for public host, port, dedupe window, min prompts, and CLI command override.
- **Feedback**: Live view of merged prompt and Claude Code output (unless `--no-local-repl`).

### Participant (client)
- **Fast join**: Single command with invite URL and username.
- **Low friction**: No API key needed on clients.
- **Prompting flow**: Type prompts as normal in a REPL-like experience.
- **Shared results**: See final merged output as it streams from the host.

## Success Metrics
- Time to host a party (install to running) is under 5 minutes.
- Time to join a party is under 1 minute.
- High prompt dedupe rate (measured by reduced total prompts executed).
- Low failure rate when joining or executing a merged prompt.

## Open Questions
- How should prompt conflicts be handled or surfaced to users?
- Should clients see the merged prompt before execution?
- What visibility or moderation controls are needed for hosts?

## Constraints & Dependencies
- Python 3.9+ required on all machines.
- Host requires Claude Code CLI installed and accessible on PATH.
- Host requires Gemini API key.
- Network connectivity between host and clients; firewall must allow inbound traffic on the host port.

## Out of Scope (for now)
- Role-based access controls and moderation tools.
- Multi-host or federated parties.
- Persistent history across sessions.
