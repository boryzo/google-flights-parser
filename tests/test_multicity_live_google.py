import logging
import os
from datetime import date, timedelta

from fast_flights import FlightData, Passengers, create_filter
from fast_flights import core

logger = logging.getLogger(__name__)

OUTBOUND_DAYS = int(os.getenv("MC_LIVE_OUTBOUND_DAYS", "90"))
RETURN_GAP_DAYS = int(os.getenv("MC_LIVE_RETURN_GAP_DAYS", "16"))


def _future_date(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


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
