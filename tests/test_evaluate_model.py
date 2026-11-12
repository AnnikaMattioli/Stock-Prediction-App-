from math import isnan
import unittest
from datetime import date, timedelta

from baseline_report import FEATURES, STOCKS
from evaluate_model import evaluate, feature_matrix, scores, training_rows


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.days = [date(2024, 1, 2) + timedelta(days=i) for i in range(16)]
        self.rows = [
            {
                **{feature: i + j / 10 for j, feature in enumerate(FEATURES)},
                "symbol": symbol,
                "forecast_session": day,
                "label_session": self.days[i + 1] if i < 15 else None,
                "label_up": i % 2 if i < 15 else None,
                "always_up": 1,
                "historical_up_probability": 0.5,
            }
            for i, day in enumerate(self.days)
            for symbol in STOCKS
        ]

    def evaluate(self):
        return evaluate(self.rows, self.days[10], self.days[14], 2, 2)

    def test_training_uses_only_observable_labels_and_prior_forecasts(self):
        cutoff = self.days[10]
        self.rows[0]["label_session"] = self.days[11]
        self.rows[1]["label_up"] = None
        train = training_rows(self.rows, cutoff)
        self.assertEqual(len(train), 10 * len(STOCKS) - 2)
        self.assertTrue(all(row["forecast_session"] < cutoff for row in train))
        self.assertTrue(all(row["label_session"] <= cutoff for row in train))
        self.assertEqual(max(row["label_session"] for row in train), cutoff)

    def test_folds_keep_each_date_together_and_expand_training(self):
        validation, test = self.evaluate()
        periods = [*validation, test]
        expected_dates = [self.days[6:8], self.days[8:10], self.days[10:15]]
        for (predictions, audit), days in zip(periods, expected_dates, strict=True):
            for day in days:
                self.assertEqual(
                    {r["symbol"] for r in predictions if r["forecast_session"] == day},
                    set(STOCKS),
                )
            self.assertEqual(len(predictions), len(days) * len(STOCKS))
            self.assertEqual(audit["fit_cutoff_session"], days[0].isoformat())
            self.assertEqual(audit["training_rows"], self.days.index(days[0]) * len(STOCKS))
            self.assertTrue(all(0 <= r["logistic_probability"] <= 1 for r in predictions))

    def test_holdout_labels_cannot_change_predictions(self):
        validation, (test, _) = self.evaluate()
        for row in self.rows:
            if row["forecast_session"] >= self.days[10] and row["label_up"] is not None:
                row["label_up"] = 1 - row["label_up"]
        new_validation, (new_test, _) = self.evaluate()
        self.assertEqual(validation, new_validation)
        self.assertEqual(
            [row["logistic_probability"] for row in test],
            [row["logistic_probability"] for row in new_test],
        )

    def test_unlabelled_test_end_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "boundary dates"):
            evaluate(self.rows, self.days[10], self.days[15], 2, 2)

    def test_features_exclude_label_and_handle_missing_volume(self):
        row = self.rows[0]
        row["relative_volume_20d"] = None
        values = feature_matrix([row])[0]
        self.assertEqual(len(values), len(FEATURES))
        self.assertTrue(isnan(values[FEATURES.index("relative_volume_20d")]))
        self.assertEqual(values[0], row[FEATURES[0]])

    def test_metrics_use_same_rows_and_half_probability_predicts_up(self):
        rows = [
            {"label_up": 1, "always_up": 1, "historical_up_probability": 0.5, "logistic_probability": 0.75},
            {"label_up": 0, "always_up": 1, "historical_up_probability": 0.5, "logistic_probability": 0.25},
        ]
        self.assertEqual(scores(rows), {
            "forecasts": 2,
            "logistic": {"accuracy": 1.0, "brier": 0.0625},
            "always_up": {"accuracy": 0.5, "brier": 0.5},
            "historical_up_rate": {"accuracy": 0.5, "brier": 0.25},
        })
