from __future__ import annotations

import base64
from datetime import date, timedelta

import pytest

from fast_flights import FlightData, Passengers, create_filter
from fast_flights import flights_pb2 as PB

AIRPORTS = ["FRA", "MUC", "LHR", "SIN", "FCO", "JFK", "ICN", "EWR", "MAD", "WAW"]


def _decode_info(tfs_b64: str) -> PB.Info:
    raw = base64.b64decode(tfs_b64)
    msg = PB.Info()
    msg.ParseFromString(raw)
    return msg


def _gen_pairs(count: int, step: int) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    size = len(AIRPORTS)
    for i in range(count):
        origin = AIRPORTS[i % size]
        dest = AIRPORTS[(i + step) % size]
        if dest == origin:
            dest = AIRPORTS[(i + step + 1) % size]
        pairs.append((origin, dest))
    return pairs


def _date_from(base: date, offset: int) -> str:
    return (base + timedelta(days=offset)).strftime("%Y-%m-%d")


BASE_DATE = date(2026, 4, 1)

OW_CASES: list[tuple[str, str, str]] = []
for idx, (origin, dest) in enumerate(_gen_pairs(20, 3)):
    OW_CASES.append((origin, dest, _date_from(BASE_DATE, idx)))

RT_CASES: list[tuple[str, str, str, str]] = []
for idx, (origin, dest) in enumerate(_gen_pairs(15, 5)):
    depart = _date_from(BASE_DATE, 30 + idx)
    ret = _date_from(BASE_DATE, 30 + idx + 6)
    RT_CASES.append((origin, dest, depart, ret))

MC_CASES: list[tuple[str, str, str, str, str, str]] = []
size = len(AIRPORTS)
for idx in range(15):
    leg1_from = AIRPORTS[idx % size]
    leg1_to = AIRPORTS[(idx + 2) % size]
    leg2_from = AIRPORTS[(idx + 5) % size]
    leg2_to = AIRPORTS[(idx + 7) % size]
    if leg1_to == leg1_from:
        leg1_to = AIRPORTS[(idx + 3) % size]
    if leg2_to == leg2_from:
        leg2_to = AIRPORTS[(idx + 8) % size]
    date1 = _date_from(BASE_DATE, 60 + idx)
    date2 = _date_from(BASE_DATE, 60 + idx + 10)
    MC_CASES.append((leg1_from, leg1_to, date1, leg2_from, leg2_to, date2))


@pytest.mark.parametrize("origin,dest,depart_date", OW_CASES)
def test_bulk_one_way_tfs(origin: str, dest: str, depart_date: str) -> None:
    filt = create_filter(
        flight_data=[
            FlightData(date=depart_date, from_airport=origin, to_airport=dest),
        ],
        trip="one-way",
        passengers=Passengers(adults=1),
        seat="economy",
    )
    info = _decode_info(filt.as_b64().decode("utf-8"))
    assert info.trip == PB.Trip.ONE_WAY
    assert info.seat == PB.Seat.ECONOMY
    assert list(info.passengers).count(PB.Passenger.ADULT) == 1
    assert len(info.data) == 1
    assert info.data[0].date == depart_date
    assert info.data[0].from_flight.airport == origin
    assert info.data[0].to_flight.airport == dest


@pytest.mark.parametrize("origin,dest,depart_date,return_date", RT_CASES)
def test_bulk_round_trip_tfs(origin: str, dest: str, depart_date: str, return_date: str) -> None:
    filt = create_filter(
        flight_data=[
            FlightData(date=depart_date, from_airport=origin, to_airport=dest),
            FlightData(date=return_date, from_airport=dest, to_airport=origin),
        ],
        trip="round-trip",
        passengers=Passengers(adults=1),
        seat="economy",
    )
    info = _decode_info(filt.as_b64().decode("utf-8"))
    assert info.trip == PB.Trip.ROUND_TRIP
    assert info.seat == PB.Seat.ECONOMY
    assert list(info.passengers).count(PB.Passenger.ADULT) == 1
    assert len(info.data) == 2
    assert info.data[0].date == depart_date
    assert info.data[0].from_flight.airport == origin
    assert info.data[0].to_flight.airport == dest
    assert info.data[1].date == return_date
    assert info.data[1].from_flight.airport == dest
    assert info.data[1].to_flight.airport == origin


@pytest.mark.parametrize("leg1_from,leg1_to,date1,leg2_from,leg2_to,date2", MC_CASES)
def test_bulk_multicity_tfs(
    leg1_from: str,
    leg1_to: str,
    date1: str,
    leg2_from: str,
    leg2_to: str,
    date2: str,
) -> None:
    filt = create_filter(
        flight_data=[
            FlightData(date=date1, from_airport=leg1_from, to_airport=leg1_to),
            FlightData(date=date2, from_airport=leg2_from, to_airport=leg2_to),
        ],
        trip="multi-city",
        passengers=Passengers(adults=1),
        seat="economy",
    )
    info = _decode_info(filt.as_b64().decode("utf-8"))
    assert info.trip == PB.Trip.MULTI_CITY
    assert info.seat == PB.Seat.ECONOMY
    assert list(info.passengers).count(PB.Passenger.ADULT) == 1
    assert len(info.data) == 2
    assert info.data[0].date == date1
    assert info.data[0].from_flight.airport == leg1_from
    assert info.data[0].to_flight.airport == leg1_to
    assert info.data[1].date == date2
    assert info.data[1].from_flight.airport == leg2_from
    assert info.data[1].to_flight.airport == leg2_to
