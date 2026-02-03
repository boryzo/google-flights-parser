from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from fast_flights import FlightData, Passengers, create_filter, get_flights
from fast_flights import core
from fast_flights.flights_impl import ItinerarySummary

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


def _extract_multicity_summary(html_text: str) -> ItinerarySummary | None:
    candidates = core._extract_js_data_candidates(html_text)
    for data in candidates:
        for item in core._iter_js_lists(data, limit=60000):
            if isinstance(item, list):
                for el in item:
                    if isinstance(el, str) and len(el) > 20:
                        try:
                            return ItinerarySummary.from_b64(el)
                        except Exception:
                            continue
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
        data_source="auto",
        target_time="12:00",
    )
    seg1_itinerary = _pick_itinerary_with_details(seg1, "GDN", "ICN")
    assert seg1_itinerary, "No one-way itinerary with details for GDN->ICN"
    seg1_dep_date = getattr(seg1_itinerary, "departure_date", None)
    seg1_arr_date = getattr(seg1_itinerary, "arrival_date", None)
    seg1_dep_time = getattr(seg1_itinerary, "departure_time", None)
    seg1_arr_time = getattr(seg1_itinerary, "arrival_time", None)
    seg1_flights = [
        f"{getattr(f, 'airline', '')}{getattr(f, 'flight_number', '')}" for f in (seg1_itinerary.flights or [])
    ]
    record_property("multicity_seg1_flight_numbers", ",".join(seg1_flights))
    record_property("multicity_seg1_departure_date", seg1_dep_date)
    record_property("multicity_seg1_arrival_date", seg1_arr_date)
    record_property("multicity_seg1_departure_time", seg1_dep_time)
    record_property("multicity_seg1_arrival_time", seg1_arr_time)
    print(
        f"[MC][live] seg1 dep_date={seg1_dep_date} dep_time={seg1_dep_time} "
        f"arr_date={seg1_arr_date} arr_time={seg1_arr_time} "
        f"flights={seg1_flights}"
    )

    seg2 = get_flights(
        flight_data=[FlightData(date=return_date, from_airport="NRT", to_airport="GDN")],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        fetch_mode="common",
        data_source="auto",
        target_time="12:00",
    )
    seg2_itinerary = _pick_itinerary_with_details(seg2, "NRT", "GDN")
    assert seg2_itinerary, "No one-way itinerary with details for NRT->GDN"
    seg2_dep_date = getattr(seg2_itinerary, "departure_date", None)
    seg2_arr_date = getattr(seg2_itinerary, "arrival_date", None)
    seg2_dep_time = getattr(seg2_itinerary, "departure_time", None)
    seg2_arr_time = getattr(seg2_itinerary, "arrival_time", None)
    seg2_flights = [
        f"{getattr(f, 'airline', '')}{getattr(f, 'flight_number', '')}" for f in (seg2_itinerary.flights or [])
    ]
    record_property("multicity_seg2_flight_numbers", ",".join(seg2_flights))
    record_property("multicity_seg2_departure_date", seg2_dep_date)
    record_property("multicity_seg2_arrival_date", seg2_arr_date)
    record_property("multicity_seg2_departure_time", seg2_dep_time)
    record_property("multicity_seg2_arrival_time", seg2_arr_time)
    print(
        f"[MC][live] seg2 dep_date={seg2_dep_date} dep_time={seg2_dep_time} "
        f"arr_date={seg2_arr_date} arr_time={seg2_arr_time} "
        f"flights={seg2_flights}"
    )
    selected_url = os.getenv(
        "MC_LIVE_SELECTED_URL",
        "https://www.google.com/travel/flights/booking?"
        "tfs=CBwQAhpgEgoyMDI2LTA0LTI1IiAKA0dEThIKMjAyNi0wNC0yNRoDV0FXKgJMTzIEMzgzMiIe"
        "CgNXQVcSCjIwMjYtMDQtMjUaA0lDTioCTE8yAjk5agcIARIDR0ROcgcIARIDSUNOGmISCjIw"
        "MjYtMDUtMTEiIAoDTlJUEgoyMDI2LTA1LTExGgNXQVcqAkxPMgQxMDgwIiAKA1dBVxIKMjAy"
        "Ni0wNS0xMRoDR0ROKgJMTzIEMzgxNWoHCAESA05SVHIHCAESA0dETkABSAFwAYIBCwj___________8BmAED"
        "&tfu=CnhDalJJTW1zemQyWlpVbVZyYlZsQlNtRmxUa0ZDUnkwdExTMHRMUzB0TFhsc1oza3lNa0ZC"
        "UVVGQlIyd3lUM2c0VDIxTlFtMUJFZzFNVHpFd09EQjhURTh6T0RFMUdnc0kzT1lHRUFJYUEx"
        "VlRSRGdjY056bUJnPT0SAggAIgMKATA",
    )
    selected_params = {k: v[0] for k, v in parse_qs(urlparse(selected_url).query).items()}
    selected_params.setdefault("hl", "en")
    selected_params.setdefault("curr", "")
    selected_res = core.fetch_search(selected_params, request_kwargs=req_kwargs)
    summary = _extract_multicity_summary(selected_res.text)
    assert summary is not None, "No multicity summary price found for selected itinerary"
    record_property("multicity_total_price", summary.price)
    record_property("multicity_total_currency", summary.currency)
    record_property("multicity_total_flights", summary.flights)
    print(f"[MC][live] total price={summary.price} {summary.currency} flights={summary.flights}")
