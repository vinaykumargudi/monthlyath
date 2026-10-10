"""Synthetic ATH fixtures and a real local HTTP/CLI integration test."""

from contextlib import contextmanager
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from urllib.parse import parse_qs, urlsplit

from download_monthly_ath import add_alltimehigh, fetch_monthly


def candles():
    return [
        {"date": "2020-01-01", "open": 90, "high": 150, "low": 80, "close": 100, "is_complete": True},
        {"date": "2020-02-01", "open": 100, "high": 160, "low": 80, "close": 90, "is_complete": True},
        {"date": "2020-03-01", "open": 90, "high": 155, "low": 85, "close": 120, "is_complete": True},
        {"date": "2020-04-01", "open": 120, "high": 170, "low": 105, "close": 110, "is_complete": False},
    ]


@contextmanager
def api(payload, status=200):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status == 503:
                self.send_header("Retry-After", "60")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", paths
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class ATHTests(unittest.TestCase):
    def test_breakout_updates_current_row_using_close_not_high(self):
        result = add_alltimehigh(candles())
        self.assertEqual([r["alltimehigh"] for r in result], [100, 100, 120, 120])
        self.assertEqual(result[0]["alltimehigh"], result[0]["close"])
        self.assertEqual(result[2]["alltimehigh"], result[2]["close"])

    def test_unsorted_dates_are_sorted_and_no_future_price_leaks_back(self):
        original = candles()[::-1]
        result = add_alltimehigh(original)
        self.assertEqual([r["date"] for r in result], [r["date"] for r in candles()])
        self.assertEqual([r["alltimehigh"] for r in result], [100, 100, 120, 120])
        self.assertNotIn("alltimehigh", original[0])

    def test_equal_and_falling_closes_preserve_the_record(self):
        rows = candles()
        for row, close in zip(rows, [100.25, 100.25, 95, 90]):
            row["close"] = close
        self.assertEqual([r["alltimehigh"] for r in add_alltimehigh(rows)], [100.25] * 4)

    def test_bad_data_fails_instead_of_silently_losing_rows(self):
        for value in [None, math.nan, math.inf, -1, 0, True, "120"]:
            with self.subTest(close=value):
                rows = candles()
                rows[1]["close"] = value
                with self.assertRaises(ValueError):
                    add_alltimehigh(rows)
        with self.assertRaises(ValueError):
            add_alltimehigh([])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            add_alltimehigh([candles()[0], candles()[0]])

    def test_cli_calls_api_and_writes_every_candle_with_ath(self):
        payload = {"symbol": "TCS.NS", "count": 4, "data": candles(), "warnings": ["Synthetic test coverage warning"]}
        script = Path(__file__).resolve().parents[1] / "download_monthly_ath.py"
        with tempfile.TemporaryDirectory() as temp, api(payload) as (url, paths):
            output = Path(temp) / "nested" / "TCS.csv"
            run = subprocess.run(
                [sys.executable, str(script), "tcs", "--api-url", url, "--output", str(output)],
                text=True, capture_output=True, timeout=10, cwd=temp,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            with output.open(newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames[-1], "alltimehigh")
                result = list(reader)
            self.assertEqual([float(r["alltimehigh"]) for r in result], [100, 100, 120, 120])
            self.assertIn("Synthetic test coverage warning", run.stderr)
            self.assertIn("ongoing-month", run.stderr)
            self.assertEqual(len(paths), 1)
            parsed = urlsplit(paths[0])
            self.assertEqual(parsed.path, "/api/stocks/TCS/monthly")
            self.assertEqual(parse_qs(parsed.query)["include_incomplete"], ["true"])

    def test_exchange_adjustments_and_completed_only_reach_the_api(self):
        with api({"count": 4, "data": candles()}) as (url, paths):
            fetch_monthly("M&M", url, exchange="NSE", adjusted=True, completed_only=True)
            parsed = urlsplit(paths[0])
            self.assertEqual(parsed.path, "/api/stocks/M%26M/monthly")
            self.assertEqual(parse_qs(parsed.query), {
                "format": ["json"], "adjusted": ["true"],
                "include_incomplete": ["false"], "exchange": ["NSE"],
            })

    def test_api_rate_limit_is_reported_and_existing_output_is_preserved(self):
        script = Path(__file__).resolve().parents[1] / "download_monthly_ath.py"
        with tempfile.TemporaryDirectory() as temp, api({"detail": "Rate limited"}, 503) as (url, _):
            output = Path(temp) / "existing.csv"
            output.write_text("previous data\n")
            run = subprocess.run(
                [sys.executable, str(script), "TCS", "--api-url", url, "--output", str(output)],
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(run.returncode, 1)
            self.assertIn("HTTP 503", run.stderr)
            self.assertIn("60 seconds", run.stderr)
            self.assertEqual(output.read_text(), "previous data\n")

    def test_wrong_response_schema_is_rejected(self):
        for payload in [{"data": candles(), "count": 3}, {"data": None}, []]:
            with self.subTest(payload=payload), api(payload) as (url, _):
                with self.assertRaises(ValueError):
                    fetch_monthly("TCS", url)


if __name__ == "__main__":
    unittest.main()
