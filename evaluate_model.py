"""Evaluate pooled logistic regression and baselines on fixed trading-date splits."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from statistics import fmean

from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from baseline_report import FEATURES, STOCKS, build_rows, load_bars

TEST_START = date(2025, 11, 7)
TEST_END = date(2026, 11, 9)
VALIDATION_FOLDS = 3
VALIDATION_SESSIONS = 126


def training_rows(rows, cutoff):
    return [
        row for row in rows
        if row["forecast_session"] < cutoff
        and row["label_session"] is not None
        and row["label_session"] <= cutoff
        and row["label_up"] is not None
    ]


def feature_matrix(rows):
    return [[float("nan") if row[key] is None else row[key] for key in FEATURES] for row in rows]


def fit_predict(rows, evaluation_rows):
    cutoff = min(row["forecast_session"] for row in evaluation_rows)
    train = training_rows(rows, cutoff)
    labels = [row["label_up"] for row in train]
    if set(labels) != {0, 1}:
        raise ValueError(f"Training at {cutoff} requires both outcome classes")

    model = make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        StandardScaler(),
        LogisticRegression(C=1.0, l1_ratio=0.0, solver="lbfgs", max_iter=1000),
    )
    model.fit(feature_matrix(train), labels)
    probabilities = model.predict_proba(feature_matrix(evaluation_rows))[:, 1]
    predictions = [
        {**row, "logistic_probability": float(probability)}
        for row, probability in zip(evaluation_rows, probabilities, strict=True)
    ]
    return predictions, {
        "fit_cutoff_session": cutoff.isoformat(),
        "last_training_forecast_session": max(row["forecast_session"] for row in train).isoformat(),
        "last_training_label_session": max(row["label_session"] for row in train).isoformat(),
        "training_rows": len(train),
    }


def split_dates(rows, test_start, test_end, validation_folds, validation_sessions):
    dates = sorted({row["forecast_session"] for row in rows if row["label_up"] is not None})
    if test_start not in dates or test_end not in dates or test_start > test_end:
        raise ValueError("The cache must cover both test boundary dates and their labels")
    pretest = [day for day in dates if day < test_start]
    required = validation_folds * validation_sessions
    if len(pretest) <= required:
        raise ValueError("Insufficient history for the validation folds and initial training")
    validation = pretest[-required:]
    folds = [
        set(validation[i:i + validation_sessions])
        for i in range(0, required, validation_sessions)
    ]
    return folds, {day for day in dates if test_start <= day <= test_end}


def evaluate(rows, test_start=TEST_START, test_end=TEST_END,
             validation_folds=VALIDATION_FOLDS, validation_sessions=VALIDATION_SESSIONS):
    folds, test = split_dates(rows, test_start, test_end, validation_folds, validation_sessions)
    results = []
    for dates in [*folds, test]:
        held_out = sorted(
            (row for row in rows if row["forecast_session"] in dates and row["label_up"] is not None),
            key=lambda row: (row["forecast_session"], row["symbol"]),
        )
        predictions, audit = fit_predict(rows, held_out)
        results.append((predictions, {
            **audit,
            "first_forecast_session": min(dates).isoformat(),
            "last_forecast_session": max(dates).isoformat(),
            "sessions": len(dates),
        }))
    return results[:-1], results[-1]


def scores(rows):
    metrics = {"forecasts": len(rows)}
    for name, field in (
        ("logistic", "logistic_probability"),
        ("always_up", "always_up"),
        ("historical_up_rate", "historical_up_probability"),
    ):
        metrics[name] = {
            "accuracy": fmean((row[field] >= 0.5) == row["label_up"] for row in rows),
            "brier": fmean((row[field] - row["label_up"]) ** 2 for row in rows),
        }
    return metrics


def summarize(predictions):
    return {
        "overall": scores(predictions),
        "by_stock": {
            symbol: scores([row for row in predictions if row["symbol"] == symbol])
            for symbol in STOCKS
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/daily_bars.parquet"))
    args = parser.parse_args()
    try:
        sessions, bars = load_bars(args.input)
        validation, (test, test_audit) = evaluate(list(build_rows(sessions, bars)))
        report = {
            "validation": summarize([row for predictions, _ in validation for row in predictions]),
            "validation_folds": [
                {**audit, "overall": scores(predictions)} for predictions, audit in validation
            ],
            "test": {**test_audit, **summarize(test)},
        }
    except (OSError, ValueError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
