# Claude Interactive Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Start Claude Code in interactive mode when party starts, keeping it running for continuous prompt submission.

**Architecture:** When `concordia_host` initializes, spawn Claude Code as a long-running subprocess with stdin open. Feed dedupe'd prompts to its stdin instead of spawning new processes. Maintain context across prompt batches.

**Tech Stack:** asyncio, subprocess, websockets (existing)

---

## Task 1: Start Claude on Party Initialization

**Files:**
- Modify: `concordia/server.py` (PartyServer class, start method)

**Step 1: Add claude process management to PartyState**

In `concordia/server.py`, update `PartyState` dataclass (around line 24):

```python
@dataclass
class PartyState:
    invite: Invite
    creator: str
    claude_command: str
    dedupe_window: float
    min_prompts: int
    pending: List[PromptItem] = field(default_factory=list)
    connections: Dict[str, websockets.WebSocketServerProtocol] = field(default_factory=dict)
    claude_process: Optional[asyncio.subprocess.Process] = None
```

**Step 2: Create method to start Claude process**

Add this method to `PartyServer` class (after `_run_claude` method, around line 161):

```python
async def _start_claude(self) -> bool:
    """Start Claude Code in interactive mode. Returns True if successful."""
    cmd = self.state.claude_command.replace("{prompt_file}", "-")  # Use stdin with "-"
    try:
        self.state.claude_process = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._broadcast({"type": "system", "message": "claude started (interactive mode)"})
        # Start pump tasks to stream output
        asyncio.create_task(self._pump_claude_stdout())
        asyncio.create_task(self._pump_claude_stderr())
        return True
    except Exception as exc:
        await self._broadcast({"type": "error", "message": f"failed to start claude: {exc}"})
        return False

async def _pump_claude_stdout(self) -> None:
    """Stream Claude stdout to all clients."""
    if not self.state.claude_process or not self.state.claude_process.stdout:
        return
    try:
        while True:
            line = await self.state.claude_process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            await self._broadcast({"type": "output", "text": text})
            print(text)
    except Exception:
        pass

async def _pump_claude_stderr(self) -> None:
    """Stream Claude stderr to all clients."""
    if not self.state.claude_process or not self.state.claude_process.stderr:
        return
    try:
        while True:
            line = await self.state.claude_process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            await self._broadcast({"type": "output", "text": text})
            print(text, file=sys.stderr)
    except Exception:
        pass
```

**Step 3: Call _start_claude in start() method**

Modify `PartyServer.start()` (around line 40):

```python
async def start(self, host: str, port: int) -> None:
    async with websockets.serve(self._handler, host, port):
        print("party created")
        print(f"invite code: {format_invite(self.state.invite.host, self.state.invite.port, self.state.invite.token)}")
        await self._start_claude()
        await self._dedupe_loop()
```

**Step 4: Add imports**

Add to top of `concordia/server.py` (line 2-6):

```python
import sys
```

**Step 5: Test the changes**

Run locally:
```bash
python3 -m concordia --create-party
```

Expected: Party starts, Claude Code launches in background with its interactive prompt visible.

**Step 6: Commit**

```bash
git add concordia/server.py
git commit -m "feat: start Claude in interactive mode on party initialization

- Spawn Claude subprocess when concordia_host starts
- Keep stdin open for continuous prompt submission
- Stream stdout/stderr to all connected clients
- Sets foundation for queuing prompts to running Claude instance"
```

---

**Next Steps (for later):**
- Task 2: Queue prompts while Claude is busy
- Task 3: Send dedupe'd prompts to Claude stdin
- Task 4: Cleanup on party shutdown

