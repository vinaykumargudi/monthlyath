"""Transport-independent monthly history service."""

from app.history import (
    START_DATE, aggregate_monthly, fetch_daily_history, india_today, normalize_symbol,
)
from app.models import MonthlyHistory


def get_monthly_history(
    symbol: str,
    exchange: str | None = None,
    adjusted: bool = False,
    include_incomplete: bool = True,
) -> MonthlyHistory:
    yahoo_symbol, resolved_exchange = normalize_symbol(symbol, exchange)
    today = india_today()
    daily = fetch_daily_history(yahoo_symbol, today, adjusted)
    cleaned, candles, missing, warnings = aggregate_monthly(daily, today, include_incomplete)
    return MonthlyHistory(
        symbol=yahoo_symbol,
        exchange=resolved_exchange,
        currency="INR",
        source="Yahoo Finance via yfinance",
        interval="1mo",
        timezone="Asia/Kolkata",
        requested_start=START_DATE,
        requested_end=today,
        first_available_date=cleaned.index[0].date(),
        last_available_date=cleaned.index[-1].date(),
        adjusted=adjusted,
        include_incomplete=include_incomplete,
        count=len(candles),
        missing_months=missing,
        warnings=warnings,
        data=candles,
    )
