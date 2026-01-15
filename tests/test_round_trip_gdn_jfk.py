import logging
import os
import re
import shutil
from typing import Iterable

from fast_flights import FlightData, Passengers, get_flights

TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")

logger = logging.getLogger(__name__)


def _configure_test_logging() -> None:
    """
    Ensure logs show up during local runs and in CI when desired.
    Pytest will usually capture logs; run with:
      pytest -s --log-cli-level=DEBUG
    or set env:
      LOG_LEVEL=DEBUG
    """
    level_name = os.getenv("LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)

    # Only configure root once (avoid duplicate handlers)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        root.setLevel(level)


def _extract_times(values: Iterable[str]) -> set[str]:
    times: set[str] = set()
    for idx, value in enumerate(values):
        if not value:
            logger.debug("[TIME] idx=%d empty value -> skip", idx)
            continue

        matches = TIME_RE.findall(value)
        logger.debug("[TIME] idx=%d raw=%r matches=%s", idx, value, matches)

        for t in matches:
            times.add(t)

    logger.debug("[TIME] unique_times=%s", sorted(times))
    return times


def _log_flights_table(flights: list, max_rows: int = 50) -> None:
    """
    Log a compact table-like view for each flight.
    Assumes fast_flights objects have common attributes like:
      name, departure, arrival, duration, stops, price, is_best, etc.
    Uses getattr defensively.
    """
    logger.debug("[RESULT] flights_count=%d", len(flights))

    for i, f in enumerate(flights[:max_rows]):
        logger.debug(
            "[FLIGHT] i=%d is_best=%r airline=%r dep=%r arr=%r duration=%r stops=%r price=%r delay=%r",
            i,
            getattr(f, "is_best", None),
            getattr(f, "name", None),
            getattr(f, "departure", None),
            getattr(f, "arrival", None),
            getattr(f, "duration", None),
            getattr(f, "stops", None),
            getattr(f, "price", None),
            getattr(f, "delay", None),
        )

    if len(flights) > max_rows:
        logger.debug("[RESULT] truncated_flights_logged=%d (of %d)", max_rows, len(flights))


def _dump_rt_failure_artifacts(err: Exception) -> None:
    """Persist listing HTML/snippet to /tmp and copy into CI workspace if available."""
    paths = ["/tmp/fast_flights_listing.html", "/tmp/fast_flights_listing_snippet.txt"]
    missing_path = "/tmp/fast_flights_listing_missing.txt"
    if not any(os.path.exists(path) for path in paths):
        try:
            with open(missing_path, "w", encoding="utf-8") as f:
                f.write(f"Listing artifacts missing. Error: {err}\n")
        except Exception:
            pass

    if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        workspace = os.getenv("GITHUB_WORKSPACE", "/tmp")
        dest_dir = os.path.join(workspace, "fast_flights_artifacts")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            for path in paths + [missing_path]:
                if os.path.exists(path):
                    shutil.copy(path, os.path.join(dest_dir, os.path.basename(path)))
            logger.warning("Copied RT debug artifacts to %s", dest_dir)
        except Exception as copy_err:
            logger.warning("Failed to copy RT debug artifacts: %s", copy_err)


def test_round_trip_gdn_to_jfk_real_time() -> None:
    _configure_test_logging()

    flight_data = [
        FlightData(date="2026-06-20", from_airport="GDN", to_airport="JFK"),
        FlightData(date="2026-06-27", from_airport="JFK", to_airport="GDN"),
    ]

    try:
        result = get_flights(
            flight_data=flight_data,
            trip="round-trip",
            seat="economy",
            passengers=Passengers(adults=1),
            fetch_mode="common",
            data_source="js",
            target_time="14:35",
        )
    except RuntimeError as err:
        _dump_rt_failure_artifacts(err)
        raise

    in_best = getattr(result.inbound, "best", []) or []
    in_other = getattr(result.inbound, "other", []) or []
    assert in_best or in_other, "No inbound options decoded"

    sample = in_best[0] if in_best else in_other[0]
    assert getattr(sample, "departure_airport", None) == "JFK"
