from fast_flights.decoder import FlightDecoder, _exact_iata, _extract_iata, _normalize_airport_fields


def test_normalize_swapped_malaga_does_not_extract_del():
    code, name = _normalize_airport_fields("Málaga-Costa del Sol Airport", "AGP")
    assert code == "AGP"
    assert name == "Málaga-Costa del Sol Airport"


def test_normalize_swapped_other_three_letter_prose_names():
    assert _normalize_airport_fields("Los Angeles International Airport", "LAX") == (
        "LAX",
        "Los Angeles International Airport",
    )
    assert _normalize_airport_fields("Newark Liberty International Airport", "EWR") == (
        "EWR",
        "Newark Liberty International Airport",
    )
    assert _normalize_airport_fields("San Diego International Airport", "SAN") == (
        "SAN",
        "San Diego International Airport",
    )


def test_normalize_swapped_auh():
    code, name = _normalize_airport_fields("Zayed International Airport", "AUH")
    assert code == "AUH"
    assert name == "Zayed International Airport"


def test_normalize_regular_and_edge_fields():
    assert _normalize_airport_fields("gdn", "Gdansk Lech Walesa Airport") == (
        "GDN",
        "Gdansk Lech Walesa Airport",
    )
    assert _normalize_airport_fields("Costa del Sol (AGP)", None) == (
        "AGP",
        "Costa del Sol (AGP)",
    )
    assert _normalize_airport_fields(None, None) == ("", "")
    assert _normalize_airport_fields(123, "AGP") == ("AGP", "AGP")


def test_extract_iata_ignores_prose_three_letter_words():
    assert _exact_iata("agp") == "AGP"
    assert _exact_iata("Malaga (AGP)") is None
    assert _extract_iata("Málaga-Costa del Sol Airport") is None
    assert _extract_iata("Los Angeles International Airport") is None
    assert _extract_iata("Costa del Sol (AGP)") == "AGP"


def test_flight_decoder_normalizes_swapped_arrival_airport():
    raw_flight = [None] * 23
    raw_flight[2] = "Wizz Air"
    raw_flight[3] = "GDN"
    raw_flight[4] = "Gdansk Lech Walesa Airport"
    raw_flight[5] = "Málaga-Costa del Sol Airport"
    raw_flight[6] = "AGP"
    raw_flight[8] = [16, 0]
    raw_flight[10] = [20, 10]
    raw_flight[11] = 250
    raw_flight[14] = "30 in"
    raw_flight[15] = []
    raw_flight[17] = "Airbus A321"
    raw_flight[20] = [2027, 2, 26]
    raw_flight[21] = [2027, 2, 26]
    raw_flight[22] = ["W6", "1613", None, "Wizz Air"]

    flights = FlightDecoder.decode([raw_flight])
    assert len(flights) == 1
    assert flights[0].departure_airport == "GDN"
    assert flights[0].departure_airport_name == "Gdansk Lech Walesa Airport"
    assert flights[0].arrival_airport == "AGP"
    assert flights[0].arrival_airport_name == "Málaga-Costa del Sol Airport"
