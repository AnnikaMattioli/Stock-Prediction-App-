import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pyarrow.parquet as pq

import check_alpaca
import update_data as cache


def bar(session, close=100):
    return {"t": f"{session}T05:00:00Z", "o": close, "h": close, "l": close, "c": close, "v": 500}


class CacheTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.output = Path(directory) / "daily_bars.parquet"
        self.calendar = [
            {"date": "2016-01-04", "close": "16:00"},
            {"date": "2024-11-27", "close": "16:00"},
            {"date": "2024-11-29", "close": "13:00"},
        ]
        self.bars = {
            symbol: [bar(day["date"]) for day in self.calendar] for symbol in cache.SYMBOLS
        }
        self.now = datetime(2024, 11, 29, 13, 16, tzinfo=cache.NY)
        self.requests = []

        class Clock(datetime):
            @classmethod
            def now(cls, tz):
                return self.now.astimezone(tz)

        self.enterContext(patch.object(cache, "datetime", Clock))
        self.enterContext(patch.object(cache, "get_json", side_effect=self.api))
        self.enterContext(patch.object(check_alpaca, "get_json", side_effect=self.api))

    def api(self, url, headers, **params):
        if url.endswith("/calendar"):
            self.assertEqual(params["start"], cache.START_DATE)
            return self.calendar

        self.assertEqual(url, cache.DATA_URL)
        self.assertEqual(params["feed"], "sip")
        self.assertEqual(params["adjustment"], "split")
        self.requests.append(params)
        end = datetime.fromisoformat(params["end"])
        page = {
            symbol: [row for row in rows if datetime.fromisoformat(row["t"]) <= end]
            for symbol, rows in self.bars.items()
        }
        if "page_token" not in params:
            return {"bars": {"AAPL": page["AAPL"]}, "next_page_token": "page-2"}
        self.assertEqual(params["page_token"], "page-2")
        del page["AAPL"]
        return {"bars": page}

    def refresh(self):
        return cache.refresh({}, cache.PAPER_URL, self.output)

    def test_backfill_writes_paginated_bars_and_metadata(self):
        report = self.refresh()
        table = pq.read_table(self.output)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(table.num_rows, 18)
        self.assertEqual(len({(r["symbol"], r["session"]) for r in table.to_pylist()}), 18)
        metadata = json.loads(table.schema.metadata[b"stock_prediction"])
        self.assertEqual(metadata["symbols"], list(cache.SYMBOLS))
        self.assertEqual(metadata["feed"], "sip")
        self.assertEqual(metadata["adjustment"], "split")
        self.assertEqual(metadata["first_session"], "2016-01-04")
        self.assertEqual(metadata["last_session"], "2024-11-29")
        self.assertEqual(metadata["row_count"], report["row_count"])

    def test_early_close_stays_excluded_until_delay_elapses(self):
        self.now = self.now.replace(minute=14, second=59)
        self.assertEqual(self.refresh()["last_session"], "2024-11-27")
        self.now = self.now.replace(minute=15, second=0)
        self.assertEqual(self.refresh()["last_session"], "2024-11-29")

    def test_refresh_replaces_revisions_without_appending_duplicates(self):
        self.refresh()
        self.bars["AAPL"][0] = bar("2016-01-04", 50)
        self.refresh()
        rows = pq.read_table(self.output).to_pylist()
        self.assertEqual(len(rows), 18)
        self.assertEqual(rows[0]["close"], 50)

    def test_missing_session_preserves_existing_cache(self):
        self.refresh()
        previous = self.output.read_bytes()
        self.bars["SPY"].pop()
        with self.assertRaisesRegex(RuntimeError, "SPY: 1 missing"):
            self.refresh()
        self.assertEqual(self.output.read_bytes(), previous)

    def test_duplicate_session_preserves_existing_cache(self):
        self.refresh()
        previous = self.output.read_bytes()
        self.bars["SPY"].append(self.bars["SPY"][-1])
        with self.assertRaisesRegex(RuntimeError, "Duplicate bar for SPY"):
            self.refresh()
        self.assertEqual(self.output.read_bytes(), previous)

    def test_write_failure_preserves_existing_cache(self):
        self.refresh()
        previous = self.output.read_bytes()
        with patch.object(cache.pq, "write_table", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                self.refresh()
        self.assertEqual(self.output.read_bytes(), previous)

    def test_concurrent_writer_cannot_publish(self):
        lock = self.output.with_suffix(".parquet.lock")
        with lock.open("ab") as active:
            cache.flock(active, cache.LOCK_EX | cache.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "Another update"):
                self.refresh()
        self.assertFalse(self.output.exists())
