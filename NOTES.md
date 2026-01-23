# Notes

- Created on 2026-01-23 to keep session context between runs.

- 2026-01-23: RT bug: return flights decoded without full details (missing return departure time, airline, flight number). We know outbound and return price, but not return time/airline/number. Add tests first; focus on JS round-trip inbound decoding choosing full data when multiple ds:* blocks exist.
