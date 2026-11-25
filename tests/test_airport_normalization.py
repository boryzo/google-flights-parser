from fast_flights.decoder import _extract_iata, _normalize_airport_fields


def test_normalize_swapped_malaga_does_not_extract_del():
    code, name = _normalize_airport_fields("Málaga-Costa del Sol Airport", "AGP")
    assert code == "AGP"
    assert name == "Málaga-Costa del Sol Airport"


def test_normalize_swapped_auh():
    code, name = _normalize_airport_fields("Zayed International Airport", "AUH")
    assert code == "AUH"
    assert name == "Zayed International Airport"


def test_normalize_regular_fields():
    code, name = _normalize_airport_fields("gdn", "Gdansk Lech Walesa Airport")
    assert code == "GDN"
    assert name == "Gdansk Lech Walesa Airport"


def test_extract_iata_ignores_prose_three_letter_words():
    assert _extract_iata("Málaga-Costa del Sol Airport") is None
    assert _extract_iata("Los Angeles International Airport") is None
    assert _extract_iata("Malaga (AGP)") == "AGP"
