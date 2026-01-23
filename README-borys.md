# README-borys.md

Below is a complete guide for running tests and debugging (local + GitHub Actions).

## TL;DR (fastest path)
```bash
./scripts/run-tests.sh
```
This runs all tests and generates an HTML report at `reports/pytest_report.html`.

---

## Python version
Your system `python3` may point to 3.9.6. This project runs with **Python 3.14**.

### Easiest way:
```bash
source scripts/activate_venv.sh
```
This script:
- creates `.venv314` using `python3.14` (if missing)
- activates the environment
- prints the Python version

---

## Install dependencies
After activating the venv:
```bash
python -m pip install -r requirements.txt pytest
```

---

## Tests: types and files

### 1) Offline / decoder tests (no network)
```bash
pytest tests/test_round_trip_js_flow.py -vv
```

### 2) Live round-trip tests (real requests to Google Flights)
```bash
pytest -s --log-cli-level=DEBUG tests/test_round_trip_live_google.py -vv
```

### 3) Live one-way tests (real requests to Google Flights)
```bash
pytest -s --log-cli-level=DEBUG tests/test_one_way_live_google.py -vv
```

---

## HTML test report
We use the wrapper script `scripts/run_pytest_html.py`.

Example:
```bash
python scripts/run_pytest_html.py tests/test_round_trip_live_google.py -s --log-cli-level=DEBUG
```
Outputs:
- `reports/pytest_report.html`
- `reports/pytest_junit.xml`

---

## One-command test runner
```bash
./scripts/run-tests.sh
```
By default it runs:
- `tests/test_round_trip_live_google.py`
- `tests/test_one_way_live_google.py`
- `tests/test_round_trip_js_flow.py`

You can pass custom pytest args:
```bash
./scripts/run-tests.sh tests/test_one_way_live_google.py -k GDN-LTN -vv
```

---

## Assertions (live tests)
Live tests require:
- **price > 1** (currency can vary; we only check numeric value)
- **departure time and arrival time present**
- **flight number present**

HTML reports include details:
- `outbound_details`, `inbound_details`, `one_way_details`

---

## Debugging

### Logs
Enable logs:
```bash
LOG_LEVEL=DEBUG pytest -s --log-cli-level=DEBUG tests/test_round_trip_live_google.py -vv
```

Optional log limits:
- `RT_LIVE_LOG_LIMIT=6`
- `OW_LIVE_LOG_LIMIT=6`

### HTML dumps (when JS parsing fails)
Auto-saved files:
- `/tmp/fast_flights_listing.html`
- `/tmp/fast_flights_listing_snippet.txt`

The log will mention when these dumps are available.

---

## Live test dates and ranges
Env overrides:
- `RT_LIVE_OUTBOUND_DAYS=60`
- `RT_LIVE_RETURN_GAP_DAYS=7`
- `OW_LIVE_OUTBOUND_DAYS=60`

---

## Known behavior
- Round-trip for `GDN -> LTN` sometimes fails due to a JS structure the decoder does not recognize yet.
- One-way for the same routes is stable.

---

## GitHub Actions
Live tests workflow:
- `.github/workflows/live_flights_tests.yml`

Triggers:
- manual (workflow_dispatch)
- schedule **every 2 days**

In Actions:
- live + decoder tests are executed
- HTML + JUnit reports are uploaded
- `/tmp` dump files are uploaded as artifacts

---

## Most common flow
1) `source scripts/activate_venv.sh`
2) `./scripts/run-tests.sh`
3) Open `reports/pytest_report.html`

