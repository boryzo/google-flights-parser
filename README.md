Try out the dev version: [**Pypi (`3.0rc0`)**](https://pypi.org/project/fast-flights/3.0rc0/)

<br /><br /><br />
<div align="center">

# ✈️ fast-flights

The fast and strongly-typed Google Flights scraper (API) implemented in Python. Based on Base64-encoded Protobuf string.

[**Documentation**](https://aweirddev.github.io/flights) • [Issues](https://github.com/AWeirdDev/flights/issues) • [PyPi](https://pypi.org/project/fast-flights)

```haskell
$ pip install fast-flights
```

</div>

## Basics
**TL;DR**: To use `fast-flights`, you'll first create a filter (for `?tfs=`) to perform a request.
Then, add `flight_data`, `trip`, `seat`, `passengers` to use the API directly.

```python
from fast_flights import FlightData, Passengers, Result, get_flights

result: Result = get_flights(
    flight_data=[
        FlightData(date="2025-01-01", from_airport="TPE", to_airport="MYJ")
    ],
    trip="one-way",
    seat="economy",
    passengers=Passengers(adults=2, children=1, infants_in_seat=0, infants_on_lap=0),
    fetch_mode="fallback",
)

print(result)

# The price is currently... low/typical/high
print("The price is currently", result.current_price)
```

**Properties & usage for `Result`**:

```python
result.current_price

# Get the first flight
flight = result.flights[0]

flight.is_best
flight.name
flight.departure
flight.arrival
flight.arrival_time_ahead
flight.duration
flight.stops
flight.delay?  # may not be present
flight.price
```

**Useless enums**: Additionally, you can use the `Airport` enum to search for airports in code (as you type)! See `_generated_enum.py` in source.

```python
Airport.TAIPEI
              ╭─────────────────────────────────╮
              │ TAIPEI_SONGSHAN_AIRPORT         │
              │ TAPACHULA_INTERNATIONAL_AIRPORT │
              │ TAMPA_INTERNATIONAL_AIRPORT     │
              ╰─────────────────────────────────╯
```

## What's new
- `v2.0` – New (much more succinct) API, fallback support for Playwright serverless functions, and [documentation](https://aweirddev.github.io/flights)!
- `v2.2` - Now supports **local playwright** for sending requests.
- **New** - Added `data_source="auto"` option for automatic fallback between JS and HTML parsers.

## Data Source Options
The `data_source` parameter controls how flight data is extracted from Google Flights:

- **`"html"`** (default) - Parses HTML directly. More stable but may have less detailed data.
- **`"js"`** - Parses JavaScript data structures. More detailed but may break if Google changes their format.
- **`"auto"`** - **Recommended** - Tries JS parser first, automatically falls back to HTML if JS parsing fails.

```python
# Recommended: Use auto mode for best reliability
result = get_flights(
    flight_data=[FlightData(date="2025-01-01", from_airport="GDN", to_airport="MAD")],
    trip="one-way",
    seat="economy",
    passengers=Passengers(adults=1),
    data_source="auto",  # Automatic fallback
)
```

For maximum reliability with detailed data, use `data_source="auto"` which provides the best of both worlds.

## Cookies & consent
The EU region is a bit tricky to solve for now, but the fallback support should be able to handle it.

## Tests
There are two kinds of tests:

1) **Decoder/unit tests (offline)** – fast, no network.  
2) **Live integration tests** – real requests to Google Flights (network required).

### Local setup
```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt pytest
```

### Run offline decoder tests
```bash
pytest tests/test_round_trip_js_flow.py -vv
```

### Run live round-trip tests (Google Flights)
```bash
pytest -s --log-cli-level=DEBUG tests/test_round_trip_live_google.py -vv
```

### Run live one-way tests (Google Flights)
```bash
pytest -s --log-cli-level=DEBUG tests/test_one_way_live_google.py -vv
```

### HTML report (local)
```bash
python scripts/run_pytest_html.py tests/test_round_trip_live_google.py -s --log-cli-level=DEBUG
```
Output:
- `reports/pytest_report.html`
- `reports/pytest_junit.xml`

### One-command runner
```bash
./scripts/run-tests.sh
```
Runs:
- `tests/test_round_trip_live_google.py`
- `tests/test_one_way_live_google.py`
- `tests/test_round_trip_js_flow.py`

You can also pass custom pytest args:
```bash
./scripts/run-tests.sh tests/test_one_way_live_google.py -k GDN-LTN -vv
```

### Debugging failures
For JS parsing failures we dump the listing HTML:
- `/tmp/fast_flights_listing.html`
- `/tmp/fast_flights_listing_snippet.txt`

Useful env vars:
- `LOG_LEVEL=DEBUG`
- `RT_LIVE_OUTBOUND_DAYS=60`
- `RT_LIVE_RETURN_GAP_DAYS=7`
- `RT_LIVE_LOG_LIMIT=6`
- `OW_LIVE_OUTBOUND_DAYS=60`
- `OW_LIVE_LOG_LIMIT=6`

### GitHub Actions
Live tests run on GitHub Actions every **2 days** and can be triggered manually.  
Workflow: `.github/workflows/live_flights_tests.yml`

## Contributing
Contributing is welcomed! I probably won't work on this project unless there's a need for a major update, but boy howdy do I love pull requests.

***

## How it's made

The other day, I was making a chat-interface-based trip recommendation app and wanted to add a feature that can search for flights available for booking. My personal choice is definitely [Google Flights](https://flights.google.com) since Google always has the best and most organized data on the web. Therefore, I searched for APIs on Google.

> 🔎 **Search** <br />
> google flights api

The results? Bad. It seems like they discontinued this service and it now lives in the Graveyard of Google.

> <sup><a href="https://duffel.com/blog/google-flights-api" target="_blank">🧏‍♂️ <b>duffel.com</b></a></sup><br />
> <sup><i>Google Flights API: How did it work & what happened to it?</i></b>
>
> The Google Flights API offered developers access to aggregated airline data, including flight times, availability, and prices. Over a decade ago, Google announced the acquisition of ITA Software Inc. which it used to develop its API. **However, in 2018, Google ended access to the public-facing API and now only offers access through the QPX enterprise product**.

That's awful! I've also looked for free alternatives but their rate limits and pricing are just 😬 (not a good fit/deal for everyone).

<br />

However, Google Flights has their UI – [flights.google.com](https://flights.google.com). So, maybe I could just use Developer Tools to log the requests made and just replicate all of that? Undoubtedly not! Their requests are just full of numbers and unreadable text, so that's not the solution.

Perhaps, we could scrape it? I mean, Google allowed many companies like [Serpapi](https://google.com/search?q=serpapi) to scrape their web just pretending like nothing happened... So let's scrape our own.

> 🔎 **Search** <br />
> google flights ~~api~~ scraper pypi

Excluding the ones that are not active, I came across [hugoglvs/google-flights-scraper](https://pypi.org/project/google-flights-scraper) on Pypi. I thought to myself: "aint no way this is the solution!"

I checked hugoglvs's code on [GitHub](https://github.com/hugoglvs/google-flights-scraper), and I immediately detected "playwright," my worst enemy. One word can describe it well: slow. Two words? Extremely slow. What's more, it doesn't even run on the **🗻 Edge** because of configuration errors, missing libraries... etc. I could just reverse [try.playwright.tech](https://try.playwright.tech) and use a better environment, but that's just too risky if they added Cloudflare as an additional security barrier 😳.

Life tells me to never give up. Let's just take a look at their URL params...

```markdown
https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI0LTA1LTI4agcIARIDVFBFcgcIARIDTVlKGh4SCjIwMjQtMDUtMzBqBwgBEgNNWUpyBwgBEgNUUEVAAUgBcAGCAQsI____________AZgBAQ&hl=en
```

| Param | Content | My past understanding |
|-------|---------|-----------------------|
| hl    | en      | Sets the language.    |
| tfs   | CBwQAhoeEgoyMDI0LTA1LTI4agcIARID… | What is this???? 🤮🤮 |

I removed the `?tfs=` parameter and found out that this is the control of our request! And it looks so base64-y.

If we decode it to raw text, we can still see the dates, but we're not quite there — there's too much unwanted Unicode text.

Or maybe it's some kind of a **data-storing method** Google uses? What if it's something like JSON? Let's look it up.

> 🔎 **Search** <br />
> google's json alternative

> 🐣 **Result**<br />
> Solution: The Power of **Protocol Buffers**
> 
> LinkedIn turned to Protocol Buffers, often referred to as **protobuf**, a binary serialization format developed by Google. The key advantage of Protocol Buffers is its efficiency, compactness, and speed, making it significantly faster than JSON for serialization and deserialization.

Gotcha, Protobuf! Let's feed it to an online decoder and see how it does:

> 🔎 **Search** <br />
> protobuf decoder

> 🐣 **Result**<br />
> [protobuf-decoder.netlify.app](https://protobuf-decoder.netlify.app)

I then pasted the Base64-encoded string to the decoder and no way! It DID return valid data!

![annotated, Protobuf Decoder screenshot](https://github.com/AWeirdDev/flights/assets/90096971/77dfb097-f961-4494-be88-3640763dbc8c)

I immediately recognized the values — that's my data, that's my query!

So, I wrote some simple Protobuf code to decode the data.

```protobuf
syntax = "proto3"

message Airport {
    string name = 2;
}

message FlightInfo {
    string date = 2;
    Airport dep_airport = 13;
    Airport arr_airport = 14;
}

message GoogleSucks {
    repeated FlightInfo = 3;
}
```

It works! Now, I won't consider myself an "experienced Protobuf developer" but rather a complete beginner.

I have no idea what I wrote but... it worked! And here it is, `fast-flights`.

***

<div align="center">

(c) 2024-2025 AWeirdDev, and other awesome people

</div>
