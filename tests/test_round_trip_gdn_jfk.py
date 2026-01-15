import logging
import os
import re
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


def test_round_trip_gdn_to_jfk_real_time() -> None:
    _configure_test_logging()

    flight_data = [
        FlightData(date="2026-06-20", from_airport="GDN", to_airport="JFK"),
        FlightData(date="2026-06-27", from_airport="JFK", to_airport="GDN"),
    ]

    result = get_flights(
        flight_data=flight_data,
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
        data_source="js",              # <-- KLUCZ
        target_time="14:35",           # opcjonalne, ale zgodne z Twoją logiką selekcji
    )

    assert result is not None

    # Dla RT+JS spodziewasz się RoundTripDecodedResult
    assert hasattr(result, "outbound") and hasattr(result, "inbound"), repr(result)

    logger.debug("[RT] selected_outbound_ref prefix=%r", getattr(result, "selected_outbound_ref", "")[:40])
    logger.debug("[RT] selected_outbound=%r", getattr(result, "selected_outbound", None))

    outbound = result.outbound
    inbound = result.inbound

    # To zależy od Twojego decoder'a: często są listy .best/.other (nie .flights)
    logger.debug("[OUTBOUND] best=%d other=%d", len(getattr(outbound, "best", [])), len(getattr(outbound, "other", [])))
    logger.debug("[INBOUND]  best=%d other=%d", len(getattr(inbound, "best", [])), len(getattr(inbound, "other", [])))

    assert (getattr(outbound, "best", []) or getattr(outbound, "other", [])), "No outbound options decoded"
    assert (getattr(inbound, "best", []) or getattr(inbound, "other", [])), "No inbound options decoded (follow-up failed)"
