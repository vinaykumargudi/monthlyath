"""Download monthly prices from MonthlyATH and add the running highest CLOSE.

Example: python download_monthly_ath.py TCS --api-url http://127.0.0.1:8000
Uses only Python's standard library. No API authentication is required.
"""

import argparse
import csv
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


def normalized_symbol(symbol: str, exchange: str | None = None) -> str:
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9&_-]{0,39}(?:\.(?:NS|BO))?", symbol):
        raise ValueError("Use an exchange symbol such as TCS, RELIANCE.NS or M&M.")
    suffix = ".BO" if exchange == "BSE" else ".NS"
    if symbol.endswith((".NS", ".BO")):
        if exchange and not symbol.endswith(suffix):
            raise ValueError(f"Symbol {symbol} conflicts with exchange {exchange}.")
        return symbol
    return symbol + suffix


def read_symbols(path: Path, exchange: str | None = None) -> list[str]:
    """Read a symbol column, validate the entire list, and remove duplicates."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = [key for key in (reader.fieldnames or []) if key.strip().lower() == "symbol"]
        if len(columns) != 1:
            raise ValueError("The symbols CSV must contain exactly one 'symbol' column.")
        symbols = []
        for row in reader:
            symbol = row.get(columns[0])
            if not symbol or not symbol.strip():
                raise ValueError(f"Missing symbol at CSV line {reader.line_num}.")
            try:
                symbols.append(normalized_symbol(symbol, exchange))
            except ValueError as exc:
                raise ValueError(f"CSV line {reader.line_num}: {exc}") from exc
    if not symbols:
        raise ValueError("The symbols CSV is empty.")
    return list(dict.fromkeys(symbols))


def add_alltimehigh(rows: list[dict]) -> list[dict]:
    """Sort oldest first and add max(close[0:i+1]) to each row.

    The current row IS included. The first row's ATH equals its own close.
    The calculation is limited to the history returned by the API.
    """
    if not isinstance(rows, list) or not rows:
        raise ValueError("The API returned no monthly candles to export.")
    dated_rows = []
    months = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each monthly candle must be a JSON object.")
        try:
            month_date = date.fromisoformat(row["date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each candle must have a valid YYYY-MM-DD date.") from exc
        month = (month_date.year, month_date.month)
        if month in months:
            raise ValueError(f"Duplicate monthly candle: {month_date:%Y-%m}.")
        months.add(month)
        close = row.get("close")
        if (
            isinstance(close, bool) or not isinstance(close, (int, float))
            or not math.isfinite(close) or close <= 0
        ):
            raise ValueError(f"Invalid closing price for {month_date}: {close!r}.")
        dated_rows.append((month_date, row))

    result = []
    highest_close = None
    for _, row in sorted(dated_rows, key=lambda pair: pair[0]):
        close = row["close"]
        highest_close = close if highest_close is None else max(highest_close, close)
        # Copy the candle so the caller's original response is unchanged.
        result.append({**row, "alltimehigh": highest_close})
    return result


def fetch_monthly(
    symbol: str,
    api_url: str,
    *,
    exchange: str | None = None,
    adjusted: bool = False,
    completed_only: bool = False,
    timeout: float = 60,
) -> dict:
    """Call the existing REST API, not Yahoo Finance directly."""
    symbol = symbol.strip().upper()
    normalized_symbol(symbol, exchange)
    api_url = api_url.strip().rstrip("/")
    parsed = urlsplit(api_url)
    if (
        parsed.scheme not in ("http", "https") or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise ValueError("--api-url must be an HTTP(S) base URL without credentials, query or fragment.")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("--timeout must be a positive finite number of seconds.")
    params = {
        "format": "json",
        "adjusted": str(adjusted).lower(),
        "include_incomplete": str(not completed_only).lower(),
    }
    if exchange:
        params["exchange"] = exchange
    url = f"{api_url}/api/stocks/{quote(symbol, safe='')}/monthly?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        with exc:
            body = exc.read(4096).decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After")
        try:
            detail = json.loads(body).get("detail", body)
        except (ValueError, AttributeError):
            detail = body
        retry = f" Retry after {retry_after} seconds." if retry_after else ""
        raise ValueError(f"API returned HTTP {exc.code}: {detail}.{retry}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError(f"Cannot reach the monthly-data API: {exc}.") from exc
    except (ValueError, UnicodeError) as exc:
        raise ValueError("The API response is not valid JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Expected a MonthlyATH JSON response containing a data array.")
    if payload.get("count") != len(payload["data"]):
        raise ValueError("The API candle count does not match the returned rows.")
    return payload


def write_csv(rows: list[dict], output: Path) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row if key != "alltimehigh"))
    fields.append("alltimehigh")
    atomic_write_csv(rows, output, fields)


def atomic_write_csv(rows: list[dict], output: Path, fields: list[str]) -> None:
    """Replace the destination only after a complete CSV has been written."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, output)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", help="Indian exchange symbol, e.g. TCS or RELIANCE.NS")
    parser.add_argument("--symbols-file", type=Path, help="CSV containing a symbol column; download each stock")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="API base URL (default: %(default)s)")
    parser.add_argument("--output", type=Path, help="CSV destination; default: data/SYMBOL_monthly_ath.csv")
    parser.add_argument("--output-dir", type=Path, default=Path("data"), help="Batch destination directory (default: %(default)s)")
    parser.add_argument("--delay", type=float, default=2, help="Seconds between batch requests (default: %(default)s)")
    parser.add_argument("--exchange", choices=["NSE", "BSE"], help="Defaults to NSE or the symbol's suffix")
    parser.add_argument("--adjusted", action="store_true", help="Ask the API for adjusted OHLC")
    parser.add_argument("--completed-only", action="store_true", help="Exclude the ongoing calendar month")
    parser.add_argument("--timeout", type=float, default=60, help="API timeout in seconds (default: %(default)s)")
    args = parser.parse_args(argv)
    if bool(args.symbol) == bool(args.symbols_file):
        parser.error("Provide either a stock symbol or --symbols-file.")
    if args.symbols_file and args.output:
        parser.error("Use --output-dir with --symbols-file, not --output.")
    if not math.isfinite(args.delay) or args.delay < 0:
        parser.error("--delay must be a non-negative finite number of seconds.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number of seconds.")
    try:
        symbols = read_symbols(args.symbols_file, args.exchange) if args.symbols_file else [args.symbol]
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    failures = []
    for index, symbol in enumerate(symbols):
        if index and args.symbols_file:
            time.sleep(args.delay)
        try:
            download_one(symbol, args)
        except (ValueError, OSError) as exc:
            print(f"Error for {symbol}: {exc}", file=sys.stderr)
            failures.append({"symbol": symbol, "error": str(exc)})
        if args.symbols_file:
            print(f"Progress: {index + 1}/{len(symbols)}", flush=True)
    if args.symbols_file:
        report = args.output_dir / "failed_symbols.csv"
        try:
            atomic_write_csv(failures, report, ["symbol", "error"])
        except OSError as exc:
            print(f"Cannot write failure report: {exc}", file=sys.stderr)
            return 1
        print(f"Finished: {len(symbols) - len(failures)} saved, {len(failures)} failed. Failure list: {report}")
    return 1 if failures else 0


def download_one(symbol: str, args: argparse.Namespace) -> None:
    payload = fetch_monthly(
        symbol, args.api_url, exchange=args.exchange, adjusted=args.adjusted,
        completed_only=args.completed_only, timeout=args.timeout,
    )
    rows = add_alltimehigh(payload["data"])
    # Use a local filename derived from validated input, not response text.
    symbol = normalized_symbol(symbol, args.exchange)
    output = args.output or args.output_dir / f"{symbol}_monthly_ath.csv"
    write_csv(rows, output)
    print(f"Saved {len(rows)} rows for {symbol} to {output}")
    print(f"Range: {rows[0]['date']} through {rows[-1]['date']}")
    print("alltimehigh = highest monthly CLOSE so far, including the current row.")
    for warning in payload.get("warnings", []):
        print(f"Coverage warning for {symbol}: {warning}", file=sys.stderr)
    if any(row.get("is_complete") is False for row in rows):
        print("Note: ongoing-month data is included and may change. Use --completed-only for closed months.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
