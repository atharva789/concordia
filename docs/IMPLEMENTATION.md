# Concordia Implementation Plan

## Current System Status

✅ **Complete:**
- Installation wrapper (install.sh, Makefile)
- One-time API key setup with re-prompt on error
- Host/client architecture with WebSocket communication
- Prompt collection and Gemini deduplication
- Claude in interactive mode (Task 1)
- Output streaming to all participants
- Shell access via `/shell` command

❌ **Issues (Task 2 - See TASK2-ISSUES.md):**
- 5 Critical race conditions/deadlocks
- 4 Important safety issues
- 3 Warnings

⚠️ **Design Gaps:**
- No queue-based prompt buffering
- No explicit backpressure handling
- Limited error recovery
- No monitoring/metrics

---

## Critical Issues to Fix (Task 2)

### Phase 1: Synchronization (BLOCKING)

**Issue #2: Unsynchronized claude_busy Flag**
- Current: Plain bool, accessed from multiple tasks without locks
- Risk: Race conditions, overlapping prompts, deadlocks
- **Fix:** Replace with `asyncio.Event`
- **Files:** server.py (PartyState, _process_batch, _pump_claude_stdout)
- **Effort:** 30 min
- **Impact:** Enables fixes for #1, #5, #11

```python
# PartyState
claude_ready: asyncio.Event = field(default_factory=asyncio.Event)

# _process_batch
self.state.claude_ready.clear()  # Mark Claude busy

# _pump_claude_stdout (on completion)
self.state.claude_ready.set()    # Mark Claude ready
```

---

**Issue #4: stdin Write Unprotected by Lock**
- Current: Multiple tasks can write concurrently, corrupting input stream
- Risk: Data corruption, mixed prompts sent to Claude
- **Fix:** Protect write with `self._lock`
- **Files:** server.py (_write_prompt_to_claude)
- **Effort:** 15 min
- **Dependency:** Requires Phase 1 (Event)

```python
async def _write_prompt_to_claude(self, prompt: str) -> None:
    async with self._lock:
        self.state.claude_process.stdin.write(prompt.encode() + b"\n")
        await asyncio.wait_for(self.state.claude_process.stdin.drain(), timeout=5.0)
```

---

### Phase 2: Safety & Error Handling

**Issue #1: Blocking drain() Without Timeout**
- Current: `await drain()` can block event loop indefinitely
- Risk: Server stalls, all clients freeze
- **Fix:** Add 5-second timeout
- **Files:** server.py (_write_prompt_to_claude)
- **Effort:** 10 min
- **Dependency:** Phase 1 (Event)

```python
try:
    await asyncio.wait_for(self.state.claude_process.stdin.drain(), timeout=5.0)
except asyncio.TimeoutError:
    await self._broadcast({"type": "error", "message": "Prompt delivery timeout"})
```

---

**Issue #5: Deadlock on Write Failure**
- Current: If write fails, claude_busy/ready flag never clears
- Risk: Next dedupe waits forever, party hung
- **Fix:** Always clear flag on exception
- **Files:** server.py (_write_prompt_to_claude)
- **Effort:** 10 min
- **Dependency:** Phase 1 (Event)

```python
except Exception as exc:
    await self._broadcast({"type": "error", "message": f"Failed to send: {exc}"})
    self.state.claude_ready.set()  # MUST clear on error
```

---

**Issue #3: Completion Detection Too Broad**
- Current: Blank lines or ">" anywhere trigger false ready
- Risk: Premature completion signal mid-output, execution breaks
- **Fix:** More robust detection - EOF handling, prompt-specific markers
- **Files:** server.py (_pump_claude_stdout)
- **Effort:** 20 min
- **Dependency:** None

```python
if not line:  # EOF
    self.state.claude_ready.set()
    await self._broadcast({"type": "system", "message": "claude disconnected"})
    break
text = line.decode("utf-8", errors="replace").rstrip()
if text == ">" or text.endswith(">>> "):  # More specific
    self.state.claude_ready.set()
```

---

### Phase 3: Code Quality

**Issue #6: Unused pending_prompts Queue**
- Current: Declared but never used
- Risk: Dead code, confuses maintainers
- **Fix:** Remove or implement queue-based prompt buffering
- **Files:** server.py (PartyState)
- **Effort:** 5 min (remove) or 60 min (implement)
- **Recommendation:** Remove for now, implement later if needed

```python
# Remove from PartyState:
# pending_prompts: asyncio.Queue = ...
```

---

**Issue #9: Empty Prompt Not Validated**
- Current: `_write_prompt_to_claude()` sends empty prompts
- Risk: Wasted cycles, confusing output
- **Fix:** Add empty check like `_run_claude()` does
- **Files:** server.py (_write_prompt_to_claude)
- **Effort:** 5 min

```python
async def _write_prompt_to_claude(self, prompt: str) -> None:
    if not prompt.strip():
        await self._broadcast({"type": "error", "message": "Cannot send empty prompt"})
        return
    # ... rest
```

---

### Phase 4: Robustness (Warnings)

**Issue #10: No Backpressure/Gating**
- Current: Multiple dedupe cycles could write concurrently
- Risk: Prompts overlap, chaos
- **Fix:** Check if Claude ready before starting dedupe
- **Files:** server.py (_dedupe_loop)
- **Effort:** 10 min
- **Dependency:** Phase 1 (Event)

```python
async def _dedupe_loop(self) -> None:
    while True:
        await asyncio.sleep(0.5)
        if not self.state.claude_ready.is_set():
            continue  # Claude still busy, skip dedupe cycle
        # ... rest
```

---

## Design Improvements (Post-Fix)

### 1. Explicit Queue Management
**Currently:** Implicit prompts in dedupe cycle
**Improvement:** Explicit queue with consumer task
**Effort:** 90 min
**Benefit:** Clearer prompt flow, easier monitoring

```python
# New task in PartyServer
async def _prompt_queue_consumer(self) -> None:
    while True:
        await self.state.claude_ready.wait()
        if self.state.pending_prompts.empty():
            continue
        prompt = await self.state.pending_prompts.get()
        await self._write_prompt_to_claude(prompt)
```

---

### 2. Error Recovery & Restart
**Currently:** Claude crash = dead party
**Improvement:** Auto-restart Claude on crash
**Effort:** 45 min
**Benefit:** Resilient to Claude failures

```python
async def _monitor_claude_process(self) -> None:
    while True:
        if not self.state.claude_process:
            await asyncio.sleep(1)
            continue
        code = await self.state.claude_process.wait()
        await self._broadcast({
            "type": "error",
            "message": f"Claude exited {code}, restarting..."
        })
        await self._start_claude()
```

---

### 3. Metrics & Logging
**Currently:** No visibility into dedup/execution times
**Improvement:** Log metrics, track performance
**Effort:** 60 min
**Benefit:** Debugging, performance tuning

```python
# Track per PartyState
prompts_processed: int = 0
total_dedupe_time: float = 0.0
total_execution_time: float = 0.0
```

---

### 4. Configurable Completion Markers
**Currently:** Hardcoded ">" detection
**Improvement:** CLI flag for custom markers
**Effort:** 20 min
**Benefit:** Support different Claude modes

```python
p.add_argument("--prompt-marker", default=">",
              help="Claude prompt marker to detect completion")
```

---

### 5. Ngrok Invite URL Generation
**Currently:** Manual host:port configuration required
**Improvement:** Auto-generate invite URLs using ngrok
**Effort:** 45 min
**Benefit:** Easy sharing, no port forwarding needed

```python
import pyngrok.ngrok as ngrok

async def _start_ngrok_tunnel(self, port: int) -> str:
    tunnel = ngrok.connect(port, "tcp")
    public_url = tunnel.public_url
    await self._broadcast({
        "type": "system",
        "message": f"Join URL: concordia_client {public_url}"
    })
    return public_url

# In main():
ngrok_tunnel = await server._start_ngrok_tunnel(port)
try:
    await server.run()
finally:
    ngrok.disconnect(ngrok_tunnel.public_url)
```

**Setup:**
```bash
# Install ngrok
pip install pyngrok

# Authenticate (once)
ngrok authtoken YOUR_NGROK_TOKEN
```

**Configuration:**
- Set `NGROK_AUTHTOKEN` env var or configure via `ngrok config add-authtoken`
- Optional: `NGROK_REGION` for regional tunnels (us, eu, ap, au)

**Protocol:**
- Invite URL format: `concordia://HOST:PORT/TOKEN`
- Ngrok provides public TCP endpoint: `concordia_client tcp://NGROK_URL/TOKEN`

---

### 6. Dynamic Invite Code with Token
**Currently:** Static host:port
**Improvement:** Generate unique token for each party
**Effort:** 15 min
**Benefit:** Security, connection verification

```python
import secrets

class PartyServer:
    def __init__(self, ...):
        self._invite_token = secrets.token_urlsafe(8)

    @property
    def invite_url(self) -> str:
        return f"concordia://{self._host}:{self._port}/{self._invite_token}"
```

## Implementation Roadmap

### Week 1: Fix Critical Issues
```
Day 1: Phase 1 - Event-based synchronization
  - Add asyncio.Event to PartyState
  - Update _process_batch & _pump_claude_stdout
  - Test: prompts should serialize properly
  - Commit: "fix: use asyncio.Event for Claude readiness"

Day 2: Phase 2 - Safety & Error Handling
  - Add timeout to drain()
  - Clear flag on exception
  - Fix completion detection
  - Test: each failure case
  - Commits: 3 separate commits (one per issue)

Day 3: Phase 3 - Code Quality
  - Remove unused queue
  - Add empty prompt validation
  - Add backpressure gating
  - Test: run full party cycle
  - Commit: "fix: code quality & robustness"

Day 4: Testing & Review
  - Manual testing with multiple clients
  - Test error scenarios (Claude crash, timeout, etc)
  - Review all changes
  - Create PR
```

### Week 2: Design Improvements (Optional)
```
Day 5-7: Implement queue management, error recovery, metrics
```

---

## Testing Checklist

### Before Merge (Required)
- [ ] Multiple clients can submit prompts concurrently
- [ ] Prompts don't overlap (no concurrent writes to stdin)
- [ ] Completion detected correctly (no false positives on blank lines)
- [ ] drain() timeout prevents server stalls
- [ ] Write failure clears ready flag (no deadlock)
- [ ] Empty prompts rejected
- [ ] Dedupe waits for Claude ready (no overlapping executions)

### Nice to Have
- [ ] Claude crash detected and broadcast
- [ ] Auto-restart on crash
- [ ] Metrics logged (dedupe time, execution time)
- [ ] Custom prompt markers supported

---

## Risk Assessment

| Phase | Risk | Mitigation |
|-------|------|-----------|
| 1 | Event not familiar | Good docs, test thoroughly |
| 2 | Timeout too short/long | Start with 5s, adjust based on testing |
| 3 | Remove queue breaks something | It's unused, safe to remove |
| 4 | Backpressure logic wrong | Test with fast prompt submission |

**Overall Risk:** LOW after Phase 1-3 fixes

---

## Effort Estimate

| Phase | Tasks | Effort | Dependencies |
|-------|-------|--------|--------------|
| 1 | Event sync | 30 min | None |
| 2 | Safety/errors | 40 min | Phase 1 |
| 3 | Code quality | 40 min | Phase 1, 2 |
| 4 | Robustness | 10 min | Phase 1, 3 |
| **Total Critical Fix** | | **2 hours** | |
| Improvements | Queue, recovery, metrics | 3-4 hours | Phases 1-4 |

---

## Success Criteria

✅ **Must Have (v1.0):**
- No race conditions in Task 2
- No deadlocks on error
- Proper completion detection
- All 9 issues resolved

✅ **Should Have (v1.1):**
- Queue-based prompt management
- Error recovery & restart
- Backpressure enforcement

✅ **Nice to Have (v1.2):**
- Metrics & performance tracking
- Custom prompt markers
- Monitoring/alerting

---

## Files Modified

```
concordia/
  server.py          [+200 lines modified]
    - PartyState: add Event, remove unused queue
    - _process_batch: add backpressure, use Event
    - _write_prompt_to_claude: add timeout, lock, validation
    - _pump_claude_stdout: robust completion detection
    - _monitor_claude_process: new (optional, Phase 2)

  cli.py             [+1 line]
    - Add --prompt-marker arg (optional)

  docs/
    - TASK2-ISSUES.md [existing]
    - IMPLEMENTATION.md [this file]
```

---

## References

- TASK2-ISSUES.md - Detailed issue descriptions
- ARCHITECTURE.md - System design
- Current commits: 52d2013, 228b398, 2415a47 (Task 1 completed)
