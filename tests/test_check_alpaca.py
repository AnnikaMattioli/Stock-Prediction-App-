import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import check_alpaca as probe


def bar(day, close, volume):
    timestamp = datetime.fromisoformat(day).replace(tzinfo=probe.NY)
    return dict(t=timestamp.isoformat(), o=close, h=close, l=close, c=close, v=volume)


class FeedCheckTests(unittest.TestCase):
    def setUp(self):
        self.calendar = [
            {"date": "2024-11-27", "close": "16:00"},
            {"date": "2024-11-29", "close": "13:00"},
        ]
        self.history = {
            symbol: [
                bar("2016-01-04", 30, 100),
                bar("2024-11-27", 90, 100),
                bar("2024-11-29", 100, 100),
            ]
            for symbol in probe.SYMBOLS
        }
        self.history["WMT"][1:1] = [
            bar("2024-02-23", 58.52, 300),
            bar("2024-02-26", 59.60, 100),
        ]
        self.raw = {
            "AAPL": [bar("2024-02-23", 182.52, 100)],
            "WMT": [bar("2024-02-23", 175.56, 100), bar("2024-02-26", 59.60, 100)],
        }

    def run_probe(self, timestamp, latest_session):
        now = datetime.fromisoformat(timestamp).replace(tzinfo=probe.NY)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz):
                return now.astimezone(tz)

        def get_json(url, headers, **params):
            if url == f"{probe.PAPER_URL}/v2/calendar":
                self.assertEqual(params["end"], now.date())
                return self.calendar
            self.assertEqual(url, probe.DATA_URL)
            self.assertEqual(params["feed"], "sip")
            self.assertEqual(params["timeframe"], "1Day")
            if params["adjustment"] == "raw":
                self.assertEqual(params["symbols"], "AAPL,WMT")
                return {"bars": self.raw}
            self.assertEqual(params["adjustment"], "split")
            self.assertEqual(params["symbols"], ",".join(probe.SYMBOLS))
            self.assertEqual(date.fromisoformat(params["start"][:10]), date(2016, 1, 1))
            end = datetime.fromisoformat(params["end"])
            self.assertEqual(end.date().isoformat(), latest_session)
            self.assertGreaterEqual(now - end, probe.SIP_DELAY)
            return {
                "bars": {
                    symbol: [row for row in rows if datetime.fromisoformat(row["t"]) <= end]
                    for symbol, rows in self.history.items()
                }
            }

        output = StringIO()
        with (
            patch.object(probe, "datetime", FrozenDateTime),
            patch.object(probe, "get_json", side_effect=get_json),
            redirect_stdout(output),
        ):
            result = probe.run_check({}, probe.PAPER_URL)
        return result, output.getvalue()

    def test_latest_session_passes_at_early_close_plus_delay(self):
        result, report = self.run_probe("2024-11-29T13:15:00", "2024-11-29")
        self.assertEqual(result, 0)
        self.assertIn("PASS: history", report)

    def test_session_is_pending_inside_the_delay(self):
        result, report = self.run_probe("2024-11-29T13:14:59", "2024-11-27")
        self.assertEqual(result, 2)
        self.assertIn("PENDING", report)

    def test_weekend_checks_last_trading_session(self):
        result, report = self.run_probe("2024-12-01T10:00:00", "2024-11-29")
        self.assertEqual(result, 0)
        self.assertIn("Checking latest eligible session 2024-11-29", report)

    def test_missing_latest_bar_fails(self):
        self.history["SPY"].pop()
        result, report = self.run_probe("2024-11-29T13:16:00", "2024-11-29")
        self.assertEqual(result, 1)
        self.assertIn("FAIL SPY", report)

    def test_published_close_mismatch_fails(self):
        self.raw["AAPL"][0]["c"] = 183
        result, report = self.run_probe("2024-11-29T13:16:00", "2024-11-29")
        self.assertEqual(result, 1)
        self.assertIn("FAIL AAPL 2024-02-23 raw close", report)

    def test_incorrect_split_volume_fails(self):
        self.history["WMT"][1]["v"] = 100
        result, report = self.run_probe("2024-11-29T13:16:00", "2024-11-29")
        self.assertEqual(result, 1)
        self.assertIn("FAIL WMT 2024-02-23 split adjustment", report)


class HistoricalRequestTests(unittest.TestCase):
    def test_follows_pagination_across_symbols(self):
        pages = [
            {"bars": {"AAPL": [bar("2024-11-27", 80, 100)]}, "next_page_token": "next"},
            {"bars": {"SPY": [bar("2024-11-29", 90, 100)]}},
        ]
        start = datetime(2024, 11, 27, tzinfo=probe.NY)
        end = datetime(2024, 11, 29, tzinfo=probe.NY)
        with patch.object(probe, "get_json", side_effect=pages) as get_json:
            bars = probe.fetch_bars({}, ("AAPL", "SPY"), start, end, "split")
        first, second = get_json.call_args_list
        self.assertNotIn("page_token", first.kwargs)
        self.assertEqual(second.kwargs["page_token"], "next")
        self.assertEqual(list(bars["AAPL"]), [start.date()])
        self.assertEqual(list(bars["SPY"]), [end.date()])


class CommandLineTests(unittest.TestCase):
    def test_missing_credentials_exit_with_setup_message(self):
        env = dict(os.environ)
        env.pop("APCA_API_KEY_ID", None)
        env.pop("APCA_API_SECRET_KEY", None)
        script = Path(__file__).resolve().parents[1] / "check_alpaca.py"
        result = subprocess.run(
            [sys.executable, str(script)], env=env, capture_output=True, text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY", result.stderr)


if __name__ == "__main__":
    unittest.main()
