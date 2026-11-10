import unittest
from datetime import date, timedelta

from baseline_report import BENCHMARK, FEATURES, STOCKS, baseline_scores, build_rows
from check_alpaca import SYMBOLS


class BaselineTests(unittest.TestCase):
    def setUp(self):
        first = date(2024, 1, 5)
        self.sessions = [
            first + timedelta(days=offset)
            for offset in range(36)
            if (first + timedelta(days=offset)).weekday() < 5
        ]
        self.bars = {
            symbol: {
                day: {"close": 100 + i, "volume": 1000}
                for i, day in enumerate(self.sessions)
            }
            for symbol in SYMBOLS
        }

    def rows(self, symbol="AAPL"):
        return [r for r in build_rows(self.sessions, self.bars) if r["symbol"] == symbol]

    def test_feature_windows_use_stock_and_spy_through_forecast_date(self):
        today = self.sessions[20]
        self.bars["AAPL"][today]["volume"] = 2000
        self.bars[BENCHMARK][today]["close"] = 130
        row = self.rows()[0]
        self.assertEqual(row["forecast_session"], today)
        self.assertAlmostEqual(row["return_1d"], 120 / 119 - 1)
        self.assertAlmostEqual(row["return_5d"], 120 / 115 - 1)
        self.assertAlmostEqual(row["spy_return_1d"], 130 / 119 - 1)
        self.assertAlmostEqual(row["spy_return_5d"], 130 / 115 - 1)
        self.assertGreater(row["volatility_20d"], 0)
        self.assertEqual(row["relative_volume_20d"], 2)

    def test_future_bars_can_change_label_only(self):
        first = self.rows()[0]
        following = self.sessions[21]
        for symbol in SYMBOLS:
            self.bars[symbol][following] = {"close": 50, "volume": 5000}
        changed = self.rows()[0]
        self.assertEqual(first["label_up"], 1)
        self.assertEqual(changed["label_up"], 0)
        for field in (*FEATURES, "historical_up_probability", "always_up"):
            self.assertEqual(first[field], changed[field])

    def test_probability_includes_latest_observable_move_per_stock(self):
        self.bars["AAPL"][self.sessions[20]]["close"] = 119
        rows = self.rows()
        self.assertEqual(rows[0]["historical_up_probability"], 19 / 20)
        self.assertEqual(rows[1]["historical_up_probability"], 20 / 21)
        self.assertEqual(self.rows("MSFT")[0]["historical_up_probability"], 1.0)

    def test_label_uses_next_trading_session_and_ties_are_zero(self):
        friday, monday = self.sessions[20:22]
        self.assertEqual((friday.weekday(), monday.weekday()), (4, 0))
        self.bars["AAPL"][monday]["close"] = self.bars["AAPL"][friday]["close"]
        row = self.rows()[0]
        self.assertEqual(row["label_session"], monday)
        self.assertEqual(row["label_up"], 0)

    def test_latest_rows_have_features_without_labels(self):
        rows = list(build_rows(self.sessions, self.bars))
        latest = [row for row in rows if row["forecast_session"] == self.sessions[-1]]
        self.assertEqual(tuple(row["symbol"] for row in latest), STOCKS)
        self.assertTrue(all(row["label_up"] is None and row["label_session"] is None for row in latest))
        self.assertEqual(baseline_scores(rows)["forecasts"], len(rows) - len(STOCKS))

    def test_scores_match_hand_calculation(self):
        rows = [
            {"label_up": 1, "always_up": 1, "historical_up_probability": 0.75},
            {"label_up": 0, "always_up": 1, "historical_up_probability": 0.50},
        ]
        self.assertEqual(
            baseline_scores(rows),
            {"forecasts": 2, "always_up_accuracy": 0.5, "historical_up_rate_brier": 0.15625},
        )
