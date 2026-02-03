# Migration Guide for External Users

## Problem

If your code uses `data_source="js"` directly, it will fail when Google Flights changes their JavaScript structure (which is currently happening). Example failing code:

```python
result = get_flights_from_filter(
    filter_data,
    currency=currency,
    mode="common",
    data_source="js",  # ❌ Will fail when JS parser breaks
)
```

## Solution: Use `data_source="auto"`

The new `auto` mode tries the JS parser first (for detailed data), then automatically falls back to the HTML parser if JS parsing fails.

### One-Way Flight Example

```python
from fast_flights import FlightData, Passengers, create_filter, get_flights_from_filter

# Before
result = get_flights_from_filter(
    filter_data,
    currency="PLN",
    mode="common",
    data_source="js",  # ❌ Strict mode - fails on JS errors
)

# After
result = get_flights_from_filter(
    filter_data,
    currency="PLN",
    mode="common",
    data_source="auto",  # ✅ Resilient mode - automatic fallback
)
```

### Round-Trip Flight Example

```python
# Before
filter_data = create_filter(
    flight_data=[
        FlightData(date="2026-06-01", from_airport="GDN", to_airport="MAD"),
        FlightData(date="2026-06-08", from_airport="MAD", to_airport="GDN"),
    ],
    trip="round-trip",
    seat="economy",
    passengers=Passengers(adults=1),
)

result = get_flights_from_filter(
    filter_data,
    currency="PLN",
    mode="common",
    data_source="js",  # ❌ Fails on JS parsing errors
)

# After
result = get_flights_from_filter(
    filter_data,
    currency="PLN",
    mode="common",
    data_source="auto",  # ✅ Automatic fallback
)
```

## What Changes?

### Return Types
- JS mode returns `DecodedResult` or `RoundTripDecodedResult`
- HTML mode returns `Result`
- Auto mode returns either, depending on which parser succeeds

### Handling Results

```python
from fast_flights import core

result = get_flights_from_filter(filter_data, data_source="auto")

# Check the result type
if isinstance(result, core.RoundTripDecodedResult):
    # Detailed round-trip result from JS parser
    outbound_items = result.outbound.best + result.outbound.other
    inbound_items = result.inbound.best + result.inbound.other
elif isinstance(result, core.DecodedResult):
    # Detailed one-way result from JS parser
    all_items = result.best + result.other
elif isinstance(result, core.Result):
    # Result from HTML parser
    flights = result.flights
else:
    # No results found
    pass
```

## Benefits of Auto Mode

1. **Resilience**: Works even when Google changes their format
2. **Detailed Data**: Gets JS parser data when available
3. **Automatic**: No need to implement fallback logic yourself
4. **Future-Proof**: Library handles parser selection
5. **Simple Migration**: Just change `"js"` to `"auto"`

## When to Use Each Mode

- **`"html"`** - Use when you only need basic data and want maximum stability
- **`"js"`** - Use when you require detailed data and can handle failures yourself
- **`"auto"`** - **Recommended** - Best of both worlds with automatic fallback

## Full Example: search_flights Function

Here's how to update the example code from the problem statement:

```python
def search_flights(
    origin: str,
    destination: str,
    date: datetime,
    *,
    currency: str = "PLN",
    return_date: datetime | None = None,
    max_outbound: Optional[int] = None,
    max_inbound: Optional[int] = None,
    target_time: Optional[str] = None,
) -> List[SimpleNamespace]:
    """Return flights from Google Flights with automatic parser fallback."""
    
    # ... (input normalization code) ...
    
    if return_date:
        filter_data = create_filter(
            flight_data=[
                FlightData(date=date.strftime("%Y-%m-%d"), from_airport=origin_code, to_airport=destination_code),
                FlightData(date=return_date.strftime("%Y-%m-%d"), from_airport=destination_code, to_airport=origin_code),
            ],
            trip="round-trip",
            seat="economy",
            passengers=Passengers(adults=1),
        )
        
        result = get_flights_from_filter(
            filter_data,
            currency=currency,
            mode="common",
            data_source="auto",  # ✅ Changed from "js" to "auto"
            target_time=target_time,
        )
        
        # Handle both RoundTripDecodedResult (from JS) and Result (from HTML)
        if isinstance(result, core.RoundTripDecodedResult):
            # JS parser succeeded - use detailed data
            outbound_items = _pick_itineraries(result.outbound, max_outbound)
            inbound_items = _pick_itineraries(result.inbound, max_inbound)
            # ... process as before ...
        elif isinstance(result, core.Result):
            # HTML parser succeeded - adapt to Result type
            # (may need to adjust data extraction)
            pass
        else:
            return []  # No results
    
    else:
        # One-way flight - similar changes
        result = get_flights_from_filter(
            filter_data,
            currency=currency,
            mode="common",
            data_source="auto",  # ✅ Changed from "js" to "auto"
            target_time=target_time,
        )
        
        if isinstance(result, core.DecodedResult):
            # JS parser succeeded
            # ... process as before ...
        elif isinstance(result, core.Result):
            # HTML parser succeeded
            # ... adapt processing ...
        
    return flights
```

## Migration Checklist

- [ ] Find all instances of `data_source="js"` in your code
- [ ] Change to `data_source="auto"`
- [ ] Add type checking for different result types if needed
- [ ] Test with current Google Flights (JS parser may be broken)
- [ ] Verify fallback behavior works as expected

## Questions?

If you have questions about migrating your code, please open an issue on the repository.
