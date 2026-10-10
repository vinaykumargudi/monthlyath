"""Synthetic Upstox HTTP fixtures; never use a real access token."""

from datetime import date, timedelta
import gzip
from io import BytesIO
import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from app import upstox
from app.errors import HistoryError
from app.history import aggregate_monthly

TODAY = date(2026, 10, 11)
KEY = "NSE_EQ|INE467B01029"


def candle(day, opening=100):
    return [f"{day}T00:00:00+05:30", opening, opening + 20, opening - 10, opening + 5, 1000, 0]


def encoded(candles):
    return json.dumps({"status": "success", "data": {"candles": candles}}).encode()


@pytest.fixture(autouse=True)
def fake_credentials_and_empty_cache(monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "synthetic-test-token")
    monkeypatch.delenv("UPSTOX_TOKEN", raising=False)
    upstox.instrument_map.cache_clear()
    yield
    upstox.instrument_map.cache_clear()


def test_full_range_chunking_auth_symbol_lookup_and_today_merge(monkeypatch):
    requests = []
    instruments = gzip.compress(json.dumps([
        {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "TCS", "instrument_key": KEY},
        {"segment": "NSE_EQ", "instrument_type": "W1", "trading_symbol": "TCS", "instrument_key": "NSE_EQ|WARRANT123"},
        {"segment": "NSE_EQ", "instrument_type": "D1", "trading_symbol": "TCS", "instrument_key": "NSE_EQ|DEBT123"},
        {"segment": "NSE_EQ", "instrument_type": "RR", "trading_symbol": "BIRET", "instrument_key": "NSE_EQ|INE0FDU25010"},
        {"segment": "NSE_FO", "trading_symbol": "TCS", "instrument_key": "NSE_FO|123"},
    ]).encode())

    def open_request(request, timeout):
        requests.append(request)
        url = request.full_url
        if url.endswith("NSE.json.gz"):
            assert request.get_header("Authorization") is None
            return BytesIO(instruments)
        assert request.get_header("Authorization") == "Bearer synthetic-test-token"
        assert "NSE_EQ%7CINE467B01029" in url
        if "/intraday/" in url:
            return BytesIO(encoded([candle("2026-10-11", 150), candle("2026-10-10", 999)]))
        if url.endswith("/2009-12-31/2000-01-01"):
            return BytesIO(encoded([candle("2000-01-03"), candle("1999-12-31", 999)]))
        if url.endswith("/2019-12-31/2010-01-01"):
            return BytesIO(encoded([]))
        assert url.endswith("/2026-10-10/2020-01-01")
        return BytesIO(encoded([candle("2026-10-09", 120), candle("2020-02-29", 110)]))

    monkeypatch.setattr(upstox._opener, "open", open_request)
    frame = upstox.fetch_daily_history("TCS.NS", TODAY)
    _, months, _, _ = aggregate_monthly(frame, TODAY, True)
    assert len(requests) == 5  # one instrument file, three history chunks, one intraday
    assert months[0]["date"] == date(2000, 1, 1)
    assert months[-1]["open"] == 120
    assert months[-1]["high"] == 170
    assert months[-1]["close"] == 155
    assert months[-1]["volume"] == 2000
    assert months[-1]["trading_days"] == 2
    assert months[-1]["is_complete"] is False
    assert upstox.resolve_instrument("TCS.NS", TODAY) == KEY
    assert upstox.resolve_instrument("BIRET.NS", TODAY) == "NSE_EQ|INE0FDU25010"
    assert len(requests) == 5  # same-day lookup uses the cache


def test_custom_start_and_completed_only_omit_intraday(monkeypatch):
    monkeypatch.setattr(upstox, "resolve_instrument", lambda *args: KEY)
    fetch = Mock(return_value=encoded([candle("2020-02-29")]))
    monkeypatch.setattr(upstox, "read_url", fetch)
    frame = upstox.fetch_daily_history("TCS.NS", TODAY, False, date(2020, 2, 29), False)
    assert len(frame) == 1
    assert fetch.call_count == 1
    assert fetch.call_args.args[0].endswith("/2026-10-10/2020-02-29")


def test_decade_chunks_are_contiguous_for_leap_day_starts():
    chunks = list(upstox.date_chunks(date(2000, 2, 29), TODAY))
    assert chunks[0][0] == date(2000, 2, 29)
    assert chunks[-1][1] == TODAY
    for i, (start, end) in enumerate(chunks):
        assert end - start < timedelta(days=3653)
        if i:
            assert chunks[i - 1][1] + timedelta(days=1) == start


def test_later_listing_empty_early_chunks_are_not_fabricated(monkeypatch):
    monkeypatch.setattr(upstox, "resolve_instrument", lambda *args: KEY)
    monkeypatch.setattr(upstox, "read_url", Mock(side_effect=[encoded([]), encoded([]), encoded([candle("2020-09-21")])]))
    frame = upstox.fetch_daily_history("TCS.NS", TODAY, include_today=False)
    assert len(frame) == 1
    assert frame.index[0].date() == date(2020, 9, 21)


def test_failed_chunk_never_returns_partial_history(monkeypatch):
    monkeypatch.setattr(upstox, "resolve_instrument", lambda *args: KEY)
    monkeypatch.setattr(upstox, "read_url", Mock(side_effect=[encoded([candle("2000-01-03")]), HistoryError(503, "rate limit")]))
    with pytest.raises(HistoryError, match="rate limit"):
        upstox.fetch_daily_history("TCS.NS", TODAY)


@pytest.mark.parametrize("provider_status,expected", [(401, 502), (403, 502), (429, 503), (500, 502), (302, 502)])
def test_provider_errors_are_safe_and_explicit(monkeypatch, provider_status, expected):
    error = HTTPError("https://api.upstox.com", provider_status, "sensitive provider response", {}, BytesIO(b"secret-body"))
    monkeypatch.setattr(upstox._opener, "open", Mock(side_effect=error))
    with pytest.raises(HistoryError) as caught:
        upstox.read_url(upstox.API_ROOT, "synthetic-test-token")
    assert caught.value.status_code == expected
    assert "secret-body" not in str(caught.value)
    assert "synthetic-test-token" not in str(caught.value)


def test_network_error_is_not_exposed(monkeypatch):
    monkeypatch.setattr(upstox._opener, "open", Mock(side_effect=URLError("sensitive details")))
    with pytest.raises(HistoryError) as caught:
        upstox.read_url(upstox.API_ROOT, "synthetic-test-token")
    assert caught.value.status_code == 502
    assert "sensitive details" not in str(caught.value)


def test_missing_token_and_alias(monkeypatch):
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN")
    with pytest.raises(HistoryError, match="UPSTOX_ACCESS_TOKEN") as caught:
        upstox.fetch_daily_history("TCS.NS", TODAY)
    assert caught.value.status_code == 500
    monkeypatch.setenv("UPSTOX_TOKEN", "alias-test-token")
    assert upstox.access_token() == "alias-test-token"
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "Bearer invalid-format")
    with pytest.raises(HistoryError, match="raw token"):
        upstox.access_token()


def test_bse_and_ambiguous_or_missing_symbol_resolution(monkeypatch):
    records = [
        {"segment": "BSE_EQ", "instrument_type": "EQ", "trading_symbol": "TCS", "instrument_key": "BSE_EQ|INE467B01029"},
        {"segment": "BSE_EQ", "instrument_type": "EQ", "trading_symbol": "BAD", "instrument_key": "BSE_EQ|INE111111111"},
        {"segment": "BSE_EQ", "instrument_type": "EQ", "trading_symbol": "BAD", "instrument_key": "BSE_EQ|INE222222222"},
    ]
    reader = Mock(return_value=gzip.compress(json.dumps(records).encode()))
    monkeypatch.setattr(upstox, "read_url", reader)
    assert upstox.resolve_instrument("TCS.BO", TODAY) == "BSE_EQ|INE467B01029"
    assert reader.call_args.args[0].endswith("/BSE.json.gz")
    with pytest.raises(HistoryError) as missing:
        upstox.resolve_instrument("UNKNOWN.BO", TODAY)
    assert missing.value.status_code == 404
    with pytest.raises(HistoryError) as ambiguous:
        upstox.resolve_instrument("BAD.BO", TODAY)
    assert ambiguous.value.status_code == 422


@pytest.mark.parametrize("response", [b"not-json", b"[]", b'{"status":"success","data":{}}', encoded([["bad"]]), encoded([["bad-date", 1, 2, 1, 2, 3]]), encoded([["2020-01-01T00:00:00+05:30", True, 2, 1, 2, 3]])])
def test_malformed_candles_are_rejected(monkeypatch, response):
    monkeypatch.setattr(upstox, "read_url", lambda *args: response)
    with pytest.raises(HistoryError) as caught:
        upstox.candle_frame(upstox.API_ROOT, "synthetic-test-token")
    assert caught.value.status_code == 502


def test_empty_history_is_explicit_404(monkeypatch):
    monkeypatch.setattr(upstox, "resolve_instrument", lambda *args: KEY)
    monkeypatch.setattr(upstox, "read_url", lambda *args: encoded([]))
    with pytest.raises(HistoryError) as caught:
        upstox.fetch_daily_history("TCS.NS", TODAY)
    assert caught.value.status_code == 404
