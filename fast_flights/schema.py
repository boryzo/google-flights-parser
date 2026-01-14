from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional


@dataclass
class Result:
    current_price: Literal["low", "typical", "high"]
    flights: List[Flight]


@dataclass
class Segment:
    origin: str
    destination: str
    carrier_code: str
    flight_number: str
    date: str  # YYYY-MM-DD


@dataclass
class Flight:
    is_best: bool
    name: str
    departure: str
    arrival: str
    arrival_time_ahead: str
    duration: str
    stops: int
    delay: Optional[str]
    price: str
    trip_type: Optional[str] = None
    stops_count: Optional[int] = None
    stop_airports: Optional[List[str]] = None
    duration_minutes: Optional[int] = None
    itinerary_raw: Optional[str] = None
    segments: Optional[List[Segment]] = None
    segments_count: Optional[int] = None
    inferred_stops_from_itinerary: Optional[int] = None
    airline_logo_url: Optional[str] = None
