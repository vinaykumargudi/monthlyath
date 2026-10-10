"""Synthetic test fixtures; these tests never call Yahoo Finance."""

import csv
from datetime import date
from io import StringIO
from unittest.mock import Mock

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import pytest
from yfinance.exceptions import YFPricesMissingError, YFRateLimitError

from app import history, main
from app.history import HistoryError, aggregate_monthly, normalize_symbol

TODAY = date(2026, 10, 10)


def prices(dates, opens=None):
    values = np.array(opens if opens is not None else [100] * len(dates), dtype=float)
    return pd.DataFrame(
        {"Open": values, "High": values + 20, "Low": values - 10,
         "Close": values + 5, "Volume": [1000] * len(dates)},
        index=pd.DatetimeIndex(dates, tz="Asia/Kolkata"),
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "india_today", lambda: TODAY)
    monkeypatch.setattr(main, "fetch_daily_history", lambda *args: prices(
        ["2020-09-21", "2020-09-30", "2020-10-01", "2026-10-09"],
        [100, 120, 110, 150],
    ))
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.mark.parametrize("symbol,exchange,expected", [
    ("tcs", None, ("TCS.NS", "NSE")),
    (" TCS.ns ", None, ("TCS.NS", "NSE")),
    ("TCS.BO", None, ("TCS.BO", "BSE")),
    ("RELIANCE", "BSE", ("RELIANCE.BO", "BSE")),
    ("m&m", None, ("M&M.NS", "NSE")),
    ("BAJAJ-AUTO", "NSE", ("BAJAJ-AUTO.NS", "NSE")),
])
def test_symbols(symbol, exchange, expected):
    assert normalize_symbol(symbol, exchange) == expected


@pytest.mark.parametrize("symbol,exchange", [
    ("", None), ("AAPL.US", None), ("^NSEI", None), ("TCS.BO", "NSE"),
    ("TCS.NS", "BSE"), ("TCS", "NYSE"), ("TCS\r\n", "BSE_BAD"),
])
def test_reject_invalid_or_conflicting_symbols(symbol, exchange):
    with pytest.raises(HistoryError) as error:
        normalize_symbol(symbol, exchange)
    assert error.value.status_code == 422


def test_exact_ohlc_and_leap_year_aggregation():
    frame = prices(["2024-02-01", "2024-02-29", "2024-03-01"], [100, 130, 200])
    _, candles, missing, _ = aggregate_monthly(frame.iloc[::-1], TODAY, True)
    assert candles[0] == {
        "date": date(2024, 2, 1), "open": 100, "high": 150, "low": 90,
        "close": 135, "volume": 2000, "trading_days": 2,
        "first_trading_date": date(2024, 2, 1), "last_trading_date": date(2024, 2, 29),
        "is_complete": True,
    }
    assert candles[1]["open"] == 200
    assert missing == []


def test_2000_cutoff_and_no_future_prices():
    frame = prices(["1999-12-31", "2000-01-03", "2026-10-09", "2026-10-12"])
    data, candles, _, _ = aggregate_monthly(frame, TODAY, True)
    assert data.index[0].date() == date(2000, 1, 3)
    assert data.index[-1].date() == date(2026, 10, 9)
    assert len(candles) == 2


def test_ipo_in_2020_is_not_padded_back_to_2000(client):
    response = client.get("/api/stocks/TCS/monthly")
    assert response.status_code == 200
    body = response.json()
    assert body["requested_start"] == "2000-01-01"
    assert body["requested_end"] == "2026-10-10"
    assert body["first_available_date"] == "2020-09-21"
    assert body["data"][0]["date"] == "2020-09-01"
    assert body["symbol"] == "TCS.NS"
    assert body["count"] == 3
    assert "2020-11" in body["missing_months"]
    assert any("not a verified" in warning for warning in body["warnings"])


def test_current_month_is_partial_even_on_last_calendar_day():
    frame = prices(["2026-09-30", "2026-10-30"])
    _, candles, _, _ = aggregate_monthly(frame, date(2026, 10, 31), True)
    assert [c["is_complete"] for c in candles] == [True, False]
    _, completed, _, _ = aggregate_monthly(frame, date(2026, 11, 1), False)
    assert len(completed) == 2
    assert all(c["is_complete"] for c in completed)


def test_completed_only(client):
    body = client.get("/api/stocks/TCS/monthly?include_incomplete=false").json()
    assert body["count"] == 2
    assert all(c["is_complete"] for c in body["data"])
    assert body["last_available_date"] == "2026-10-09"


def test_just_listed_stock_can_have_zero_completed_months():
    _, candles, missing, _ = aggregate_monthly(prices(["2026-10-09"]), TODAY, False)
    assert candles == []
    assert missing == []


def test_utc_timestamps_group_in_india_calendar_month():
    frame = prices(["2024-02-01", "2024-02-29"])
    frame.index = frame.index.tz_convert("UTC")
    _, candles, _, _ = aggregate_monthly(frame, TODAY, True)
    assert len(candles) == 1
    assert candles[0]["date"] == date(2024, 2, 1)


def test_csv_contains_same_prices_and_partial_flags(client):
    response = client.get("/api/stocks/TCS/monthly?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="TCS.NS_monthly.csv"' in response.headers["content-disposition"]
    rows = list(csv.DictReader(StringIO(response.text)))
    assert len(rows) == 3
    assert float(rows[0]["open"]) == 100
    assert float(rows[0]["close"]) == 125
    assert rows[-1]["is_complete"] == "False"


def test_exchange_and_adjustment_passed_to_provider(client, monkeypatch):
    download = Mock(return_value=prices(["2026-09-30"]))
    monkeypatch.setattr(main, "fetch_daily_history", download)
    response = client.get("/api/stocks/TCS.BO/monthly?adjusted=true")
    assert response.status_code == 200
    assert response.json()["exchange"] == "BSE"
    assert response.json()["adjusted"] is True
    download.assert_called_once_with("TCS.BO", TODAY, True)


@pytest.mark.parametrize("path", [
    "/api/stocks/TCS.NS/monthly?exchange=BSE",
    "/api/stocks/TCS/monthly?exchange=NYSE",
    "/api/stocks/TCS/monthly?format=xml",
    "/api/stocks/TCS/monthly?adjusted=invalid",
    "/api/stocks/AAPL.US/monthly",
])
def test_invalid_requests(client, path):
    assert client.get(path).status_code == 422


def test_download_requests_full_daily_range_and_includes_today(monkeypatch):
    ticker = Mock()
    ticker.history.return_value = prices(["2000-01-03", "2026-10-09"])
    factory = Mock(return_value=ticker)
    monkeypatch.setattr(history.yf, "Ticker", factory)
    history.fetch_daily_history("TCS.NS", TODAY, True)
    factory.assert_called_once_with("TCS.NS")
    args = ticker.history.call_args.kwargs
    assert args["start"] == "2000-01-01"
    assert args["end"] == "2026-10-11"
    assert args["interval"] == "1d"
    assert args["auto_adjust"] is True
    assert "period" not in args
    assert args["raise_errors"] is True


@pytest.mark.parametrize("exception,status", [
    (YFRateLimitError(), 503),
    (YFPricesMissingError("BAD.NS", "no prices"), 404),
    (TimeoutError("provider timed out"), 502),
])
def test_provider_exceptions_have_explicit_http_status(client, monkeypatch, exception, status):
    ticker = Mock()
    ticker.history.side_effect = exception
    monkeypatch.setattr(history.yf, "Ticker", Mock(return_value=ticker))
    monkeypatch.setattr(main, "fetch_daily_history", history.fetch_daily_history)
    response = client.get("/api/stocks/TCS/monthly")
    assert response.status_code == status
    if status == 503:
        assert response.headers["retry-after"] == "60"


def test_empty_provider_response_is_404(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_daily_history", lambda *args: pd.DataFrame())
    assert client.get("/api/stocks/TCS/monthly").status_code == 404


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, 0, -1])
def test_invalid_prices_cannot_be_silently_returned(client, monkeypatch, value):
    frame = prices(["2020-01-01"])
    frame.loc[frame.index[0], "High"] = value
    monkeypatch.setattr(main, "fetch_daily_history", lambda *args: frame)
    assert client.get("/api/stocks/TCS/monthly").status_code == 502


def test_inconsistent_high_low_rejected():
    frame = prices(["2020-01-01"])
    frame.loc[frame.index[0], "High"] = 99
    with pytest.raises(HistoryError, match="inconsistent"):
        aggregate_monthly(frame, TODAY, True)


def test_missing_volume_and_duplicate_dates():
    frame = prices(["2020-01-01", "2020-01-01"], [100, 200]).drop(columns="Volume")
    _, candles, _, warnings = aggregate_monthly(frame, TODAY, True)
    assert candles[0]["open"] == 200
    assert candles[0]["trading_days"] == 1
    assert candles[0]["volume"] is None
    assert any("Duplicate" in warning for warning in warnings)


def test_health_and_openapi(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "/api/stocks/{symbol}/monthly" in client.get("/openapi.json").json()["paths"]
