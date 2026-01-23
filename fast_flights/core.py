# core.py

import json
import logging
from typing import List, Literal, Optional, Union

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .decoder import DecodedResult, Itinerary, ResultDecoder, RoundTripDecodedResult
from .schema import Flight, Result, Segment
from .flights_impl import FlightData, Passengers, build_tfs_with_segments
from . import flights_pb2 as PB
from .filter import TFSData
from .fallback_playwright import fallback_playwright_fetch
from .bright_data_fetch import bright_data_fetch
from .primp import Client, Response
from .core_helpers import (
    TRIP_TYPE_RE,
    _apply_round_trip_total_price,
    _decode_js_result_from_html,
    _dump_listing_debug,
    _extract_js_data,
    _extract_js_data_candidates,
    _extract_tfs_candidates_from_html,
    _extract_tfs_candidates_from_js_data,
    _find_card,
    _find_match,
    _find_travelimpact_node,
    _merge_binary_cookies,
    _parse_airline_logo_url,
    _parse_duration_minutes,
    _parse_stops_text,
    _parse_target_time,
    _parse_travelimpact_url,
    _pick_iata_code,
    _segments_payload_from_tfs,
    _select_outbound,
    _decoded_result_has_route,
)

DataSource = Literal["html", "js"]
logger = logging.getLogger(__name__)

_DEFAULT_COOKIES = {
    "CONSENT": "PENDING+987",
    "SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmRlIAEaBgiAo_CmBg",
}
_DEFAULT_COOKIES_BYTES = json.dumps(_DEFAULT_COOKIES).encode("utf-8")


def fetch(params: dict, request_kwargs: dict | None = None) -> Response:
    client = Client(impersonate="chrome_126", verify=False)
    req_kwargs = request_kwargs.copy() if request_kwargs else {}
    res = client.get("https://www.google.com/travel/flights", params=params, **req_kwargs)
    assert res.status_code == 200, f"{res.status_code} Result: {res.text_markdown}"
    return res


def fetch_search(params: dict, request_kwargs: dict | None = None) -> Response:
    client = Client(impersonate="chrome_126", verify=False)
    req_kwargs = request_kwargs.copy() if request_kwargs else {}
    res = client.get("https://www.google.com/travel/flights/search", params=params, **req_kwargs)
    assert res.status_code == 200, f"{res.status_code} Result: {res.text_markdown}"
    return res


def fetch_booking(tfs: str, request_kwargs: dict | None = None) -> Response:
    client = Client(impersonate="chrome_126", verify=False)
    req_kwargs = request_kwargs.copy() if request_kwargs else {}
    res = client.get("https://www.google.com/travel/flights/booking", params={"tfs": tfs}, **req_kwargs)
    assert res.status_code == 200, f"{res.status_code} Result: {res.text_markdown}"
    return res


def _fetch_with_mode(
    params: dict,
    *,
    mode: Literal["common", "fallback", "force-fallback", "local", "bright-data"],
    req_kwargs: dict,
) -> Response:
    if mode in {"common", "fallback"}:
        try:
            return fetch(params, request_kwargs=req_kwargs)
        except AssertionError as e:
            if mode == "fallback":
                return fallback_playwright_fetch(params, request_kwargs=req_kwargs)
            raise e

    if mode == "local":
        from .local_playwright import local_playwright_fetch

        return local_playwright_fetch(params, request_kwargs=req_kwargs)

    if mode == "bright-data":
        return bright_data_fetch(params, request_kwargs=req_kwargs)

    return fallback_playwright_fetch(params, request_kwargs=req_kwargs)


def get_flights_from_filter(
    filter: TFSData,
    currency: str = "",
    *,
    mode: Literal["common", "fallback", "force-fallback", "local", "bright-data"] = "common",
    data_source: DataSource = "html",
    cookies: bytes | None = None,
    request_kwargs: dict | None = None,
    cookie_consent: bool = True,
    target_time: Optional[str] = None,
) -> Union[Result, DecodedResult, RoundTripDecodedResult, None]:
    data = filter.as_b64()

    params = {
        "tfs": data.decode("utf-8"),
        "hl": "en",
        "curr": currency,
    }

    # Apply default cookies if caller did not provide any.
    if cookies is None and cookie_consent:
        has_cookies_in_req = False
        if request_kwargs:
            if "cookies" in request_kwargs:
                has_cookies_in_req = True
            elif (
                "headers" in request_kwargs
                and isinstance(request_kwargs["headers"], dict)
                and "Cookie" in request_kwargs["headers"]
            ):
                has_cookies_in_req = True
        if not has_cookies_in_req:
            cookies = _DEFAULT_COOKIES_BYTES

    req_kwargs = _merge_binary_cookies(cookies, request_kwargs)

    # Request #1: listing
    res1 = _fetch_with_mode(params, mode=mode, req_kwargs=req_kwargs)

    try:
        if data_source == "js" and filter.trip == PB.Trip.ROUND_TRIP:
            debug_info: dict = {"path": None, "steps": []}
            origin = getattr(filter.flight_data[0], "from_airport", None) if filter.flight_data else None
            destination = getattr(filter.flight_data[0], "to_airport", None) if filter.flight_data else None

            def _record_step(step: str, **fields) -> dict:
                entry = {"step": step}
                entry.update(fields)
                debug_info["steps"].append(entry)
                return entry

            def _flights_url(params_dict: dict) -> str:
                return "https://www.google.com/travel/flights?" + "&".join(
                    f"{k}={v}" for k, v in params_dict.items()
                )

            def _search_url(params_dict: dict) -> str:
                return "https://www.google.com/travel/flights/search?" + "&".join(
                    f"{k}={v}" for k, v in params_dict.items()
                )

            def _maybe_set_tfs_segments(tfs_value: str | None) -> None:
                if not tfs_value:
                    return
                if debug_info.get("tfs_segments") is not None:
                    return
                debug_info["tfs_segments"] = _segments_payload_from_tfs(tfs_value)

            def _decode_followup_from_search(
                tfs_value: str,
                *,
                entry: Optional[dict] = None,
            ) -> Optional[DecodedResult]:
                try:
                    search_params = {
                        "tfs": tfs_value,
                        "hl": params["hl"],
                        "curr": params["curr"],
                    }
                    if entry is not None:
                        entry["url"] = _search_url(search_params)
                    res_search = fetch_search(search_params, request_kwargs=req_kwargs)
                    decoded = _decode_js_result_from_html(res_search.text)
                    if entry is not None:
                        entry["status"] = "success"
                    return decoded
                except Exception as err:
                    if entry is not None:
                        entry["status"] = "error"
                        entry["error"] = str(err)
                    return None

            def _decode_followup_from_flights(
                tfs_value: str,
                *,
                entry: Optional[dict] = None,
            ) -> Optional[DecodedResult]:
                try:
                    followup_params = {
                        "tfs": tfs_value,
                        "hl": params["hl"],
                        "curr": params["curr"],
                    }
                    if entry is not None:
                        entry["url"] = _flights_url(followup_params)
                    res_followup = _fetch_with_mode(followup_params, mode=mode, req_kwargs=req_kwargs)
                    decoded = _decode_js_result_from_html(res_followup.text)
                    if entry is not None:
                        entry["status"] = "success"
                    return decoded
                except Exception as err:
                    if entry is not None:
                        entry["status"] = "error"
                        entry["error"] = str(err)
                    logger.warning("RT JS flow: flights endpoint decode failed: %s", err)
                    return None

            logger.info("RT JS flow: listing outbound options (request #1).")
            debug_info["listing_url"] = _flights_url(params)
            outbound_raw = _extract_js_data(res1.text)
            try:
                outbound_decoded = ResultDecoder.decode(outbound_raw)
            except Exception as err:
                logger.warning("RT JS flow: primary outbound decode failed; trying multi-candidate decode: %s", err)
                _dump_listing_debug(res1.text)
                outbound_decoded = _decode_js_result_from_html(res1.text)

            out_best = getattr(outbound_decoded, "best", []) or []
            out_other = getattr(outbound_decoded, "other", []) or []
            outbound_itineraries: list[Itinerary] = list(out_best) + list(out_other)

            logger.info("RT JS flow: decoded outbound itineraries=%d.", len(outbound_itineraries))

            target_minutes = _parse_target_time(target_time)
            selected_outbound = _select_outbound(outbound_itineraries, target_minutes)
            selected_summary = getattr(selected_outbound, "itinerary_summary", None)
            debug_info["selected_outbound"] = {
                "departure_airport": getattr(selected_outbound, "departure_airport", None),
                "arrival_airport": getattr(selected_outbound, "arrival_airport", None),
                "departure_time": getattr(selected_outbound, "departure_time", None),
                "arrival_time": getattr(selected_outbound, "arrival_time", None),
                "price": getattr(selected_summary, "price", None),
                "currency": getattr(selected_summary, "currency", None),
            }

            # Build a tfs with outbound segments embedded (no tfu).
            selected_segments: list[Segment] = []
            for f in getattr(selected_outbound, "flights", []) or []:
                dep_date = getattr(f, "departure_date", None)
                date_str = None
                if isinstance(dep_date, (list, tuple)) and len(dep_date) >= 3:
                    date_str = f"{int(dep_date[0]):04d}-{int(dep_date[1]):02d}-{int(dep_date[2]):02d}"
                elif isinstance(dep_date, str):
                    date_str = dep_date
                if not date_str:
                    continue
                dep_code = _pick_iata_code(
                    getattr(f, "departure_airport", None),
                    getattr(f, "departure_airport_name", None),
                )
                arr_code = _pick_iata_code(
                    getattr(f, "arrival_airport", None),
                    getattr(f, "arrival_airport_name", None),
                )
                if not dep_code or not arr_code:
                    continue
                selected_segments.append(
                    Segment(
                        origin=dep_code,
                        destination=arr_code,
                        carrier_code=getattr(f, "airline", None) or "",
                        flight_number=str(getattr(f, "flight_number", "")),
                        date=date_str,
                    )
                )
            selected_tfs_with_segments: str | None = None
            if selected_segments:
                try:
                    selected_tfs_with_segments = build_tfs_with_segments(
                        filter,
                        [selected_segments, []],
                    )
                    debug_info["selected_tfs_segments_len"] = len(selected_tfs_with_segments)
                except Exception as err:
                    logger.debug("RT JS flow: build_tfs_with_segments failed: %s", err)

            # Path A disabled: ignore tfu-based follow-ups (user request).
            pairs: list[tuple[str, str]] = []
            debug_info["followup_pairs_found"] = 0
            debug_info["best_pair_score"] = 0

            selected_ref = getattr(selected_outbound, "selection_ref", None)
            if selected_ref:
                logger.info("RT JS flow: using selected outbound ref for follow-up request #2.")
                selected_tfs = filter.with_selected_outbound(selected_ref).as_b64().decode("utf-8")
                debug_info["selected_outbound_ref"] = selected_ref
                entry = _record_step("selected_outbound_ref", tfs_len=len(selected_tfs))
                entry["url"] = _flights_url(
                    {
                        "tfs": selected_tfs,
                        "hl": params["hl"],
                        "curr": params["curr"],
                    }
                )
                _maybe_set_tfs_segments(selected_tfs)
                inbound_decoded = _decode_followup_from_search(selected_tfs, entry=entry)
                if inbound_decoded and _decoded_result_has_route(inbound_decoded, destination, origin):
                    _apply_round_trip_total_price(inbound_decoded, selected_outbound)
                    debug_info["path"] = "selected_outbound_ref"
                    logger.info("RT JS flow: follow-up decode succeeded via selected outbound ref.")
                    return RoundTripDecodedResult(
                        outbound=outbound_decoded,
                        inbound=inbound_decoded,
                        selected_outbound_ref=selected_ref,
                        selected_outbound=selected_outbound,
                        debug=debug_info,
                    )

            # Path D: try /search endpoint with extracted tfu + tfs candidates.
            if origin and destination:
                if selected_ref:
                    selected_tfs = filter.with_selected_outbound(selected_ref).as_b64().decode("utf-8")
                else:
                    selected_tfs = None
                tfs_candidates = _extract_tfs_candidates_from_html(res1.text, origin, destination)
                if tfs_candidates:
                    pass
                else:
                    js_candidates = _extract_js_data_candidates(res1.text)
                    for data in js_candidates:
                        tfs_candidates.extend(_extract_tfs_candidates_from_js_data(data, origin, destination))
                    if not tfs_candidates:
                        tfs_candidates.extend(_extract_tfs_candidates_from_js_data(outbound_raw, origin, destination))
                if not tfs_candidates:
                    # Fallback: try the base listing tfs even if it may not include selections.
                    tfs_candidates = [params["tfs"]]
                if selected_tfs_with_segments and selected_tfs_with_segments not in tfs_candidates:
                    tfs_candidates.insert(0, selected_tfs_with_segments)
                if selected_tfs and selected_tfs not in tfs_candidates:
                    tfs_candidates.insert(0, selected_tfs)

                debug_info["tfs_candidates"] = tfs_candidates[:5]
                if tfs_candidates:
                    for tfs_candidate in tfs_candidates[:5]:
                        _maybe_set_tfs_segments(tfs_candidate)
                        params_search = {
                            "tfs": tfs_candidate,
                            "hl": params["hl"],
                            "curr": params["curr"],
                        }
                        entry = _record_step(
                            "search_endpoint",
                            tfs_len=len(tfs_candidate),
                        )
                        entry["url"] = _search_url(params_search)
                        try:
                            res_search = fetch_search(params_search, request_kwargs=req_kwargs)
                            inbound_decoded = _decode_js_result_from_html(res_search.text)
                        except Exception as err:
                            entry["status"] = "error"
                            entry["error"] = str(err)
                            continue
                        if not _decoded_result_has_route(inbound_decoded, destination, origin):
                            entry["status"] = "error"
                            entry["error"] = "search_result_missing_inbound_route"
                            continue
                        entry["status"] = "success"
                        _apply_round_trip_total_price(inbound_decoded, selected_outbound)
                        debug_info["path"] = "search_endpoint"
                        logger.info("RT JS flow: /search decode succeeded.")
                        return RoundTripDecodedResult(
                            outbound=outbound_decoded,
                            inbound=inbound_decoded,
                            selected_outbound_ref=tfs_candidate,
                            selected_outbound=selected_outbound,
                            debug=debug_info,
                        )

            # Nothing worked; dump listing for diagnosis.
            if len(getattr(filter, "flight_data", []) or []) > 1:
                logger.warning("RT JS flow: falling back to one-way search for return leg.")
                return_leg = filter.flight_data[1]
                one_way_filter = TFSData(
                    flight_data=[return_leg],
                    seat=filter.seat,
                    trip=PB.Trip.ONE_WAY,
                    passengers=filter.passengers,
                    max_stops=filter.max_stops,
                )
                fallback_result = get_flights_from_filter(
                    one_way_filter,
                    currency=currency,
                    mode=mode,
                    data_source=data_source,
                    cookies=None,
                    request_kwargs=req_kwargs,
                    cookie_consent=cookie_consent,
                    target_time=target_time,
                )
                if isinstance(fallback_result, DecodedResult):
                    logger.info("RT JS flow: one-way fallback decode succeeded.")
                    _apply_round_trip_total_price(fallback_result, selected_outbound)
                    debug_info["path"] = "one_way_fallback"
                    return RoundTripDecodedResult(
                        outbound=outbound_decoded,
                        inbound=fallback_result,
                        selected_outbound_ref="one-way-fallback",
                        selected_outbound=selected_outbound,
                        debug=debug_info,
                    )

            _dump_listing_debug(res1.text)
            raise RuntimeError(
                "Round-trip follow-up not found: no (tfs2, tfu2) pairs, no explicit booking?tfs, "
                "and no inferable booking token from embedded strings. "
                "Dumped listing HTML/snippet to /tmp/fast_flights_listing.html and /tmp/fast_flights_listing_snippet.txt."
            )

        return parse_response(res1, data_source)

    except RuntimeError as e:
        if mode == "fallback":
            return get_flights_from_filter(
                filter,
                currency=currency,
                mode="force-fallback",
                data_source=data_source,
                cookies=None,
                request_kwargs=req_kwargs,
                cookie_consent=cookie_consent,
                target_time=target_time,
            )
        raise e


def get_flights(
    *,
    flight_data: List[FlightData],
    trip: Literal["round-trip", "one-way", "multi-city"],
    passengers: Optional[Passengers] = None,
    adults: Optional[int] = None,
    children: int = 0,
    infants_in_seat: int = 0,
    infants_on_lap: int = 0,
    seat: Literal["economy", "premium-economy", "business", "first"] = "economy",
    fetch_mode: Literal["common", "fallback", "force-fallback", "local", "bright-data"] = "common",
    max_stops: Optional[int] = None,
    data_source: DataSource = "html",
    cookies: bytes | None = None,
    request_kwargs: dict | None = None,
    cookie_consent: bool = True,
    target_time: Optional[str] = None,
) -> Union[Result, DecodedResult, RoundTripDecodedResult, None]:
    if passengers is None:
        ad = 1 if adults is None else adults
        passengers = Passengers(
            adults=ad,
            children=children,
            infants_in_seat=infants_in_seat,
            infants_on_lap=infants_on_lap,
        )

    tfs: TFSData = TFSData.from_interface(
        flight_data=flight_data,
        trip=trip,
        passengers=passengers,
        seat=seat,
        max_stops=max_stops,
    )

    return get_flights_from_filter(
        tfs,
        mode=fetch_mode,
        data_source=data_source,
        cookies=cookies,
        request_kwargs=request_kwargs,
        cookie_consent=cookie_consent,
        target_time=target_time,
    )


def parse_response(
    r: Response,
    data_source: DataSource,
    *,
    dangerously_allow_looping_last_item: bool = False,
) -> Union[Result, DecodedResult, None]:
    class _blank:
        def text(self, *_, **__):
            return ""

        def iter(self):
            return []

    blank = _blank()

    def safe(n: Optional[LexborNode]):
        return n or blank

    if data_source == "js":
        try:
            return _decode_js_result_from_html(r.text)
        except Exception as err:
            logger.warning("JS parse failed; dumped listing HTML for debugging: %s", err)
            _dump_listing_debug(r.text)
            raise

    parser = LexborHTMLParser(r.text)
    flights = []

    for i, fl in enumerate(parser.css('div[jsname="IWWDBc"], div[jsname="YdtKid"]')):
        is_best_flight = i == 0

        for item in fl.css("ul.Rk10dc li")[
            : (None if dangerously_allow_looping_last_item or i == 0 else -1)
        ]:
            name = safe(item.css_first("div.sSHqwe.tPgKwe.ogfYpf span")).text(strip=True)

            dp_ar_node = item.css("span.mv1WYe div")
            try:
                departure_time = dp_ar_node[0].text(strip=True)
                arrival_time = dp_ar_node[1].text(strip=True)
            except IndexError:
                departure_time = ""
                arrival_time = ""

            time_ahead = safe(item.css_first("span.bOzv6")).text()
            duration = safe(item.css_first("li div.Ak5kof div")).text()
            stops = safe(item.css_first(".BbR8Ec .ogfYpf")).text()
            delay = safe(item.css_first(".GsCCve")).text() or None
            price = safe(item.css_first(".YMlIz.FpEdX")).text() or "0"

            try:
                stops_fmt = 0 if stops == "Nonstop" else int(stops.split(" ", 1)[0])
            except ValueError:
                stops_fmt = "Unknown"

            card = _find_card(item)
            trip_type = _find_match(TRIP_TYPE_RE, card.text()) if card else None
            if trip_type:
                trip_type = trip_type.lower()

            stops_count, stop_airports = _parse_stops_text(stops)
            duration_minutes = _parse_duration_minutes(duration)

            impact_node = _find_travelimpact_node(item)
            travelimpact_url = (
                impact_node.attributes.get("data-travelimpactmodelwebsiteurl") if impact_node else None
            )
            itinerary_raw, segments = _parse_travelimpact_url(travelimpact_url)

            airline_logo_url = _parse_airline_logo_url(card)

            flights.append(
                {
                    "is_best": is_best_flight,
                    "name": name,
                    "departure": " ".join(departure_time.split()),
                    "arrival": " ".join(arrival_time.split()),
                    "arrival_time_ahead": time_ahead,
                    "duration": duration,
                    "stops": stops_fmt,
                    "delay": delay,
                    "price": price.replace(",", ""),
                    "trip_type": trip_type,
                    "stops_count": stops_count,
                    "stop_airports": stop_airports,
                    "duration_minutes": duration_minutes,
                    "itinerary_raw": itinerary_raw,
                    "segments": segments,
                    "segments_count": len(segments) if segments else None,
                    "inferred_stops_from_itinerary": (len(segments) - 1) if segments else None,
                    "airline_logo_url": airline_logo_url,
                }
            )

    current_price = safe(parser.css_first("span.gOatQ")).text()
    if not flights:
        raise RuntimeError("No flights found:\n{}".format(r.text_markdown))

    return Result(current_price=current_price, flights=[Flight(**fl) for fl in flights])  # type: ignore
