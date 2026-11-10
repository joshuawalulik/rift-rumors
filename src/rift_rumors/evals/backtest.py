"""Leave-one-year-out backtest.

With three labeled offseasons (2023-2025), each fold trains on two and tests on
the third. Every prediction is scored on a year the model never saw, which is
the closest a backtest gets to "predicting the future".

Only `scoreable` examples count: labeled, and not already known on the as-of date.
"""

from __future__ import annotations

from collections.abc import Callable

from rift_rumors.evals import metrics
from rift_rumors.predict.features import CLASSES, Example


def scoreable(examples: list[Example]) -> list[Example]:
    return [e for e in examples if e.outcome in CLASSES and not e.already_known]


def leave_one_year_out(examples: list[Example], make_model: Callable) -> dict:
    data = scoreable(examples)
    years = sorted({e.year for e in data})
    folds, all_probs, all_outcomes = [], [], []
    for test_year in years:
        train = [e for e in data if e.year != test_year]
        test = [e for e in data if e.year == test_year]
        model = make_model().fit(train)
        probs = [model.predict(e) for e in test]
        outcomes = [e.outcome for e in test]
        folds.append({"test_year": test_year, "n": len(test), **_score(probs, outcomes)})
        all_probs += probs
        all_outcomes += outcomes
    return {
        "folds": folds,
        "pooled": {"n": len(all_outcomes), **_score(all_probs, all_outcomes)},
        "calibration": metrics.calibration(all_probs, all_outcomes),
    }


def _score(probs, outcomes) -> dict:
    return {
        "brier": metrics.brier(probs, outcomes),
        "leave_brier": metrics.leave_brier(probs, outcomes),
        "log_loss": metrics.log_loss(probs, outcomes),
        "accuracy": metrics.accuracy(probs, outcomes),
    }
