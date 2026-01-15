import base64
import json
from unittest import TestCase
from unittest.mock import patch

from fast_flights import FlightData, Passengers
from fast_flights.filter import TFSData
from fast_flights import flights_pb2 as PB
from fast_flights import core


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def _make_itinerary_summary_b64(price: int, currency: str = "USD") -> str:
    summary = PB.ItinerarySummary()
    summary.flights = "AA123"
    summary.price.price = price
    summary.price.currency = currency
    return base64.b64encode(summary.SerializeToString()).decode("utf-8")


def _make_flight_raw(
    *,
    dep_time: tuple[int, int],
    arr_time: tuple[int, int],
    dep_date: tuple[int, int, int],
    arr_date: tuple[int, int, int],
    dep_airport: str,
    arr_airport: str,
    airline_code: str,
    flight_number: str,
):
    flight = [None] * 23
    flight[2] = "Test Operator"
    flight[3] = dep_airport
    flight[4] = f"{dep_airport} Airport"
    flight[5] = arr_airport
    flight[6] = f"{arr_airport} Airport"
    flight[8] = list(dep_time)
    flight[10] = list(arr_time)
    flight[11] = 90
    flight[14] = "32 in"
    flight[15] = [[airline_code, flight_number, None, "Test Airline"]]
    flight[17] = "A320"
    flight[20] = list(dep_date)
    flight[21] = list(arr_date)
    flight[22] = [airline_code, flight_number, None, "Test Airline"]
    return flight


def _make_itinerary_raw(selection_ref: str, price: int) -> list:
    flight = _make_flight_raw(
        dep_time=(8, 0),
        arr_time=(10, 0),
        dep_date=(2025, 1, 20),
        arr_date=(2025, 1, 20),
        dep_airport="SFO",
        arr_airport="LAX",
        airline_code="AA",
        flight_number="123",
    )

    details = [None] * 14
    details[0] = "AA"
    details[1] = ["Test Airline"]
    details[2] = [flight]
    details[3] = "SFO"
    details[4] = [2025, 1, 20]
    details[5] = [8, 0]
    details[6] = "LAX"
    details[7] = [2025, 1, 20]
    details[8] = [10, 0]
    details[9] = 120
    details[13] = []

    summary_b64 = _make_itinerary_summary_b64(price)
    summary = [None, summary_b64]
    return [details, summary, selection_ref]


def _wrap_js_data(raw: list) -> str:
    payload = json.dumps(raw)
    script = f"AF_initDataCallback({{key: 'ds:1', data:{payload}, errorHasStatus: false,}});"
    return f'<html><head><script class="ds:1">{script}</script></head><body></body></html>'


class RoundTripFlowTests(TestCase):
    def test_selected_outbound_changes_tfs(self) -> None:
        tfs = TFSData.from_interface(
            flight_data=[
                FlightData(date="2025-01-20", from_airport="SFO", to_airport="LAX"),
                FlightData(date="2025-01-25", from_airport="LAX", to_airport="SFO"),
            ],
            trip="round-trip",
            passengers=Passengers(adults=1),
            seat="economy",
        )

        with_ref = tfs.with_selected_outbound("OUTBOUND_REF_123456")
        self.assertNotEqual(tfs.as_b64(), with_ref.as_b64())

    def test_round_trip_followup_parses_inbound(self) -> None:
        outbound_raw = [None, None, [[_make_itinerary_raw("OUTBOUND_REF_123456", 12345)]], [[]]]
        inbound_raw = [None, None, [[_make_itinerary_raw("INBOUND_REF_654321", 23456)]], [[]]]

        outbound_html = _wrap_js_data(outbound_raw)
        inbound_html = _wrap_js_data(inbound_raw)

        tfs = TFSData.from_interface(
            flight_data=[
                FlightData(date="2025-01-20", from_airport="SFO", to_airport="LAX"),
                FlightData(date="2025-01-25", from_airport="LAX", to_airport="SFO"),
            ],
            trip="round-trip",
            passengers=Passengers(adults=1),
            seat="economy",
        )

        with patch.object(core, "fetch", side_effect=[_FakeResponse(outbound_html), _FakeResponse(inbound_html)]):
            result = core.get_flights_from_filter(
                tfs,
                data_source="js",
                target_time="08:00",
            )

        self.assertIsInstance(result, core.RoundTripDecodedResult)
        assert result is not None
        self.assertEqual(result.selected_outbound_ref, "OUTBOUND_REF_123456")
        self.assertGreater(len(result.inbound.best), 0)
