from __future__ import annotations

import logging
import os
import re
import shutil
import json
from datetime import date, timedelta
from typing import Iterable

import pytest

from fast_flights import FlightData, Passengers, create_filter, get_flights
from fast_flights import core

TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
IATA_RE = re.compile(r"^[A-Z]{3}$")

logger = logging.getLogger(__name__)

LIVE_ROUTES = [
    ("GDN", "LTN"),
    ("GDN", "WAW"),
    ("FRA", "MUC"),
    ("LHR", "JFK"),
    ("POZ", "JFK"),
    ("WRO", "BKK"),
    ("GDN", "BCN"),
    ("WAW", "NRT"),
    ("KRK", "LIS"),
    ("WAW", "CPT"),
    ("GDN", "DXB"),
]
EXPECTED_AIRLINES = {
    ("GDN", "LTN"): "W6",
    ("GDN", "WAW"): "LO",
}

OUTBOUND_DAYS = int(os.getenv("RT_LIVE_OUTBOUND_DAYS", "60"))
RETURN_GAP_DAYS = int(os.getenv("RT_LIVE_RETURN_GAP_DAYS", "7"))
LOG_LIMIT = int(os.getenv("RT_LIVE_LOG_LIMIT", "6"))
OUTBOUND_OFFSETS = [
    OUTBOUND_DAYS,
    OUTBOUND_DAYS + 1,
    OUTBOUND_DAYS + 2,
    OUTBOUND_DAYS + 7,
    OUTBOUND_DAYS + 14,
]


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


def _future_date(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _has_complete_flight_details(itinerary, dep_airport: str, arr_airport: str) -> bool:
    if not itinerary:
        return False
    if getattr(itinerary, "departure_airport", None) != dep_airport:
        return False
    if getattr(itinerary, "arrival_airport", None) != arr_airport:
        return False
    if not getattr(itinerary, "departure_time", None):
        return False
    if not getattr(itinerary, "arrival_time", None):
        return False
    flights = getattr(itinerary, "flights", None) or []
    if not flights:
        return False
    for flight in flights:
        if not getattr(flight, "airline", None):
            return False
        if not getattr(flight, "flight_number", None):
            return False
    price = getattr(getattr(itinerary, "itinerary_summary", None), "price", None)
    if price is None or float(price) <= 1:
        return False
    return True


def _assert_leg_has_complete_details(decoded, dep_airport: str, arr_airport: str, label: str):
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    all_itineraries = list(best) + list(other)
    assert all_itineraries, f"No {label} options decoded"

    for itinerary in all_itineraries:
        if _has_complete_flight_details(itinerary, dep_airport, arr_airport):
            return itinerary

    raise AssertionError(f"No {label} itinerary with full details for {dep_airport}->{arr_airport}")


def _expected_airline(origin: str, destination: str) -> str | None:
    return EXPECTED_AIRLINES.get((origin, destination)) or EXPECTED_AIRLINES.get((destination, origin))


def _assert_itinerary_has_airline(itinerary, expected: str, label: str) -> None:
    flights = getattr(itinerary, "flights", None) or []
    if not flights:
        raise AssertionError(f"{label} has no flights to verify airline")
    if not any(getattr(f, "airline", None) == expected for f in flights):
        found = [getattr(f, "airline", None) for f in flights]
        raise AssertionError(f"{label} expected airline {expected}, found {found}")


def _log_itineraries(decoded, label: str) -> None:
    if not decoded:
        logger.debug("[RT][%s] no decoded result", label)
        return
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    all_itineraries = list(best) + list(other)
    logger.debug("[RT][%s] itineraries=%d (best=%d, other=%d)", label, len(all_itineraries), len(best), len(other))
    for idx, itinerary in enumerate(all_itineraries[:LOG_LIMIT]):
        price = getattr(getattr(itinerary, "itinerary_summary", None), "price", None)
        logger.debug(
            "[RT][%s] i=%d dep=%r arr=%r dep_time=%r arr_time=%r price=%r flights=%d",
            label,
            idx,
            getattr(itinerary, "departure_airport", None),
            getattr(itinerary, "arrival_airport", None),
            getattr(itinerary, "departure_time", None),
            getattr(itinerary, "arrival_time", None),
            price,
            len(getattr(itinerary, "flights", []) or []),
        )
        for fidx, flight in enumerate(getattr(itinerary, "flights", []) or []):
            if fidx >= 4:
                break
            logger.debug(
                "[RT][%s] i=%d f=%d airline=%r flight_number=%r dep=%r arr=%r dep_time=%r arr_time=%r",
                label,
                idx,
                fidx,
                getattr(flight, "airline", None),
                getattr(flight, "flight_number", None),
                getattr(flight, "departure_airport", None),
                getattr(flight, "arrival_airport", None),
                getattr(flight, "departure_time", None),
                getattr(flight, "arrival_time", None),
            )


def _format_itinerary_details(itinerary, label: str) -> str:
    flights = getattr(itinerary, "flights", []) or []
    flight_numbers = [f"{getattr(f, 'airline', '')}{getattr(f, 'flight_number', '')}" for f in flights]
    summary = getattr(itinerary, "itinerary_summary", None)
    price = getattr(summary, "price", None)
    currency = getattr(summary, "currency", None)
    dep_date = getattr(itinerary, "departure_date", None)
    arr_date = getattr(itinerary, "arrival_date", None)
    return (
        f"{label}: {getattr(itinerary, 'departure_airport', None)}"
        f"->{getattr(itinerary, 'arrival_airport', None)}, "
        f"dep_date={dep_date}, "
        f"arr_date={arr_date}, "
        f"dep_time={getattr(itinerary, 'departure_time', None)}, "
        f"arr_time={getattr(itinerary, 'arrival_time', None)}, "
        f"price={price} {currency}, "
        f"flight_numbers={flight_numbers}"
    )


def _get_price_summary(itinerary) -> tuple[float | None, str | None]:
    summary = getattr(itinerary, "itinerary_summary", None)
    price = getattr(summary, "price", None)
    currency = getattr(summary, "currency", None)
    try:
        return (float(price) if price is not None else None, currency)
    except Exception:
        return (None, currency)


def _itinerary_flight_tuples(
    itinerary,
) -> list[tuple[str | None, str | None, str | None, str | None, tuple | None, tuple | None]]:
    flights = getattr(itinerary, "flights", None) or []
    items: list[tuple[str | None, str | None, str | None, str | None, tuple | None, tuple | None]] = []

    def _norm_time(value: object) -> tuple | None:
        if not value:
            return None
        if isinstance(value, (list, tuple)):
            if len(value) == 2:
                return (int(value[0]), int(value[1]))
            if len(value) == 1:
                return (int(value[0]), 0)
        return None

    for f in flights:
        dep_t = getattr(f, "departure_time", None)
        arr_t = getattr(f, "arrival_time", None)
        items.append(
            (
                getattr(f, "airline", None),
                str(getattr(f, "flight_number", None)) if getattr(f, "flight_number", None) is not None else None,
                getattr(f, "departure_airport", None),
                getattr(f, "arrival_airport", None),
                _norm_time(dep_t),
                _norm_time(arr_t),
            )
        )
    return items


def _find_itinerary_with_exact_flights(decoded, expected: list[tuple]) -> object | None:
    if not decoded:
        return None
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    for it in list(best) + list(other):
        if _itinerary_flight_tuples(it) == expected:
            return it
    return None


@pytest.mark.parametrize("origin,destination", LIVE_ROUTES)
def test_round_trip_live_google_flights(origin: str, destination: str, record_property) -> None:
    _configure_test_logging()

    last_error: Exception | None = None
    for outbound_days in OUTBOUND_OFFSETS:
        depart_date = _future_date(outbound_days)
        return_date = _future_date(outbound_days + RETURN_GAP_DAYS)

        flight_data = [
            FlightData(date=depart_date, from_airport=origin, to_airport=destination),
            FlightData(date=return_date, from_airport=destination, to_airport=origin),
        ]

        # Try JS parser first, fall back to HTML parser
        result = None
        for data_source in ["js", "html"]:
            try:
                result = get_flights(
                    flight_data=flight_data,
                    trip="round-trip",
                    seat="economy",
                    passengers=Passengers(adults=1),
                    fetch_mode="common",
                    data_source=data_source,
                    target_time="12:00",
                )
                break  # Success, exit data_source loop
            except Exception as err:
                last_error = err
                logger.warning(
                    "RT live test: %s->%s failed for %s/%s with data_source=%s: %s",
                    origin,
                    destination,
                    depart_date,
                    return_date,
                    data_source,
                    err,
                )
                if data_source == "html":
                    # Both parsers failed, try next date
                    logger.debug("RT live test: check /tmp/fast_flights_listing.html for raw listing dump")
                # Continue to try next data_source if not HTML yet

        # Check if we got a result
        if result is None:
            # Both parsers failed, try next date
            continue

        try:
            assert isinstance(result, core.RoundTripDecodedResult)
            selected_outbound = getattr(result, "selected_outbound", None)
            if selected_outbound and _has_complete_flight_details(selected_outbound, origin, destination):
                outbound = selected_outbound
            else:
                outbound = _assert_leg_has_complete_details(result.outbound, origin, destination, "outbound")
            inbound = _assert_leg_has_complete_details(result.inbound, destination, origin, "inbound")
            rt_debug = getattr(result, "debug", None)
            if rt_debug is not None:
                record_property("rt_debug", json.dumps(rt_debug, default=str))
                if rt_debug.get("tfs_segments") is not None:
                    record_property("rt_tfs_segments", json.dumps(rt_debug["tfs_segments"], default=str))
                    for leg in rt_debug["tfs_segments"]:
                        for seg in leg or []:
                            origin = seg.get("origin")
                            destination = seg.get("destination")
                            assert IATA_RE.fullmatch(origin or ""), f"segment origin not IATA: {origin}"
                            assert IATA_RE.fullmatch(destination or ""), f"segment destination not IATA: {destination}"
            expected_airline = _expected_airline(origin, destination)
            if expected_airline:
                _assert_itinerary_has_airline(outbound, expected_airline, "outbound")
                _assert_itinerary_has_airline(inbound, expected_airline, "inbound")
            outbound_price, outbound_currency = _get_price_summary(outbound)
            inbound_price, inbound_currency = _get_price_summary(inbound)
            assert outbound_price is not None and inbound_price is not None
            assert outbound_currency == inbound_currency
            assert abs(outbound_price - inbound_price) <= 0.01
            record_property("rt_total_price", f"{outbound_price} {outbound_currency}")
            record_property("outbound_details", _format_itinerary_details(outbound, "outbound"))
            record_property("inbound_details", _format_itinerary_details(inbound, "inbound"))
            logger.info(_format_itinerary_details(outbound, "outbound"))
            logger.info(_format_itinerary_details(inbound, "inbound"))
        except AssertionError as err:
            last_error = err
            logger.warning(
                "RT live test: %s->%s missing details for %s/%s: %s",
                origin,
                destination,
                depart_date,
                return_date,
                err,
            )
            _log_itineraries(getattr(result, "outbound", None), "outbound")
            _log_itineraries(getattr(result, "inbound", None), "inbound")
            continue

        logger.info("RT live test: %s->%s OK for %s/%s", origin, destination, depart_date, return_date)
        return

    if last_error:
        _dump_rt_failure_artifacts(last_error)
        raise last_error

    raise AssertionError(f"RT live test: {origin}->{destination} had no successful attempts")


def test_round_trip_live_google_fixed_cph_icn_etihad(record_property) -> None:
    """
    Diagnostic RT test for a known Etihad connection via AUH.

    Case (user-reported):
      CPH -> AUH 11:10-19:25 EY178
      AUH -> ICN 21:10-10:50 (+1) EY822
      ICN -> AUH 17:45-23:00 EY823
      AUH -> CPH 02:15 (+1)-07:00 (+1) EY177
    """
    _configure_test_logging()

    if os.getenv("RUN_FIXED_RT_CPH_ICN", "1") == "0":
        pytest.skip("Set RUN_FIXED_RT_CPH_ICN=1 to run this live diagnostic test.")

    depart_date = "2026-08-22"
    return_date = "2026-09-07"

    flight_data = [
        FlightData(date=depart_date, from_airport="CPH", to_airport="ICN"),
        FlightData(date=return_date, from_airport="ICN", to_airport="CPH"),
    ]

    expected_outbound = [
        ("EY", "178", "CPH", "AUH", (11, 10), (19, 25)),
        ("EY", "822", "AUH", "ICN", (21, 10), (10, 50)),
    ]
    expected_inbound = [
        ("EY", "823", "ICN", "AUH", (17, 45), (23, 0)),
        ("EY", "177", "AUH", "CPH", (2, 15), (7, 0)),
    ]

    last_err: Exception | None = None
    for currency in ("PLN", "DKK"):
        # Try JS parser first, fall back to HTML parser
        result = None
        for data_source in ["js", "html"]:
            try:
                filter_data = create_filter(
                    flight_data=flight_data,
                    trip="round-trip",
                    seat="economy",
                    passengers=Passengers(adults=1),
                )
                result = core.get_flights_from_filter(
                    filter_data,
                    currency=currency,
                    mode="common",
                    data_source=data_source,
                )
                logger.info("RT fixed CPH-ICN (%s): Successfully parsed with data_source=%s", currency, data_source)
                break  # Success
            except Exception as err:
                last_err = err
                logger.warning("RT fixed CPH-ICN (%s) fetch failed with data_source=%s: %s", currency, data_source, err)
                # Continue to try next data_source if not HTML yet

        # Check if we got a result
        if result is None:
            # Both parsers failed for this currency, try next currency
            continue

        assert isinstance(result, core.RoundTripDecodedResult)

        rt_debug = getattr(result, "debug", None)
        if rt_debug is not None:
            record_property(f"rt_debug_{currency}", json.dumps(rt_debug, default=str))

        outbound = _find_itinerary_with_exact_flights(getattr(result, "outbound", None), expected_outbound)
        inbound = _find_itinerary_with_exact_flights(getattr(result, "inbound", None), expected_inbound)

        logger.info("RT fixed CPH-ICN (%s) outbound_exact=%r inbound_exact=%r", currency, bool(outbound), bool(inbound))
        _log_itineraries(getattr(result, "outbound", None), f"outbound_{currency}")
        _log_itineraries(getattr(result, "inbound", None), f"inbound_{currency}")

        if outbound and inbound:
            outbound_price, outbound_currency = _get_price_summary(outbound)
            inbound_price, inbound_currency = _get_price_summary(inbound)
            record_property(f"rt_total_price_{currency}", f"{outbound_price} {outbound_currency}")
            record_property(f"rt_inbound_price_{currency}", f"{inbound_price} {inbound_currency}")
            record_property(f"outbound_details_{currency}", _format_itinerary_details(outbound, "outbound"))
            record_property(f"inbound_details_{currency}", _format_itinerary_details(inbound, "inbound"))
            logger.info(_format_itinerary_details(outbound, f"outbound_{currency}"))
            logger.info(_format_itinerary_details(inbound, f"inbound_{currency}"))
            return

    if last_err:
        _dump_rt_failure_artifacts(last_err)
        raise last_err

    raise AssertionError("RT fixed CPH-ICN: expected Etihad via AUH itinerary not found (see logs).")
