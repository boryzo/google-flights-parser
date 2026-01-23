#!/usr/bin/env bash
set -euo pipefail

VENV_DIR=".venv314"
PYTHON_BIN="python3.14"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: $PYTHON_BIN not found in PATH" >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

python --version
