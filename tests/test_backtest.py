import unittest
from datetime import date
from unittest.mock import patch

from backtest import STOCKS, backtest, execution_sessions, select_threshold, simulate

FRIDAY = date(2024, 11, 29)
MONDAY = date(2024, 12, 2)
TUESDAY = date(2024, 12, 3)


class BacktestTests(unittest.TestCase):
    def test_trade_uses_next_sessions_open_and_close_even_when_label_is_up(self):
        forecasts = [
            {"symbol": symbol, "forecast_session": FRIDAY, "logistic_probability": 0.55, "label_up": 1}
            for symbol in STOCKS
        ]
        bars = {
            symbol: {FRIDAY: {"open": 95, "close": 100}, MONDAY: {"open": 110, "close": 105}}
            for symbol in STOCKS
        }
        days = list(execution_sessions(forecasts, [FRIDAY, MONDAY], bars))
        self.assertEqual(days[0][0], MONDAY)
        self.assertAlmostEqual(simulate(days, 0.55, 0)["net_return"], 105 / 110 - 1)

    def test_cash_allocation_and_costs_use_both_sides(self):
        days = [(MONDAY, [(0.55, 1.1)] + [(0.4, 1.5)] * 4)]
        result = simulate(days, 0.55, 5)
        self.assertEqual(result["round_trips"], 1)
        self.assertAlmostEqual(result["gross_return"], 0.02)
        self.assertAlmostEqual(result["net_return"], (1.1 * 0.9995 / 1.0005 - 1) / 5)
        self.assertAlmostEqual(result["mean_daily_two_way_turnover"], 2.1 / (5 * 1.0005))

    def test_compounded_return_drawdown_and_cash_day(self):
        days = [
            (FRIDAY, [(0.6, 1.1)] * 5),
            (MONDAY, [(0.6, 0.9)] * 5),
            (TUESDAY, [(0.4, 2.0)] * 5),
        ]
        result = simulate(days, 0.5, 0)
        self.assertAlmostEqual(result["net_return"], -0.01)
        self.assertAlmostEqual(result["max_net_drawdown"], 0.10)
        self.assertAlmostEqual(result["mean_daily_two_way_turnover"], 4 / 3)
        self.assertEqual(result["active_sessions"], 2)

    def test_cash_can_win_validation_after_costs(self):
        threshold, candidates = select_threshold([(MONDAY, [(0.55, 1.0)] * 5)])
        self.assertIsNone(threshold)
        self.assertEqual(candidates[-1]["net_return"], 0.0)

    def test_held_out_prices_cannot_change_threshold(self):
        def forecasts(day):
            return [
                {
                    "symbol": symbol,
                    "forecast_session": day,
                    "logistic_probability": 0.55 if i == 0 else 0.50,
                }
                for i, symbol in enumerate(STOCKS)
            ]

        bars = {
            symbol: {
                MONDAY: {"open": 100, "close": 110 if i == 0 else 90},
                TUESDAY: {"open": 100, "close": 90 if i == 0 else 110},
            }
            for i, symbol in enumerate(STOCKS)
        }
        with (
            patch("backtest.build_rows", return_value=[]),
            patch("backtest.evaluate", return_value=([(forecasts(FRIDAY), {})], (forecasts(MONDAY), {}))),
        ):
            original = backtest([FRIDAY, MONDAY, TUESDAY], bars)
            bars[STOCKS[0]][TUESDAY]["close"] = 130
            changed = backtest([FRIDAY, MONDAY, TUESDAY], bars)
        self.assertGreater(original["selected_threshold"], 0.50)
        self.assertEqual(original["selected_threshold"], changed["selected_threshold"])
        self.assertNotEqual(original["test"]["net_return"], changed["test"]["net_return"])
