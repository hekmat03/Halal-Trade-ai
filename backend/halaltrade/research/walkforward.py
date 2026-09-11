"""Walk-forward validation (Delivery 6) — research/testing tool only.

Evaluates a strategy factory over candle history with the existing backtest
engine using rolling train/validation/test folds:

* params are optimized ONLY on train,
* the winner is selected on validation,
* honest out-of-sample (OOS) numbers are reported on test.

Output is a ``WalkForwardReport`` (per-fold in-sample/out-of-sample metrics,
parameter stability, degradation notes). This is a research/testing tool —
never a guarantee, never trading advice. Its output feeds research, NOT
automatic trading: there is no path from here to order placement except
through the four-gate pipeline.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field

from ..backtest.engine import BacktestConfig, BacktestEngine
from ..backtest.performance import PerformanceReport
from ..config import Settings
from ..marketdata.models import Candle

logger = logging.getLogger(__name__)

__all__ = [
    "FoldReport",
    "WalkForwardReport",
    "WalkForwardConfig",
    "walk_forward",
    "score_report",
]

METRICS = ("total_return", "profit_factor", "sharpe", "win_rate")


def score_report(report: PerformanceReport, metric: str = "total_return") -> float:
    """Scalar score used to rank param sets (higher is better)."""
    if metric == "total_return":
        return float(report.total_return)
    if metric == "profit_factor":
        return float(report.profit_factor)
    if metric == "sharpe":
        return float(report.sharpe)
    if metric == "win_rate":
        return float(report.win_rate)
    raise ValueError(f"unknown metric {metric!r}; choose from {METRICS}")


class FoldReport(BaseModel):
    """One train/validate/test fold."""

    fold: int
    train_start: int
    train_end: int
    val_start: int
    val_end: int
    test_start: int
    test_end: int
    best_params: dict[str, Any] = Field(default_factory=dict)
    train: PerformanceReport | None = None       # in-sample (selected params)
    validation: PerformanceReport | None = None  # selection segment
    test: PerformanceReport | None = None        # honest out-of-sample
    candidates_evaluated: int = 0


@dataclass
class WalkForwardConfig:
    """How to split and search."""

    n_folds: int = 3
    train_pct: float = 0.6
    val_pct: float = 0.2
    test_pct: float = 0.2
    metric: str = "total_return"   # optimized on train, selected on validation
    top_k: int = 3                 # train shortlist carried into validation
    min_segment_bars: int = 10     # skip folds with smaller segments


@dataclass
class WalkForwardReport:
    """Full walk-forward outcome (research only, not trading advice)."""

    strategy_name: str
    metric: str
    folds: list[FoldReport] = field(default_factory=list)
    # Mean out-of-sample metric across folds that produced a test segment.
    mean_oos_metric: float = 0.0
    mean_is_metric: float = 0.0
    parameter_stability: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Walk-forward: {self.strategy_name} (metric={self.metric})",
            f"folds={len(self.folds)} mean IS={self.mean_is_metric:.4f} "
            f"mean OOS={self.mean_oos_metric:.4f}",
        ]
        lines.extend(f"- {n}" for n in self.notes)
        return "\n".join(lines)


def _fold_bounds(n: int, cfg: WalkForwardConfig) -> list[tuple[int, int, int, int, int, int]]:
    """Rolling folds: split ``n`` bars into ``n_folds`` sequential blocks.

    Each block is internally split train/val/test by the configured fractions.
    """
    total = cfg.train_pct + cfg.val_pct + cfg.test_pct
    train_f = cfg.train_pct / total
    val_f = cfg.val_pct / total
    bounds: list[tuple[int, int, int, int, int, int]] = []
    base = n // cfg.n_folds
    rem = n % cfg.n_folds
    start = 0
    for f in range(cfg.n_folds):
        size = base + (1 if f < rem else 0)
        end = start + size
        n_train = int(size * train_f)
        n_val = int(size * val_f)
        bounds.append((start, start + n_train,
                       start + n_train, start + n_train + n_val,
                       start + n_train + n_val, end))
        start = end
    return bounds


def walk_forward(
    strategy_factory: Callable[..., Any],
    candles: list[Candle],
    param_grid: dict[str, list[Any]],
    config: WalkForwardConfig | None = None,
    backtest_config: BacktestConfig | None = None,
    settings: Settings | None = None,
) -> WalkForwardReport:
    """Run walk-forward validation of ``strategy_factory`` over ``candles``.

    ``strategy_factory`` is called as ``strategy_factory(**params)`` for each
    combination in ``param_grid``. Per fold: every combo runs on train, the
    top-``top_k`` by ``metric`` are re-run on validation, the validation
    winner is re-run on test, and the test report is the honest OOS result.
    Every backtest still enforces the four-gate pipeline internally.
    """
    cfg = config or WalkForwardConfig()
    bt_cfg = backtest_config or BacktestConfig()
    run_settings = settings or Settings(trading_mode="backtest", live_enabled=False)
    if cfg.metric not in METRICS:
        raise ValueError(f"unknown metric {cfg.metric!r}; choose from {METRICS}")
    if cfg.n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    if not param_grid:
        raise ValueError("param_grid must not be empty")

    candles = list(candles)
    keys = list(param_grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*(param_grid[k] for k in keys))]
    if not combos:
        raise ValueError("param_grid produced no combinations")

    engine = BacktestEngine(run_settings)
    strategy_name = getattr(strategy_factory, "__name__", "strategy")
    report = WalkForwardReport(strategy_name=strategy_name, metric=cfg.metric)
    oos_scores: list[float] = []
    is_scores: list[float] = []

    for f, (ts, te, vs, ve, es, ee) in enumerate(_fold_bounds(len(candles), cfg)):
        train = candles[ts:te]
        val = candles[vs:ve]
        test = candles[es:ee]
        if min(len(train), len(val), len(test)) < cfg.min_segment_bars:
            report.notes.append(
                f"fold {f}: skipped — segment too small "
                f"(train={len(train)}, val={len(val)}, test={len(test)} "
                f"< min {cfg.min_segment_bars})"
            )
            continue
        # 1. Optimize on train only.
        train_scored: list[tuple[float, dict[str, Any], PerformanceReport]] = []
        for params in combos:
            strategy = strategy_factory(**params)
            res = engine.run(strategy, train, bt_cfg,
                             strategy_name=f"{strategy_name}@{params}")
            train_scored.append((score_report(res.report, cfg.metric), params, res.report))
        train_scored.sort(key=lambda t: t[0], reverse=True)
        shortlist = train_scored[: max(1, cfg.top_k)]
        # 2. Select on validation.
        best_params: dict[str, Any] = shortlist[0][1]
        best_val_score = float("-inf")
        best_val_report = None
        best_train_report = shortlist[0][2]
        for _, params, _ in shortlist:
            strategy = strategy_factory(**params)
            res = engine.run(strategy, val, bt_cfg,
                             strategy_name=f"{strategy_name}@{params}")
            s = score_report(res.report, cfg.metric)
            if s > best_val_score:
                best_val_score = s
                best_params = params
                best_val_report = res.report
        # Re-fetch the train report for the selected params (in-sample record).
        selected_train = next(r for _, p, r in shortlist if p == best_params) \
            if any(p == best_params for _, p, _ in shortlist) else None
        if selected_train is None:
            strategy = strategy_factory(**best_params)
            selected_train = engine.run(
                strategy, train, bt_cfg,
                strategy_name=f"{strategy_name}@{best_params}").report
        # 3. Honest out-of-sample on test.
        strategy = strategy_factory(**best_params)
        test_res = engine.run(strategy, test, bt_cfg,
                              strategy_name=f"{strategy_name}@{best_params}")
        fold = FoldReport(
            fold=f, train_start=ts, train_end=te,
            val_start=vs, val_end=ve, test_start=es, test_end=ee,
            best_params=dict(best_params),
            train=selected_train, validation=best_val_report,
            test=test_res.report, candidates_evaluated=len(combos),
        )
        report.folds.append(fold)
        is_s = score_report(selected_train, cfg.metric)
        oos_s = score_report(test_res.report, cfg.metric)
        is_scores.append(is_s)
        oos_scores.append(oos_s)
        if oos_s < is_s:
            report.notes.append(
                f"fold {f}: OOS degradation ({cfg.metric} IS={is_s:.4f} "
                f"OOS={oos_s:.4f}, params={best_params}) — "
                "in-sample edge did not fully carry over; not a guarantee."
            )
        else:
            report.notes.append(
                f"fold {f}: OOS held ({cfg.metric} IS={is_s:.4f} OOS={oos_s:.4f}, "
                f"params={best_params})."
            )

    if oos_scores:
        report.mean_oos_metric = sum(oos_scores) / len(oos_scores)
    if is_scores:
        report.mean_is_metric = sum(is_scores) / len(is_scores)
    # Parameter stability: which values won across folds.
    if report.folds:
        for key in keys:
            vals = [fold.best_params.get(key) for fold in report.folds]
            uniq = sorted(set(map(repr, vals)))
            report.parameter_stability[key] = {
                "values": vals,
                "unique_count": len(uniq),
                "stable": len(uniq) == 1,
            }
        unstable = [k for k, v in report.parameter_stability.items() if not v["stable"]]
        if unstable:
            report.notes.append(
                f"parameter instability across folds: {unstable} — "
                "the edge is parameter-sensitive; treat as unvalidated research."
            )
        else:
            report.notes.append("parameters stable across all folds.")
    if not report.folds:
        report.notes.append("no folds produced results — data too short; DATA UNAVAILABLE.")
    return report
