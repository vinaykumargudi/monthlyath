# MonthlyATH — Indian stock monthly OHLC API

A Python FastAPI service that takes an Indian stock symbol and downloads available
history from **1 January 2000 through today in India**, returning one OHLC candle
per calendar month. NSE is the default; BSE is supported. No API key is required.

For a company first listed in 2020, the response starts with its first available
trading month in 2020, **provided Yahoo Finance has that history**. It never adds
artificial pre-listing candles or fills gaps with invented prices. Provider coverage
can start later than the actual listing date, so the response exposes the actual
first/last available dates and coverage warnings. Complete listing-to-today coverage
for every symbol is not guaranteed by this source.

## Run locally

Use Python 3.12:

```bash
git clone https://github.com/vinaykumargudi/monthlyath.git
cd monthlyath
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API documentation: <http://localhost:8000/docs>.

## Download data

```bash
# NSE: TCS, RELIANCE, INFY, HDFCBANK, etc.
curl 'http://localhost:8000/api/stocks/TCS/monthly'

# Explicit Yahoo symbol
curl 'http://localhost:8000/api/stocks/RELIANCE.NS/monthly'

# BSE: use Yahoo's .BO symbol, or the exchange parameter
curl 'http://localhost:8000/api/stocks/TCS.BO/monthly'
curl 'http://localhost:8000/api/stocks/TCS/monthly?exchange=BSE'

# Download CSV to a file
curl --fail-with-body 'http://localhost:8000/api/stocks/TCS/monthly?format=csv' -o TCS_monthly.csv

# Completed calendar months only, for monthly signal calculations
curl 'http://localhost:8000/api/stocks/TCS/monthly?include_incomplete=false'

# Apply Yahoo's adjusted-close factor to daily OHLC before aggregation
curl 'http://localhost:8000/api/stocks/TCS/monthly?adjusted=true'
```

Symbols are case-insensitive. Bare symbols use NSE. An explicit `.NS` or `.BO`
suffix selects its exchange; a conflicting `exchange` parameter is rejected.
For symbols containing `&`, URL-encode that character as `%26` when needed.

### Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `symbol` (path) | Required | Exchange symbol such as `TCS`, `TCS.NS`, `TCS.BO` |
| `exchange` | NSE, or symbol suffix | `NSE` or `BSE` |
| `adjusted` | `false` | Apply yfinance's adjusted-close factor to OHLC |
| `include_incomplete` | `true` | Include the ongoing month through the latest available price |
| `format` | `json` | `json` or downloadable `csv` |

The start is always `2000-01-01`. The end is calculated on **each request**; there
is no hard-coded end year or short default lookback. Each request contacts Yahoo;
the service does not add an application-level response cache.

### Response

JSON contains `symbol`, `exchange`, `currency`, `source`, `interval`, `timezone`,
`requested_start`, `requested_end`, `first_available_date`, `last_available_date`,
`adjusted`, `include_incomplete`, `count`, `missing_months`, `warnings`, and `data`.
`data` contains candles in chronological order:

| Candle field | Meaning |
| --- | --- |
| `date` | Month label, e.g. `2020-09-01`; not necessarily a trading day |
| `open` | First available daily open in that month |
| `high` | Highest daily high in that month |
| `low` | Lowest daily low in that month |
| `close` | Last available daily close in that month |
| `volume` | Sum of daily volume; null if any daily volume is invalid or missing |
| `trading_days` | Number of available daily observations used |
| `first_trading_date` | First observation used in this candle |
| `last_trading_date` | Last observation used in this candle |
| `is_complete` | Whether this calendar month has ended in India |

The available-date fields describe downloaded daily history, before filtering out
an incomplete month. A stock listed this month returns `count=0` and `data=[]`
when `include_incomplete=false`. A listing-month candle starts at its first
available trading day. `is_complete=true` means the calendar month has ended,
**not** that every exchange session was independently verified.

CSV contains these candle fields. Response headers identify the source, adjustment
setting, available-date range, and missing-month count. Use JSON for full warnings.

## History and price adjustments

1. Fetch **daily** prices with `start=2000-01-01`, `end=tomorrow`, and `interval=1d`.
   Yahoo's end date is exclusive, so tomorrow includes today's data when available.
   Explicit dates avoid the default one-month window and intraday history limits.
2. Convert timestamps to `Asia/Kolkata`, sort by date, and aggregate by calendar month.
3. Include the ongoing month by default, with `is_complete=false` until the next
   calendar month. Today's price may be delayed or still updating. On holidays or
   weekends, the last observation is the latest available trading session.

`adjusted=false` returns Yahoo's supplied OHLC without yfinance's additional
adjusted-close transformation. It does **not** promise original unadjusted exchange
prints: Yahoo's history may already reflect splits. `adjusted=true` applies Yahoo's
adjusted-close factor to daily OHLC before aggregation and can incorporate dividend
adjustments. Do not mix adjustment modes within a backtest.

This is a single-provider API, not an independently audited exchange archive.
Renamed/delisted symbols and gaps depend on Yahoo's coverage. Entire missing months
between the first and last observation are reported, but there is no exchange
holiday calendar to detect every missing session. Missing, non-finite, non-positive
or inconsistent OHLC values cause an error rather than a misleading candle.

## Errors

| HTTP status | Meaning |
| --- | --- |
| `404` | Provider reports no available history / unknown symbol |
| `422` | Invalid symbol, conflicting exchange or invalid query value |
| `502` | Download failed or provider returned invalid price data |
| `503` | Yahoo rate limit; includes `Retry-After: 60` |

`GET /health` checks application liveness without contacting Yahoo.

## Docker

```bash
docker build -t monthlyath .
docker run --rm -p 8000:8000 monthlyath
```

Runs as a non-root user, listening on `PORT` (default `8000`), including on platforms
such as Cloud Run. This repository does not deploy or provision cloud resources.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use explicitly synthetic prices and mocked provider responses. They check
exact OHLC, the 2000 cutoff, a 2020 listing case, exchange routing, India month
boundaries, completed-only filtering, CSV, bad data and provider failures. They do
not prove live Yahoo availability.

A live TCS request during development encountered Yahoo rate limiting and a
connection timeout; a successful live full-history download was **not** verified
in this environment. Run the TCS/RELIANCE examples locally and inspect
`first_available_date`, `last_available_date`, `count` and `warnings` to confirm coverage.

Reference: [yfinance PriceHistory documentation](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html).
