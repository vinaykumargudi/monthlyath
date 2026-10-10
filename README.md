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
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
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

## Download a CSV with a running all-time-high close

[`download_monthly_ath.py`](download_monthly_ath.py) takes a stock symbol, calls
this API, sorts the monthly candles oldest first and adds one column:
**`alltimehigh` = the highest monthly CLOSE up to and including the current row.**
The first row's `alltimehigh` equals its own close. A new closing-price record
updates `alltimehigh` on that same row. This uses the closing price, not the
monthly high, and never uses a future row.

| Month | Close | alltimehigh |
| --- | ---: | ---: |
| January | 100 | 100 |
| February | 90 | 100 |
| March | 120 | 120 |
| April | 110 | 120 |

With the API already running:

```bash
# Default API: http://127.0.0.1:8000
python download_monthly_ath.py TCS

# Call your deployed API and choose the CSV filename
python download_monthly_ath.py TCS --api-url https://YOUR_HOST --output TCS_monthly_ath.csv

# Only completed months, for monthly backtests
python download_monthly_ath.py TCS --completed-only

# BSE or dividend-adjusted prices, if required
python download_monthly_ath.py TCS.BO --adjusted
```

The default output is `data/TCS.NS_monthly_ath.csv`. Original candle columns are
preserved and `alltimehigh` is appended. Pass an exchange ticker such as `TCS`,
not a full company name. `--api-url` is the base URL without `/mcp` or the REST
endpoint path. The script uses only Python's standard library and no credentials.

By default, the ongoing month is included, matching the API. Its close and ATH
may change; use `--completed-only` to exclude it. Coverage warnings from the API
are printed. ATH is calculated only over the returned history beginning in 2000
or later; earlier history and gaps cannot be reconstructed by this script.

For a closing-price breakout signal, compare the current close with the **previous
row's** `alltimehigh`. Comparing it with its own inclusive `alltimehigh` using `>`
would never trigger. The stored column still includes the current row as shown above.

An API failure, empty response or invalid close exits with a nonzero status and
leaves an existing CSV untouched. The script makes one API request per invocation;
it is a single-stock exporter, not a 500-stock batch job.

Run the script's tests without additional packages:

```bash
python -m unittest discover -s tests -p 'test_download_monthly_ath.py' -v
```

These tests use synthetic prices, including an HTTP server exercising the real
CLI download-to-CSV path; they do not establish live Yahoo availability.

## MCP server — no authentication

The same application serves a **Streamable HTTP MCP endpoint** at
`http://127.0.0.1:8000/mcp`. No API key, bearer token, login or OAuth registration
is required. Updating an existing checkout:

```bash
git pull
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The tool is named **`get_monthly_history`**. It accepts `symbol`, optional `exchange`,
`adjusted` (default false), and `include_incomplete` (default true). It returns the
same structured history and coverage warnings as the REST JSON endpoint.

Example tool arguments:

```json
{"symbol": "TCS", "include_incomplete": false}
```

### HTTP client configuration

[`mcp.json`](mcp.json) contains a ready-to-use entry for clients accepting the
`mcpServers` + `url` format, such as Cursor. Merge this entry into your client's
MCP configuration while preserving other servers:

```json
{
  "mcpServers": {
    "monthlyath": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

For VS Code, the equivalent `.vscode/mcp.json` uses its own schema:

```json
{
  "servers": {
    "monthlyath": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

For a client with a server URL form, enter the MCP URL and select **None** for
authentication if prompted. A cloud-hosted client needs a reachable deployed URL;
`127.0.0.1` refers to the machine running that client.

### Local stdio client configuration

For local clients that launch an MCP subprocess, use
[`mcp.stdio.example.json`](mcp.stdio.example.json). Replace both absolute paths
with the repository's location on your computer:

```json
{
  "mcpServers": {
    "monthlyath": {
      "command": "/absolute/path/to/monthlyath/.venv/bin/python",
      "args": ["/absolute/path/to/monthlyath/mcp_server.py"]
    }
  }
}
```

This mode needs no separately running HTTP API. The client launches the process
and communicates over stdin/stdout; protocol logs use stderr. The module entry
point `python -m app.mcp_server` also works from the repository directory.

### Hosting the HTTP endpoint

For an existing deployment, set `MCP_PUBLIC_URL` to its origin, for example
`https://your-service.run.app`, then use `https://your-service.run.app/mcp` in
the client. This sets the accepted Host/Origin; it is not a credential. Local
loopback addresses work without any environment variables. Host/Origin validation
remains enabled even though authentication is absent. If the hosting platform has
its own access controls, it must also permit unauthenticated requests.

REST and MCP use the same shared history service. Provider failures become MCP
tool errors with the corresponding history error code; no credentials resolve
Yahoo's rate limits or expand its historical coverage.

## Docker

```bash
docker build -t monthlyath .
docker run --rm -p 127.0.0.1:8000:8000 monthlyath
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
not prove live Yahoo availability. MCP tests additionally cover unauthenticated
initialization, tool discovery and invocation, REST/MCP result parity, provider
errors, Host/Origin validation, client JSON, and a real stdio subprocess handshake.

A live TCS request during development encountered Yahoo rate limiting and a
connection timeout; a successful live full-history download was **not** verified
in this environment. Run the TCS/RELIANCE examples locally and inspect
`first_available_date`, `last_available_date`, `count` and `warnings` to confirm coverage.

Reference: [yfinance PriceHistory documentation](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html).
