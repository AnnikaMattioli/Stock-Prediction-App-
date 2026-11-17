"""Simulate next-session open-to-close trades from held-out forecasts."""

import argparse
import json
import sys
from collections import defaultdict
from math import fsum
from pathlib import Path
from statistics import fmean

from baseline_report import STOCKS, build_rows, load_bars
from evaluate_model import evaluate

COST_BPS_PER_SIDE = 5
THRESHOLDS = (0.0, *(value / 100 for value in range(50, 61)), None)


def execution_sessions(predictions, sessions, bars):
    """Join forecasts to the next exchange session's open and close."""
    following = dict(zip(sessions, sessions[1:]))
    grouped = defaultdict(dict)
    for prediction in predictions:
        symbol = prediction["symbol"]
        day = following[prediction["forecast_session"]]
        if symbol in grouped[day]:
            raise ValueError(f"Duplicate forecast for {symbol} on execution session {day}")
        bar = bars[symbol][day]
        grouped[day][symbol] = (prediction["logistic_probability"], bar["close"] / bar["open"])

    for day, orders in sorted(grouped.items()):
        if orders.keys() != set(STOCKS):
            raise ValueError(f"Incomplete set of stock forecasts for execution session {day}")
        yield day, tuple(orders[symbol] for symbol in STOCKS)


def simulate(days, threshold, cost_bps=COST_BPS_PER_SIDE):
    cost = cost_bps / 10_000
    slot = 1 / len(STOCKS)
    gross_equity = net_equity = peak = 1.0
    drawdown = 0.0
    turnovers = []
    trade_count = active_days = 0
    for _, orders in days:
        selected = [ratio for probability, ratio in orders if threshold is not None and probability >= threshold]
        trade_count += len(selected)
        active_days += bool(selected)
        opening_notional = slot * len(selected) / (1 + cost)
        closing_notional = slot * fsum(selected) / (1 + cost)
        turnovers.append(opening_notional + closing_notional)
        gross_equity *= 1 + slot * fsum(ratio - 1 for ratio in selected)
        net_equity *= 1 + closing_notional * (1 - cost) - slot * len(selected)
        peak = max(peak, net_equity)
        drawdown = max(drawdown, 1 - net_equity / peak)

    return {
        "first_execution_session": days[0][0].isoformat(),
        "last_execution_session": days[-1][0].isoformat(),
        "sessions": len(days),
        "active_sessions": active_days,
        "round_trips": trade_count,
        "gross_return": gross_equity - 1,
        "net_return": net_equity - 1,
        "max_net_drawdown": drawdown,
        "mean_daily_two_way_turnover": fmean(turnovers),
    }


def select_threshold(validation_days):
    candidates = [
        {"threshold": threshold, **simulate(validation_days, threshold)}
        for threshold in THRESHOLDS
    ]
    best = max(candidates, key=lambda row: (
        row["net_return"], -row["mean_daily_two_way_turnover"], row["threshold"] is None,
    ))
    return best["threshold"], candidates


def backtest(sessions, bars):
    validation, (test, _) = evaluate(list(build_rows(sessions, bars)))
    return simulate_evaluation(validation, test, sessions, bars)


def simulate_evaluation(validation, test, sessions, bars):
    validation_rows = [row for fold, _ in validation for row in fold]
    validation_days = list(execution_sessions(validation_rows, sessions, bars))
    threshold, candidates = select_threshold(validation_days)

    test_days = list(execution_sessions(test, sessions, bars))
    return {
        "cost_bps_per_side": COST_BPS_PER_SIDE,
        "selected_threshold": threshold,
        "validation_candidates": candidates,
        "test": simulate(test_days, threshold),
        "test_always_invested": simulate(test_days, 0.0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/daily_bars.parquet"))
    args = parser.parse_args()
    try:
        sessions, bars = load_bars(args.input, include_open=True)
        report = backtest(sessions, bars)
    except (OSError, ValueError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
