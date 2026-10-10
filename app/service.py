"""Transport-independent monthly history service."""

from datetime import date

from app.history import (
    START_DATE, HistoryError, aggregate_monthly, fetch_daily_history, india_today, normalize_symbol,
)
from app.models import MonthlyHistory


def get_monthly_history(
    symbol: str,
    exchange: str | None = None,
    adjusted: bool = False,
    include_incomplete: bool = True,
    start_date: date = START_DATE,
) -> MonthlyHistory:
    resolved_symbol, resolved_exchange = normalize_symbol(symbol, exchange)
    today = india_today()
    if not isinstance(start_date, date) or start_date < START_DATE or start_date > today:
        raise HistoryError(422, f"start_date must be between {START_DATE} and {today}.")
    if adjusted:
        raise HistoryError(422, "adjusted=true is not supported by Upstox. Use adjusted=false for provider-supplied OHLC.")
    daily = fetch_daily_history(resolved_symbol, today, adjusted, start_date, include_incomplete)
    cleaned, candles, missing, warnings = aggregate_monthly(daily, today, include_incomplete, start_date)
    return MonthlyHistory(
        symbol=resolved_symbol,
        exchange=resolved_exchange,
        currency="INR",
        source="Upstox Historical Candle Data V3",
        interval="1mo",
        timezone="Asia/Kolkata",
        requested_start=start_date,
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
