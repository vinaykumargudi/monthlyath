"""Synthetic ATH fixtures and a real local HTTP/CLI integration test."""

from contextlib import contextmanager
import csv
from datetime import date
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

from download_monthly_ath import add_alltimehigh, fetch_monthly, read_symbols


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
            response_status, response_payload = payload(self.path) if callable(payload) else (status, payload)
            self.send_response(response_status)
            self.send_header("Content-Type", "application/json")
            if response_status == 503:
                self.send_header("Retry-After", "60")
            self.end_headers()
            self.wfile.write(json.dumps(response_payload).encode())

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

    def test_exchange_start_date_and_completed_only_reach_the_api(self):
        with api({"count": 4, "data": candles()}) as (url, paths):
            fetch_monthly("M&M", url, exchange="NSE", completed_only=True, start_date=date(2020, 1, 1))
            parsed = urlsplit(paths[0])
            self.assertEqual(parsed.path, "/api/stocks/M%26M/monthly")
            self.assertEqual(parse_qs(parsed.query), {
                "format": ["json"], "adjusted": ["false"], "start_date": ["2020-01-01"],
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

    def test_symbols_csv_accepts_bom_official_header_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "symbols.csv"
            path.write_text('\ufeffSymbol,Company Name\ntcs,Tata\nTCS.NS,Tata\nM&M,"Mahindra, Ltd"\n')
            self.assertEqual(read_symbols(path), ["TCS.NS", "M&M.NS"])

    def test_invalid_csv_is_rejected_before_any_download(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "symbols.csv"
            for content in ['company\nTCS\n', 'symbol\n', 'symbol\nTCS\n../bad\n',
                            'symbol,company\n,Tata\n', 'symbol,Symbol\nTCS,TCS\n']:
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        read_symbols(path)

    def test_batch_continues_after_failure_and_failure_file_can_be_retried(self):
        def response(path):
            if '/BAD.NS/' in path:
                return 503, {"detail": "Synthetic rate limit"}
            return 200, {"count": 4, "data": candles()}

        script = Path(__file__).resolve().parents[1] / "download_monthly_ath.py"
        with tempfile.TemporaryDirectory() as temp, api(response) as (url, paths):
            root = Path(temp)
            symbols = root / "symbols.csv"
            symbols.write_text('symbol\nTCS\nBAD\nM&M\nTCS.NS\n')
            output = root / "data"
            output.mkdir()
            old = output / "BAD.NS_monthly_ath.csv"
            old.write_text("previous data\n")
            command = [sys.executable, str(script), '--symbols-file', str(symbols),
                       '--api-url', url, '--output-dir', str(output), '--delay', '0', '--completed-only', '--start-date', '2020-01-01']
            run = subprocess.run(command, text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertIn('2 saved, 1 failed', run.stdout)
            self.assertEqual(len(paths), 3)
            self.assertEqual(old.read_text(), 'previous data\n')
            for symbol in ['TCS.NS', 'M&M.NS']:
                with (output / f'{symbol}_monthly_ath.csv').open() as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual([float(r['alltimehigh']) for r in rows], [100, 100, 120, 120])
            failed = output / 'failed_symbols.csv'
            self.assertEqual(read_symbols(failed), ['BAD.NS'])
            self.assertTrue(all(parse_qs(urlsplit(path).query)['include_incomplete'] == ['false'] for path in paths))
            self.assertTrue(all(parse_qs(urlsplit(path).query)['start_date'] == ['2020-01-01'] for path in paths))
            # Retry only the failed symbol. The input and failure-report paths may be identical.
            with api({"count": 4, "data": candles()}) as (retry_url, retry_paths):
                command[command.index(str(symbols))] = str(failed)
                command[command.index(url)] = retry_url
                run = subprocess.run(command, text=True, capture_output=True, timeout=10)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(len(retry_paths), 1)
            self.assertEqual(failed.read_text(), 'symbol,error\n')
            self.assertIn('alltimehigh', old.read_text())


if __name__ == "__main__":
    unittest.main()
