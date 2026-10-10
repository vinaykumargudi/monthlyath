"""Response models shared by REST and MCP."""

from datetime import date
from pydantic import BaseModel


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


