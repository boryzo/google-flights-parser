import base64
import json
from typing import Optional
from unittest import TestCase
from unittest.mock import patch

from fast_flights import FlightData, Passengers
from fast_flights.filter import TFSData
from fast_flights import flights_pb2 as PB
from fast_flights import core


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def _make_itinerary_summary_b64(price: int, currency: str = "USD", flights: str = "AA123") -> str:
    summary = PB.ItinerarySummary()
    summary.flights = flights
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
    return _make_itinerary_raw_with_details(
        selection_ref=selection_ref,
        price=price,
    )


def _make_itinerary_raw_with_details(
    *,
    selection_ref: str,
    price: int,
    dep_time: Optional[tuple[int, int]] = (8, 0),
    arr_time: Optional[tuple[int, int]] = (10, 0),
    dep_date: tuple[int, int, int] = (2025, 1, 20),
    arr_date: tuple[int, int, int] = (2025, 1, 20),
    dep_airport: str = "SFO",
    arr_airport: str = "LAX",
    airline_code: str = "AA",
    flight_number: str = "123",
    include_flights: bool = True,
) -> list:
    flights = []
    if include_flights:
        flights = [
            _make_flight_raw(
                dep_time=dep_time or (0, 0),
                arr_time=arr_time or (0, 0),
                dep_date=dep_date,
                arr_date=arr_date,
                dep_airport=dep_airport,
                arr_airport=arr_airport,
                airline_code=airline_code,
                flight_number=flight_number,
            )
        ]

    details = [None] * 14
    details[0] = airline_code
    details[1] = ["Test Airline"]
    details[2] = flights
    details[3] = dep_airport
    details[4] = list(dep_date)
    details[5] = list(dep_time) if dep_time is not None else None
    details[6] = arr_airport
    details[7] = list(arr_date)
    details[8] = list(arr_time) if arr_time is not None else None
    details[9] = 120
    details[13] = []

    summary_b64 = _make_itinerary_summary_b64(price, flights=f"{airline_code}{flight_number}")
    summary = [None, summary_b64]
    return [details, summary, selection_ref]


def _make_result_raw(best_itineraries: list, other_itineraries: Optional[list] = None) -> list:
    return [None, None, [best_itineraries], [other_itineraries or []]]


def _wrap_js_data(raw: list, *, key: str = "ds:1") -> str:
    payload = json.dumps(raw)
    script = f"AF_initDataCallback({{key: '{key}', data:{payload}, errorHasStatus: false,}});"
    return f'<script class="{key}">{script}</script>'


def _wrap_js_page(*scripts: str) -> str:
    return f"<html><head>{''.join(scripts)}</head><body></body></html>"


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
        outbound_raw = _make_result_raw([_make_itinerary_raw("OUTBOUND_REF_123456", 12345)])
        inbound_raw = _make_result_raw([_make_itinerary_raw("INBOUND_REF_654321", 23456)])

        outbound_html = _wrap_js_page(_wrap_js_data(outbound_raw))
        inbound_html = _wrap_js_page(_wrap_js_data(inbound_raw))

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

    def test_round_trip_followup_prefers_full_inbound_details(self) -> None:
        outbound_raw = _make_result_raw(
            [
                _make_itinerary_raw_with_details(
                    selection_ref="OUTBOUND_REF_123456",
                    price=12345,
                    dep_airport="SFO",
                    arr_airport="LAX",
                    dep_time=(8, 0),
                    arr_time=(10, 0),
                    airline_code="AA",
                    flight_number="123",
                )
            ]
        )

        inbound_summary_raw = _make_result_raw(
            [
                _make_itinerary_raw_with_details(
                    selection_ref="INBOUND_REF_SUMMARY",
                    price=23456,
                    dep_airport="LAX",
                    arr_airport="SFO",
                    dep_time=None,
                    arr_time=None,
                    airline_code="UA",
                    flight_number="999",
                    include_flights=False,
                )
            ]
        )
        inbound_full_raw = _make_result_raw(
            [
                _make_itinerary_raw_with_details(
                    selection_ref="INBOUND_REF_FULL",
                    price=23456,
                    dep_airport="LAX",
                    arr_airport="SFO",
                    dep_time=(15, 30),
                    arr_time=(21, 45),
                    airline_code="DL",
                    flight_number="456",
                )
            ]
        )

        outbound_html = _wrap_js_page(_wrap_js_data(outbound_raw, key="ds:1"))
        inbound_html = _wrap_js_page(
            _wrap_js_data(inbound_summary_raw, key="ds:1"),
            _wrap_js_data(inbound_full_raw, key="ds:2"),
        )

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
        inbound = result.inbound.best[0]
        self.assertEqual(inbound.departure_airport, "LAX")
        self.assertEqual(inbound.arrival_airport, "SFO")
        self.assertEqual(tuple(inbound.departure_time), (15, 30))
        self.assertTrue(inbound.flights)
        self.assertEqual(inbound.flights[0].airline, "DL")
        self.assertEqual(inbound.flights[0].flight_number, "456")

    def test_itinerary_decoder_skips_non_list_padding_elements(self) -> None:
        """Regression: Google sometimes inserts integer padding (e.g. 0) into the
        itinerary list. ItineraryDecoder should skip them instead of failing."""
        valid_itinerary = _make_itinerary_raw("OUTBOUND_REF_123456", 12345)
        # Simulate padding elements that Google may insert (integers, None, etc.)
        itinerary_list_with_padding = [0, None, valid_itinerary, 0]
        outbound_raw = [None, None, [itinerary_list_with_padding], [[]]]
        inbound_raw = _make_result_raw([_make_itinerary_raw("INBOUND_REF_654321", 23456)])

        outbound_html = _wrap_js_page(_wrap_js_data(outbound_raw))
        inbound_html = _wrap_js_page(_wrap_js_data(inbound_raw))

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

    def test_itinerary_decoder_skips_short_or_malformed_elements(self) -> None:
        """Regression: non-itinerary list elements with short details (e.g. ['GDN', 0])
        must not crash ResultDecoder with '[0, 5]' out-of-range."""
        valid_itinerary = _make_itinerary_raw("OUTBOUND_REF_123456", 12345)
        malformed_short = [["GDN", 0], ["summary"]]
        outbound_raw = [None, None, [[malformed_short, valid_itinerary]], [[]]]
        inbound_raw = _make_result_raw([_make_itinerary_raw("INBOUND_REF_654321", 23456)])

        outbound_html = _wrap_js_page(_wrap_js_data(outbound_raw))
        inbound_html = _wrap_js_page(_wrap_js_data(inbound_raw))

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

    def test_round_trip_one_way_fallback_accepts_html_result(self) -> None:
        """When RT follow-up falls back to one-way and one-way falls back to HTML Result,
        convert Result into DecodedResult instead of failing."""
        outbound_raw = _make_result_raw([_make_itinerary_raw("OUTBOUND_REF_123456", 50000)])
        outbound_html = _wrap_js_page(_wrap_js_data(outbound_raw))
        inbound_html_only = (
            '<html><body><div jsname="IWWDBc"><ul class="Rk10dc"><li>'
            '<div class="sSHqwe tPgKwe ogfYpf"><span>Wizz Air</span></div>'
            '<span class="mv1WYe"><div>1:15 PM</div><div>5:10 PM</div></span>'
            '<div class="YMlIz FpEdX">PLN\xa0250</div>'
            '<div data-travelimpactmodelwebsiteurl="https://example.com?itinerary=LAX-SFO-W6-1654-20250125"></div>'
            "</li></ul></div></body></html>"
        )

        tfs = TFSData.from_interface(
            flight_data=[
                FlightData(date="2025-01-20", from_airport="SFO", to_airport="LAX"),
                FlightData(date="2025-01-25", from_airport="LAX", to_airport="SFO"),
            ],
            trip="round-trip",
            passengers=Passengers(adults=1),
            seat="economy",
        )

        with (
            patch.object(core, "fetch", side_effect=[_FakeResponse(outbound_html), _FakeResponse("<html></html>"), _FakeResponse(inbound_html_only)]),
            patch.object(core, "fetch_search", side_effect=RuntimeError("search blocked")),
        ):
            result = core.get_flights_from_filter(
                tfs,
                currency="PLN",
                data_source="auto",
                target_time="08:00",
            )

        self.assertIsInstance(result, core.RoundTripDecodedResult)
        assert result is not None
        self.assertEqual(result.selected_outbound_ref, "one-way-fallback")
        self.assertEqual(len(result.inbound.best), 1)
        self.assertEqual(result.inbound.best[0].departure_airport, "LAX")
        self.assertEqual(result.inbound.best[0].arrival_airport, "SFO")
        self.assertEqual(result.inbound.best[0].itinerary_summary.price, 500.0)

    def test_fetch_retries_on_sgsync_bot_challenge(self) -> None:
        class _FakeClient:
            calls = 0

            def __init__(self, *args, **kwargs):
                pass

            def get(self, url, params=None, **kwargs):
                _FakeClient.calls += 1
                if _FakeClient.calls == 1:
                    return type("R", (), {
                        "status_code": 200,
                        "url": "https://www.google.com/sgsync?url=...",
                        "text": "<html><head><title>Verifying...</title></head></html>",
                        "text_markdown": "Verifying...",
                    })()
                return type("R", (), {
                    "status_code": 200,
                    "url": "https://www.google.com/travel/flights?tfs=abc",
                    "text": "<html><script class='ds:1'>AF_initDataCallback({});</script></html>",
                    "text_markdown": "Flights",
                })()

        with patch.object(core, "Client", _FakeClient), patch.object(core.time, "sleep"):
            res = core.fetch({"tfs": "abc"})
        self.assertEqual(_FakeClient.calls, 2)
        self.assertIn("travel/flights", res.url)
