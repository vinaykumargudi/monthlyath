# MonthlyATH — Indian stock monthly OHLC with Upstox

Download monthly open, high, low and close for Indian stocks using **Upstox V3**.
The optional `start_date` defaults to **2000-01-01**. The end date is today in
`Asia/Kolkata`. A later-listed company starts at its first available Upstox trading
record; the application never invents prices before listing or fills missing months.

Upstox documents daily history from January 2000. This describes provider coverage,
not a guarantee that every stock has complete listing-to-today history. Check the
returned available-date fields and coverage warnings.

## Run with your existing token

Use Python 3.12. From your existing checkout, update and install:

```bash
git pull
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If your virtual environment already exists, activate it and run the install command.
On Windows PowerShell, activate it with `.venv\Scripts\Activate.ps1`.

The API reads **`UPSTOX_ACCESS_TOKEN`**, with **`UPSTOX_TOKEN`** accepted as a fallback.
Since your token is already set, start the API in the same terminal/environment.
Use the raw access token, without a `Bearer ` prefix. If your variable has a different
name, map it to `UPSTOX_ACCESS_TOKEN` before starting the server. For example, if you
currently use `ACCESS_TOKEN`:

```bash
export UPSTOX_ACCESS_TOKEN="$ACCESS_TOKEN"
```

PowerShell equivalent: `$env:UPSTOX_ACCESS_TOKEN = $env:ACCESS_TOKEN`.
No token goes in a URL, the downloader arguments, the symbol CSV or this repository.
A `.env` file is not loaded automatically; the token must be in the server process's
environment. Refresh an expired token in that environment and restart the API.

Start the API and leave this terminal running:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API docs: <http://127.0.0.1:8000/docs>. `/health` checks application liveness only;
it does not test token validity or provider availability.

## Create monthly CSVs with the ATH close

Open a second terminal in the repository. The downloader uses Python's standard
library and calls your running API. It does not need the Upstox token itself.

One stock, with the default start of 2000-01-01:

```bash
python download_monthly_ath.py TCS --completed-only --output-dir data_upstox
```

All stocks from the committed list:

```bash
python download_monthly_ath.py --symbols-file symbols/nifty_total_market.csv --completed-only --output-dir data_upstox --delay 2
```

Optional custom start date, for one stock or the whole batch:

```bash
python download_monthly_ath.py TCS --start-date 2020-01-01 --completed-only --output-dir data_upstox
python download_monthly_ath.py --symbols-file symbols/nifty_total_market.csv --start-date 2020-01-01 --completed-only --output-dir data_upstox --delay 2
```

Each stock gets a file such as `data_upstox/TCS.NS_monthly_ath.csv`. The separate
`data_upstox` directory keeps this dataset distinguishable from earlier downloads.
If you omit `--output-dir`, the script uses `data/`. `--output filename.csv` chooses
a filename in single-stock mode. `--api-url https://YOUR_HOST` selects a deployed API.
The default request timeout is 120 seconds; change it with `--timeout` if necessary.

The loop validates all symbols, removes duplicates, makes sequential requests and
waits two seconds between stocks by default. It continues when a stock fails and
writes `failed_symbols.csv` in the output directory. Failed stocks' existing CSVs
are preserved. Successful downloads replace their existing CSVs. The process exits
with status 1 if any stock failed, otherwise 0. On success, the failure file contains
only its header. This file is written when the batch finishes.

Retry a nonempty failure list, keeping the same start date and completed-month option
as the original run:

```bash
python download_monthly_ath.py --symbols-file data_upstox/failed_symbols.csv --completed-only --output-dir data_upstox --delay 10
```

Add `--start-date 2020-01-01` to that retry command if you used it in the original run.
Delays do not guarantee avoiding provider rate limits. If the server token is
invalid, update it and restart the API before retrying.

### Meaning of alltimehigh

Rows are sorted oldest first. **`alltimehigh` is the highest monthly CLOSE through
and including the current row**, not the highest intramonth price.

| Month | Close | alltimehigh |
| --- | ---: | ---: |
| January | 100 | 100 |
| February | 90 | 100 |
| March | 120 | 120 |
| April | 110 | 120 |

The first row's ATH equals its close. A record close updates ATH on the same row.
For a breakout signal, compare this row's close with the **previous row's** ATH.
Comparing the close against its own inclusive ATH using `>` would never trigger.

ATH is limited to the downloaded history. A custom start date resets the running
maximum at the first returned row; earlier records are not included. Use the earliest
available history when you need the broadest historical record. `--completed-only`
excludes the unfinished current month; omit it to include current-month data, which
can change. A stock with no completed candles produces a reported download failure.

### Stock universe

[`symbols/nifty_total_market.csv`](symbols/nifty_total_market.csv) contains the
**755 unique NSE symbols** in the official Nifty Total Market constituent download
retrieved on **10 October 2026**. Columns: `symbol`, `company_name`, `industry`, `isin`.
The batch reader uses `symbol`; you can supply your own CSV with this column.

No official "Nifty 700" index was found in NSE's catalogue. Nifty Total Market targets
750 stocks, but the downloaded file contained 755; all source rows were preserved.
[Source and retrieval metadata](symbols/nifty_total_market.metadata.json) record the
URL and source hash. The HTTP Last-Modified was 9 October 2026; the source CSV states
no membership effective date. This snapshot does not refresh automatically and is
not historical membership. Using it for earlier years can introduce survivorship bias.
Not every NSE symbol is guaranteed to resolve in Upstox's current instrument file.
The instrument check on 11 October 2026 resolved 751 of 755 entries. `DUMMYHEG`,
`DUMMYINGL1`, `DUMMYINGL2` and `DUMMYINXGN` were absent from Upstox's current list;
the batch reports these in the failure CSV rather than substituting other instruments.

## REST API

```bash
# Default start: 2000-01-01
curl 'http://127.0.0.1:8000/api/stocks/TCS/monthly'

# Optional start date and completed months only
curl 'http://127.0.0.1:8000/api/stocks/TCS/monthly?start_date=2020-01-01&include_incomplete=false'

# API CSV contains monthly candles; use the downloader to add alltimehigh
curl --fail-with-body 'http://127.0.0.1:8000/api/stocks/TCS/monthly?format=csv' -o TCS_monthly.csv

# BSE, using the exchange's trading symbol
curl 'http://127.0.0.1:8000/api/stocks/TCS/monthly?exchange=BSE'
```

| Parameter | Default | Meaning |
| --- | --- | --- |
| `symbol` | Required | Exchange trading symbol such as `TCS`, `M&M`, `RELIANCE` |
| `exchange` | `NSE` | `NSE` or `BSE`; `.NS`/`.BO` suffixes also select the exchange |
| `start_date` | `2000-01-01` | Inclusive date; must be from 2000-01-01 through today in India |
| `include_incomplete` | `true` | Include the ongoing calendar month |
| `format` | `json` | JSON metadata and candles, or candle CSV |
| `adjusted` | `false` | Compatibility parameter; `true` is rejected with HTTP 422 |

Symbols are case-insensitive. Legacy `.NS`/`.BO` suffixes remain supported and appear
in filenames; the provider is Upstox. A conflicting suffix/exchange is rejected.
URL-encode `&` in an API path as `%26`.

JSON includes `source`, `requested_start`, `requested_end`, `first_available_date`,
`last_available_date`, `count`, `missing_months`, `warnings`, and `data`. Each candle
contains `date`, `open`, `high`, `low`, `close`, `volume`, `trading_days`,
`first_trading_date`, `last_trading_date`, and `is_complete`.

`date` is the calendar month's label, not necessarily a trading day. For a mid-month
start date, the first candle uses only observations on or after that date and carries
a warning. `is_complete` means the month has ended in India; it does not certify that
all trading sessions exist. Missing or invalid volume is null, not an invented zero.
Available-date fields describe the downloaded daily records before monthly filtering.

### How Upstox data is fetched

1. Resolve the exchange trading symbol to an `instrument_key` using Upstox's public
   JSON instrument file, selecting EQ/BE equity records and RR REIT units, excluding warrants and
   debt instruments that can share the same symbol. Cache the equity lookup per exchange and India calendar day
   in each server process. A renamed, suspended or delisted stock may be absent.
2. Call V3 historical candles with `days/1`, the explicit start date and yesterday as
   the inclusive end. Split long history into contiguous chunks of at most ten calendar
   years. From 2000 through 2026 this uses three historical requests per stock.
3. When `include_incomplete=true`, also request V3 intraday `days/1` for today's
   available daily candle. Ignore records outside the requested range. Empty early
   chunks are allowed for later listings; a failed chunk fails the whole request.
4. Sort daily prices in India time and aggregate each calendar month: first open,
   maximum high, minimum low, last close and summed volume. No missing prices are filled.

Upstox prices are used as supplied. This application does not apply corporate-action
adjustments or independently verify Upstox's adjustment policy. The old Yahoo
`--adjusted` option is unsupported and now reports an error; omit it. Keep provider,
start-date and price conventions consistent within a backtest. There is no Yahoo fallback.

| HTTP status | Meaning |
| --- | --- |
| `404` | Symbol absent from current Upstox equity instruments, or no history |
| `422` | Invalid symbol/date, conflicting exchange, ambiguous symbol, or adjusted=true |
| `500` | Upstox token missing or malformed in the server environment |
| `502` | Provider unavailable, invalid data, or rejected/expired server token |
| `503` | Provider rate limit; `Retry-After: 60` |

The REST API and MCP endpoint remain unauthenticated for clients. The server's
provider token is sent only to Upstox's market-data API. Error messages do not include
provider response bodies or the token. This application does not place orders.

## MCP — no client authentication

Starting the API also starts Streamable HTTP MCP at <http://127.0.0.1:8000/mcp>.
Use [`mcp.json`](mcp.json) for an HTTP MCP client. The tool `get_monthly_history` shares
the REST service and accepts `symbol`, `exchange`, `include_incomplete`, and optional
`start_date`; keep `adjusted=false`.

```json
{
  "mcpServers": {
    "monthlyath": {"url": "http://127.0.0.1:8000/mcp"}
  }
}
```

For local stdio clients, use [`mcp.stdio.example.json`](mcp.stdio.example.json), replacing
its Python and script paths with absolute paths on your computer. The launched server
process must receive `UPSTOX_ACCESS_TOKEN` or `UPSTOX_TOKEN` in its environment. Some
clients require explicitly configuring environment inheritance. No separate HTTP server
is needed for stdio, and the token is not an MCP tool argument.

For HTTP hosting, set the provider token in the deployment environment and set
`MCP_PUBLIC_URL` to the deployed origin, for example `https://your-service.run.app`.
Clients then connect to `https://your-service.run.app/mcp`, choosing no authentication.
Host/Origin validation remains enabled. A cloud client needs a reachable URL;
`127.0.0.1` refers to the machine running that client. Hosting-platform access controls
must also allow the intended clients. The repository does not deploy cloud resources.

## Docker

With `UPSTOX_ACCESS_TOKEN` already exported in your shell:

```bash
docker build -t monthlyath .
docker run --rm -p 127.0.0.1:8000:8000 -e UPSTOX_ACCESS_TOKEN monthlyath
```

If you use the alias instead, pass `-e UPSTOX_TOKEN`. The container runs as a non-root
user, listening on `PORT` (default 8000). Run the CSV downloader from your checkout in
another terminal; it is not packaged inside the API image.

## Tests and verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use synthetic prices and mocked Upstox HTTP responses. They cover symbol lookup,
credential handling, chunk boundaries, empty pre-listing history, provider failures,
monthly OHLC, optional start dates, REST/MCP parity, stdio, and batch CSVs with ATH.
The downloader-only tests also run without additional packages:

```bash
python -m unittest discover -s tests -p 'test_download_monthly_ath.py' -v
```

A real authenticated candle download has not been verified in the development environment
because no Upstox token was available there. Run the TCS example with your token and
inspect `source`, available dates, `count`, and `warnings` before starting the full batch.

References:

- [Upstox Historical Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
- [Upstox Intraday Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/)
- [Upstox instruments and JSON downloads](https://upstox.com/developer/api-documentation/instruments/)
- [Nifty Total Market](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-total-market)
- [Official Nifty constituent CSV](https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv)
