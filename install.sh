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
echo "============================="

# Use the pipx-installed python interpreter to ensure dependencies are available
if command -v pipx &> /dev/null && [ -d ~/.local/pipx/venvs/concordia ]; then
    PIPX_PYTHON="$HOME/.local/pipx/venvs/concordia/bin/python"
    if [ -f "$PIPX_PYTHON" ]; then
        $PIPX_PYTHON << 'PYTHON_EOF'
import os
import sys
from pathlib import Path

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
    else
        echo "⚠ Pipx virtual environment not found, skipping interactive setup"
        echo "You can add the Gemini API key later:"
        echo "  mkdir -p ~/.config/concordia"
        echo "  echo 'GEMINI_API_KEY=your_key' > ~/.config/concordia/.env"
    fi
else
    echo "⚠ Pipx not fully configured yet, skipping interactive setup"
    echo "You can add the Gemini API key later:"
    echo "  mkdir -p ~/.config/concordia"
    echo "  echo 'GEMINI_API_KEY=your_key' > ~/.config/concordia/.env"
fi

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
