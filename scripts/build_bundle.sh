#!/usr/bin/env bash
set -euo pipefail

mkdir -p dist

# Build a self-contained zipapp with bundled dependencies.
# Requires network access to download deps.
pipx run shiv -o dist/concordia.pyz -c concordia .
