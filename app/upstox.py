"""Read-only Upstox V3 candles and cached symbol-to-instrument resolution."""

from datetime import date, timedelta
from functools import lru_cache
import gzip
import json
import math
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pandas as pd

from app.errors import HistoryError

API_ROOT = "https://api.upstox.com/v3/historical-candle"
INSTRUMENT_ROOT = "https://assets.upstox.com/market-quote/instruments/exchange"
DEFAULT_START = date(2000, 1, 1)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the bearer token to a redirected URL.
        return None


_opener = build_opener(NoRedirect())


def access_token() -> str:
    token = (os.environ.get("UPSTOX_ACCESS_TOKEN") or os.environ.get("UPSTOX_TOKEN") or "").strip()
    if not token:
        raise HistoryError(500, "Set UPSTOX_ACCESS_TOKEN (or UPSTOX_TOKEN) in the API server environment.")
    if any(character.isspace() for character in token):
        raise HistoryError(500, "The Upstox token must be the raw token without 'Bearer ' or whitespace.")
    return token


def read_url(url: str, token: str | None = None) -> bytes:
    headers = {"Accept": "application/json", "User-Agent": "monthlyath/2.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with _opener.open(Request(url, headers=headers), timeout=20) as response:
            return response.read()
    except HTTPError as exc:
        status = exc.code
        exc.close()
        # Do not expose provider response bodies, request headers or credentials.
        if status in (401, 403) and token:
            raise HistoryError(502, "Upstox rejected the server token. Check its validity, expiry and market-data permissions; update the environment and restart the API.") from None
        if status == 429:
            raise HistoryError(503, "Upstox rate limit reached. Retry after 60 seconds.") from None
        raise HistoryError(502, f"Upstox data request failed (HTTP {status}). Please retry or check the instrument.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise HistoryError(502, "Unable to reach Upstox market data. Please retry.") from None


@lru_cache(maxsize=4)
def instrument_map(exchange: str, cache_date: date) -> dict[str, set[str]]:
    """Refresh once per India calendar day per process; never cache credentials."""
    raw = read_url(f"{INSTRUMENT_ROOT}/{exchange}.json.gz")
    try:
        records = json.loads(gzip.decompress(raw))
    except (OSError, EOFError, ValueError, UnicodeError):
        raise HistoryError(502, "Upstox returned an invalid instrument file.") from None
    if not isinstance(records, list):
        raise HistoryError(502, "Upstox returned an invalid instrument list.")
    result = {}
    for record in records:
        if not isinstance(record, dict) or record.get("segment") != f"{exchange}_EQ":
            continue
        # Warrants/debentures can share a stock's trading symbol and EQ segment.
        # Include REIT units (RR) present in the Nifty Total Market universe too.
        if record.get("instrument_type") not in {"EQ", "BE", "RR"}:
            continue
        symbol, key = record.get("trading_symbol"), record.get("instrument_key")
        if isinstance(symbol, str) and isinstance(key, str) and re.fullmatch(rf"{exchange}_EQ\|[A-Z0-9]+", key):
            result.setdefault(symbol.upper(), set()).add(key)
    if not result:
        raise HistoryError(502, f"Upstox's {exchange} instrument file contains no equity instruments.")
    return result


def resolve_instrument(symbol: str, today: date) -> str:
    exchange = "BSE" if symbol.endswith(".BO") else "NSE"
    bare_symbol = symbol[:-3]
    keys = instrument_map(exchange, today).get(bare_symbol, set())
    if not keys:
        raise HistoryError(404, f"{bare_symbol} was not found in Upstox's current {exchange} equity instruments. Check for a renamed, suspended or delisted stock.")
    if len(keys) != 1:
        raise HistoryError(422, f"{bare_symbol} matches multiple Upstox instruments; an unambiguous symbol is required.")
    return next(iter(keys))


def candle_frame(url: str, token: str) -> pd.DataFrame:
    try:
        payload = json.loads(read_url(url, token))
    except (ValueError, UnicodeError):
        raise HistoryError(502, "Upstox returned invalid JSON.") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    candles = data.get("candles") if isinstance(data, dict) else None
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(candles, list):
        raise HistoryError(502, "Upstox returned an unexpected candle response.")
    if not candles:
        return pd.DataFrame()
    rows, dates = [], []
    for candle in candles:
        if not isinstance(candle, list) or len(candle) < 6:
            raise HistoryError(502, "Upstox returned a malformed candle.")
        try:
            timestamp = pd.Timestamp(candle[0])
            if pd.isna(timestamp) or timestamp.tzinfo is None:
                raise ValueError
            timestamp = timestamp.tz_convert("Asia/Kolkata")
        except (TypeError, ValueError, OverflowError):
            raise HistoryError(502, "Upstox returned an invalid candle timestamp.") from None
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in candle[1:5]):
            raise HistoryError(502, "Upstox returned missing, non-finite or non-positive OHLC prices.")
        dates.append(timestamp)
        rows.append(candle[1:6])
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(dates))


def date_chunks(start: date, end: date):
    """Inclusive, contiguous chunks shorter than ten years, including leap days."""
    while start <= end:
        # Year boundaries avoid February 29 replacement problems.
        chunk_end = min(end, date(start.year + 9, 12, 31))
        yield start, chunk_end
        start = chunk_end + timedelta(days=1)


def fetch_daily_history(
    symbol: str, today: date, adjusted: bool = False,
    start_date: date = DEFAULT_START, include_today: bool = True,
) -> pd.DataFrame:
    if adjusted:
        raise HistoryError(422, "Upstox does not expose the Yahoo adjusted-close option. Use adjusted=false; prices are returned as supplied by Upstox.")
    token = access_token()
    key = quote(resolve_instrument(symbol, today), safe="")
    frames = []
    for start, end in date_chunks(start_date, today - timedelta(days=1)):
        frame = candle_frame(f"{API_ROOT}/{key}/days/1/{end.isoformat()}/{start.isoformat()}", token)
        if not frame.empty:
            # A provider must not contribute prices from outside this request.
            frame = frame.loc[(frame.index.date >= start) & (frame.index.date <= end)]
            frames.append(frame)
    if include_today:
        frame = candle_frame(f"{API_ROOT}/intraday/{key}/days/1", token)
        if not frame.empty:
            frames.append(frame.loc[frame.index.date == today])
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise HistoryError(404, f"Upstox returned no history for {symbol} since {start_date}.")
    # Any failed chunk raises before this point: never present partial downloads as complete.
    return pd.concat(frames)
