#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Activate Python 3.14 venv (creates it if missing)
# shellcheck disable=SC1090
source "$ROOT_DIR/scripts/activate_venv.sh"

python3 -m pip install -r requirements.txt pytest --break-system-packages >/dev/null

if [ "$#" -eq 0 ]; then
  python3 scripts/run_pytest_html.py \
    -s --log-cli-level=DEBUG \
    tests/test_round_trip_live_google.py \
    tests/test_one_way_live_google.py \
    tests/test_round_trip_js_flow.py \
    tests/test_multicity_live_google.py 
else
  python3 scripts/run_pytest_html.py "$@"
fi
