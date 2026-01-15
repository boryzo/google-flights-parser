# core.py

import base64
import html
import json
import logging
import os
import re
import urllib.parse
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

_DEFAULT_COOKIES = {
    "CONSENT": "PENDING+987",
    "SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmRlIAEaBgiAo_CmBg",
}
_DEFAULT_COOKIES_BYTES = json.dumps(_DEFAULT_COOKIES).encode("utf-8")

# Observed listing tfu for initial search in browser.
_TFU_LISTING_DEFAULT = "KgIIAw"

# Observed follow-up patterns in browser for selected outbound.
_TFS2_PREFIXES = ("CBwQAh",)
_TFU2_PREFIXES = ("Cn",)

# More tolerant extraction of "tfs=... tfu=..." pairs from HTML/JS.
_TFS_TFU_PAIR_RE = re.compile(
    r"""
    tfs(?:=|\\u003d)(?P<tfs>[A-Za-z0-9%_\-+/=]{20,})
    (?:
        [^A-Za-z0-9%_\-+/=]{0,400}
    )
    tfu(?:=|\\u003d)(?P<tfu>[A-Za-z0-9%_\-+/=]{4,})
    """,
    re.VERBOSE,
)

# Booking deep link in various encodings.
_BOOKING_TFS_RE = re.compile(
    r"""
    (?:
        https?:\/\/[^"' ]+\/travel\/flights\/booking\?tfs=|
        \/travel\/flights\/booking\?tfs=|
        travel\/flights\/booking\?tfs=|
        booking\?tfs=
    )
    (?P<tfs>[A-Za-z0-9%_\-+/=]{20,})
    """,
    re.VERBOSE,
)

# Candidate base64-ish strings embedded in HTML/JS (quoted strings).
# This is used to infer "booking tfs" when no explicit booking link exists.
_B64_STRING_RE = re.compile(r'["\']([A-Za-z0-9\-_+/=]{60,})["\']')

_B64ISH_RE = re.compile(r"^[A-Za-z0-9\-_+/=]+$")


def fetch(params: dict, request_kwargs: dict | None = None) -> Response:
    client = Client(impersonate="chrome_126", verify=False)
    req_kwargs = request_kwargs.copy() if request_kwargs else {}
    res = client.get("https://www.google.com/travel/flights", params=params, **req_kwargs)
    assert res.status_code == 200, f"{res.status_code} Result: {res.text_markdown}"
    return res


def fetch_booking(tfs: str, request_kwargs: dict | None = None) -> Response:
    client = Client(impersonate="chrome_126", verify=False)
    req_kwargs = request_kwargs.copy() if request_kwargs else {}
    res = client.get("https://www.google.com/travel/flights/booking", params={"tfs": tfs}, **req_kwargs)
    assert res.status_code == 200, f"{res.status_code} Result: {res.text_markdown}"
    return res


def _merge_binary_cookies(cookies_bytes: bytes | None, request_kwargs: dict | None) -> dict:
    """Parse binary cookies into request kwargs."""
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
    return int(match.group("h")) * 60 + int(match.group("m"))


def _parse_stops_text(text: str) -> tuple[Optional[int], Optional[List[str]]]:
    if not text:
        return None, None
    t = text.strip()
    if "nonstop" in t.lower():
        return 0, []
    match = STOPS_RE.search(t)
    if not match:
        return None, None
    return int(match.group("count")), [x.strip().upper() for x in match.group("airports").split(",")]


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
    m = pattern.search(text)
    return m.group(0).strip() if m else None


def _find_card(node: LexborNode) -> Optional[LexborNode]:
    card = node
    for _ in range(10):
        if card is None:
            break
        if card.css_first('span[role="text"][aria-label]') or card.css_first(".h1fkLb"):
            return card
        card = card.parent
    return card


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
    m = re.search(r"url\((https://www\.gstatic\.com/flights/airline_logos/70px/[^)]+)\)", style)
    return m.group(1) if m else None


def _extract_js_data(html_text: str) -> list:
    parser = LexborHTMLParser(html_text)
    script = parser.css_first(r"script.ds\:1")
    if not script:
        raise RuntimeError("Malformed js data: cannot find script.ds:1")
    match = re.search(r"^.*?\{.*?data:(\[.*\]).*}", script.text())
    if not match:
        raise RuntimeError("Malformed js data: cannot find JSON data array in script.ds:1")
    return json.loads(match.group(1))


def _extract_js_data_candidates(html_text: str) -> list[list]:
    parser = LexborHTMLParser(html_text)
    candidates: list[list] = []
    for script in parser.css("script"):
        cls = script.attributes.get("class", "")
        if not cls.startswith("ds:"):
            continue
        match = re.search(r"^.*?\{.*?data:(\[.*\]).*}", script.text())
        if not match:
            continue
        try:
            candidates.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return candidates


def _iter_js_lists(data: object, *, limit: int = 200) -> list[list]:
    lists: list[list] = []
    seen: set[int] = set()
    stack = [data]
    while stack and len(lists) < limit:
        item = stack.pop()
        if isinstance(item, list):
            obj_id = id(item)
            if obj_id in seen:
                continue
            seen.add(obj_id)
            lists.append(item)
            stack.extend(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
    return lists


def _decode_js_result_from_html(html_text: str) -> DecodedResult:
    candidates = _extract_js_data_candidates(html_text)
    if not candidates:
        return ResultDecoder.decode(_extract_js_data(html_text))

    last_error: Exception | None = None
    for data in candidates:
        for candidate in _iter_js_lists(data):
            try:
                decoded = ResultDecoder.decode(candidate)
            except Exception as err:
                last_error = err
                continue
            if _decoded_result_has_itineraries(decoded):
                return decoded
    if last_error:
        raise last_error
    raise RuntimeError("No decodable js data candidates found.")


def _decoded_result_has_itineraries(decoded: DecodedResult) -> bool:
    for items in (getattr(decoded, "best", None), getattr(decoded, "other", None)):
        if not items or not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if getattr(item, "departure_airport", None):
                return True
    return False


def _parse_target_time(target_time: Optional[str]) -> Optional[int]:
    if not target_time:
        return None
    m = re.match(r"^(?P<h>\d{1,2}):(?P<m>\d{2})$", target_time.strip())
    if not m:
        raise ValueError(f"target_time should be HH:MM, got {target_time!r}")
    h = int(m.group("h"))
    mm = int(m.group("m"))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        raise ValueError(f"target_time should be HH:MM, got {target_time!r}")
    return h * 60 + mm


def _time_to_minutes(value: object, *, field_name: str) -> int:
    """Normalize decoder time shapes to minutes since midnight."""
    if isinstance(value, (list, tuple)):
        if len(value) == 2:
            h, m = value[0], value[1]
        elif len(value) == 1:
            h, m = value[0], 0
        else:
            raise ValueError(f"{field_name} invalid length: {value!r}")
        h_i = int(h)
        m_i = int(m)
        if not (0 <= h_i <= 23 and 0 <= m_i <= 59):
            raise ValueError(f"{field_name} out of range: {value!r}")
        return h_i * 60 + m_i

    if isinstance(value, str):
        m = re.match(r"^(?P<h>\d{1,2}):(?P<m>\d{2})$", value.strip())
        if not m:
            raise ValueError(f"{field_name} invalid string: {value!r}")
        h_i = int(m.group("h"))
        m_i = int(m.group("m"))
        if not (0 <= h_i <= 23 and 0 <= m_i <= 59):
            raise ValueError(f"{field_name} out of range: {value!r}")
        return h_i * 60 + m_i

    raise ValueError(f"{field_name} unsupported type: {type(value).__name__} {value!r}")


def _itinerary_departure_minutes(itinerary: Itinerary) -> int:
    return _time_to_minutes(getattr(itinerary, "departure_time", None), field_name="departure_time")


def _itinerary_stops(itinerary: Itinerary) -> int:
    return max(0, len(itinerary.flights) - 1)


def _safe_price_value(itinerary: Itinerary) -> float:
    try:
        return float(itinerary.itinerary_summary.price)
    except Exception:
        return float("inf")


def _select_outbound(itineraries: list[Itinerary], target_time_minutes: Optional[int]) -> Itinerary:
    """Select outbound by target time; otherwise prefer nonstop then cheapest."""
    if not itineraries:
        raise RuntimeError("No outbound options available for selection")

    if target_time_minutes is not None:
        return min(
            itineraries,
            key=lambda it: (
                abs(_itinerary_departure_minutes(it) - target_time_minutes),
                _itinerary_stops(it),
                _safe_price_value(it),
            ),
        )

    return min(itineraries, key=lambda it: (_itinerary_stops(it), _safe_price_value(it)))


def _b64url_decode_bytes(s: str) -> bytes:
    """Decode URL-safe base64-ish strings and tolerate missing padding."""
    s = urllib.parse.unquote(s).strip()
    pad = (-len(s) % 4)
    if pad:
        s += "=" * pad
    return base64.urlsafe_b64decode(s.encode("utf-8"))


def _extract_followup_pairs_from_html(html: str) -> list[tuple[str, str]]:
    """Extract follow-up (tfs2, tfu2) pairs from HTML."""
    html = _normalize_html_token_text(html)
    pairs: list[tuple[str, str]] = []
    for m in _TFS_TFU_PAIR_RE.finditer(html):
        tfs = urllib.parse.unquote(m.group("tfs"))
        tfu = urllib.parse.unquote(m.group("tfu"))

        if not tfs or not tfu:
            continue
        if " " in tfs or " " in tfu:
            continue
        if not _B64ISH_RE.fullmatch(tfs) or not _B64ISH_RE.fullmatch(tfu):
            continue

        if tfs.startswith(_TFS2_PREFIXES) and tfu.startswith(_TFU2_PREFIXES) and len(tfu) >= 40:
            pairs.append((tfs, tfu))

    return pairs


def _iter_js_strings(data: object) -> list[str]:
    strings: list[str] = []
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
    return strings


def _extract_followup_pairs_from_js_data(data: object) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for text in _iter_js_strings(data):
        normalized = _normalize_html_token_text(text)
        for m in _TFS_TFU_PAIR_RE.finditer(normalized):
            tfs = urllib.parse.unquote(m.group("tfs"))
            tfu = urllib.parse.unquote(m.group("tfu"))
            if not tfs or not tfu:
                continue
            if " " in tfs or " " in tfu:
                continue
            if not _B64ISH_RE.fullmatch(tfs) or not _B64ISH_RE.fullmatch(tfu):
                continue
            if tfs.startswith(_TFS2_PREFIXES) and tfu.startswith(_TFU2_PREFIXES) and len(tfu) >= 40:
                pairs.append((tfs, tfu))
    return pairs


def _extract_booking_tfs_from_html(html: str) -> Optional[str]:
    """Extract explicit booking?tfs=... if present."""
    html = _normalize_html_token_text(html)
    m = _BOOKING_TFS_RE.search(html)
    if not m:
        return None
    return urllib.parse.unquote(m.group("tfs"))


def _extract_booking_tfs_from_js_data(data: object) -> Optional[str]:
    for text in _iter_js_strings(data):
        normalized = _normalize_html_token_text(text)
        m = _BOOKING_TFS_RE.search(normalized)
        if m:
            return urllib.parse.unquote(m.group("tfs"))
    return None


def _score_token_bytes_match(selected: Itinerary, token_b: bytes) -> int:
    """
    Score decoded bytes against selected itinerary using substrings.
    This works without knowing the exact protobuf fields.
    """
    score = 0

    for code in {getattr(selected, "departure_airport", None), getattr(selected, "arrival_airport", None)}:
        if isinstance(code, str) and code and code.encode("utf-8") in token_b:
            score += 3

    flights = getattr(selected, "flights", []) or []
    for f in flights:
        carrier = getattr(f, "airline", None) or getattr(f, "airline_code", None)
        fn = getattr(f, "flight_number", None)
        orig = getattr(f, "departure_airport", None)
        dest = getattr(f, "arrival_airport", None)

        if isinstance(carrier, str) and carrier.encode("utf-8") in token_b:
            score += 2
        if isinstance(fn, str) and fn.encode("utf-8") in token_b:
            score += 2
        if isinstance(orig, str) and orig.encode("utf-8") in token_b:
            score += 1
        if isinstance(dest, str) and dest.encode("utf-8") in token_b:
            score += 1

    return score


def _rank_followup_pairs(
    selected: Itinerary,
    pairs: list[tuple[str, str]],
    *,
    limit: int = 5,
) -> list[tuple[str, str, int]]:
    scored: list[tuple[str, str, int]] = []
    for tfs2, tfu2 in pairs:
        try:
            b = _b64url_decode_bytes(tfs2)
        except Exception:
            continue
        s = _score_token_bytes_match(selected, b)
        if s > 0:
            scored.append((tfs2, tfu2, s))
    scored.sort(key=lambda item: item[2], reverse=True)
    return scored[:limit]


def _infer_booking_tfs_from_embedded_strings(
    html: str,
    selected: Itinerary,
    *,
    limit: int = 10,
) -> list[tuple[str, int]]:
    """
    When no explicit booking link exists, try to infer a booking 'tfs' token from embedded base64-ish strings.
    """
    scored: list[tuple[str, int]] = []

    html = _normalize_html_token_text(html)

    # Limit candidates to keep CPU reasonable in CI.
    candidates = _B64_STRING_RE.findall(html)
    if len(candidates) > 5000:
        candidates = candidates[:5000]

    for tok in candidates:
        # Quick filter: must look like base64-ish content.
        if len(tok) < 80:
            continue
        if " " in tok:
            continue
        if not _B64ISH_RE.fullmatch(tok):
            continue

        try:
            b = _b64url_decode_bytes(tok)
        except Exception:
            continue

        s = _score_token_bytes_match(selected, b)
        if s >= 6:
            scored.append((tok, s))

    scored.sort(key=lambda item: item[1], reverse=True)
    best_score = scored[0][1] if scored else 0
    logger.info("RT JS flow: inferred booking token best_score=%d.", best_score)
    return scored[:limit]


def _normalize_html_token_text(html_text: str) -> str:
    """Normalize HTML/JS escaping for token extraction."""
    normalized = html_text.replace("\\u0026", "&").replace("\\u003d", "=")
    return html.unescape(normalized)


def _dump_listing_debug(html: str) -> None:
    """Dump listing HTML/snippet to help diagnose missing follow-up tokens."""
    if os.getenv("FAST_FLIGHTS_DUMP_HTML", "1").lower() not in ("1", "true", "yes"):
        return

    try:
        with open("/tmp/fast_flights_listing.html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass

    try:
        idx = html.find("tfs")
        if idx == -1:
            snippet = html[:4000]
        else:
            start = max(0, idx - 1500)
            end = min(len(html), idx + 4000)
            snippet = html[start:end]
        with open("/tmp/fast_flights_listing_snippet.txt", "w", encoding="utf-8") as f:
            f.write(snippet)
    except Exception:
        pass


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
        "tfu": _TFU_LISTING_DEFAULT,
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
            def _decode_followup_from_flights(tfs_value: str) -> Optional[DecodedResult]:
                try:
                    res_followup = _fetch_with_mode(
                        {"tfs": tfs_value, "hl": params["hl"], "tfu": _TFU_LISTING_DEFAULT, "curr": params["curr"]},
                        mode=mode,
                        req_kwargs=req_kwargs,
                    )
                    return _decode_js_result_from_html(res_followup.text)
                except Exception as err:
                    logger.warning("RT JS flow: flights endpoint decode failed: %s", err)
                    return None

            logger.info("RT JS flow: listing outbound options (request #1).")
            outbound_raw = _extract_js_data(res1.text)
            outbound_decoded = ResultDecoder.decode(outbound_raw)

            out_best = getattr(outbound_decoded, "best", []) or []
            out_other = getattr(outbound_decoded, "other", []) or []
            outbound_itineraries: list[Itinerary] = list(out_best) + list(out_other)

            logger.info("RT JS flow: decoded outbound itineraries=%d.", len(outbound_itineraries))

            target_minutes = _parse_target_time(target_time)
            selected_outbound = _select_outbound(outbound_itineraries, target_minutes)

            # Path A: follow-up pairs (tfs2, tfu2) embedded directly in listing HTML
            pairs = _extract_followup_pairs_from_html(res1.text)
            logger.info("RT JS flow: follow-up pairs found in listing HTML=%d.", len(pairs))
            if not pairs:
                pairs = _extract_followup_pairs_from_js_data(outbound_raw)
                logger.info("RT JS flow: follow-up pairs found in JS data=%d.", len(pairs))

            ranked_pairs = _rank_followup_pairs(selected_outbound, pairs)
            best_pair_score = ranked_pairs[0][2] if ranked_pairs else 0
            logger.info("RT JS flow: best follow-up match score=%d.", best_pair_score)

            for tfs2, tfu2, score in ranked_pairs:
                logger.info("RT JS flow: issuing follow-up request #2 via flights endpoint.")
                res2 = _fetch_with_mode(
                    {"tfs": tfs2, "hl": params["hl"], "tfu": tfu2, "curr": params["curr"]},
                    mode=mode,
                    req_kwargs=req_kwargs,
                )
                try:
                    inbound_decoded = _decode_js_result_from_html(res2.text)
                except Exception as err:
                    logger.warning(
                        "RT JS flow: follow-up decode failed (score=%d): %s",
                        score,
                        err,
                    )
                    continue
                logger.info("RT JS flow: booking page decode succeeded via flights endpoint.")
                return RoundTripDecodedResult(
                    outbound=outbound_decoded,
                    inbound=inbound_decoded,
                    selected_outbound_ref=tfs2,
                    selected_outbound=selected_outbound,
                )

            # Path B: explicit booking deep link present in listing HTML
            booking_tfs = _extract_booking_tfs_from_html(res1.text)
            if not booking_tfs:
                booking_tfs = _extract_booking_tfs_from_js_data(outbound_raw)
            if booking_tfs:
                logger.warning("RT JS flow: explicit booking?tfs token detected; score=explicit.")
                resb = fetch_booking(booking_tfs, request_kwargs=req_kwargs)
                try:
                    inbound_decoded = _decode_js_result_from_html(resb.text)
                except Exception as err:
                    logger.warning("RT JS flow: explicit booking decode failed: %s", err)
                else:
                    logger.info("RT JS flow: booking page decode succeeded (explicit token).")
                    return RoundTripDecodedResult(
                        outbound=outbound_decoded,
                        inbound=inbound_decoded,
                        selected_outbound_ref=booking_tfs,
                        selected_outbound=selected_outbound,
                    )
                fallback_decoded = _decode_followup_from_flights(booking_tfs)
                if fallback_decoded:
                    logger.info("RT JS flow: flights endpoint decode succeeded (explicit token).")
                    return RoundTripDecodedResult(
                        outbound=outbound_decoded,
                        inbound=fallback_decoded,
                        selected_outbound_ref=booking_tfs,
                        selected_outbound=selected_outbound,
                    )

            # Path C: infer booking token from embedded base64-ish strings
            inferred_candidates = _infer_booking_tfs_from_embedded_strings(
                res1.text,
                selected_outbound,
            )
            seen_inferred: set[str] = set()
            for inferred_booking_tfs, inferred_score in inferred_candidates:
                if inferred_booking_tfs in seen_inferred:
                    continue
                seen_inferred.add(inferred_booking_tfs)
                logger.warning(
                    "RT JS flow: inferred booking token; score=%d; using booking?tfs flow.",
                    inferred_score,
                )
                resb = fetch_booking(inferred_booking_tfs, request_kwargs=req_kwargs)
                try:
                    inbound_decoded = _decode_js_result_from_html(resb.text)
                except Exception as err:
                    logger.warning(
                        "RT JS flow: inferred booking decode failed (score=%d): %s",
                        inferred_score,
                        err,
                    )
                else:
                    logger.info("RT JS flow: booking page decode succeeded (inferred token).")
                    return RoundTripDecodedResult(
                        outbound=outbound_decoded,
                        inbound=inbound_decoded,
                        selected_outbound_ref=inferred_booking_tfs,
                        selected_outbound=selected_outbound,
                    )
                fallback_decoded = _decode_followup_from_flights(inferred_booking_tfs)
                if fallback_decoded:
                    logger.info("RT JS flow: flights endpoint decode succeeded (inferred token).")
                    return RoundTripDecodedResult(
                        outbound=outbound_decoded,
                        inbound=fallback_decoded,
                        selected_outbound_ref=inferred_booking_tfs,
                        selected_outbound=selected_outbound,
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
                    return RoundTripDecodedResult(
                        outbound=outbound_decoded,
                        inbound=fallback_result,
                        selected_outbound_ref="one-way-fallback",
                        selected_outbound=selected_outbound,
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
