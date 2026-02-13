# Fix: GEMINI_API_KEY Not Loading from .env File

**Date:** January 27, 2025
**Issue:** Concordia always prompted for GEMINI_API_KEY even when it existed in `.env`

---

## Problem

When running `concordia_host`, the application prompted for GEMINI_API_KEY despite the key being present in `~/.config/concordia/.env`.

---

## Root Cause

**Load order inversion.** The code checked for the API key before loading the `.env` file.

**Flow (before fix):**

```
cli.py:_run_create_party()
  └─→ ensure_gemini_key_interactive()  ← Called FIRST
       └─→ os.environ.get("GEMINI_API_KEY")  ← Returns None!
       └─→ Prompts user for key
       └─→ Saves to .env (but already have it!)
  └─→ server.py:run_server()
       └─→ load_env()  ← Called too late
            └─→ Loads .env (but key already prompted)
```

**Code at cli.py:38:**
```python
async def _run_create_party(args: argparse.Namespace) -> None:
    key = ensure_gemini_key_interactive()  # Checks env BEFORE load_env()
    if not key:
        raise SystemExit("Missing GEMINI_API_KEY")
```

The `ensure_gemini_key_interactive()` function checks `os.environ.get("GEMINI_API_KEY")`, but at this point the `.env` file hasn't been loaded yet, so the environment variable is empty.

---

## Fix Applied

**File:** `concordia/cli.py`

1. Added `load_env` to imports
2. Called `load_env()` before `ensure_gemini_key_interactive()`

**Diff:**
```diff
- from .config import ensure_gemini_key_interactive
+ from .config import ensure_gemini_key_interactive, load_env

 async def _run_create_party(args: argparse.Namespace) -> None:
+    load_env()
     key = ensure_gemini_key_interactive()
```

**Flow (after fix):**

```
cli.py:_run_create_party()
  └─→ load_env()  ← Loads .env FIRST
       └─→ os.environ["GEMINI_API_KEY"] = "..."
  └─→ ensure_gemini_key_interactive()  ← Now finds the key!
       └─→ os.environ.get("GEMINI_API_KEY")  ← Returns the key!
       └─→ No prompt needed
```

---

## Why This Happened

The `.env` loading was in `server.py:run_server()`, which is called **after** the API key check in `cli.py`. This was a simple ordering bug.

---

## Additional Complication: pipx Virtual Environment

During debugging, discovered that `concordia_host` runs from a **pipx virtual environment** at:
```
/Users/thorbthorb/.local/pipx/venvs/concordia/
```

The editable install (`pipx install --editable`) links the local code, but dependencies must be installed in that venv. This wasn't the root cause but added confusion during debugging.

**Solution:** Ran `pipx reinstall concordia` to ensure the venv reflected the latest code.

---

## Files Modified

- `concordia/cli.py` - Added `load_env()` call before key check

---

## Verification

```bash
# Test that .env loads correctly
/Users/thorbthorb/.local/pipx/venvs/concordia/bin/python -c \
  "from concordia.config import load_env, env_path; load_env(); print('GEMINI_API_KEY:', os.environ.get('GEMINI_API_KEY'))"

# Output: GEMINI_API_KEY: AIzaSyCGoqy76avTDPdWLXU9DzANFkGUQGcscu4
```
