import re

from fast_flights import FlightData, Passengers, get_flights

TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def _extract_times(values: list[str]) -> set[str]:
    times: set[str] = set()
    for value in values:
        if not value:
            continue
        for match in TIME_RE.findall(value):
            times.add(match)
    return times


def test_round_trip_gdn_to_jfk_real_time() -> None:
    result = get_flights(
        flight_data=[
            FlightData(
                date="2026-06-20",
                from_airport="GDN",
                to_airport="JFK",
            ),
            FlightData(
                date="2026-06-27",
                from_airport="JFK",
                to_airport="GDN",
            ),
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
    )

    assert result is not None, "Expected a Result from get_flights()"
    assert result.flights, "Expected at least one flight option"

    departure_times = _extract_times([flight.departure for flight in result.flights])

    expected_outbound_times = {"2:35", "14:35"}
    expected_return_times = {"9:50", "21:50"}

    assert departure_times & expected_outbound_times, (
        "Expected to find a departure around 14:35 in the results. "
        f"Seen times: {sorted(departure_times)}"
    )
    assert departure_times & expected_return_times, (
        "Expected to find a departure around 21:50 in the results. "
        f"Seen times: {sorted(departure_times)}"
    )
