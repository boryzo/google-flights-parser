from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any

from fast_flights import FlightData, Passengers, get_flights
from fast_flights import core


def _dt_from_parts(date_part, time_part) -> Optional[datetime]:
    if not date_part or not time_part:
        return None
    try:
        return datetime(
            int(date_part[0]),
            int(date_part[1]),
            int(date_part[2]),
            int(time_part[0]),
            int(time_part[1]),
        )
    except Exception:
        return None


def _pick_first(decoded) -> Optional[core.Itinerary]:
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    if best:
        return best[0]
    if other:
        return other[0]
    return None


def _flight_numbers(itinerary: core.Itinerary) -> List[str]:
    numbers: List[str] = []
    for fl in getattr(itinerary, "flights", []) or []:
        airline = getattr(fl, "airline", None)
        number = getattr(fl, "flight_number", None)
        if airline and number:
            numbers.append(f"{airline}{number}")
    return numbers


def search_round_trip_js(
    origin: str,
    destination: str,
    depart_date: datetime,
    return_date: datetime,
    *,
    currency: str = "PLN",
) -> Dict[str, Any]:
    """Round-trip search using JS decoding (has return details).

    Returns a single RT option (best/first) with outbound + inbound details
    and total price.
    """

    result = get_flights(
        flight_data=[
            FlightData(
                date=depart_date.strftime("%Y-%m-%d"),
                from_airport=origin.upper(),
                to_airport=destination.upper(),
            ),
            FlightData(
                date=return_date.strftime("%Y-%m-%d"),
                from_airport=destination.upper(),
                to_airport=origin.upper(),
            ),
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
        data_source="js",
        target_time="12:00",
    )

    if not isinstance(result, core.RoundTripDecodedResult):
        raise RuntimeError("Expected RoundTripDecodedResult (data_source='js').")

    outbound = _pick_first(result.outbound)
    inbound = _pick_first(result.inbound)
    if not outbound or not inbound:
        raise RuntimeError("Missing outbound or inbound details.")

    # Total price for RT is stored in the outbound itinerary summary.
    summary = getattr(outbound, "itinerary_summary", None)
    total_price = getattr(summary, "price", None)
    total_currency = getattr(summary, "currency", None) or currency

    return {
        "total_price": total_price,
        "currency": total_currency,
        "outbound": {
            "origin": outbound.departure_airport,
            "destination": outbound.arrival_airport,
            "departure": _dt_from_parts(outbound.departure_date, outbound.departure_time),
            "arrival": _dt_from_parts(outbound.arrival_date, outbound.arrival_time),
            "flight_numbers": _flight_numbers(outbound),
        },
        "inbound": {
            "origin": inbound.departure_airport,
            "destination": inbound.arrival_airport,
            "departure": _dt_from_parts(inbound.departure_date, inbound.departure_time),
            "arrival": _dt_from_parts(inbound.arrival_date, inbound.arrival_time),
            "flight_numbers": _flight_numbers(inbound),
        },
    }


if __name__ == "__main__":
    from pprint import pprint

    payload = search_round_trip_js(
        origin="GDN",
        destination="WAW",
        depart_date=datetime(2026, 3, 24),
        return_date=datetime(2026, 3, 31),
        currency="USD",
    )
    pprint(payload)
