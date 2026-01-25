import logging
import os
from datetime import date, timedelta

from fast_flights import FlightData, Passengers, create_filter, get_flights
from fast_flights import core

logger = logging.getLogger(__name__)

OUTBOUND_DAYS = int(os.getenv("MC_LIVE_OUTBOUND_DAYS", "90"))
RETURN_GAP_DAYS = int(os.getenv("MC_LIVE_RETURN_GAP_DAYS", "16"))


def _future_date(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _pick_itinerary_with_details(decoded, dep_airport: str, arr_airport: str):
    if not decoded:
        return None
    best = getattr(decoded, "best", []) or []
    other = getattr(decoded, "other", []) or []
    for itinerary in list(best) + list(other):
        if getattr(itinerary, "departure_airport", None) != dep_airport:
            continue
        if getattr(itinerary, "arrival_airport", None) != arr_airport:
            continue
        flights = getattr(itinerary, "flights", None) or []
        if not flights:
            continue
        price = getattr(getattr(itinerary, "itinerary_summary", None), "price", None)
        if price is None:
            continue
        return itinerary
    return None


def test_multicity_live_google_search_decodes(record_property) -> None:
    depart_date = _future_date(OUTBOUND_DAYS)
    return_date = _future_date(OUTBOUND_DAYS + RETURN_GAP_DAYS)

    filt = create_filter(
        flight_data=[
            FlightData(date=depart_date, from_airport="GDN", to_airport="ICN"),
            FlightData(date=return_date, from_airport="NRT", to_airport="GDN"),
        ],
        trip="multi-city",
        passengers=Passengers(adults=1),
        seat="economy",
    )

    params = {
        "tfs": filt.as_b64().decode("utf-8"),
        "hl": "en",
        "curr": "",
    }

    logger.debug("[MC][live] params=%s", params)
    record_property("multicity_depart_date", depart_date)
    record_property("multicity_return_date", return_date)
    record_property("multicity_tfs", params["tfs"])
    print(f"[MC][live] depart_date={depart_date} return_date={return_date}")
    print(f"[MC][live] tfs={params['tfs']}")

    req_kwargs = core._merge_binary_cookies(core._DEFAULT_COOKIES_BYTES, None)
    res = core.fetch(params, request_kwargs=req_kwargs)
    candidates = core._extract_js_data_candidates(res.text)
    record_property("multicity_candidate_count", len(candidates))
    record_property("multicity_html_len", len(res.text))
    print(f"[MC][live] candidate_count={len(candidates)} html_len={len(res.text)}")
    assert candidates, "No JS data candidates found for multicity search response"

    seg1 = get_flights(
        flight_data=[FlightData(date=depart_date, from_airport="GDN", to_airport="ICN")],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
        data_source="js",
        target_time="12:00",
    )
    seg1_itinerary = _pick_itinerary_with_details(seg1, "GDN", "ICN")
    assert seg1_itinerary, "No one-way itinerary with details for GDN->ICN"
    seg1_price = getattr(getattr(seg1_itinerary, "itinerary_summary", None), "price", None)
    seg1_currency = getattr(getattr(seg1_itinerary, "itinerary_summary", None), "currency", None)
    seg1_dep_date = getattr(seg1_itinerary, "departure_date", None)
    seg1_arr_date = getattr(seg1_itinerary, "arrival_date", None)
    seg1_dep_time = getattr(seg1_itinerary, "departure_time", None)
    seg1_arr_time = getattr(seg1_itinerary, "arrival_time", None)
    seg1_flights = [
        f"{getattr(f, 'airline', '')}{getattr(f, 'flight_number', '')}" for f in (seg1_itinerary.flights or [])
    ]
    record_property("multicity_seg1_price", seg1_price)
    record_property("multicity_seg1_currency", seg1_currency)
    record_property("multicity_seg1_flight_numbers", ",".join(seg1_flights))
    record_property("multicity_seg1_departure_date", seg1_dep_date)
    record_property("multicity_seg1_arrival_date", seg1_arr_date)
    record_property("multicity_seg1_departure_time", seg1_dep_time)
    record_property("multicity_seg1_arrival_time", seg1_arr_time)
    print(
        f"[MC][live] seg1 price={seg1_price} {seg1_currency} "
        f"dep_date={seg1_dep_date} dep_time={seg1_dep_time} "
        f"arr_date={seg1_arr_date} arr_time={seg1_arr_time} "
        f"flights={seg1_flights}"
    )

    seg2 = get_flights(
        flight_data=[FlightData(date=return_date, from_airport="NRT", to_airport="GDN")],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
        data_source="js",
        target_time="12:00",
    )
    seg2_itinerary = _pick_itinerary_with_details(seg2, "NRT", "GDN")
    assert seg2_itinerary, "No one-way itinerary with details for NRT->GDN"
    seg2_price = getattr(getattr(seg2_itinerary, "itinerary_summary", None), "price", None)
    seg2_currency = getattr(getattr(seg2_itinerary, "itinerary_summary", None), "currency", None)
    seg2_dep_date = getattr(seg2_itinerary, "departure_date", None)
    seg2_arr_date = getattr(seg2_itinerary, "arrival_date", None)
    seg2_dep_time = getattr(seg2_itinerary, "departure_time", None)
    seg2_arr_time = getattr(seg2_itinerary, "arrival_time", None)
    seg2_flights = [
        f"{getattr(f, 'airline', '')}{getattr(f, 'flight_number', '')}" for f in (seg2_itinerary.flights or [])
    ]
    record_property("multicity_seg2_price", seg2_price)
    record_property("multicity_seg2_currency", seg2_currency)
    record_property("multicity_seg2_flight_numbers", ",".join(seg2_flights))
    record_property("multicity_seg2_departure_date", seg2_dep_date)
    record_property("multicity_seg2_arrival_date", seg2_arr_date)
    record_property("multicity_seg2_departure_time", seg2_dep_time)
    record_property("multicity_seg2_arrival_time", seg2_arr_time)
    print(
        f"[MC][live] seg2 price={seg2_price} {seg2_currency} "
        f"dep_date={seg2_dep_date} dep_time={seg2_dep_time} "
        f"arr_date={seg2_arr_date} arr_time={seg2_arr_time} "
        f"flights={seg2_flights}"
    )

    if seg1_currency and seg2_currency and seg1_currency == seg2_currency:
        total_price = float(seg1_price) + float(seg2_price)
        record_property("multicity_total_price", total_price)
        record_property("multicity_total_currency", seg1_currency)
        print(f"[MC][live] total price={total_price} {seg1_currency}")
