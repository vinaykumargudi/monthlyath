"""Fetch daily history and aggregate it into Indian calendar-month candles."""

from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from app.errors import HistoryError
from app.upstox import fetch_daily_history

INDIA = ZoneInfo("Asia/Kolkata")
START_DATE = date(2000, 1, 1)
PRICE_COLUMNS = ["Open", "High", "Low", "Close"]


def india_today() -> date:
    return datetime.now(INDIA).date()


def normalize_symbol(symbol: str, exchange: str | None = None) -> tuple[str, str]:
    """Bare symbols default to NSE; legacy .NS/.BO suffixes select an exchange."""
    symbol = symbol.strip().upper()
    suffix_exchange = None
    if symbol.endswith((".NS", ".BO")):
        suffix_exchange = "NSE" if symbol.endswith(".NS") else "BSE"
        symbol = symbol[:-3]
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9&_-]{0,39}", symbol):
        raise HistoryError(422, "Use an Indian symbol such as TCS, M&M, TCS.NS or TCS.BO.")
    if exchange is not None and exchange not in ("NSE", "BSE"):
        raise HistoryError(422, "exchange must be NSE or BSE.")
    if exchange and suffix_exchange and exchange != suffix_exchange:
        raise HistoryError(422, "exchange conflicts with the symbol's .NS/.BO suffix.")
    resolved_exchange = exchange or suffix_exchange or "NSE"
    suffix = ".NS" if resolved_exchange == "NSE" else ".BO"
    return symbol + suffix, resolved_exchange


def aggregate_monthly(
    daily: pd.DataFrame, today: date, include_incomplete: bool, start_date: date = START_DATE,
) -> tuple[pd.DataFrame, list[dict], list[str], list[str]]:
    """Return cleaned daily rows, candles, missing months, and coverage warnings."""
    if daily is None or daily.empty:
        raise HistoryError(404, "No historical prices were returned.")
    if not set(PRICE_COLUMNS).issubset(daily.columns):
        raise HistoryError(502, "The data provider returned an unexpected price format.")
    data = daily.copy()
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.hasnans:
        raise HistoryError(502, "The data provider returned invalid trading dates.")
    if data.index.tz is not None:
        data.index = data.index.tz_convert(INDIA).tz_localize(None)
    data.index = data.index.normalize()
    data = data.loc[(data.index.date >= start_date) & (data.index.date <= today)]
    data = data.sort_index(kind="stable")
    warnings = []
    if data.index.has_duplicates:
        warnings.append("Duplicate trading dates were returned; the last row for each date was used.")
        data = data.loc[~data.index.duplicated(keep="last")]
    if data.empty:
        raise HistoryError(404, f"No prices are available between {start_date} and today.")

    prices = data[PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    # Never silently calculate a candle from corrupt or partially missing OHLC rows.
    if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
        raise HistoryError(502, "The provider returned missing, non-finite or non-positive OHLC prices.")
    if (
        (prices["High"] < prices[["Open", "Close", "Low"]].max(axis=1)).any()
        or (prices["Low"] > prices[["Open", "Close", "High"]].min(axis=1)).any()
    ):
        raise HistoryError(502, "The provider returned inconsistent high/low prices.")
    data[PRICE_COLUMNS] = prices
    # Volume is optional; missing or invalid volume becomes null, never a made-up zero.
    volume = pd.to_numeric(data.get("Volume", pd.Series(index=data.index, dtype=float)), errors="coerce")
    volume = volume.where(np.isfinite(volume) & (volume >= 0) & (volume % 1 == 0))
    data["Volume"] = volume

    periods = data.index.to_period("M")
    expected = pd.period_range(periods.min(), periods.max(), freq="M")
    available_months = set(periods)
    missing_months = [str(month) for month in expected if month not in available_months]
    if missing_months:
        warnings.append("Some months within the returned history have no prices; no candles were fabricated.")
    if data.index[0].to_period("M") > pd.Period(start_date, freq="M"):
        warnings.append(
            "History starts after the requested start month. This may reflect a later listing or limited provider coverage; "
            "the first returned date is not a verified IPO/listing date."
        )
    if (today - data.index[-1].date()).days > 7:
        warnings.append("The latest returned trading date is over 7 days old; history may be stale or trading suspended.")
    warnings.append(
        "History is limited to Upstox coverage. Listing-date coverage and individual missing "
        "trading sessions are not independently verified."
    )
    warnings.append("OHLC is used as supplied by Upstox; this application does not apply corporate-action adjustments.")
    if start_date.day != 1:
        warnings.append("The requested start date is mid-month; the first candle includes only dates on or after that date.")

    current_month = pd.Period(today, freq="M")
    candles = []
    for month, group in data.groupby(periods, sort=True):
        is_complete = month < current_month
        if not include_incomplete and not is_complete:
            continue
        candles.append({
            "date": month.start_time.date(),
            "open": float(group["Open"].iloc[0]),
            "high": float(group["High"].max()),
            "low": float(group["Low"].min()),
            "close": float(group["Close"].iloc[-1]),
            "volume": int(group["Volume"].sum()) if group["Volume"].notna().all() else None,
            "trading_days": len(group),
            "first_trading_date": group.index[0].date(),
            "last_trading_date": group.index[-1].date(),
            "is_complete": is_complete,
        })
    return data, candles, missing_months, warnings
