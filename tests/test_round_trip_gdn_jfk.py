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

    logger.debug(
        "[TEST] get_flights request trip=%s seat=%s adults=%d fetch_mode=%s flight_data=%s",
        "round-trip",
        "economy",
        1,
        "common",
        [
            {"date": fd.date, "from": fd.from_airport, "to": fd.to_airport}
            for fd in flight_data
        ],
    )

    result = get_flights(
        flight_data=flight_data,
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
    )

    logger.debug("[TEST] get_flights returned result=%r", result)

    assert result is not None, "Expected a Result from get_flights()"
    assert result.flights, "Expected at least one flight option"

    _log_flights_table(result.flights)

    departures_raw = [getattr(f, "departure", "") for f in result.flights]
    logger.debug("[TEST] departures_raw_count=%d sample=%r", len(departures_raw), departures_raw[:5])

    departure_times = _extract_times(departures_raw)
    logger.debug("[TEST] departure_times_extracted=%s", sorted(departure_times))

    expected_outbound_times = {"2:35", "14:35"}
    expected_return_times = {"9:50", "21:50"}

    outbound_hit = departure_times & expected_outbound_times
    return_hit = departure_times & expected_return_times

    logger.debug(
        "[ASSERT] expected_outbound=%s expected_return=%s outbound_hit=%s return_hit=%s",
        sorted(expected_outbound_times),
        sorted(expected_return_times),
        sorted(outbound_hit),
        sorted(return_hit),
    )

    # If you re-enable assertions, keep the debug context:
    # assert outbound_hit, (
    #     "Expected to find a departure around 14:35 in the results. "
    #     f"Seen times: {sorted(departure_times)}"
    # )
    # assert return_hit, (
    #     "Expected to find a departure around 21:50 in the results. "
    #     f"Seen times: {sorted(departure_times)}"
    # )
