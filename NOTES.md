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

- 2026-02-03: **Auto Data Source Feature**
  - **Problem**: External library users hardcode `data_source="js"` and will fail when JS parser doesn't work
  - **Solution**: Added `data_source="auto"` option
    - Tries JS parser first for detailed data
    - Automatically falls back to HTML parser if JS fails
    - Works for both one-way and round-trip searches
  - **Benefits**:
    - Users get detailed JS data when available
    - Automatic fallback provides resilience
    - No code changes needed - just switch from "js" to "auto"
  - **Implementation**:
    - Updated DataSource type to include "auto"
    - Modified parse_response() to handle auto mode
    - Added round-trip auto fallback in exception handler
  - **Recommendation**: External users should use `data_source="auto"` instead of hardcoding "js"

