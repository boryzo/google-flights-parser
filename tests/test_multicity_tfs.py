from fast_flights import FlightData, Passengers, create_filter


def test_multicity_tfs_matches_google_link():
    # Matches the /travel/flights tfs for:
    # 2026-08-22 GDN -> ICN
    # 2026-09-07 NRT -> GDN
    expected_tfs = (
        "GhoSCjIwMjYtMDgtMjJqBRIDR0ROcgUSA0lDThoaEgoyMDI2LTA5LTA3"
        "agUSA05SVHIFEgNHRE5CAQFIAZgBAw=="
    )

    filt = create_filter(
        flight_data=[
            FlightData(date="2026-08-22", from_airport="GDN", to_airport="ICN"),
            FlightData(date="2026-09-07", from_airport="NRT", to_airport="GDN"),
        ],
        trip="multi-city",
        passengers=Passengers(adults=1, children=0, infants_in_seat=0, infants_on_lap=0),
        seat="economy",
    )

    assert filt.as_b64().decode("utf-8") == expected_tfs
