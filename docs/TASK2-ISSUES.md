# Task 2: Queue Prompts to Claude stdin - Issues Found

**Status:** ✅ Spec compliant but ⚠️ **UNSAFE FOR PRODUCTION**

**9 Issues:** 5 Critical/Important, 4 Warnings

---

## CRITICAL ISSUES

### 1. Blocking drain() Without Timeout (Line 215)
**Severity:** CRITICAL
**Impact:** Server stalls, clients freeze

```python
await self.state.claude_process.stdin.drain()  # Can block indefinitely
```

**Problem:** `drain()` flushes OS buffers. If Claude is slow reading, event loop stalls.

**Fix:**
```python
try:
    await asyncio.wait_for(self.state.claude_process.stdin.drain(), timeout=5.0)
except asyncio.TimeoutError:
    await self._broadcast({"type": "error", "message": "Prompt delivery timeout"})
```

---

### 2. Unsynchronized claude_busy Flag (Lines 149, 232)
**Severity:** CRITICAL
**Impact:** Race conditions, deadlocks, overlapping prompts

```python
# Writer (line 149)
self.state.claude_busy = True

# Reader (line 232)
self.state.claude_busy = False
```

**Problem:** Multiple async tasks read/write bool without locks. CPython GIL hides this but violates asyncio spec.

**Fix:** Use `asyncio.Event`:
```python
# In PartyState:
claude_ready: asyncio.Event = field(default_factory=asyncio.Event)

# In _process_batch:
self.state.claude_ready.clear()

# In _pump_claude_stdout:
self.state.claude_ready.set()

# Callers:
await self.state.claude_ready.wait()
```

---

### 3. Completion Detection Too Broad (Line 231)
**Severity:** CRITICAL
**Impact:** Premature "ready" signal mid-output, breaks execution flow

```python
if text.strip() == "" or text.strip() == ">":
    self.state.claude_busy = False
```

**Problem:**
- Blank lines in normal output trigger false ready signal
- ">" anywhere in output (e.g., "value > 10") misdetected as prompt
- No EOF handling - if Claude crashes, flag never clears

**Fix:**
```python
if not line:  # EOF
    self.state.claude_busy = False
    await self._broadcast({"type": "system", "message": "claude disconnected"})
    break
text = line.decode("utf-8", errors="replace").rstrip()
if text == ">" or text.endswith(">>> "):  # More specific
    self.state.claude_busy = False
```

---

### 4. stdin Write Unprotected by Lock (Line 214)
**Severity:** CRITICAL
**Impact:** Concurrent writes corrupt input stream

```python
self.state.claude_process.stdin.write(prompt.encode() + b"\n")
```

**Problem:** No `self._lock` protection. Multiple `_process_batch()` calls could write concurrently.

**Fix:**
```python
async with self._lock:
    self.state.claude_process.stdin.write(prompt.encode() + b"\n")
    await asyncio.wait_for(self.state.claude_process.stdin.drain(), timeout=5.0)
```

---

### 5. Deadlock on Write Failure (Lines 210-217)
**Severity:** CRITICAL
**Impact:** Deadlock - claude_busy stuck True forever

```python
try:
    self.state.claude_process.stdin.write(...)
except Exception as exc:
    await self._broadcast(...)
    # BUG: claude_busy NEVER cleared!
```

**Problem:** If stdin write fails, flag stays True. Next dedupe waits forever (if you add gating).

**Fix:**
```python
except Exception as exc:
    await self._broadcast({"type": "error", "message": f"Failed to send: {exc}"})
    self.state.claude_busy = False  # MUST clear on error
```

---

## IMPORTANT ISSUES

### 6. Unused pending_prompts Queue (Line 34)
**Severity:** IMPORTANT
**Impact:** Dead code, confuses maintainers

```python
pending_prompts: asyncio.Queue = field(default_factory=asyncio.Queue)
```

**Problem:** Declared but never used anywhere. Spec says "queue prompts" but implementation uses existing `pending` list instead.

**Fix:** Remove field or implement queue-based prompt buffering (future work).

---

### 7. No stdin Lock Protection (Line 214)
**Severity:** IMPORTANT
**Impact:** Race condition with pump tasks

Same as Issue #4 - the write needs to be inside `async with self._lock`.

---

### 8. stdin Closed Before Write Race (Lines 210-217)
**Severity:** IMPORTANT
**Impact:** Write fails, but flag cleanup missing

**Problem:** Check at line 210 (`if not stdin`) passes, but Claude dies before write at line 214.

**Fix:** Clear flag on exception (Issue #5 fix).

---

### 9. Empty Prompt Not Validated (Line 208)
**Severity:** IMPORTANT
**Impact:** Sends empty prompts to Claude, wasted cycles

```python
async def _write_prompt_to_claude(self, prompt: str) -> None:
    # NO CHECK for empty prompt
```

**Problem:** `_run_claude()` validates (line 152) but `_write_prompt_to_claude()` doesn't.

**Fix:**
```python
if not prompt.strip():
    await self._broadcast({"type": "error", "message": "Cannot send empty prompt"})
    return
```

---

## WARNINGS

### 10. No Backpressure/Gating (Lines 115-130)
**Severity:** WARNING
**Impact:** Multiple prompts could write concurrently if dedupe cycles overlap

**Problem:** Nothing prevents `_process_batch()` from being called while Claude is busy processing previous prompt.

**Fix:** Add check in `_dedupe_loop()`:
```python
if self.state.claude_busy:
    continue  # Skip this cycle, wait for ready
```

---

### 11. Pump Task Precedence (Lines 149-150)
**Severity:** WARNING
**Impact:** Subtle race - flag might appear unset if pump runs first

**Problem:** Race between setting flag and pump detecting completion.

**Fix:** Use `asyncio.Event` (Issue #2 fix).

---

### 12. Check-Then-Act Race (Lines 210-214)
**Severity:** WARNING
**Impact:** Unlikely but possible - stdin closes between check and write

**Problem:** Standard TOCTOU (Time-of-check to time-of-use) race.

**Fix:** Generic exception handler already catches it (Issue #5 fix addresses this).

---

## SUMMARY

| # | Issue | Severity | Line(s) | Status |
|---|-------|----------|---------|--------|
| 1 | Blocking drain() no timeout | CRITICAL | 215 | Fix required |
| 2 | Unsynchronized claude_busy | CRITICAL | 149, 232 | Fix required |
| 3 | Completion detection too broad | CRITICAL | 231 | Fix required |
| 4 | stdin write unprotected | CRITICAL | 214 | Fix required |
| 5 | Deadlock on write failure | CRITICAL | 217 | Fix required |
| 6 | Unused pending_prompts | IMPORTANT | 34 | Remove/implement |
| 7 | No stdin lock | IMPORTANT | 214 | Add lock |
| 8 | stdin closed race | IMPORTANT | 210-217 | Covered by #5 |
| 9 | Empty prompt validation | IMPORTANT | 208 | Add check |
| 10 | No backpressure gating | WARNING | 115-130 | Add check |
| 11 | Pump precedence | WARNING | 149-150 | Covered by #2 |
| 12 | TOCTOU race | WARNING | 210-214 | Covered by #5 |

---

## RECOMMENDED ACTION

**DO NOT MERGE** until issues #1-5 and #6, #9 are addressed.

Issues #7-8, #10-12 are either redundant with other fixes or acceptable as warnings.

**Priority:**
1. Fix #2 (Event-based synchronization) - enables #1, #5, #11 fixes
2. Fix #3 (Completion detection)
3. Fix #4 (Lock protection)
4. Fix #6 (Remove queue or implement)
5. Fix #9 (Empty validation)

Estimated effort: 30-45 minutes for experienced developer.
