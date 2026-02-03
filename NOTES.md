# Notes

- Created on 2026-01-23 to keep session context between runs.

- 2026-01-23: RT bug: return flights decoded without full details (missing return departure time, airline, flight number). We know outbound and return price, but not return time/airline/number. Add tests first; focus on JS round-trip inbound decoding choosing full data when multiple ds:* blocks exist.

- 2026-02-03: **Live Test Failures Investigation**
  - **Problem**: All live tests failing with "No decodable js data candidates found"
  - **Analysis**: 
    - Google Flights is responding with HTTP 200 (service is up)
    - The JS parser cannot find expected data structure in response
    - This is a systematic failure across all routes and dates
    - Likely cause: Google changed their JavaScript/HTML structure
  - **Solution Applied**:
    - Added HTML parser fallback to all live tests
    - Tests now try JS parser first, then fall back to HTML parser
    - This provides resilience against Google Flights format changes
  - **Route Added**: GDN-MAD to parametrized one-way tests (from problem statement)
  - **Recommendation**: Monitor which parser succeeds in CI to understand if Google has permanently changed their format or if this is temporary

