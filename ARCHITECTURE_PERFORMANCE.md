# Concordia Low-Level Architecture and Performance

This document explains the **current** architecture (as implemented now), where latency comes from, and what terms like "binary frames", "per-client queues", and "slow-client blocking" actually mean.

---

## 1) Big Picture

Concordia is a relay around a single Claude PTY running on the host machine.

```text
                 Internet (via ngrok tcp tunnel)
   ┌──────────────────────────────────────────────────────────┐
   │                                                          │
   │  Remote Client(s) <---- websocket ----> Host Server      │
   │                                                          │
   └──────────────────────────────────────────────────────────┘
                                      |
                                      | os.write / os.read on PTY master fd
                                      v
                               Claude CLI process
                               (stdin/stdout/stderr via PTY)
```

Important: the host also usually starts a **local client** and connects to its own server over loopback (`127.0.0.1`).

---

## 2) Actual Runtime Flow (Current Code)

### 2.1 Create Party

1. `concordia_host` starts.
2. ngrok creates a TCP public endpoint.
3. server starts websocket listener.
4. server starts Claude attached to PTY.
5. host local client connects to websocket (unless disabled).
6. remote clients connect with invite code.

```text
concordia_host
  ├─ ngrok.connect(port, "tcp")
  ├─ run_server(...)
  │    ├─ websockets.serve(...)
  │    └─ _start_claude()
  │         ├─ pty.openpty() -> (master_fd, slave_fd)
  │         ├─ create_subprocess_shell("claude ...", stdin=slave_fd, stdout=slave_fd, stderr=slave_fd)
  │         └─ start _read_claude_and_broadcast()
  └─ run_client("ws://127.0.0.1:port", token, user)  # local host client
```

---

### 2.2 Input Path (User typing -> Claude)

```text
User keypress
  -> client TUI raw mode reads bytes from stdin
  -> ClientTransport.send_input_bytes(raw)
  -> websocket binary frame
  -> server _handler() receives bytes
  -> server _write_input_bytes(raw)
  -> os.write(claude_master_fd, raw)
  -> Claude PTY stdin
```

Notes:
- This is byte-level streaming, not line-buffered prompts.
- Multiple clients can write concurrently into the same PTY stream.

---

### 2.3 Output Path (Claude -> all users)

```text
Claude writes to PTY (stdout/stderr merged by PTY)
  -> server _read_claude_and_broadcast() calls os.read(master_fd, 4096)
  -> server _broadcast_raw(chunk)
  -> websocket binary frame to each connected client
  -> each client writes raw bytes to terminal
  -> terminal interprets ANSI/control sequences
```

Notes:
- Because PTY is used, stdout and stderr are merged into one terminal stream.
- Clients render raw bytes directly; that is why ANSI UI (Claude's TUI) appears correctly.

---

## 3) Why It Feels Slow

Latency is the sum of multiple components:

```text
Total perceived lag
  = network RTT (client <-> host/ngrok)
  + tunnel overhead/jitter
  + server fanout behavior
  + terminal redraw cost
  + Claude processing time
```

The major practical contributors:

1. Network distance + ngrok path
- Remote keystrokes and output chunks traverse internet + tunnel.
- Even 60-120ms RTT feels laggy with full-screen TUIs.

2. Bursty terminal output
- Claude TUI emits many control/redraw bytes.
- High byte volume increases jitter and perceived stutter.

3. Fanout coupling
- For each chunk, server sends to all sockets.
- If one connection is slow, it can degrade fanout pacing (details below).

4. Shared PTY contention
- All users writing at once can create chaotic input and "laggy feeling."

---

## 4) Key Terms (Plain English)

## 4.1 Binary Frames

WebSocket messages can be:
- text frames (string/JSON)
- binary frames (raw bytes)

In Concordia:
- control events (`invite`, `participants`, `error`) use JSON text frames.
- hot stream (`stdin` bytes, PTY output bytes) uses binary frames.

Why binary matters:
- no base64 encoding overhead
- no JSON wrapping per chunk
- lower CPU and fewer bytes on wire

---

## 4.2 Slow-Client Blocking

A "slow client" is one that cannot consume outbound data fast enough (poor network/device).

If broadcasting is tightly coupled, one slow receiver can delay progress for others.

Conceptually:

```text
Server has output chunk C
  -> send C to fast client A (quick)
  -> send C to slow client B (waits)
  -> meanwhile next chunk C2 can't advance as smoothly
```

Even with concurrent send calls, if you await all sends for each chunk, the pacing can still be dragged by stragglers.

---

## 4.3 Per-Client Queues

Per-client queue = each connection gets its own outbound buffer and sender task.

Instead of:
- "read chunk, send to everyone synchronously"

you do:
- "read chunk once, enqueue to each client queue, return immediately"
- each client sender task drains at its own speed

Diagram:

```text
PTY reader
  -> chunk C
  -> enqueue C into Q_A, Q_B, Q_C

Sender A drains Q_A fast
Sender B drains Q_B slow
Sender C drains Q_C medium
```

Benefits:
- fast clients remain smooth
- slow clients don't stall global stream

Tradeoff:
- queue memory can grow unless bounded (drop/compress policy required)

---

## 5) Current Strengths / Weaknesses

### Strengths
- PTY model preserves true terminal behavior.
- Input/output hot path already uses raw bytes.
- Host and remote share same stream semantics.

### Weaknesses
- No per-client outbound queue isolation yet.
- ngrok adds external tunnel jitter.
- Shared multi-writer PTY can feel noisy under concurrent typing.

---

## 6) Practical Optimization Order (Highest ROI)

1. Add per-client bounded outbound queues + dedicated sender tasks.
2. Add slow-client policy (drop oldest chunks or disconnect lagging clients).
3. Keep binary frames only for stream path (already done).
4. Consider managed relay architecture (replace ngrok path).
5. Optional: single active writer lock/token for input clarity.

---

## 7) Mental Model to Keep

Concordia is not "chat messages." It is effectively a distributed terminal multiplexer:

```text
many keyboards  -> one PTY stdin
one PTY output -> many screens
```

Performance tuning should therefore follow terminal-multiplexer principles:
- decouple producer from consumers,
- isolate slow consumers,
- minimize bytes and hops,
- avoid unnecessary render work.

