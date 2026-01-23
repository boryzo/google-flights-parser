# core_helpers.py

import base64
import html
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .decoder import DecodedResult, Itinerary, ResultDecoder
from .schema import Segment
from .flights_impl import segments_from_tfs
from . import flights_pb2 as PB

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

_B64_STRING_RE = re.compile(r'["\']([A-Za-z0-9\-_+/=]{60,})["\']')
_B64ISH_RE = re.compile(r"^[A-Za-z0-9\-_+/=]+$")


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


def _iter_js_lists(data: object, *, limit: int = 2000) -> list[list]:
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


def _looks_like_itinerary(el: object) -> bool:
    if not isinstance(el, list) or len(el) < 2:
        return False
    details = el[0]
    summary = el[1]
    if not isinstance(details, list) or len(details) < 9:
        return False
    if not isinstance(summary, list) or len(summary) < 2:
        return False
    return True


def _looks_like_itinerary_list(candidate: object) -> bool:
    if not isinstance(candidate, list) or not candidate:
        return False
    if not all(_looks_like_itinerary(el) for el in candidate):
        return False
    for el in candidate:
        try:
            details = el[0]
            dep = details[3]
            arr = details[6]
        except Exception:
            continue
        if isinstance(dep, str) and len(dep) == 3 and isinstance(arr, str) and len(arr) == 3:
            return True
    return False


def _wrap_itinerary_list(candidate: list) -> list:
    # ResultDecoder expects [2][0] and [3][0] to be lists of itineraries.
    return [None, None, [candidate], [[]]]


def _looks_like_result_root(candidate: list) -> bool:
    if not isinstance(candidate, list) or len(candidate) < 4:
        return False
    if not isinstance(candidate[2], list) or not isinstance(candidate[3], list):
        return False
    # Must have nested list slots for itineraries.
    if candidate[2]:
        if not isinstance(candidate[2][0], list):
            return False
        if not any(_looks_like_itinerary(el) for el in candidate[2][0]):
            return False
    if candidate[3]:
        if not isinstance(candidate[3][0], list):
            return False
        if not any(_looks_like_itinerary(el) for el in candidate[3][0]):
            return False
    return True


def _decoded_result_has_itineraries(decoded: DecodedResult) -> bool:
    for items in (getattr(decoded, "best", None), getattr(decoded, "other", None)):
        if not items or not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if getattr(item, "departure_airport", None):
                return True
    return False


def _decoded_result_score(decoded: DecodedResult) -> int:
    """
    Prefer results with richer itinerary details (times, flights, flight numbers).
    Used to select the best candidate when multiple ds:* blocks exist.
    """
    score = 0
    items: list[Itinerary] = []
    for group in (getattr(decoded, "best", None), getattr(decoded, "other", None)):
        if group and isinstance(group, (list, tuple)):
            items.extend(group)

    for item in items:
        if not item:
            continue
        # Base signal for a usable itinerary
        score += 10
        if getattr(item, "departure_time", None):
            score += 3
        if getattr(item, "arrival_time", None):
            score += 3
        flights = getattr(item, "flights", None) or []
        if flights:
            score += 5
        for flight in flights:
            if getattr(flight, "airline", None):
                score += 1
            if getattr(flight, "flight_number", None):
                score += 1

    return score


def _decode_js_result_from_html(html_text: str) -> DecodedResult:
    candidates = _extract_js_data_candidates(html_text)
    if not candidates:
        candidates = [_extract_js_data(html_text)]

    last_error: Exception | None = None
    best_decoded: DecodedResult | None = None
    best_score = -1
    for data in candidates:
        for candidate in _iter_js_lists(data):
            if not _looks_like_result_root(candidate):
                continue
            try:
                decoded = ResultDecoder.decode(candidate)
            except Exception as err:
                last_error = err
                continue
            if not _decoded_result_has_itineraries(decoded):
                continue
            score = _decoded_result_score(decoded)
            if score > best_score:
                best_decoded = decoded
                best_score = score

    if best_decoded is not None:
        return best_decoded

    for data in candidates:
        for candidate in _iter_js_lists(data):
            if not _looks_like_itinerary_list(candidate):
                continue
            try:
                decoded = ResultDecoder.decode(_wrap_itinerary_list(candidate))
            except Exception as err:
                last_error = err
                continue
            if not _decoded_result_has_itineraries(decoded):
                continue
            score = _decoded_result_score(decoded)
            if score > best_score:
                best_decoded = decoded
                best_score = score

    if best_decoded is not None:
        return best_decoded

    if last_error:
        raise last_error
    raise RuntimeError("No decodable js data candidates found.")


def _decoded_result_has_route(decoded: DecodedResult, dep: str, arr: str) -> bool:
    if not decoded:
        return False
    for items in (getattr(decoded, "best", None), getattr(decoded, "other", None)):
        if not items or not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if (
                getattr(item, "departure_airport", None) == dep
                and getattr(item, "arrival_airport", None) == arr
            ):
                return True
    return False


def _segments_payload_from_tfs(tfs_value: str | None) -> list[list[dict]] | None:
    if not tfs_value:
        return None
    try:
        segments_by_leg = segments_from_tfs(tfs_value)
    except Exception as err:
        logger.debug("RT JS flow: segments_from_tfs failed: %s", err)
        return None
    payload: list[list[dict]] = []
    for leg in segments_by_leg:
        if not leg:
            payload.append([])
            continue
        normalized_leg: list[dict] = []
        for s in leg:
            origin_code, origin_name = _normalize_airport_value(getattr(s, "origin", None))
            destination_code, destination_name = _normalize_airport_value(getattr(s, "destination", None))
            if not origin_code or not destination_code:
                continue
            entry = {
                "origin": origin_code,
                "destination": destination_code,
                "carrier_code": s.carrier_code,
                "flight_number": s.flight_number,
                "date": s.date,
            }
            if origin_name:
                entry["origin_airport_name"] = origin_name
            if destination_name:
                entry["destination_airport_name"] = destination_name
            normalized_leg.append(entry)
        payload.append(normalized_leg)
    return payload


def _pick_iata_code(value: object, alt_value: object | None = None) -> str | None:
    """Return 3-letter IATA code from primary/alt values if present."""
    for candidate in (value, alt_value):
        if isinstance(candidate, str) and re.fullmatch(r"[A-Z]{3}", candidate):
            return candidate
    return None


def _normalize_airport_value(value: object) -> tuple[str | None, str | None]:
    """Return (iata_code, airport_name) from a raw airport field."""
    if not isinstance(value, str):
        return None, None
    cleaned = value.strip()
    if re.fullmatch(r"[A-Z]{3}", cleaned):
        return cleaned, None
    match = re.search(r"\b([A-Z]{3})\b", cleaned.upper())
    if match:
        return match.group(1), cleaned
    return None, cleaned or None


def _apply_round_trip_total_price(inbound: DecodedResult, selected_outbound: Itinerary) -> None:
    """
    When round-trip follow-up returns per-leg prices, normalize inbound prices
    to the total round-trip price from the selected outbound.
    """
    summary = getattr(selected_outbound, "itinerary_summary", None)
    total_price = getattr(summary, "price", None)
    total_currency = getattr(summary, "currency", None)
    if total_price is None:
        return

    for group in (getattr(inbound, "best", None), getattr(inbound, "other", None)):
        if not group or not isinstance(group, (list, tuple)):
            continue
        for itinerary in group:
            it_summary = getattr(itinerary, "itinerary_summary", None)
            if it_summary is None:
                continue
            it_summary.price = total_price
            if total_currency:
                it_summary.currency = total_currency


def _b64url_decode_bytes(s: str) -> bytes:
    s = s.strip().replace("-", "+").replace("_", "/")
    pad = (-len(s) % 4)
    if pad:
        s += "=" * pad
    return base64.urlsafe_b64decode(s.encode("utf-8"))


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


def _extract_tfs_candidates_from_html(
    html_text: str,
    origin: str,
    destination: str,
    *,
    limit: int = 10,
) -> list[str]:
    """
    Try to find tfs tokens in HTML that decode into PB.Info with the expected route.
    This is used for /search follow-ups when explicit pairs are missing.
    """
    normalized = _normalize_html_token_text(html_text)
    candidates = _B64_STRING_RE.findall(normalized)
    if len(candidates) > 5000:
        candidates = candidates[:5000]
    out: list[str] = []
    for tok in candidates:
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
        info = PB.Info()
        try:
            info.ParseFromString(b)
        except Exception:
            continue
        if info.trip != PB.Trip.ROUND_TRIP:
            continue
        if len(info.data) < 2:
            continue
        fd_out = info.data[0]
        fd_in = info.data[1]
        if (
            fd_out.from_flight.airport == origin
            and fd_out.to_flight.airport == destination
            and fd_in.from_flight.airport == destination
            and fd_in.to_flight.airport == origin
        ):
            out.append(tok)
            if len(out) >= limit:
                break
    return out


def _extract_tfs_candidates_from_js_data(
    data: object,
    origin: str,
    destination: str,
    *,
    limit: int = 10,
) -> list[str]:
    candidates: list[str] = []
    for text in _iter_js_strings(data):
        if not text:
            continue
        token_pool: list[str] = []
        if len(text) >= 80 and _B64ISH_RE.fullmatch(text):
            token_pool.append(text)
        token_pool.extend(_B64_STRING_RE.findall(text))
        for tok in token_pool:
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
            info = PB.Info()
            try:
                info.ParseFromString(b)
            except Exception:
                continue
            if info.trip != PB.Trip.ROUND_TRIP:
                continue
            if len(info.data) < 2:
                continue
            fd_out = info.data[0]
            fd_in = info.data[1]
            if (
                fd_out.from_flight.airport == origin
                and fd_out.to_flight.airport == destination
                and fd_in.from_flight.airport == destination
                and fd_in.to_flight.airport == origin
            ):
                candidates.append(tok)
                if len(candidates) >= limit:
                    return candidates
    return candidates


def _normalize_html_token_text(html_text: str) -> str:
    """Normalize HTML/JS escaping for token extraction."""
    normalized = html_text.replace("\\u0026", "&").replace("\\u003d", "=")
    return html.unescape(normalized)


def _dump_listing_debug(html_text: str) -> None:
    """Dump listing HTML/snippet to help diagnose missing follow-up tokens."""
    if os.getenv("FAST_FLIGHTS_DUMP_HTML", "1").lower() not in ("1", "true", "yes"):
        return

    try:
        with open("/tmp/fast_flights_listing.html", "w", encoding="utf-8") as f:
            f.write(html_text)
    except Exception:
        pass

    try:
        idx = html_text.find("tfs")
        if idx == -1:
            snippet = html_text[:4000]
        else:
            start = max(0, idx - 1500)
            end = min(len(html_text), idx + 4000)
            snippet = html_text[start:end]
        with open("/tmp/fast_flights_listing_snippet.txt", "w", encoding="utf-8") as f:
            f.write(snippet)
    except Exception:
        pass


def _parse_target_time(target_time: Optional[str]) -> Optional[int]:
    if not target_time:
        return None
    try:
        parts = target_time.split(":")
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        return None
    return hours * 60 + minutes


def _select_outbound(itineraries: list[Itinerary], target_time_minutes: Optional[int]) -> Itinerary:
    if not itineraries:
        raise RuntimeError("No outbound itineraries found")
    if target_time_minutes is None:
        return itineraries[0]

    def _time_or_default(item: Itinerary) -> int:
        dep = getattr(item, "departure_time", None)
        if isinstance(dep, (list, tuple)) and len(dep) >= 2:
            return int(dep[0]) * 60 + int(dep[1])
        return 0

    best = min(
        itineraries,
        key=lambda item: abs(_time_or_default(item) - target_time_minutes),
    )
    return best
