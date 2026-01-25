# Smart Installation Wrapper Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `pipx install .` with a single `./install.sh` command that detects dependencies, installs the package, and prompts for initial setup.

**Architecture:** Create a Bash script that (1) detects if pipx/pip are installed, (2) installs dependencies and the package, (3) runs initial Gemini key setup interactively, (4) validates Claude CLI is available. Add a Makefile as a convenient wrapper for non-Bash users. Update README with the new one-command quickstart.

**Tech Stack:** Bash, Python, pipx/pip (existing), Makefile

---

## Task 1: Create Smart Installation Script

**Files:**
- Create: `install.sh`
- Modify: `README.md` (update quickstart section)

**Step 1: Write the install.sh script**

Create `/Users/thorbthorb/Downloads/concordia/install.sh`:

```bash
#!/bin/bash
set -e

echo "🔧 Concordia Installation"
echo "========================"
echo ""

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Step 1: Check for Python
echo "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 not found${NC}"
    echo "Please install Python 3.9 or later and try again."
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"
echo ""

# Step 2: Detect package manager (pipx or pip)
echo "Detecting package manager..."
INSTALL_METHOD=""

if command -v pipx &> /dev/null; then
    echo -e "${GREEN}✓ pipx found (recommended)${NC}"
    INSTALL_METHOD="pipx"
elif command -v pip3 &> /dev/null; then
    echo -e "${YELLOW}⚠ pipx not found, falling back to pip${NC}"
    INSTALL_METHOD="pip"
else
    echo -e "${RED}❌ Neither pipx nor pip found${NC}"
    echo "Install pipx: https://pipx.pypa.io/latest/"
    echo "Or install pip: python3 -m ensurepip"
    exit 1
fi
echo ""

# Step 3: Install concordia package
echo "Installing concordia package..."
if [ "$INSTALL_METHOD" = "pipx" ]; then
    pipx install .
elif [ "$INSTALL_METHOD" = "pip" ]; then
    python3 -m pip install --user .
fi
echo -e "${GREEN}✓ Package installed${NC}"
echo ""

# Step 4: Check for Claude CLI
echo "Checking for Claude Code CLI..."
if ! command -v claude &> /dev/null; then
    echo -e "${YELLOW}⚠ Claude Code CLI not found on PATH${NC}"
    echo "Install it: https://claude.com/claude-code"
    echo "Or set --claude-command when running concordia_host"
else
    echo -e "${GREEN}✓ Claude CLI found${NC}"
fi
echo ""

# Step 5: Run initial setup (Gemini key)
echo "Initial setup: Gemini API Key"
echo "=============================..."
python3 << 'PYTHON_EOF'
import os
import sys
from pathlib import Path

# Add current directory to path to import concordia
sys.path.insert(0, os.getcwd())

try:
    from concordia.config import ensure_gemini_key_interactive

    print("You'll need a Gemini API key to run as host.")
    print("Get one free: https://ai.google.dev/")
    print("")

    key = ensure_gemini_key_interactive()
    if key:
        print("")
        print("✓ Gemini API key saved to ~/.config/concordia/.env")
    else:
        print("")
        print("⚠ Skipped key setup. You can add it later.")
        print("  Run: echo 'GEMINI_API_KEY=your_key' >> ~/.config/concordia/.env")
except ImportError as e:
    print(f"Error: Could not import concordia: {e}")
    sys.exit(1)
PYTHON_EOF

echo ""
echo -e "${GREEN}✅ Installation complete!${NC}"
echo ""
echo "Next steps:"
echo "==========="
echo "Host (create a party):"
echo "  concordia_host"
echo ""
echo "Client (join a party):"
echo "  concordia_client concordia://HOST:PORT/TOKEN"
echo ""
echo "For more options: concordia_host --help"
```

**Step 2: Make the script executable**

```bash
chmod +x install.sh
```

**Step 3: Test the script (dry run in current environment)**

```bash
bash install.sh
```

Expected output: Should detect Python, find pipx/pip, install package, prompt for Gemini key, check for Claude CLI.

**Step 4: Verify installation worked**

```bash
which concordia_host
concordia_host --help
```

Expected: Shows help without errors.

**Step 5: Update README.md quickstart**

Replace the "Quickstart" section (lines 5-25) with:

```markdown
## Quickstart

**1) One-command install & setup:**

```bash
./install.sh
```

This will:
- Install concordia (using pipx if available, else pip)
- Prompt for your Gemini API key (stored in `~/.config/concordia/.env`)
- Check for Claude Code CLI
- Guide you through next steps

**2) Start a party (host):**

```bash
concordia_host
```

Share the invite code printed in your terminal.

**3) Join the party (client):**

```bash
concordia_client concordia://HOST:PORT/TOKEN --user alice
```

## Alternative: From source

If you prefer installing manually:

```bash
pipx install .
# or: python3 -m pip install --user .
```

Then run:
```bash
concordia_host  # First run prompts for Gemini key
```
```

**Step 6: Commit**

```bash
git add install.sh README.md
git commit -m "feat: add smart installation wrapper script

- Auto-detect pipx/pip and install package
- Interactive Gemini key setup during install
- Claude CLI availability check
- Simplified one-command quickstart in README"
```

---

## Task 2: Create Makefile for Convenience

**Files:**
- Create: `Makefile`

**Step 1: Write the Makefile**

Create `/Users/thorbthorb/Downloads/concordia/Makefile`:

```makefile
.PHONY: install help clean

install:
	@bash install.sh

help:
	@echo "Concordia - Multi-user prompt party"
	@echo ""
	@echo "Available commands:"
	@echo "  make install    - Install concordia and run initial setup"
	@echo "  make host       - Start a party (host)"
	@echo "  make help       - Show this help"
	@echo ""
	@echo "For client commands, use concordia_client directly"

host:
	concordia_host

.DEFAULT_GOAL := help
```

**Step 2: Verify Makefile syntax**

```bash
make help
```

Expected output: Shows help menu.

**Step 3: Test install via make**

```bash
make install
```

Expected: Runs `bash install.sh` successfully.

**Step 4: Commit**

```bash
git add Makefile
git commit -m "feat: add Makefile with convenient install target

- 'make install' runs smart installation script
- 'make host' shorthand for concordia_host
- 'make help' shows available commands"
```

---

## Task 3: Add .gitignore Entry & Documentation

**Files:**
- Modify: `.gitignore`
- Modify: `README.md` (add installation notes section)

**Step 1: Check current .gitignore**

```bash
cat .gitignore
```

**Step 2: Ensure no build artifacts are tracked**

Add to `.gitignore` if not present:

```
*.pyc
__pycache__/
*.egg-info/
dist/
build/
.Python
env/
venv/
```

**Step 3: Add installation troubleshooting to README**

Add a new section before "Requirements":

```markdown
## Installation Troubleshooting

**"pipx not found"**
- Install pipx: `brew install pipx` (macOS) or `pip3 install --user pipx`
- Or let the script use pip instead

**"Claude CLI not found"**
- Install Claude Code: https://claude.com/claude-code
- Or pass `--claude-command` to concordia_host for a custom command

**"Python 3.9+ required"**
- Check version: `python3 --version`
- Update Python if needed

**"Gemini API key issues"**
- Get a free key: https://ai.google.dev/
- Re-run setup: Edit `~/.config/concordia/.env` and add your key
```

**Step 4: Commit**

```bash
git add .gitignore README.md
git commit -m "docs: add installation troubleshooting guide"
```

---

## Task 4: Validation & Testing

**Files:**
- Test: Manual verification (no code changes)

**Step 1: Clean install test**

Create a temporary directory and test:

```bash
cd /tmp
git clone /Users/thorbthorb/Downloads/concordia test_concordia
cd test_concordia
./install.sh
```

Expected:
- Script runs without errors
- Detects Python, pipx/pip, Claude
- Prompts for Gemini key
- Installs concordia successfully
- Final message shows "Installation complete!"

**Step 2: Verify commands work**

```bash
concordia_host --help
concordia_client --help
```

Expected: Both show help without errors.

**Step 3: Verify Makefile targets**

```bash
make help
make install  # Should succeed if already installed
```

Expected: Shows help, make install runs without errors.

**Step 4: Commit (if any fixes needed)**

```bash
git add .
git commit -m "test: validate smart installation wrapper"
```

---

## Summary

| Task | Deliverable | Time Estimate |
|------|-------------|---|
| 1 | `install.sh` + README update | ~20 min |
| 2 | `Makefile` | ~5 min |
| 3 | `.gitignore` + troubleshooting docs | ~5 min |
| 4 | Manual validation & testing | ~10 min |

**Key Benefits:**
- ✅ Single `./install.sh` or `make install` command
- ✅ Automatic dependency detection (pipx → pip fallback)
- ✅ Interactive setup (Gemini key, Claude validation)
- ✅ Clear error messages and guidance
- ✅ Low token usage (just shell scripts, minimal Python)
