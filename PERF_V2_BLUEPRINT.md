# Concordia Perf V2 Blueprint

This is an implementation-ready plan for the next performance iteration.

## Goals

1. Keep fast clients smooth even if one client is slow.
2. Bound memory under bursty PTY output.
3. Reduce end-to-end latency variance (jitter).
4. Add observability so tuning is data-driven.

---

## 1) Core Design Change: Per-Client Outbound Queues

Current issue:
- PTY reader broadcasts each chunk and awaits sends for all clients.
- Slow client can still influence pacing.

V2:
- PTY reader enqueues chunk into each client queue and returns immediately.
- Each client has its own sender task draining that queue.

```text
PTY reader
  -> chunk
  -> enqueue to Q(client1), Q(client2), Q(client3)
  -> continue reading PTY

sender(client1): fast drain
sender(client2): slow drain
sender(client3): medium drain
```

---

## 2) Data Structures (Server)

Add in `concordia/server.py`:

```python
from dataclasses import dataclass, field
from collections import deque
from typing import Deque, Optional

@dataclass
class OutboundQueue:
    chunks: Deque[bytes] = field(default_factory=deque)
    bytes_queued: int = 0
    dropped_chunks: int = 0
    dropped_bytes: int = 0
    wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False

@dataclass
class ClientConn:
    name: str
    ws: websockets.WebSocketServerProtocol
    q: OutboundQueue = field(default_factory=OutboundQueue)
    sender_task: Optional[asyncio.Task] = None
    joined_at: float = field(default_factory=time.time)
```

Replace:
- `state.connections: Dict[str, WebSocketServerProtocol]`

With:
- `state.connections: Dict[str, ClientConn]`

---

## 3) Queue Limits and Policy

Defaults:
- `MAX_QUEUE_BYTES_PER_CLIENT = 512 * 1024` (512KB)
- `MAX_QUEUE_CHUNKS_PER_CLIENT = 1024`
- `MAX_CHUNK_SIZE = 4096` (same read size)

Drop policy (recommended for terminal stream):
- Drop **oldest** chunks until queue fits.
- Keep newest output so client catches up to current state.

Disconnect policy:
- If dropped_bytes exceeds threshold (e.g. 8MB in 30s), disconnect lagging client with error.

---

## 4) Server Flow Changes

### 4.1 On client join

1. create `ClientConn`
2. start `sender_task = create_task(_client_sender_loop(client))`
3. store in `state.connections[name]`

### 4.2 PTY read path

In `_read_claude_and_broadcast`:
- read bytes from PTY
- call `_fanout_chunk(chunk)` (enqueue only)

`_fanout_chunk`:
- iterate all clients
- enqueue chunk into each queue with bounds/drop policy
- set `wake_event`

### 4.3 Per-client sender loop

`_client_sender_loop(client)`:
1. wait for `wake_event`
2. pop queued chunks in order
3. `await client.ws.send(chunk)` for each
4. on close/error, cleanup connection

### 4.4 Control JSON messages

Keep existing `_broadcast` for small JSON control events.
Optional later: route control messages through same queue for strict ordering.

---

## 5) Input Path (No major change)

Keep:
- binary client frames -> `_write_input_bytes` -> `os.write(pty_fd, raw)`

Optional safety improvement:
- cap input chunk size (e.g. 8KB/frame) and reject larger frames.

---

## 6) Metrics to Add

Global counters/gauges:
- `pty_chunks_read_total`
- `pty_bytes_read_total`
- `fanout_enqueue_total`
- `fanout_enqueue_bytes_total`

Per-client:
- `queue_bytes_current`
- `queue_chunks_current`
- `queue_dropped_chunks_total`
- `queue_dropped_bytes_total`
- `send_errors_total`
- `disconnect_slow_client_total`

Latency sampling:
- enqueue timestamp attached to some chunks
- compute `send_delay_ms` at sender

---

## 7) Tuning Defaults (Initial)

Start with:
- queue byte cap: 512KB/client
- drop oldest on overflow
- slow-client disconnect: >8MB dropped in 30s
- websocket `compression=None`
- websocket `max_queue=256` (already set)

Then tune based on observed:
- average send delay
- dropped bytes rate
- user-perceived stutter

---

## 8) Rollout Plan

1. Refactor connection model to `ClientConn` + sender task.
2. Implement bounded queue + drop-oldest.
3. Add structured metrics logging (stdout JSON or debug counters).
4. Add slow-client disconnect guardrail.
5. Load test with:
   - 1 fast + 1 intentionally slow client
   - bursty PTY output playback
6. Verify host remains smooth while slow client degrades independently.

---

## 9) Acceptance Criteria

1. One slow client no longer causes visible stutter for fast clients.
2. Memory remains bounded under sustained high output.
3. No unbounded queue growth.
4. PTY reader loop continues at stable cadence under load.
5. Host local UI remains responsive when remote client has poor network.

---

## 10) Optional V3 Ideas

1. Active writer token (avoid multi-user stdin collisions).
2. Delta/compression at semantic layer (not websocket compression).
3. Relay migration (replace ngrok) for lower/jitter-stable network path.
4. Region-aware relay placement if multi-continent usage grows.

