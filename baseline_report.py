"""Build causal daily features and evaluate two directional baselines."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from statistics import fmean, pstdev

import pyarrow.parquet as pq

from check_alpaca import SYMBOLS

BENCHMARK = "SPY"
STOCKS = tuple(symbol for symbol in SYMBOLS if symbol != BENCHMARK)
WINDOW = 20
FEATURES = (
    "return_1d", "return_5d", "volatility_20d", "relative_volume_20d",
    "spy_return_1d", "spy_return_5d",
)


def load_bars(path):
    table = pq.ParquetFile(path).read(columns=["symbol", "session", "close", "volume"])
    metadata = json.loads(table.schema.metadata[b"stock_prediction"])
    if (
        metadata["schema_version"] != 1
        or (metadata["feed"], metadata["adjustment"], metadata["timeframe"]) != ("sip", "split", "1Day")
        or set(metadata["symbols"]) != set(SYMBOLS)
    ):
        raise ValueError("Expected the Phase 2 daily SIP split-adjusted cache for all six symbols")

    bars = {symbol: {} for symbol in SYMBOLS}
    for row in table.to_pylist():
        by_session = bars[row["symbol"]]
        session = row["session"]
        if session in by_session:
            raise ValueError(f"Duplicate cache bar for {row['symbol']} on {session}")
        by_session[session] = row
    sessions = sorted(bars[BENCHMARK])
    if (
        not sessions
        or len(sessions) != metadata["session_count"]
        or sessions[0] != date.fromisoformat(metadata["first_session"])
        or sessions[-1] != date.fromisoformat(metadata["last_session"])
        or any(series.keys() != bars[BENCHMARK].keys() for series in bars.values())
    ):
        raise ValueError("Cache sessions do not match across symbols or with cache metadata")
    return sessions, bars


def build_rows(sessions, bars):
    """Yield one row per stock and forecast date after the feature warm-up."""
    closes = {symbol: [bars[symbol][day]["close"] for day in sessions] for symbol in SYMBOLS}
    spy = closes[BENCHMARK]
    for symbol in STOCKS:
        prices = closes[symbol]
        volumes = [bars[symbol][day]["volume"] for day in sessions]
        daily_returns = [new / old - 1 for old, new in zip(prices, prices[1:])]
        known_ups = 0
        for i, day in enumerate(sessions):
            if i:
                # Today's close resolves the previous forecast's outcome.
                known_ups += prices[i] > prices[i - 1]
            if i < WINDOW:
                continue
            prior_volume = fmean(volumes[i - WINDOW:i])
            has_label = i + 1 < len(sessions)
            yield {
                "symbol": symbol,
                "forecast_session": day,
                "return_1d": daily_returns[i - 1],
                "return_5d": prices[i] / prices[i - 5] - 1,
                "volatility_20d": pstdev(daily_returns[i - WINDOW:i]),
                "relative_volume_20d": volumes[i] / prior_volume if prior_volume else None,
                "spy_return_1d": spy[i] / spy[i - 1] - 1,
                "spy_return_5d": spy[i] / spy[i - 5] - 1,
                "always_up": 1,
                "historical_up_probability": known_ups / i,
                "label_session": sessions[i + 1] if has_label else None,
                "label_up": int(prices[i + 1] > prices[i]) if has_label else None,
            }


def baseline_scores(rows):
    labelled = [row for row in rows if row["label_up"] is not None]
    if not labelled:
        raise ValueError("At least 22 trading sessions are required to score the baselines")
    return {
        "forecasts": len(labelled),
        "always_up_accuracy": fmean(row["label_up"] == row["always_up"] for row in labelled),
        "historical_up_rate_brier": fmean(
            (row["historical_up_probability"] - row["label_up"]) ** 2 for row in labelled
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/daily_bars.parquet"))
    args = parser.parse_args()
    try:
        sessions, bars = load_bars(args.input)
        rows = list(build_rows(sessions, bars))
        report = {
            "first_scored_forecast_session": sessions[WINDOW].isoformat() if rows else None,
            "latest_forecast_session": sessions[-1].isoformat(),
            "overall": baseline_scores(rows),
            "by_stock": {
                symbol: baseline_scores(row for row in rows if row["symbol"] == symbol)
                for symbol in STOCKS
            },
            "latest_historical_up_probability": {
                row["symbol"]: row["historical_up_probability"]
                for row in rows if row["label_up"] is None
            },
        }
    except (OSError, ValueError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
