from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode


SEGMENT_RE = re.compile(
    r"^(?P<orig>[A-Z]{3})-(?P<dest>[A-Z]{3})-(?P<carrier>[A-Z0-9]{2,3})-(?P<flight>\d{1,5})-(?P<date>\d{8})$"
)

DURATION_RE = re.compile(r"(?P<h>\d+)\s*hr\s*(?P<m>\d+)\s*min", re.IGNORECASE)

STOPS_RE = re.compile(
    r"(?P<count>\d+)\s+stops?\s+in\s+(?P<airports>[A-Z]{3}(?:,\s*[A-Z]{3})*)",
    re.IGNORECASE,
)

PRICE_ARIA_RE = re.compile(
    r"(?P<amount>[\d,.\s]+)\s*(?P<currency>[A-Za-z]{3}|dollars|zloty|euros|pounds)\b",
    re.IGNORECASE,
)

CO2_RE = re.compile(r"\b\d+[.,]?\d*\s*kg\s*CO2e\b", re.IGNORECASE)
TRIP_TYPE_RE = re.compile(r"\b(round trip|one way)\b", re.IGNORECASE)
OPERATED_BY_RE = re.compile(r"\bOperated by\b.*", re.IGNORECASE)
STOPS_TEXT_RE = re.compile(r"\bstops?\s+in\s+[A-Z]{3}", re.IGNORECASE)
TIME_RE = re.compile(r"\b\d+\s*hr\s*\d+\s*min\b", re.IGNORECASE)


@dataclass
class Segment:
    origin: str
    destination: str
    carrier_code: str
    flight_number: str
    date: str  # YYYY-MM-DD


@dataclass
class Offer:
    price_text: Optional[str] = None
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None
    price_aria_label: Optional[str] = None

    trip_type: Optional[str] = None

    airlines_text: Optional[str] = None
    operated_by: Optional[str] = None

    stops_text: Optional[str] = None
    stops_count: Optional[int] = None
    stop_airports: Optional[list[str]] = None

    duration_text: Optional[str] = None
    duration_minutes: Optional[int] = None

    co2_current_g: Optional[int] = None
    co2_typical_g: Optional[int] = None
    co2_savings_g: Optional[int] = None
    co2_percent_diff: Optional[int] = None
    co2_display_text: Optional[str] = None

    travelimpact_url: Optional[str] = None
    itinerary_raw: Optional[str] = None
    segments: Optional[list[Segment]] = None
    segments_count: Optional[int] = None
    inferred_stops_from_itinerary: Optional[int] = None

    airline_logo_url: Optional[str] = None


def _normalize_currency(s: str) -> Optional[str]:
    if not s:
        return None
    ss = s.strip().lower()
    if ss in ("usd", "us dollars", "dollars"):
        return "USD"
    if ss in ("eur", "euros"):
        return "EUR"
    if ss in ("gbp", "pounds"):
        return "GBP"
    if ss in ("pln", "zloty", "zł", "zl"):
        return "PLN"
    if re.fullmatch(r"[a-z]{3}", ss):
        return ss.upper()
    return None


def _parse_price(span: Optional[LexborNode]) -> tuple[Optional[str], Optional[float], Optional[str], Optional[str]]:
    price_text = span.text().strip() if span else None
    aria = span.attributes.get("aria-label") if span else None

    amount = None
    curr = None

    if aria:
        m = PRICE_ARIA_RE.search(aria)
        if m:
            raw_amount = m.group("amount").replace(" ", "")
            try:
                amount = float(raw_amount.replace(",", ""))
            except ValueError:
                amount = None
            curr = _normalize_currency(m.group("currency"))

    if curr is None and price_text:
        if price_text.startswith("$"):
            curr = "USD"
        elif price_text.startswith("€"):
            curr = "EUR"
        elif price_text.startswith("£"):
            curr = "GBP"
        elif "zł" in price_text or "PLN" in price_text:
            curr = "PLN"

    if amount is None and price_text:
        raw = re.sub(r"[^\d,\.]", "", price_text)
        if raw:
            try:
                amount = float(raw.replace(",", ""))
            except ValueError:
                amount = None

    return price_text, amount, curr, aria


def _parse_duration(text: str) -> tuple[Optional[str], Optional[int]]:
    if not text:
        return None, None
    t = text.strip()
    m = DURATION_RE.search(t)
    if not m:
        return t, None
    h = int(m.group("h"))
    mm = int(m.group("m"))
    return t, h * 60 + mm


def _parse_stops(text: str) -> tuple[Optional[str], Optional[int], Optional[list[str]]]:
    if not text:
        return None, None, None
    t = text.strip()
    m = STOPS_RE.search(t)
    if not m:
        if "nonstop" in t.lower():
            return t, 0, []
        return t, None, None
    cnt = int(m.group("count"))
    airports = [x.strip().upper() for x in m.group("airports").split(",")]
    return t, cnt, airports


def _parse_travelimpact_url(url: Optional[str]) -> tuple[Optional[str], Optional[list[Segment]]]:
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
        m = SEGMENT_RE.match(part)
        if not m:
            continue
        date_raw = m.group("date")
        try:
            date_iso = datetime.strptime(date_raw, "%Y%m%d").date().isoformat()
        except ValueError:
            date_iso = date_raw

        segments.append(
            Segment(
                origin=m.group("orig"),
                destination=m.group("dest"),
                carrier_code=m.group("carrier"),
                flight_number=m.group("flight"),
                date=date_iso,
            )
        )
    return itinerary, segments if segments else None


def _text_or_none(node: Optional[LexborNode]) -> Optional[str]:
    if not node:
        return None
    txt = node.text().strip()
    return txt or None


def _find_match(pattern: re.Pattern[str], text: str) -> Optional[str]:
    match = pattern.search(text)
    return match.group(0).strip() if match else None


def parse_advanced_response(html: str) -> dict[str, Any]:
    parser = LexborHTMLParser(html)

    query_meta: dict[str, Any] = {}
    m = re.search(r'"GDN"\s*,0\]\]\],\[\[\["JFK"', html)
    if m:
        query_meta["hint_route_found"] = "GDN->JFK"

    dates = sorted(set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", html)))
    if dates:
        query_meta["dates_found"] = dates

    impact_divs = parser.css("[data-travelimpactmodelwebsiteurl]")
    offers: list[Offer] = []

    for impact in impact_divs:
        url = impact.attributes.get("data-travelimpactmodelwebsiteurl")
        itinerary_raw, segments = _parse_travelimpact_url(url)

        offer = Offer(
            travelimpact_url=url,
            itinerary_raw=itinerary_raw,
            segments=segments,
            segments_count=len(segments) if segments else None,
            inferred_stops_from_itinerary=(len(segments) - 1) if segments else None,
        )

        def _int_attr(name: str) -> Optional[int]:
            v = impact.attributes.get(name)
            if v is None:
                return None
            try:
                return int(v)
            except ValueError:
                return None

        offer.co2_current_g = _int_attr("data-co2currentflight")
        offer.co2_typical_g = _int_attr("data-co2typical")
        offer.co2_savings_g = _int_attr("data-co2savings")
        offer.co2_percent_diff = _int_attr("data-percentagediff")

        offer.co2_display_text = _text_or_none(impact.css_first(".AdWm1c"))
        if not offer.co2_display_text:
            offer.co2_display_text = _find_match(CO2_RE, impact.text())

        card = impact
        for _ in range(10):
            if not card:
                break
            if card.css_first('span[role="text"][aria-label]') or card.css_first(".h1fkLb"):
                break
            card = card.parent

        price_span = None
        if card:
            price_span = card.css_first('span[role="text"][aria-label][data-gs]')
            if not price_span:
                price_span = card.css_first('span[role="text"][aria-label]')
        if not price_span and card and card.parent:
            price_span = card.parent.css_first('span[role="text"][aria-label]')

        if price_span:
            (
                offer.price_text,
                offer.price_amount,
                offer.price_currency,
                offer.price_aria_label,
            ) = _parse_price(price_span)

        if card:
            trip_type = _find_match(TRIP_TYPE_RE, card.text())
            if trip_type:
                offer.trip_type = trip_type.lower()

        airlines_block = card.css_first(".h1fkLb") if card else None
        if airlines_block:
            spans = airlines_block.css("span")
            for span in spans:
                text = _text_or_none(span)
                if text:
                    offer.airlines_text = text
                    break
            operated = _find_match(OPERATED_BY_RE, airlines_block.text())
            if operated:
                offer.operated_by = operated
        elif card:
            text = _find_match(re.compile(r"[A-Za-z].*,.*"), card.text())
            if text:
                offer.airlines_text = text

        if card:
            stops_node = _find_match(STOPS_TEXT_RE, card.text())
            if stops_node:
                offer.stops_text, offer.stops_count, offer.stop_airports = _parse_stops(stops_node)

            dur_node = _find_match(TIME_RE, card.text())
            if dur_node:
                offer.duration_text, offer.duration_minutes = _parse_duration(dur_node)

        if card:
            logo_div = card.css_first('[style*="airline_logos/70px"]')
            if logo_div:
                style = logo_div.attributes.get("style", "")
                mlogo = re.search(
                    r"url\((https://www\.gstatic\.com/flights/airline_logos/70px/[^)]+)\)",
                    style,
                )
                if mlogo:
                    offer.airline_logo_url = mlogo.group(1)

        offers.append(offer)

    uniq: dict[str, Offer] = {}
    for offer in offers:
        key = offer.travelimpact_url or json.dumps(asdict(offer), sort_keys=True)
        if key not in uniq:
            uniq[key] = offer

    return {
        "meta": query_meta,
        "offers_count": len(uniq),
        "offers": [asdict(v) for v in uniq.values()],
    }
