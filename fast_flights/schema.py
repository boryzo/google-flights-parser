from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Literal, Optional


@dataclass
class Result:
    current_price: Literal["low", "typical", "high"]
    flights: List[Flight]
    advanced: Optional[dict[str, Any]] = None


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
