# core.py

import json
import logging
import re
from datetime import datetime
from typing import List, Literal, Optional, Union
from urllib.parse import parse_qs, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .decoder import DecodedResult, Itinerary, ResultDecoder, RoundTripDecodedResult
from .schema import Flight, Result, Segment
from .flights_impl import FlightData, Passengers
from . import flights_pb2 as PB
from .filter import TFSData
from .fallback_playwright import fallback_playwright_fetch
from .bright_data_fetch import bright_data_fetch
from .primp import Client, Response

DataSource = Literal["html", "js"]

logger = logging.getLogger(__name__)

SEGMENT_RE = re.compile(
    r"^(?P<orig>[A-Z]{3})-(?P<dest>[A-Z]{3})-(?P<carrier>[A-Z0-9]{2,3})-(?P<flight>\d{1,5})-(?P<date>\d{8})$"
)
DURATION_RE = re.compile(r"(?P<h>\d+)\s*hr\s*(?P<m>\d+)\s*min", re.IGNORECASE)
STOPS_RE = re.compile(
    r"(?P<count>\d+)\s+stops?\s+in\s+(?P<airports>[A-Z]{3}(?:,\s*[A-Z]{3})*)",
    re.IGNORECASE,
)
TRIP_TYPE_RE = re.compile(r"\b(round trip|one way)\b", re.IGNORECASE)

# Default cookies embedded into the app to help bypass common consent gating.
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


def _merge_binary_cookies(cookies_bytes: bytes | None, request_kwargs: dict | None) -> dict:
    """Parse binary cookies into request kwargs.

    Supported formats:
    - JSON bytes -> dict or list of pairs
    - Pickle bytes -> dict
    - Raw cookie header bytes -> sets the 'Cookie' header
    """
    req_kwargs = request_kwargs.copy() if request_kwargs else {}
    if not cookies_bytes:
        return req_kwargs

    # Try JSON first
    try:
        s = cookies_bytes.decode("utf-8")
        parsed = json.loads(s)
        if isinstance(parsed, dict):
            req_kwargs["cookies"] = parsed
            return req_kwargs
        if isinstance(parsed, list):
            try:
                req_kwargs["cookies"] = dict(parsed)
                return req_kwargs
            except Exception:
                pass
    except Exception:
        pass

    # Try pickle
    try:
        import pickle

        parsed = pickle.loads(cookies_bytes)
        if isinstance(parsed, dict):
            req_kwargs["cookies"] = parsed
            return req_kwargs
    except Exception:
        pass

    # Fallback: treat as raw Cookie header
    try:
        s = cookies_bytes.decode("utf-8")
        headers = req_kwargs.get("headers", {})
        headers = headers.copy() if isinstance(headers, dict) else {}
        headers["Cookie"] = s
        req_kwargs["headers"] = headers
    except Exception:
        pass

    return req_kwargs


def _parse_duration_minutes(text: str) -> Optional[int]:
    if not text:
        return None
    match = DURATION_RE.search(text.strip())
    if not match:
        return None
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    return hours * 60 + minutes


def _parse_stops_text(text: str) -> tuple[Optional[int], Optional[List[str]]]:
    if not text:
        return None, None
    t = text.strip()
    if "nonstop" in t.lower():
        return 0, []
    match = STOPS_RE.search(t)
    if not match:
        return None, None
    count = int(match.group("count"))
    airports = [x.strip().upper() for x in match.group("airports").split(",")]
    return count, airports


def _parse_travelimpact_url(url: Optional[str]) -> tuple[Optional[str], Optional[List[Segment]]]:
    if not url:
        return None, None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    itinerary = qs.get("itinerary", [None])[0]
    if not itinerary:
        return None, None
    segments: list[Segment] = []
    for part in itinerary.split(","):
        part = part.strip()
        if not part:
            continue
        match = SEGMENT_RE.match(part)
        if not match:
            continue
        date_raw = match.group("date")
        try:
            date_iso = datetime.strptime(date_raw, "%Y%m%d").date().isoformat()
        except ValueError:
            date_iso = date_raw
        segments.append(
            Segment(
                origin=match.group("orig"),
                destination=match.group("dest"),
                carrier_code=match.group("carrier"),
                flight_number=match.group("flight"),
                date=date_iso,
            )
        )
    return itinerary, segments if segments else None


def _find_match(pattern: re.Pattern[str], text: str) -> Optional[str]:
    match = pattern.search(text)
    return match.group(0).strip() if match else None


def _find_card(node: LexborNode) -> Optional[LexborNode]:
    card = node
    for _ in range(10):
        if card is None:
            break
        if card.css_first('span[role="text"][aria-label]') or card.css_first(".h1fkLb"):
            return card
        card = card.parent
    return None


def _find_travelimpact_node(node: LexborNode) -> Optional[LexborNode]:
    current = node
    for _ in range(10):
        if current is None:
            break
        if current.attributes.get("data-travelimpactmodelwebsiteurl"):
            return current
        impact = current.css_first("[data-travelimpactmodelwebsiteurl]")
        if impact:
            return impact
        current = current.parent
    return None


def _parse_airline_logo_url(card: Optional[LexborNode]) -> Optional[str]:
    if not card:
        return None
    logo_div = card.css_first('[style*="airline_logos/70px"]')
    if not logo_div:
        return None
    style = logo_div.attributes.get("style", "")
    match = re.search(
        r"url\((https://www\.gstatic\.com/flights/airline_logos/70px/[^)]+)\)",
        style,
    )
    return match.group(1) if match else None


def _extract_js_data(html_text: str) -> list:
    parser = LexborHTMLParser(html_text)
    script = parser.css_first(r"script.ds\:1")
    if not script:
        raise RuntimeError("Malformed js data: cannot find script.ds:1")
    match = re.search(r"^.*?\{.*?data:(\[.*\]).*}", script.text())
    if not match:
        raise RuntimeError("Malformed js data: cannot find JSON data array in script.ds:1")
    return json.loads(match.group(1))


def _parse_target_time(target_time: Optional[str]) -> Optional[int]:
    if not target_time:
        return None
    match = re.match(r"^(?P<h>\d{1,2}):(?P<m>\d{2})$", target_time.strip())
    if not match:
        raise ValueError(f"target_time should be HH:MM, got {target_time!r}")
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"target_time should be HH:MM, got {target_time!r}")
    return hours * 60 + minutes


def _itinerary_departure_minutes(itinerary: Itinerary) -> int:
    hours, minutes = itinerary.departure_time
    return hours * 60 + minutes


def _itinerary_stops(itinerary: Itinerary) -> int:
    return max(0, len(itinerary.flights) - 1)


def _itinerary_duration_minutes(itinerary: Itinerary) -> int:
    # If decoder provides duration_minutes, use it; otherwise approximate by segment count (stable tie-break only).
    dm = getattr(itinerary, "duration_minutes", None)
    if isinstance(dm, int) and dm > 0:
        return dm
    return 10_000 + _itinerary_stops(itinerary) * 100  # keep deterministic ordering


def _safe_price_value(itinerary: Itinerary) -> float:
    # decoder uses itinerary.itinerary_summary.price; keep resilient
    try:
        return float(itinerary.itinerary_summary.price)
    except Exception:
        return float("inf")


def _looks_like_token(s: str) -> bool:
    """Heuristic filter for selection tokens.
    This is intentionally conservative; adjust once you identify the real field path."""
    if not isinstance(s, str):
        return False
    if len(s) < 18:
        return False
    # skip obvious ISO dates / airport codes
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    if re.fullmatch(r"[A-Z]{3}", s):
        return False
    # often tokens are url-safe/base64-ish
    if re.fullmatch(r"[A-Za-z0-9\-_+/=]+", s) is None:
        return False
    return True


def _extract_selection_ref(raw_item: Optional[list]) -> Optional[str]:
    """Extract outbound selection reference from the raw JS item.

    IMPORTANT:
    This is still heuristic unless you pin the exact field path.
    To make it deterministic, log raw_item for a few real runs and replace this with index-based extraction.
    """
    if not raw_item or not isinstance(raw_item, list):
        return None

    # Some entries contain a base64 "ItinerarySummary" which we should ignore as a selection token.
    summary_b64 = None
    try:
        if isinstance(raw_item[1], list) and isinstance(raw_item[1][1], str):
            summary_b64 = raw_item[1][1]
    except Exception:
        summary_b64 = None

    candidates: list[str] = []

    def walk(v: object) -> None:
        if isinstance(v, list):
            for it in v:
                walk(it)
        elif isinstance(v, str):
            candidates.append(v)

    walk(raw_item)

    # Prefer token-like strings, avoid picking itinerary_summary b64
    token_like = [c for c in candidates if c != summary_b64 and _looks_like_token(c)]
    if token_like:
        # Prefer longer tokens (often more specific)
        token_like.sort(key=len, reverse=True)
        return token_like[0]

    # fallback: legacy behavior
    for c in candidates:
        if c != summary_b64 and len(c) >= 16:
            return c
    return None


def _decode_outbound_options(raw: list) -> list[tuple[Itinerary, Optional[str], Optional[list]]]:
    """Return (itinerary, selection_ref, raw_item) for best+other.

    raw_item is included for logging when selection_ref extraction fails.
    """
    decoded = ResultDecoder.decode(raw)
    options: list[tuple[Itinerary, Optional[str], Optional[list]]] = []

    def iter_section(section_index: int, itineraries: list[Itinerary]) -> None:
        try:
            raw_section = raw[section_index][0]
        except (IndexError, TypeError):
            raw_section = []
        for idx, itinerary in enumerate(itineraries):
            raw_item = None
            if isinstance(raw_section, list) and idx < len(raw_section):
                raw_item = raw_section[idx]
            selection_ref = _extract_selection_ref(raw_item if isinstance(raw_item, list) else None)
            options.append((itinerary, selection_ref, raw_item if isinstance(raw_item, list) else None))

    iter_section(2, decoded.best)
    iter_section(3, decoded.other)
    return options


def _select_outbound(
    options: list[tuple[Itinerary, Optional[str], Optional[list]]],
    target_time_minutes: Optional[int],
) -> tuple[Itinerary, Optional[str], Optional[list]]:
    """Selection logic:
    - if target_time: minimize |dep-target|, tie: nonstop, then price, then duration
    - else: nonstop, then cheapest, then duration, then earliest departure
    """
    if not options:
        raise RuntimeError("No outbound options available for selection")

    if target_time_minutes is not None:
        return min(
            options,
            key=lambda opt: (
                abs(_itinerary_departure_minutes(opt[0]) - target_time_minutes),
                _itinerary_stops(opt[0]),
                _safe_price_value(opt[0]),
                _itinerary_duration_minutes(opt[0]),
                _itinerary_departure_minutes(opt[0]),
            ),
        )

    return min(
        options,
        key=lambda opt: (
            _itinerary_stops(opt[0]),
            _safe_price_value(opt[0]),
            _itinerary_duration_minutes(opt[0]),
            _itinerary_departure_minutes(opt[0]),
        ),
    )


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
        "tfu": "EgQIABABIgA",
        "curr": currency,
    }

    # Default cookies if caller provided nothing and cookie consent is enabled.
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

    # First fetch (listing)
    res = _fetch_with_mode(params, mode=mode, req_kwargs=req_kwargs)

    try:
        # RT JS flow: outbound listing -> auto-select -> follow-up -> inbound decode
        if data_source == "js" and filter.trip == PB.Trip.ROUND_TRIP:
            logger.info("RT JS flow: listing outbound options (request #1).")
            outbound_raw = _extract_js_data(res.text)
            outbound_decoded = ResultDecoder.decode(outbound_raw)

            outbound_options = _decode_outbound_options(outbound_raw)
            missing_refs = sum(1 for _, ref, _ in outbound_options if not ref)
            logger.info(
                "RT JS flow: decoded %d outbound options (%d missing refs).",
                len(outbound_options),
                missing_refs,
            )

            target_minutes = _parse_target_time(target_time)
            selected_itinerary, selected_ref, selected_raw_item = _select_outbound(outbound_options, target_minutes)

            if not selected_ref:
                # High-signal debugging: log part of the raw item so you can pin the exact token path.
                snippet = ""
                try:
                    snippet = json.dumps(selected_raw_item)[:2500] if selected_raw_item is not None else ""
                except Exception:
                    snippet = ""
                logger.error(
                    "Selected outbound missing selection_ref. "
                    "You must pin exact field path in _extract_selection_ref(). raw_item_snippet=%s",
                    snippet,
                )
                raise RuntimeError("Selected outbound option missing selection reference.")

            logger.info(
                "Selected outbound: dep=%s stops=%s price=%s ref_len=%s ref_prefix=%s",
                getattr(selected_itinerary, "departure_time", None),
                _itinerary_stops(selected_itinerary),
                _safe_price_value(selected_itinerary),
                len(selected_ref),
                selected_ref[:40] + "...",
            )

            if not hasattr(filter, "with_selected_outbound"):
                raise RuntimeError("TFSData.with_selected_outbound() missing. Implement it in flights_impl.TFSData.")

            followup_filter = filter.with_selected_outbound(selected_ref)  # type: ignore[attr-defined]
            tfs1 = params["tfs"]
            tfs2 = followup_filter.as_b64().decode("utf-8")
            params["tfs"] = tfs2

            logger.info("RT JS flow: issuing follow-up request #2 (tfs changed=%s).", tfs1 != tfs2)

            res2 = _fetch_with_mode(params, mode=mode, req_kwargs=req_kwargs)
            inbound_raw = _extract_js_data(res2.text)
            inbound_decoded = ResultDecoder.decode(inbound_raw)

            logger.info("RT JS flow: follow-up decoded inbound results.")

            return RoundTripDecodedResult(
                outbound=outbound_decoded,
                inbound=inbound_decoded,
                selected_outbound_ref=selected_ref,
                selected_outbound=selected_itinerary,
            )

        # non-RT or HTML flow
        return parse_response(res, data_source)

    except RuntimeError as e:
        if mode == "fallback":
            return get_flights_from_filter(
                filter,
                mode="force-fallback",
                request_kwargs=req_kwargs,
                cookies=None,
                cookie_consent=cookie_consent,
                target_time=target_time,
                data_source=data_source,
                currency=currency,
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
        data = _extract_js_data(r.text)
        return ResultDecoder.decode(data) if data is not None else None

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
            segments_count = len(segments) if segments else None
            inferred_stops_from_itinerary = (len(segments) - 1) if segments else None

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
                    "segments_count": segments_count,
                    "inferred_stops_from_itinerary": inferred_stops_from_itinerary,
                    "airline_logo_url": airline_logo_url,
                }
            )

    current_price = safe(parser.css_first("span.gOatQ")).text()
    if not flights:
        raise RuntimeError(f"No flights found:\n{r.text_markdown}")

    return Result(
        current_price=current_price,
        flights=[Flight(**fl) for fl in flights],
    )
