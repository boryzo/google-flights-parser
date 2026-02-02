#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-}"
VENV_DIR="${VENV_DIR:-}"

if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "Error: no Python interpreter found (tried python3.14..python3 and python)" >&2
  exit 1
fi

if [ -z "$VENV_DIR" ]; then
  if [ "$PYTHON_BIN" = "python3.14" ]; then
    VENV_DIR=".venv314"
  else
    VENV_DIR=".venv"
  fi
fi

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

python --version 2>/dev/null || python3 --version
