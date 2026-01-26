# Concordia System Architecture

## Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLI Entry Points                          │
├─────────────────────────────────────────────────────────────────┤
│  host_cli.py:main()          client_cli.py:main()               │
│  ↓ prepends --create-party   ↓ prepends --join                  │
│  cli.py:build_parser()       cli.py:build_parser()              │
│  ↓                           ↓                                   │
│  _run_create_party()         _run_join()                        │
└─────────────────────────────────────────────────────────────────┘
         │                            │
         ↓                            ↓
    ┌──────────────┐        ┌─────────────────┐
    │ Host/Server  │        │ Client/Joiner   │
    └──────────────┘        └─────────────────┘
```

---

## HOST FLOW (Party Creator)

### Startup Phase
```
host_cli.py:main()
  └─→ cli.py:build_parser()
  └─→ cli.py:_run_create_party(args)
       ├─→ config.py:ensure_gemini_key_interactive()
       │    ├─→ Checks GEMINI_API_KEY env var
       │    ├─→ If missing: prompts user interactively
       │    └─→ Saves to ~/.config/concordia/.env
       │
       ├─→ utils.py:fetch_public_ip() or guess_public_host()
       │    └─→ Detects public IP for invite code
       │
       ├─→ server.py:run_server()
       │    ├─→ config.py:load_env()  [loads persisted key]
       │    ├─→ server.py:create_party_state()
       │    │    └─→ PartyState(invite, creator, claude_cmd, dedupe_window, ...)
       │    │
       │    └─→ PartyServer.start(host, port)
       │         ├─→ asyncio.serve() [WebSocket server]
       │         ├─→ Print: "party created"
       │         ├─→ Print: "invite code: ..."
       │         ├─→ await PartyServer._start_claude()  ★ TASK 1
       │         │    ├─→ Spawns: cat {prompt_file} | claude
       │         │    ├─→ Opens stdin/stdout/stderr pipes
       │         │    ├─→ Creates pump tasks:
       │         │    │   ├─→ _pump_claude_stdout()
       │         │    │   └─→ _pump_claude_stderr()
       │         │    └─→ Broadcast: "claude started (interactive mode)"
       │         │
       │         └─→ await PartyServer._dedupe_loop()  [∞ loop]
       │
       └─→ [if not --no-local-repl] run_client() for host's local REPL
```

### Runtime: Prompt Collection & Deduplication
```
PartyServer._handler(websocket)  [per connection]
  ├─→ Receives: {"type": "hello", "user": "alice", "token": "..."}
  ├─→ Stores connection in PartyState.connections["alice"]
  └─→ Broadcast to all: {"type": "participants", "users": [...]}

[When user submits prompt via sender loop]

PartyServer._enqueue_prompt(user, text)
  ├─→ Validates non-empty
  ├─→ Appends to PartyState.pending: PromptItem(user, text, ts)
  ├─→ Sets self._last_prompt_ts = now()
  └─→ Broadcast: "received prompt from {user}"

PartyServer._dedupe_loop()  [infinite loop]
  └─→ Every 0.5 seconds:
       ├─→ if PartyState.pending is empty: continue
       ├─→ if now() - _last_prompt_ts < dedupe_window (3s): continue
       └─→ if len(pending) < min_prompts: continue
            │
            ├─→ Extract batch = list(PartyState.pending)
            ├─→ Clear pending list
            └─→ await _process_batch(batch)
```

### Deduplication & Claude Execution
```
PartyServer._process_batch(batch: List[PromptItem])
  │
  ├─→ Broadcast: "deduping {N} prompts"
  │
  ├─→ Extract prompts: [{"user": "alice", "text": "..."}, ...]
  │
  ├─→ asyncio.to_thread(dedupe.build_deduped_prompt(prompts, api_key))
  │    │
  │    └─→ dedupe.py:build_deduped_prompt(prompts, api_key)
  │         └─→ dedupe.py:dedupe_with_gemini(prompts, api_key)
  │              ├─→ requests.post(GEMINI_ENDPOINT, ...)
  │              ├─→ Parse response: candidates[0].content.parts[0].text
  │              └─→ Return deduped_prompt (string)
  │              [or fallback to simple concatenation if no key]
  │
  ├─→ Handle dedupe error: if API key bad, clear env var, broadcast error
  │
  ├─→ Broadcast: "running claude"
  │
  ├─→ PartyServer._write_prompt_to_claude(combined_prompt)  ★ TASK 2
  │    ├─→ Validate process running
  │    ├─→ Encode prompt to bytes + "\n"
  │    ├─→ self.state.claude_process.stdin.write(encoded)
  │    ├─→ await self.state.claude_process.stdin.drain()
  │    └─→ Set self.state.claude_busy = True
  │
  └─→ [pump tasks already running, stream output]
```

### Output Streaming
```
PartyServer._pump_claude_stdout()  [async task]
  └─→ While True:
       ├─→ line = await claude_process.stdout.readline()
       ├─→ if not line: break  [EOF]
       ├─→ Decode UTF-8
       ├─→ Broadcast: {"type": "output", "text": line}
       ├─→ print(line) to host console
       │
       └─→ [Check for completion]
            ├─→ if line == ">" or line == "":
            │    ├─→ Set self.state.claude_busy = False
            │    ├─→ Broadcast: "claude ready for next prompt"
            │    └─→ [Dedupe loop can proceed]
            └─→ [Loop continues to next line]

PartyServer._pump_claude_stderr()  [async task]
  └─→ Same as stdout but:
       ├─→ print(line, file=sys.stderr)
       └─→ Broadcast with error emphasis
```

---

## CLIENT FLOW (Participant)

### Connection Phase
```
client_cli.py:main()
  └─→ cli.py:build_parser()
  └─→ cli.py:_run_join(args)
       │
       └─→ utils.py:parse_invite("concordia://IP:PORT/TOKEN")
            └─→ Invite(host=IP, port=PORT, token=TOKEN)
       │
       └─→ client.py:run_client(uri, token, user)
            │
            └─→ websockets.connect("ws://IP:PORT")
                 ├─→ Send: {"type": "hello", "user": "alice", "token": "..."}
                 └─→ Start concurrent tasks:
                      ├─→ receiver()
                      └─→ sender()
```

### Runtime: Receive & Send
```
client.py:run_client()

sender()  [reads from stdin]
  └─→ input("> ")  [blocking read on main thread via asyncio.to_thread]
       ├─→ Check for special commands:
       │    ├─→ /quit, /exit: close connection
       │    └─→ /shell <cmd>: run shell command locally
       │
       └─→ Otherwise: send prompt
            └─→ websocket.send({"type": "prompt", "text": "..."})

receiver()  [listens on WebSocket]
  └─→ async for raw_msg in websocket:
       ├─→ msg = protocol.decode(raw_msg)
       │
       ├─→ if msg["type"] == "output":
       │    └─→ print(msg["text"])  [clean, no prefixes]
       │
       ├─→ if msg["type"] == "system":
       │    └─→ print(f"[system] {msg['message']}")
       │
       ├─→ if msg["type"] == "participants":
       │    └─→ print(f"[party] main={...} users={...}")
       │
       └─→ if msg["type"] == "error":
            └─→ print(f"[error] {msg['message']}")
```

---

## DATA STRUCTURES

### PartyState (server.py)
```python
@dataclass
class PartyState:
    invite: Invite                                      # Connection info
    creator: str                                        # Host username
    claude_command: str                                 # "cat {prompt_file} | claude"
    dedupe_window: float                                # 3.0 seconds
    min_prompts: int                                    # 1 (start on first prompt)
    pending: List[PromptItem]                          # Prompts awaiting dedupe
    connections: Dict[str, WebSocketServerProtocol]   # Connected clients
    pending_prompts: asyncio.Queue                     # Unused (Task 2 placeholder)
    claude_busy: bool                                   # Is Claude processing?
    claude_process: Optional[asyncio.subprocess.Process] # Running Claude
```

### PromptItem (server.py)
```python
@dataclass
class PromptItem:
    user: str          # "alice"
    text: str          # The prompt text
    ts: float          # Timestamp when received
```

### Invite (utils.py)
```python
@dataclass
class Invite:
    host: str          # Public IP or hostname
    port: int          # Port number
    token: str         # Random 16-char token
```

### Message Types (protocol.py)
```
Client → Server:
  {"type": "hello", "user": "alice", "token": "abc123"}
  {"type": "prompt", "text": "write a function"}
  {"type": "ping"}

Server → All:
  {"type": "output", "text": "function output line"}
  {"type": "system", "message": "alice joined"}
  {"type": "participants", "main_user": "host", "users": ["alice"]}
  {"type": "error", "message": "Something went wrong"}
```

---

## KEY FUNCTIONS BY RESPONSIBILITY

### Configuration & Startup
| Function | File | Purpose |
|----------|------|---------|
| `ensure_gemini_key_interactive()` | config.py | Prompt/persist API key |
| `load_env()` | config.py | Load `.env` file |
| `env_path()` | config.py | Get `~/.config/concordia/.env` path |
| `fetch_public_ip()` | utils.py | Detect host public IP |
| `guess_public_host()` | utils.py | Fallback IP/hostname |
| `generate_token()` | utils.py | Random 16-char token |

### Server Core
| Function | File | Purpose |
|----------|------|---------|
| `run_server()` | server.py | Main server coroutine |
| `PartyServer.start()` | server.py | Start WebSocket + Claude + dedupe loop |
| `PartyServer._handler()` | server.py | Handle per-connection |
| `PartyServer._broadcast()` | server.py | Send message to all clients |

### Prompt Pipeline
| Function | File | Purpose |
|----------|------|---------|
| `PartyServer._enqueue_prompt()` | server.py | Add prompt to pending list |
| `PartyServer._dedupe_loop()` | server.py | Infinite loop: wait → dedupe → execute |
| `PartyServer._process_batch()` | server.py | Dedupe batch, send to Claude |
| `dedupe_with_gemini()` | dedupe.py | Call Gemini API for dedup |
| `dedupe_fallback()` | dedupe.py | Simple concat if no API key |

### Claude Integration (Task 1 & 2)
| Function | File | Purpose |
|----------|------|---------|
| `PartyServer._start_claude()` | server.py | Spawn Claude subprocess |
| `PartyServer._write_prompt_to_claude()` | server.py | Send prompt to stdin |
| `PartyServer._pump_claude_stdout()` | server.py | Stream stdout to clients |
| `PartyServer._pump_claude_stderr()` | server.py | Stream stderr to clients |
| `PartyServer.shutdown()` | server.py | Clean up process & tasks |

### Client
| Function | File | Purpose |
|----------|------|---------|
| `run_client()` | client.py | Main client coroutine |
| `sender()` | client.py | Read stdin, send prompts |
| `receiver()` | client.py | Listen for broadcasts |

---

## Concurrency Model

### Host (Server)
```
Main: run_server()
  ├─→ Event loop: asyncio.run()
  │
  ├─→ WebSocket server (async context manager)
  │
  ├─→ Task 1: _dedupe_loop()  [infinite, waits on prompts]
  │
  ├─→ Task 2: _pump_claude_stdout()  [infinite, reads Claude output]
  │
  ├─→ Task 3: _pump_claude_stderr()  [infinite, reads Claude errors]
  │
  └─→ Task N: _handler() per connected client
       └─→ Concurrent coroutines for each WebSocket connection
```

All tasks run in **single event loop** - true concurrency via async/await, not threading.

### Client
```
Main: run_client()
  ├─→ Task 1: sender()  [blocking input via asyncio.to_thread]
  │
  └─→ Task 2: receiver()  [async WebSocket listen]
```

---

## Protocol Flow Diagram

```
Host                          Gemini API          Client (Alice)
 │                                │                    │
 │◄─────────── invitation code ──────────────────────│
 │                                │                    │
 │                                │◄─── join party ───│
 │◄──────── hello + token ────────────────────────────│
 │                                │                    │
 │ broadcast participants         │                    │
 ├──────────────────────────────────────────────────→ │
 │                                │                    │
 │                                │                    │
 │◄──────────── send prompt ──────────────────────────│
 │ (wait 3s for more)             │                    │
 │                                │                    │
 │ collect batch                  │                    │
 ├──────────► dedupe request ─────→                    │
 │◄───── deduped prompt ──────────│                    │
 │                                │                    │
 │ write to Claude stdin          │                    │
 │ stream stdout                  │                    │
 ├──────────────────────────────────────────────────→ │
 │ (output lines)                 │                    │
 │                                │                    │
 │ detect completion              │                    │
 │ broadcast "ready"              │                    │
 ├──────────────────────────────────────────────────→ │
```

---

## Summary

**Host responsibilities:**
- Accept client connections via WebSocket
- Collect prompts, wait for dedupe window
- Call Gemini to dedupe similar requests
- Send merged prompt to Claude stdin
- Stream Claude output to all clients

**Client responsibilities:**
- Send prompts to host
- Display streamed output and system messages
- Support `/shell` escape hatch for local commands

**Decoupling:**
- Clients don't know about dedupe or Claude
- Server doesn't need multiple Claude instances
- All communication is message-based (websockets)
