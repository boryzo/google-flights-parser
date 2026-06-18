from __future__ import annotations

import logging
import os
import calendar
from datetime import date, timedelta

import pytest

from fast_flights import FlightData, Passengers, get_flights, core

logger = logging.getLogger(__name__)

ROUTE_CASES = [
    ("GDN", "LTN", 2),
    ("KRK", "STN", 3),
    ("WAW", "GDN", 6),
    ("WAW", "JFK", 2),
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


def _get_base_days_offset(months: int) -> int:
    today = date.today()
    month = today.month - 1 + months
    year = today.year + month // 12
    month = month % 12 + 1
    _, last_day = calendar.monthrange(year, month)
    day = min(today.day, last_day)
    target_date = date(year, month, day)
    return (target_date - today).days


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


def _assert_leg_has_complete_details(decoded, dep_airport: str, arr_airport: str, label: str):
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    all_itineraries = list(best) + list(other)
    assert all_itineraries, f"No {label} options decoded"

    for itinerary in all_itineraries:
        if _has_complete_flight_details(itinerary, dep_airport, arr_airport):
            return itinerary

    raise AssertionError(f"No {label} itinerary with full details for {dep_airport}->{arr_airport}")


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


@pytest.mark.parametrize("origin,destination,months_ahead", ROUTE_CASES)
def test_specific_routes_one_way(origin: str, destination: str, months_ahead: int, record_property) -> None:
    _configure_test_logging()

    base_offset = _get_base_days_offset(months_ahead)
    offsets = [base_offset, base_offset + 1, base_offset + 2, base_offset + 7, base_offset + 14]

    last_error: Exception | None = None
    for offset in offsets:
        depart_date = _future_date(offset)
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
                data_source="auto",
                target_time="12:00",
            )
        except Exception as err:
            last_error = err
            logger.warning(
                "Specific route OW test: %s->%s failed for %s: %s",
                origin,
                destination,
                depart_date,
                err,
            )
            continue

        try:
            assert result is not None
            itinerary = _assert_has_complete_details(result, origin, destination, "one-way")
            record_property("one_way_details", _format_itinerary_details(itinerary, "one-way"))
            logger.info(_format_itinerary_details(itinerary, "one-way"))
        except AssertionError as err:
            last_error = err
            logger.warning(
                "Specific route OW test: %s->%s missing details for %s: %s",
                origin,
                destination,
                depart_date,
                err,
            )
            continue

        logger.info("Specific route OW test: %s->%s OK for %s", origin, destination, depart_date)
        return

    if last_error:
        raise last_error

    raise AssertionError(f"Specific route OW test: {origin}->{destination} had no successful attempts")


@pytest.mark.parametrize("origin,destination,months_ahead", ROUTE_CASES)
def test_specific_routes_round_trip(origin: str, destination: str, months_ahead: int, record_property) -> None:
    _configure_test_logging()

    base_offset = _get_base_days_offset(months_ahead)
    offsets = [base_offset, base_offset + 1, base_offset + 2, base_offset + 7, base_offset + 14]
    return_gap = 7

    last_error: Exception | None = None
    for offset in offsets:
        depart_date = _future_date(offset)
        return_date = _future_date(offset + return_gap)

        flight_data = [
            FlightData(date=depart_date, from_airport=origin, to_airport=destination),
            FlightData(date=return_date, from_airport=destination, to_airport=origin),
        ]

        try:
            result = get_flights(
                flight_data=flight_data,
                trip="round-trip",
                seat="economy",
                passengers=Passengers(adults=1),
                fetch_mode="common",
                data_source="auto",
                target_time="12:00",
            )
        except Exception as err:
            last_error = err
            logger.warning(
                "Specific route RT test: %s->%s failed for %s/%s: %s",
                origin,
                destination,
                depart_date,
                return_date,
                err,
            )
            continue

        try:
            assert isinstance(result, core.RoundTripDecodedResult)
            selected_outbound = getattr(result, "selected_outbound", None)
            if selected_outbound and _has_complete_flight_details(selected_outbound, origin, destination):
                outbound = selected_outbound
            else:
                outbound = _assert_leg_has_complete_details(result.outbound, origin, destination, "outbound")
            inbound = _assert_leg_has_complete_details(result.inbound, destination, origin, "inbound")

            record_property("outbound_details", _format_itinerary_details(outbound, "outbound"))
            record_property("inbound_details", _format_itinerary_details(inbound, "inbound"))
            logger.info(_format_itinerary_details(outbound, "outbound"))
            logger.info(_format_itinerary_details(inbound, "inbound"))
        except AssertionError as err:
            last_error = err
            logger.warning(
                "Specific route RT test: %s->%s missing details for %s/%s: %s",
                origin,
                destination,
                depart_date,
                return_date,
                err,
            )
            continue

        logger.info("Specific route RT test: %s->%s OK for %s/%s", origin, destination, depart_date, return_date)
        return

    if last_error:
        raise last_error

    raise AssertionError(f"Specific route RT test: {origin}->{destination} had no successful attempts")
