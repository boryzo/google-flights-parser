from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import pytest

from fast_flights import FlightData, Passengers, get_flights

logger = logging.getLogger(__name__)

ONE_WAY_ROUTES = [
    ("GDN", "LTN"),
    ("GDN", "WAW"),
    ("FRA", "MUC"),
    ("LHR", "JFK"),
    ("GDN", "SIN"),
    ("KRK", "JFK"),
    ("WAW", "BKK"),
    ("KTW", "ICN"),
    ("WRO", "KEF"),
]
EXPECTED_AIRLINES = {
    ("GDN", "LTN"): "W6",
    ("GDN", "WAW"): "LO",
    ("GDN", "MAD"): "W6",
}

FIXED_ONE_WAY_CASES = [
    ("GDN", "MAD", "2026-06-03"),
]

FIXED_DIRECT_ONE_WAY_CASES = [
    ("GDN", "NAP", "2026-02-27"),
]

OUTBOUND_DAYS = int(os.getenv("OW_LIVE_OUTBOUND_DAYS", "60"))
LOG_LIMIT = int(os.getenv("OW_LIVE_LOG_LIMIT", "6"))
OUTBOUND_OFFSETS = [
    OUTBOUND_DAYS,
    OUTBOUND_DAYS + 1,
    OUTBOUND_DAYS + 2,
    OUTBOUND_DAYS + 7,
    OUTBOUND_DAYS + 14,
]


def _configure_test_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        root.setLevel(level)


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


def _assert_has_complete_details(decoded, dep_airport: str, arr_airport: str, label: str):
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    all_itineraries = list(best) + list(other)
    assert all_itineraries, f"No {label} options decoded"

    for itinerary in all_itineraries:
        if _has_complete_flight_details(itinerary, dep_airport, arr_airport):
            return itinerary

    raise AssertionError(f"No {label} itinerary with full details for {dep_airport}->{arr_airport}")


def _all_itineraries(decoded) -> list:
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    return list(best) + list(other)


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
        logger.debug("[OW][%s] no decoded result", label)
        return
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    all_itineraries = list(best) + list(other)
    logger.debug("[OW][%s] itineraries=%d (best=%d, other=%d)", label, len(all_itineraries), len(best), len(other))
    for idx, itinerary in enumerate(all_itineraries[:LOG_LIMIT]):
        price = getattr(getattr(itinerary, "itinerary_summary", None), "price", None)
        logger.debug(
            "[OW][%s] i=%d dep=%r arr=%r dep_time=%r arr_time=%r price=%r flights=%d",
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
                "[OW][%s] i=%d f=%d airline=%r flight_number=%r dep=%r arr=%r dep_time=%r arr_time=%r",
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


@pytest.mark.parametrize("origin,destination", ONE_WAY_ROUTES)
def test_one_way_live_google_flights(origin: str, destination: str, record_property) -> None:
    _configure_test_logging()

    last_error: Exception | None = None
    for outbound_days in OUTBOUND_OFFSETS:
        depart_date = _future_date(outbound_days)

        flight_data = [
            FlightData(date=depart_date, from_airport=origin, to_airport=destination),
        ]

        try:
            result = get_flights(
                flight_data=flight_data,
                trip="one-way",
                seat="economy",
                passengers=Passengers(adults=1),
                fetch_mode="common",
                data_source="js",
                target_time="12:00",
            )
        except Exception as err:
            last_error = err
            logger.warning(
                "OW live test: %s->%s failed for %s: %s",
                origin,
                destination,
                depart_date,
                err,
            )
            logger.debug("OW live test: check /tmp/fast_flights_listing.html for raw listing dump")
            continue

        try:
            assert result is not None
            itinerary = _assert_has_complete_details(result, origin, destination, "one-way")
            expected_airline = _expected_airline(origin, destination)
            if expected_airline:
                _assert_itinerary_has_airline(itinerary, expected_airline, "one-way")
            record_property("one_way_details", _format_itinerary_details(itinerary, "one-way"))
            logger.info(_format_itinerary_details(itinerary, "one-way"))
        except AssertionError as err:
            last_error = err
            logger.warning(
                "OW live test: %s->%s missing details for %s: %s",
                origin,
                destination,
                depart_date,
                err,
            )
            _log_itineraries(result, "one-way")
            continue

        logger.info("OW live test: %s->%s OK for %s", origin, destination, depart_date)
        return

    if last_error:
        raise last_error

    raise AssertionError(f"OW live test: {origin}->{destination} had no successful attempts")


@pytest.mark.parametrize("origin,destination,depart_date", FIXED_ONE_WAY_CASES)
def test_one_way_live_google_fixed_date(origin: str, destination: str, depart_date: str, record_property) -> None:
    _configure_test_logging()

    flight_data = [
        FlightData(date=depart_date, from_airport=origin, to_airport=destination),
    ]

    result = get_flights(
        flight_data=flight_data,
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
        data_source="js",
        target_time="12:00",
    )

    assert result is not None
    itinerary = _assert_has_complete_details(result, origin, destination, "one-way")
    expected_airline = _expected_airline(origin, destination)
    if expected_airline:
        _assert_itinerary_has_airline(itinerary, expected_airline, "one-way")
    record_property("one_way_details", _format_itinerary_details(itinerary, "one-way"))
    logger.info(_format_itinerary_details(itinerary, "one-way"))


@pytest.mark.parametrize("origin,destination,depart_date", FIXED_DIRECT_ONE_WAY_CASES)
def test_one_way_live_google_fixed_direct_filter(origin: str, destination: str, depart_date: str, record_property) -> None:
    """
    Verify that `max_stops=0` behaves like a "direct flights only" flag.

    This test doesn't require that a direct flight exists on the date. It asserts that:
      - the baseline (no max_stops) returns some itineraries (otherwise we skip), and
      - the max_stops=0 result contains only 1-segment itineraries (or none).
    """
    _configure_test_logging()

    base = get_flights(
        flight_data=[FlightData(date=depart_date, from_airport=origin, to_airport=destination)],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
        data_source="js",
    )

    assert base is not None
    base_its = _all_itineraries(base)
    if not base_its:
        pytest.skip(f"Baseline one-way returned no itineraries for {origin}->{destination} on {depart_date}")

    direct = get_flights(
        flight_data=[FlightData(date=depart_date, from_airport=origin, to_airport=destination)],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
        data_source="js",
        max_stops=0,
    )

    assert direct is not None
    direct_its = _all_itineraries(direct)

    record_property("direct_filter_baseline_count", str(len(base_its)))
    record_property("direct_filter_direct_count", str(len(direct_its)))

    for i, it in enumerate(direct_its[:LOG_LIMIT]):
        record_property(f"direct_filter_itinerary_{i}", _format_itinerary_details(it, "direct"))

    for it in direct_its:
        assert getattr(it, "departure_airport", None) == origin
        assert getattr(it, "arrival_airport", None) == destination
        flights = getattr(it, "flights", None) or []
        assert len(flights) == 1, f"Expected direct itinerary (1 flight), got {len(flights)}"
        f = flights[0]
        assert getattr(f, "airline", None)
        assert getattr(f, "flight_number", None)
        assert getattr(f, "departure_time", None)
        assert getattr(f, "arrival_time", None)
