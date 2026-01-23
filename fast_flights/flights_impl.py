"""Typed implementation of flights_pb2.py"""

import base64
from dataclasses import dataclass
from typing import Any, List, Optional, TYPE_CHECKING, Literal, Union

from . import flights_pb2 as PB
from ._generated_enum import Airport
from .schema import Segment as TfsSegment

if TYPE_CHECKING:
    PB: Any

AIRLINE_ALLIANCES = ["SKYTEAM", "STAR_ALLIANCE", "ONEWORLD"]

def debug_info_fields() -> None:
    """Print PB.Info descriptor fields for debugging."""
    print(PB.Info.DESCRIPTOR.fields)

class FlightData:
    """Represents flight data.

    Args:
        date (str): Date.
        from_airport (str): Departure (airport). Where from?
        to_airport (str): Arrival (airport). Where to?
        max_stops (int, optional): Maximum number of stops. Default is None.
        airlines (List[str], optional): Airlines this flight should be taken with. Default is None.
    """

    __slots__ = ("date", "from_airport", "to_airport", "max_stops", "airlines")
    date: str
    from_airport: str
    to_airport: str
    max_stops: Optional[int]
    airlines: Optional[List[str]]

    def __init__(
        self,
        *,
        date: str,
        from_airport: Union[Airport, str],
        to_airport: Union[Airport, str],
        max_stops: Optional[int] = None,
        airlines: Optional[List[str]] = None,
    ):
        self.date = date
        self.from_airport = (
            from_airport.value if isinstance(from_airport, Airport) else from_airport
        )
        self.to_airport = (
            to_airport.value if isinstance(to_airport, Airport) else to_airport
        )
        self.max_stops = max_stops
        # TODO: All the list of airlines should technically be added to ._generated_enum like Airports
        # but I don't know how to find the comprehensive list of airlines now.
        if airlines is not None:
            self.airlines = []
            for airline in airlines:
                airline = airline.upper()
                if not (len(airline) == 2 or airline in AIRLINE_ALLIANCES):
                    raise ValueError(
                        f"Invalid airline code: {airline}. "
                        f"Airline codes should be 2 characters long or in the list of airline alliances: {AIRLINE_ALLIANCES}"
                    )
                self.airlines.append(airline)
        else:
            # make it consistent with self.max_stops and set it to None
            self.airlines = None

    def attach(self, info: PB.Info) -> None:  # type: ignore
        data = info.data.add()
        data.date = self.date
        data.from_flight.airport = self.from_airport
        data.to_flight.airport = self.to_airport
        if self.max_stops is not None:
            data.max_stops = self.max_stops
        if self.airlines is not None:
            data.airlines.extend(self.airlines)

    def __repr__(self) -> str:
        return (
            f"FlightData(date={self.date!r}, "
            f"from_airport={self.from_airport}, "
            f"to_airport={self.to_airport}, "
            f"max_stops={self.max_stops}, "
            f"airlines={self.airlines}"
        )


class Passengers:
    def __init__(
        self,
        *,
        adults: int = 0,
        children: int = 0,
        infants_in_seat: int = 0,
        infants_on_lap: int = 0,
    ):
        assert (
            sum((adults, children, infants_in_seat, infants_on_lap)) <= 9
        ), "Too many passengers (> 9)"
        assert (
            infants_on_lap <= adults
        ), "You must have at least one adult per infant on lap"

        self.pb = []
        self.pb += [PB.Passenger.ADULT for _ in range(adults)]
        self.pb += [PB.Passenger.CHILD for _ in range(children)]
        self.pb += [PB.Passenger.INFANT_IN_SEAT for _ in range(infants_in_seat)]
        self.pb += [PB.Passenger.INFANT_ON_LAP for _ in range(infants_on_lap)]

        self._data = (adults, children, infants_in_seat, infants_on_lap)

    def attach(self, info: PB.Info) -> None:  # type: ignore
        for p in self.pb:
            info.passengers.append(p)

    def __repr__(self) -> str:
        return f"Passengers({self._data})"


class TFSData:
    """``?tfs=`` data. (internal)

    Use `TFSData.from_interface` instead.
    """

    def __init__(
        self,
        *,
        flight_data: List[FlightData],
        seat: PB.Seat,  # type: ignore
        trip: PB.Trip,  # type: ignore
        passengers: Passengers,
        max_stops: Optional[int] = None,  # Add max_stops to the constructor
        selected_outbound_ref: Optional[Union[str, bytes]] = None,
    ):
        self.flight_data = flight_data
        self.seat = seat
        self.trip = trip
        self.passengers = passengers
        self.max_stops = max_stops  # Store max_stops
        self.selected_outbound_ref = selected_outbound_ref

    def pb(self) -> PB.Info:  # type: ignore
        info = PB.Info()
        info.seat = self.seat
        info.trip = self.trip

        self.passengers.attach(info)

        for fd in self.flight_data:
            fd.attach(info)

        # If max_stops is set, attach it to all flight data entries
        if self.max_stops is not None:
            for flight in info.data:
                flight.max_stops = self.max_stops

        if self.selected_outbound_ref is not None:
            if isinstance(self.selected_outbound_ref, bytes):
                info.selected_outbound_ref = self.selected_outbound_ref.decode("utf-8")
            else:
                info.selected_outbound_ref = self.selected_outbound_ref

        return info

    def to_string(self) -> bytes:
        return self.pb().SerializeToString()

    def as_b64(self) -> bytes:
        return base64.b64encode(self.to_string())

    @staticmethod
    def from_interface(
        *,
        flight_data: List[FlightData],
        trip: Literal["round-trip", "one-way", "multi-city"],
        passengers: Passengers,
        seat: Literal["economy", "premium-economy", "business", "first"],
        max_stops: Optional[int] = None,  # Add max_stops to the method signature
        selected_outbound_ref: Optional[Union[str, bytes]] = None,
    ):
        """Use ``?tfs=`` from an interface.

        Args:
            flight_data (list[FlightData]): Flight data as a list.
            trip ("one-way" | "round-trip" | "multi-city"): Trip type.
            passengers (Passengers): Passengers.
            seat ("economy" | "premium-economy" | "business" | "first"): Seat.
            max_stops (int, optional): Maximum number of stops.
        """
        trip_t = {
            "round-trip": PB.Trip.ROUND_TRIP,
            "one-way": PB.Trip.ONE_WAY,
            "multi-city": PB.Trip.MULTI_CITY,
        }[trip]
        seat_t = {
            "economy": PB.Seat.ECONOMY,
            "premium-economy": PB.Seat.PREMIUM_ECONOMY,
            "business": PB.Seat.BUSINESS,
            "first": PB.Seat.FIRST,
        }[seat]

        return TFSData(
            flight_data=flight_data,
            seat=seat_t,
            trip=trip_t,
            passengers=passengers,
            max_stops=max_stops,  # Pass max_stops into TFSData
            selected_outbound_ref=selected_outbound_ref,
        )

    def with_selected_outbound(self, ref: Union[str, bytes]) -> "TFSData":
        return TFSData(
            flight_data=self.flight_data,
            seat=self.seat,
            trip=self.trip,
            passengers=self.passengers,
            max_stops=self.max_stops,
            selected_outbound_ref=ref,
        )

    def __repr__(self) -> str:
        return (
            "TFSData("
            f"flight_data={self.flight_data!r}, "
            f"max_stops={self.max_stops!r}, "
            f"selected_outbound_ref={self.selected_outbound_ref!r})"
        )

    def decode_segments(self) -> list[list[TfsSegment]]:
        return segments_from_tfs(self.as_b64())

@dataclass
class ItinerarySummary:
    flights: str
    price: int
    currency: str

    @classmethod
    def from_b64(cls, b64_string: str) -> 'ItinerarySummary':
        raw = base64.b64decode(b64_string)
        pb = PB.ItinerarySummary()
        pb.ParseFromString(raw)
        return cls(pb.flights, pb.price.price / 100, pb.price.currency)


def _read_varint(buf: bytes, idx: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        if idx >= len(buf):
            raise ValueError("varint truncated")
        b = buf[idx]
        idx += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long")
    return result, idx


def _iter_length_delimited_fields(buf: bytes, field_number: int) -> list[bytes]:
    idx = 0
    out: list[bytes] = []
    while idx < len(buf):
        try:
            tag, idx = _read_varint(buf, idx)
        except ValueError:
            break
        if tag == 0:
            break
        field = tag >> 3
        wire = tag & 7
        if wire == 0:
            try:
                _, idx = _read_varint(buf, idx)
            except ValueError:
                break
        elif wire == 1:
            if idx + 8 > len(buf):
                break
            idx += 8
        elif wire == 2:
            try:
                length, idx = _read_varint(buf, idx)
            except ValueError:
                break
            if idx + length > len(buf):
                break
            blob = buf[idx : idx + length]
            idx += length
            if field == field_number:
                out.append(blob)
        elif wire == 5:
            if idx + 4 > len(buf):
                break
            idx += 4
        else:
            break
    return out


def _parse_segment_message(buf: bytes) -> Optional[TfsSegment]:
    idx = 0
    origin = None
    destination = None
    carrier = None
    flight_number = None
    date = None
    while idx < len(buf):
        try:
            tag, idx = _read_varint(buf, idx)
        except ValueError:
            break
        if tag == 0:
            break
        field = tag >> 3
        wire = tag & 7
        if wire == 2:
            try:
                length, idx = _read_varint(buf, idx)
            except ValueError:
                break
            if idx + length > len(buf):
                break
            blob = buf[idx : idx + length]
            idx += length
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if field == 1:
                origin = text
            elif field == 2:
                date = text
            elif field == 3:
                destination = text
            elif field == 5:
                carrier = text
            elif field == 6:
                flight_number = text
        elif wire == 0:
            try:
                _, idx = _read_varint(buf, idx)
            except ValueError:
                break
        elif wire == 1:
            if idx + 8 > len(buf):
                break
            idx += 8
        elif wire == 5:
            if idx + 4 > len(buf):
                break
            idx += 4
        else:
            break
    if not (origin and destination and carrier and flight_number and date):
        return None
    return TfsSegment(
        origin=origin,
        destination=destination,
        carrier_code=carrier,
        flight_number=flight_number,
        date=date,
    )


def _decode_tfs_bytes(tfs: Union[str, bytes]) -> bytes:
    if isinstance(tfs, bytes):
        try:
            s = tfs.decode("utf-8")
        except UnicodeDecodeError:
            return tfs
    else:
        s = tfs
    s = s.strip().replace("-", "+").replace("_", "/")
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    try:
        return base64.b64decode(s + pad)
    except Exception:
        return s.encode("utf-8", errors="ignore")


def segments_from_tfs(tfs: Union[str, bytes]) -> list[list[TfsSegment]]:
    """Parse segments embedded in a tfs protobuf string (booking/search with selections)."""
    raw = _decode_tfs_bytes(tfs)
    flight_data_blobs = _iter_length_delimited_fields(raw, 3)
    all_segments: list[list[TfsSegment]] = []
    for flight_blob in flight_data_blobs:
        seg_blobs = _iter_length_delimited_fields(flight_blob, 4)
        segments: list[TfsSegment] = []
        for seg_blob in seg_blobs:
            seg = _parse_segment_message(seg_blob)
            if seg:
                segments.append(seg)
        all_segments.append(segments)
    return all_segments


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    v = value
    while True:
        to_write = v & 0x7F
        v >>= 7
        if v:
            out.append(to_write | 0x80)
        else:
            out.append(to_write)
            break
    return bytes(out)


def _encode_key(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def _encode_len_field(field_number: int, payload: bytes) -> bytes:
    return _encode_key(field_number, 2) + _encode_varint(len(payload)) + payload


def _encode_string_field(field_number: int, value: str) -> bytes:
    return _encode_len_field(field_number, value.encode("utf-8"))


def _encode_varint_field(field_number: int, value: int) -> bytes:
    return _encode_key(field_number, 0) + _encode_varint(value)


def _encode_airport(code: str) -> bytes:
    # Observed Airport message uses field 1=1 and field 2=IATA code.
    return _encode_varint_field(1, 1) + _encode_string_field(2, code)


def _encode_segment(seg: TfsSegment) -> bytes:
    parts = [
        _encode_string_field(1, seg.origin),
        _encode_string_field(2, seg.date),
        _encode_string_field(3, seg.destination),
        _encode_string_field(5, seg.carrier_code),
        _encode_string_field(6, seg.flight_number),
    ]
    return b"".join(parts)


def _encode_flight_data(fd: "FlightData", segments: Optional[list[TfsSegment]] = None) -> bytes:
    parts = [
        _encode_string_field(2, fd.date),
        _encode_len_field(13, _encode_airport(fd.from_airport)),
        _encode_len_field(14, _encode_airport(fd.to_airport)),
    ]
    if fd.max_stops is not None:
        parts.append(_encode_varint_field(5, int(fd.max_stops)))
    if fd.airlines:
        for airline in fd.airlines:
            parts.append(_encode_string_field(6, airline))
    if segments:
        for seg in segments:
            parts.append(_encode_len_field(4, _encode_segment(seg)))
    return b"".join(parts)


def build_tfs_with_segments(
    filter_data: "TFSData",
    segments_by_leg: list[list[TfsSegment]],
) -> str:
    """
    Build a tfs string with explicit segments in FlightData field 4.
    segments_by_leg length should match flight_data length.
    """
    fd_bytes = []
    for idx, fd in enumerate(filter_data.flight_data):
        segs = segments_by_leg[idx] if idx < len(segments_by_leg) else []
        fd_bytes.append(_encode_len_field(3, _encode_flight_data(fd, segs)))

    passenger_vals = getattr(filter_data.passengers, "pb", None) or []
    passenger_fields = b"".join(_encode_varint_field(8, int(p)) for p in passenger_vals)

    payload = b"".join(
        fd_bytes
        + [
            passenger_fields,
            _encode_varint_field(9, int(filter_data.seat)),
            _encode_varint_field(19, int(filter_data.trip)),
        ]
    )
    return base64.b64encode(payload).decode("utf-8")
