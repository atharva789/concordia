# Claude Interactive Mode - Task 2: Queue Prompts

**Goal:** Queue dedupe'd prompts to Claude stdin instead of spawning new processes.

**Architecture:** Track whether Claude is busy. When busy, queue prompts. When idle, write prompt to stdin and set busy flag. Monitor pump tasks to detect when prompt completes.

---

## Task 1: Modify _process_batch to use stdin

**Files:**
- Modify: `concordia/server.py` (_process_batch, _write_prompt_to_claude)

**Step 1: Add prompt queue and busy flag to PartyState (line 31)**

```python
pending_prompts: asyncio.Queue = field(default_factory=asyncio.Queue)
claude_busy: bool = False
```

**Step 2: Implement _write_prompt_to_claude() (replace stub at line 202)**

```python
async def _write_prompt_to_claude(self, prompt: str) -> None:
    """Write prompt to Claude stdin."""
    if not self.state.claude_process or not self.state.claude_process.stdin:
        await self._broadcast({"type": "error", "message": "Claude not running"})
        return
    try:
        self.state.claude_process.stdin.write(prompt.encode() + b"\n")
        await self.state.claude_process.stdin.drain()
    except Exception as exc:
        await self._broadcast({"type": "error", "message": f"Failed to send prompt: {exc}"})
```

**Step 3: Modify _process_batch to write to stdin instead of file (line 127)**

Replace `await self._run_claude(combined)` with:

```python
self.state.claude_busy = True
await self._write_prompt_to_claude(combined)
```

**Step 4: Detect when Claude finishes (modify _pump_claude_stdout, line 219)**

After `await self._broadcast({"type": "output", "text": text})`, check for completion markers. When Claude prompt ends (blank line or ">"), set `claude_busy = False`.

**Step 5: Test**

```bash
python3 -m concordia --create-party
# Submit prompt, see it go to Claude stdin
```

**Step 6: Commit**

```bash
git add concordia/server.py
git commit -m "feat: queue prompts to Claude stdin for continuous execution"
```
