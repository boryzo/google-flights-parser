from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import re
from typing import List, Optional, Any, Tuple

from fast_flights import FlightData, Passengers, create_filter, get_flights_from_filter
from fast_flights import core


IATA_RE = re.compile(r"^[A-Z]{3}$")


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


def _normalize_airport_input(value: Any) -> tuple[str, Any, Optional[str]]:
    """Return (iata_code, raw_input, extracted_code_if_any)."""
    if value is None:
        raise ValueError("Airport code is required (IATA, e.g. GDN).")
    # Accept Airport enum (or similar) with .value holding IATA code.
    if hasattr(value, "value"):
        enum_value = getattr(value, "value")
        if isinstance(enum_value, str) and re.fullmatch(r"[A-Z]{3}", enum_value.upper()):
            return enum_value.upper(), value, None
    if not isinstance(value, str):
        raise ValueError(f"Airport code must be IATA (3 letters). Got: {value!r}")
    cleaned = value.strip().upper()
    if re.fullmatch(r"[A-Z]{3}", cleaned):
        return cleaned, value, None
    match = re.findall(r"\b[A-Z]{3}\b", cleaned)
    if match:
        extracted = match[-1]
        return extracted, value, extracted
    raise ValueError(f"Airport code must be IATA (3 letters). Got: {value!r}")


def _is_iata(value: object) -> bool:
    return isinstance(value, str) and IATA_RE.fullmatch(value.strip().upper()) is not None


def _split_airport_fields(code_value: object, name_value: object) -> Tuple[Optional[str], Optional[str]]:
    """Return (iata_code, airport_name) while preserving IATA in the main field."""
    code = None
    name = None

    code_str = code_value.strip() if isinstance(code_value, str) else None
    name_str = name_value.strip() if isinstance(name_value, str) else None

    if _is_iata(code_str):
        code = code_str.upper()
        if name_str and not _is_iata(name_str):
            name = name_str
    elif _is_iata(name_str):
        code = name_str.upper()
        if code_str and not _is_iata(code_str):
            name = code_str
    else:
        for candidate in (code_str, name_str):
            if isinstance(candidate, str):
                match = re.search(r"\b([A-Z]{3})\b", candidate.upper())
                if match:
                    code = match.group(1)
                    if candidate != code:
                        name = candidate
                    break
        if code is None:
            if code_str:
                name = code_str
            elif name_str:
                name = name_str

    return code, name


def _flight_numbers(itinerary: core.Itinerary) -> List[str]:
    numbers: List[str] = []
    for fl in getattr(itinerary, "flights", []) or []:
        airline = getattr(fl, "airline", None)
        number = getattr(fl, "flight_number", None)
        if airline and number:
            numbers.append(f"{airline}{number}")
    return numbers


def _format_segments(itinerary: core.Itinerary) -> List[dict] | None:
    segments = []
    for fl in getattr(itinerary, "flights", []) or []:
        origin_code, origin_name = _split_airport_fields(
            getattr(fl, "departure_airport", None),
            getattr(fl, "departure_airport_name", None),
        )
        destination_code, destination_name = _split_airport_fields(
            getattr(fl, "arrival_airport", None),
            getattr(fl, "arrival_airport_name", None),
        )
        if not origin_code or not destination_code:
            continue
        entry = {
            "carrier_code": getattr(fl, "airline", None),
            "flight_number": getattr(fl, "flight_number", None),
            "origin": origin_code,
            "destination": destination_code,
            "date": getattr(fl, "departure_date", None),
            "departure_time": getattr(fl, "departure_time", None),
            "arrival_time": getattr(fl, "arrival_time", None),
        }
        if origin_name:
            entry["origin_airport_name"] = origin_name
        if destination_name:
            entry["destination_airport_name"] = destination_name
        if any(entry.values()):
            segments.append({k: v for k, v in entry.items() if v is not None})
    return segments or None


def _pick_itineraries(decoded: core.DecodedResult, limit: Optional[int] = None) -> List[core.Itinerary]:
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    items = list(best) + list(other)
    return items if limit is None else items[:limit]


def search_flights(
    origin: str,
    destination: str,
    date: datetime,
    *,
    currency: str = "PLN",
    return_date: datetime | None = None,
    max_outbound: Optional[int] = None,
    max_inbound: Optional[int] = None,
    target_time: Optional[str] = None,
) -> List[SimpleNamespace]:
    """Return flights from Google Flights with return details for RT.

    Uses JS decoding to get explicit inbound flight details.
    For round-trip searches, the total RT price is taken from the selected outbound.
    """

    origin_code, origin_raw, origin_extracted = _normalize_airport_input(origin)
    destination_code, destination_raw, destination_extracted = _normalize_airport_input(destination)

    if return_date:
        filter_data = create_filter(
            flight_data=[
                FlightData(
                    date=date.strftime("%Y-%m-%d"),
                    from_airport=origin_code,
                    to_airport=destination_code,
                ),
                FlightData(
                    date=return_date.strftime("%Y-%m-%d"),
                    from_airport=destination_code,
                    to_airport=origin_code,
                ),
            ],
            trip="round-trip",
            seat="economy",
            passengers=Passengers(adults=1),
        )

        result = get_flights_from_filter(
            filter_data,
            currency=currency,
            mode="common",
            data_source="js",
            target_time=target_time,
        )

        if not isinstance(result, core.RoundTripDecodedResult):
            raise RuntimeError("Expected RoundTripDecodedResult (data_source='js').")

        outbound_items = _pick_itineraries(result.outbound, max_outbound)
        inbound_items = _pick_itineraries(result.inbound, max_inbound)

        flights: List[SimpleNamespace] = []
        for outbound in outbound_items:
            # Total RT price comes from outbound itinerary summary
            summary = getattr(outbound, "itinerary_summary", None)
            total_price = getattr(summary, "price", None)
            total_currency = getattr(summary, "currency", None) or currency

            for inbound in inbound_items:
                dep_dt = _dt_from_parts(outbound.departure_date, outbound.departure_time)
                arr_dt = _dt_from_parts(outbound.arrival_date, outbound.arrival_time)
                ret_dep_dt = _dt_from_parts(inbound.departure_date, inbound.departure_time)
                ret_arr_dt = _dt_from_parts(inbound.arrival_date, inbound.arrival_time)

                # Skip incomplete records (prevents None in downstream sorting/comparisons).
                if dep_dt is None or ret_dep_dt is None:
                    continue

                flights.append(
                    SimpleNamespace(
                        originFull=origin_code,
                        destinationFull=destination_code,
                        originInput=origin_raw,
                        destinationInput=destination_raw,
                        originIataExtracted=origin_extracted,
                        destinationIataExtracted=destination_extracted,
                        departureTime=dep_dt,
                        arrivalTime=arr_dt,
                        returnDepartureTime=ret_dep_dt,
                        returnArrivalTime=ret_arr_dt,
                        price=total_price,
                        currency=total_currency,
                        airline=(
                            (outbound.airline_names[0] if outbound.airline_names else None)
                            or outbound.airline_code
                        ),
                        stops=max(0, len(outbound.flights) - 1),
                        flightNumber=", ".join(_flight_numbers(outbound)) or None,
                        returnFlightNumber=", ".join(_flight_numbers(inbound)) or None,
                        segments=_format_segments(outbound),
                        returnSegments=_format_segments(inbound),
                    )
                )
        return flights

    # One-way
    filter_data = create_filter(
        flight_data=[
            FlightData(
                date=date.strftime("%Y-%m-%d"),
                from_airport=origin_code,
                to_airport=destination_code,
            )
        ],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
    )

    result = get_flights_from_filter(
        filter_data,
        currency=currency,
        mode="common",
        data_source="js",
        target_time=target_time,
    )

    if not isinstance(result, core.DecodedResult):
        raise RuntimeError("Expected DecodedResult (data_source='js').")

    flights: List[SimpleNamespace] = []
    for itinerary in _pick_itineraries(result, max_outbound):
        summary = getattr(itinerary, "itinerary_summary", None)
        price = getattr(summary, "price", None)
        cur = getattr(summary, "currency", None) or currency
        dep_dt = _dt_from_parts(itinerary.departure_date, itinerary.departure_time)
        arr_dt = _dt_from_parts(itinerary.arrival_date, itinerary.arrival_time)

        if dep_dt is None:
            continue

        flights.append(
            SimpleNamespace(
                originFull=origin_code,
                destinationFull=destination_code,
                originInput=origin_raw,
                destinationInput=destination_raw,
                originIataExtracted=origin_extracted,
                destinationIataExtracted=destination_extracted,
                departureTime=dep_dt,
                arrivalTime=arr_dt,
                returnDepartureTime=None,
                returnArrivalTime=None,
                price=price,
                currency=cur,
                airline=(
                    (itinerary.airline_names[0] if itinerary.airline_names else None)
                    or itinerary.airline_code
                ),
                stops=max(0, len(itinerary.flights) - 1),
                flightNumber=", ".join(_flight_numbers(itinerary)) or None,
                returnFlightNumber=None,
                segments=_format_segments(itinerary),
                returnSegments=None,
            )
        )
    return flights


 
