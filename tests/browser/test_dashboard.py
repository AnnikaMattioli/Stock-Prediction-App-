import json
import os
import re
import socket
import subprocess
import sys
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

import pyarrow as pa
import pyarrow.parquet as pq
from playwright.sync_api import expect, sync_playwright

from check_alpaca import SYMBOLS

ROOT = Path(__file__).resolve().parents[2]


def write_snapshot(path, last):
    first = date(2022, 1, 3)
    sessions = [
        first + timedelta(days=i) for i in range((last - first).days + 1)
        if (first + timedelta(days=i)).weekday() < 5
    ]
    rows = []
    for j, symbol in enumerate(SYMBOLS):
        for i, day in enumerate(sessions):
            close = 100 + j + i / 10 + (i % 5)
            rows.append(dict(symbol=symbol, session=day, close=close, volume=1000 + i, open=close + 0.1))
    metadata = {
        "schema_version": 1, "feed": "sip", "adjustment": "split", "timeframe": "1Day",
        "symbols": SYMBOLS, "session_count": len(sessions), "first_session": first.isoformat(),
        "last_session": last.isoformat(), "received_at": f"{last}T22:00:00+00:00",
    }
    table = pa.Table.from_pylist(rows).replace_schema_metadata({
        b"stock_prediction": json.dumps(metadata).encode(),
    })
    pq.write_table(table, path)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = Path(cls.enterClassContext(TemporaryDirectory()))
        cls.cache = folder / "snapshot.parquet"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}"
        log = cls.enterClassContext((folder / "streamlit.log").open("w+"))
        server = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app.py",
             "--server.address=127.0.0.1", f"--server.port={port}",
             "--server.headless=true", "--browser.gatherUsageStats=false"],
            cwd=ROOT, env={**os.environ, "STOCK_CACHE_PATH": str(cls.cache)},
            stdout=log, stderr=subprocess.STDOUT,
        )

        def stop_server():
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()

        cls.addClassCleanup(stop_server)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and server.poll() is None:
            try:
                with urlopen(f"{cls.url}/_stcore/health", timeout=1):
                    break
            except URLError:
                time.sleep(0.2)
        else:
            log.seek(0)
            raise RuntimeError(f"Streamlit did not start:\n{log.read()}")

        playwright = cls.enterClassContext(sync_playwright())
        cls.browser = playwright.chromium.launch()
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.cache.unlink(missing_ok=True)
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)

    def test_dashboard_shows_forecasts_chart_and_holdout(self):
        write_snapshot(self.cache, date(2026, 11, 10))
        self.page.goto(self.url)
        expect(self.page.get_by_role("heading", name="Stock Forecasts")).to_be_visible(timeout=30000)
        expect(self.page.get_by_text(re.compile("Forecast session 2026-11-10.*Data received"))).to_be_visible(timeout=30000)
        metrics = self.page.get_by_test_id("stMetric")
        expect(metrics).to_have_count(5)
        for i, symbol in enumerate(SYMBOLS[:-1]):
            expect(metrics.nth(i)).to_contain_text(symbol)
            expect(metrics.nth(i)).to_contain_text(re.compile(r"\d+\.\d%"))
        expect(self.page.get_by_role("heading", name="Held-out results")).to_be_visible()
        expect(self.page.get_by_role("heading", name="Next-session trading simulation")).to_be_visible()
        expect(self.page.get_by_test_id("stVegaLiteChart").locator("canvas, svg").first).to_be_visible()

    def test_stock_selection_updates_price_history_and_scores(self):
        write_snapshot(self.cache, date(2026, 11, 10))
        self.page.goto(self.url)
        selector = self.page.get_by_role("combobox", name="Stock")
        selector.click(timeout=30000)
        self.page.get_by_role("option", name="XOM", exact=True).click()
        expect(self.page.get_by_role("heading", name="XOM split-adjusted close")).to_be_visible()
        expect(self.page.get_by_role("heading", name="XOM", exact=True)).to_be_visible()

    def test_missing_cache_recovers_and_reloads_new_snapshot(self):
        self.page.goto(self.url)
        expect(self.page.get_by_text(re.compile("No local bar cache found"))).to_be_visible(timeout=30000)
        write_snapshot(self.cache, date(2026, 11, 10))
        reload_button = self.page.get_by_role("button", name="Reload local snapshot")
        reload_button.click()
        expect(self.page.get_by_text(re.compile("Forecast session 2026-11-10"))).to_be_visible(timeout=30000)
        write_snapshot(self.cache, date(2026, 11, 11))
        reload_button.click()
        expect(self.page.get_by_text(re.compile("Forecast session 2026-11-11"))).to_be_visible(timeout=30000)
