"""HTTP interface. Run with: uvicorn app.main:app --reload"""

import csv
from datetime import date
from io import StringIO
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Path, Query, Response
from pydantic import BaseModel

from app.history import (
    START_DATE,
    HistoryError,
    aggregate_monthly,
    fetch_daily_history,
    india_today,
    normalize_symbol,
)

app = FastAPI(
    title="Indian Stock Monthly OHLC API",
    version="1.0.0",
    description=(
        "Monthly OHLC from 2000-01-01 through today in Asia/Kolkata, "
        "limited to available Yahoo Finance history. No pre-listing candles are created."
    ),
)


class MonthlyCandle(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int | None
    trading_days: int
    first_trading_date: date
    last_trading_date: date
    is_complete: bool


class MonthlyHistory(BaseModel):
    symbol: str
    exchange: str
    currency: str
    source: str
    interval: str
    timezone: str
    requested_start: date
    requested_end: date
    first_available_date: date
    last_available_date: date
    adjusted: bool
    include_incomplete: bool
    count: int
    missing_months: list[str]
    warnings: list[str]
    data: list[MonthlyCandle]


@app.get("/health")
def health() -> dict[str, str]:
    """Application liveness; does not call the data provider."""
    return {"status": "ok"}


@app.get(
    "/api/stocks/{symbol}/monthly",
    response_model=MonthlyHistory,
    responses={
        200: {"content": {"text/csv": {"schema": {"type": "string"}}}},
        404: {"description": "No history available for this symbol"},
        502: {"description": "Provider unavailable or invalid prices"},
        503: {"description": "Provider rate limit; retry later"},
    },
)
def monthly_history(
    symbol: Annotated[str, Path(min_length=1, max_length=43, examples=["TCS"])],
    exchange: Annotated[Literal["NSE", "BSE"] | None, Query(description="Defaults to NSE, or the symbol's .NS/.BO suffix.")] = None,
    adjusted: Annotated[bool, Query(description="Apply Yahoo's adjusted-close factor to daily OHLC before aggregation.")] = False,
    include_incomplete: Annotated[bool, Query(description="Include the ongoing month. Set false for completed calendar months only.")] = True,
    format: Annotated[Literal["json", "csv"], Query(description="JSON metadata and candles, or a downloadable CSV.")] = "json",
) -> MonthlyHistory | Response:
    try:
        yahoo_symbol, resolved_exchange = normalize_symbol(symbol, exchange)
        today = india_today()
        daily = fetch_daily_history(yahoo_symbol, today, adjusted)
        cleaned, candles, missing, warnings = aggregate_monthly(daily, today, include_incomplete)
    except HistoryError as exc:
        headers = {"Retry-After": "60"} if exc.status_code == 503 else None
        raise HTTPException(exc.status_code, str(exc), headers=headers) from exc

    result = MonthlyHistory(
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
    if format == "csv":
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=list(MonthlyCandle.model_fields))
        writer.writeheader()
        for candle in result.data:
            writer.writerow(candle.model_dump(mode="json"))
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{yahoo_symbol}_monthly.csv"',
                "X-Data-Source": "Yahoo Finance",
                "X-Price-Adjusted": str(adjusted).lower(),
                "X-First-Available-Date": result.first_available_date.isoformat(),
                "X-Last-Available-Date": result.last_available_date.isoformat(),
                "X-Missing-Months-Count": str(len(missing)),
                "X-Coverage-Verified": "false",
            },
        )
    return result
