#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from fast_flights import flights_pb2 as PB


def _urlsafe_b64decode(data: str) -> bytes:
    data = data.strip()
    data = data.replace("-", "+").replace("_", "/")
    pad = "=" * ((4 - (len(data) % 4)) % 4)
    return base64.b64decode(data + pad)


def _extract_tfs(value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        qs = parse_qs(urlparse(value).query)
        tfs = qs.get("tfs", [""])[0]
        if not tfs:
            raise ValueError("URL does not contain tfs parameter")
        return tfs
    return value


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


def _is_printable_ascii(data: bytes, *, min_ratio: float = 0.85) -> bool:
    if not data:
        return False
    printable = sum(1 for b in data if 32 <= b <= 126)
    return (printable / len(data)) >= min_ratio


def _looks_like_proto(data: bytes) -> bool:
    if len(data) < 2:
        return False
    try:
        tag, idx = _read_varint(data, 0)
    except ValueError:
        return False
    if tag == 0:
        return False
    wire_type = tag & 7
    if wire_type not in (0, 1, 2, 5):
        return False
    # basic sanity: idx must advance and not consume all bytes for tiny blobs
    return idx < len(data)


def _parse_wire(
    data: bytes,
    *,
    depth: int = 0,
    max_items: int = 200,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    idx = 0
    while idx < len(data) and len(items) < max_items:
        try:
            tag, idx = _read_varint(data, idx)
        except ValueError:
            break
        if tag == 0:
            break
        field = tag >> 3
        wire = tag & 7
        entry: dict[str, Any] = {"field": field, "wire": wire}
        if wire == 0:
            val, idx = _read_varint(data, idx)
            entry["value"] = val
        elif wire == 1:
            if idx + 8 > len(data):
                break
            entry["value"] = int.from_bytes(data[idx : idx + 8], "little", signed=False)
            idx += 8
        elif wire == 2:
            length, idx = _read_varint(data, idx)
            if idx + length > len(data):
                break
            blob = data[idx : idx + length]
            idx += length
            entry["len"] = length
            if _is_printable_ascii(blob):
                entry["ascii"] = blob[:200].decode("ascii", errors="replace")
            else:
                entry["hex_head"] = blob[:16].hex()
            if depth < 2 and not _is_printable_ascii(blob) and _looks_like_proto(blob):
                entry["nested"] = _parse_wire(blob, depth=depth + 1, max_items=max_items)
        elif wire == 5:
            if idx + 4 > len(data):
                break
            entry["value"] = int.from_bytes(data[idx : idx + 4], "little", signed=False)
            idx += 4
        else:
            break
        items.append(entry)
    return items


def _summarize_info(info: PB.Info) -> dict[str, Any]:
    return {
        "trip": int(info.trip),
        "seat": int(info.seat),
        "passengers": [int(p) for p in info.passengers],
        "selected_outbound_ref": info.selected_outbound_ref,
        "flights": [
            {
                "date": fd.date,
                "from": fd.from_flight.airport,
                "to": fd.to_flight.airport,
                "max_stops": fd.max_stops,
                "airlines": list(fd.airlines),
            }
            for fd in info.data
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Google Flights tfs protobuf.")
    parser.add_argument("tfs_or_url", nargs="+", help="tfs string or full URL")
    parser.add_argument("--max-items", type=int, default=200, help="max wire items to parse")
    parser.add_argument("--pretty", action="store_true", help="pretty JSON output")
    args = parser.parse_args()

    results = []
    for raw in args.tfs_or_url:
        tfs = _extract_tfs(raw)
        payload = _urlsafe_b64decode(tfs)
        info = PB.Info()
        info.ParseFromString(payload)
        results.append(
            {
                "tfs_len": len(tfs),
                "info": _summarize_info(info),
                "wire": _parse_wire(payload, max_items=args.max_items),
            }
        )

    if args.pretty:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(json.dumps(results, default=str))


if __name__ == "__main__":
    main()
