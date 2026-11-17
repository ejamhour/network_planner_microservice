from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
import json
import math

import numpy as np
import pandas as pd


# -----------------------------------------------------------------------------
# Basic data access
# -----------------------------------------------------------------------------

def load_json(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("JSON root must be a list of records")
    return data


def load_jsonl(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def get_nested(record: dict, path: str, default: Any = None) -> Any:
    cur: Any = record
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def is_number(x: Any) -> bool:
    return isinstance(x, (int, float, np.integer, np.floating)) and not math.isnan(float(x))


# -----------------------------------------------------------------------------
# Filtering
# -----------------------------------------------------------------------------

def filter_records_by_feature(
    records: list[dict],
    *,
    feature_path: str,
    min_value: float | None = None,
    max_value: float | None = None,
    include_min: bool = True,
    include_max: bool = False,
) -> list[dict]:
    out: list[dict] = []

    for rec in records:
        x = get_nested(rec, feature_path)
        if not is_number(x):
            continue

        x = float(x)

        if min_value is not None:
            if include_min and x < min_value:
                continue
            if not include_min and x <= min_value:
                continue

        if max_value is not None:
            if include_max and x > max_value:
                continue
            if not include_max and x >= max_value:
                continue

        out.append(rec)

    return out


def filter_records_by_many(records: list[dict], rules: list[dict]) -> list[dict]:
    out = records
    for rule in rules:
        out = filter_records_by_feature(
            out,
            feature_path=rule["feature_path"],
            min_value=rule.get("min_value"),
            max_value=rule.get("max_value"),
            include_min=rule.get("include_min", True),
            include_max=rule.get("include_max", False),
        )
    return out


# -----------------------------------------------------------------------------
# Expression evaluation
# -----------------------------------------------------------------------------

def _to_ns(x: Any) -> Any:
    if isinstance(x, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in x.items()})
    if isinstance(x, list):
        return [_to_ns(v) for v in x]
    return x


def eq(a: Any, b: Any) -> float:
    return 1.0 if a == b else 0.0


def ne(a: Any, b: Any) -> float:
    return 1.0 if a != b else 0.0


def gt(a: Any, b: Any) -> float:
    return 1.0 if a > b else 0.0


def ge(a: Any, b: Any) -> float:
    return 1.0 if a >= b else 0.0


def lt(a: Any, b: Any) -> float:
    return 1.0 if a < b else 0.0


def le(a: Any, b: Any) -> float:
    return 1.0 if a <= b else 0.0


def eval_expr(record: dict, expr: str, default: float | None = None) -> float | None:
    """
    Evaluate a trusted expression against a link record.

    Example:
        eval_expr(r, "tx.pw + tx.ant_gain + rx.ant_gain - features.fspl")
        eval_expr(r, "features.delta_diffra * eq(rx.ant_type, 'YAGI')")
    """
    try:
        ns = _to_ns(record)
        local_vars = vars(ns)
        local_vars.update(
            {
                "eq": eq,
                "ne": ne,
                "gt": gt,
                "ge": ge,
                "lt": lt,
                "le": le,
                "min": min,
                "max": max,
                "abs": abs,
                "math": math,
                "np": np,
            }
        )
        value = eval(expr, {"__builtins__": {}}, local_vars)
        if not is_number(value):
            return default
        return float(value)
    except Exception:
        return default


# -----------------------------------------------------------------------------
# Linear expression model
# -----------------------------------------------------------------------------

@dataclass
class ExpressionModel:
    fixed_terms: dict[str, str]
    fixed_coeffs: dict[str, float]
    learned_terms: dict[str, str]
    learned_coeffs: dict[str, float]
    intercept: float = 0.0
    y_expr: str = "measures.rssi"
    training_info: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixed_terms": dict(self.fixed_terms),
            "fixed_coeffs": {k: float(v) for k, v in self.fixed_coeffs.items()},
            "learned_terms": dict(self.learned_terms),
            "learned_coeffs": {k: float(v) for k, v in self.learned_coeffs.items()},
            "intercept": float(self.intercept),
            "y_expr": self.y_expr,
            "training_info": dict(self.training_info),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExpressionModel":
        required = {
            "fixed_terms",
            "fixed_coeffs",
            "learned_terms",
            "learned_coeffs",
            "intercept",
            "y_expr",
        }
        missing = required - set(data)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"Missing model field(s): {names}")

        return cls(
            fixed_terms=dict(data["fixed_terms"]),
            fixed_coeffs={k: float(v) for k, v in data["fixed_coeffs"].items()},
            learned_terms=dict(data["learned_terms"]),
            learned_coeffs={k: float(v) for k, v in data["learned_coeffs"].items()},
            intercept=float(data["intercept"]),
            y_expr=str(data["y_expr"]),
            training_info=dict(data.get("training_info", {})),
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "ExpressionModel":
        path = Path(path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Model JSON root must be an object")
        return cls.from_dict(data)

    def predict(self, record: dict) -> float | None:
        total = float(self.intercept)

        for name, expr in self.fixed_terms.items():
            x = eval_expr(record, expr)
            if x is None:
                return None
            total += float(self.fixed_coeffs.get(name, 1.0)) * x

        for name, expr in self.learned_terms.items():
            x = eval_expr(record, expr)
            if x is None:
                return None
            total += float(self.learned_coeffs.get(name, 0.0)) * x

        return total

    def explain(self, record: dict) -> dict[str, Any] | None:
        total = float(self.intercept)

        fixed: dict[str, dict[str, float | str]] = {}
        for name, expr in self.fixed_terms.items():
            x = eval_expr(record, expr)
            if x is None:
                return None
            coeff = float(self.fixed_coeffs.get(name, 1.0))
            contrib = coeff * x
            total += contrib
            fixed[name] = {"expr": expr, "value": x, "coeff": coeff, "contribution": contrib}

        learned: dict[str, dict[str, float | str]] = {}
        for name, expr in self.learned_terms.items():
            x = eval_expr(record, expr)
            if x is None:
                return None
            coeff = float(self.learned_coeffs.get(name, 0.0))
            contrib = coeff * x
            total += contrib
            learned[name] = {"expr": expr, "value": x, "coeff": coeff, "contribution": contrib}

        y = eval_expr(record, self.y_expr)

        return {
            "intercept": self.intercept,
            "fixed": fixed,
            "learned": learned,
            "prediction": total,
            "measured": y,
            "residual": None if y is None else y - total,
        }

    def residual(self, record: dict) -> float | None:
        y = eval_expr(record, self.y_expr)
        pred = self.predict(record)
        if y is None or pred is None:
            return None
        return y - pred


def _build_matrix(
    records: list[dict],
    *,
    fixed_terms: dict[str, str],
    fixed_coeffs: dict[str, float],
    learned_terms: dict[str, str],
    y_expr: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict]]:
    y_values: list[float] = []
    base_values: list[float] = []
    X_rows: list[list[float]] = []
    used_records: list[dict] = []

    for rec in records:
        y = eval_expr(rec, y_expr)
        if y is None:
            continue

        base = 0.0
        ok = True
        for name, expr in fixed_terms.items():
            x = eval_expr(rec, expr)
            if x is None:
                ok = False
                break
            base += float(fixed_coeffs.get(name, 1.0)) * x
        if not ok:
            continue

        row: list[float] = []
        for _name, expr in learned_terms.items():
            x = eval_expr(rec, expr)
            if x is None:
                ok = False
                break
            row.append(x)
        if not ok:
            continue

        y_values.append(y)
        base_values.append(base)
        X_rows.append(row)
        used_records.append(rec)

    if not y_values:
        raise ValueError("No valid records for training/evaluation")

    return (
        np.asarray(y_values, dtype=float),
        np.asarray(base_values, dtype=float),
        np.asarray(X_rows, dtype=float),
        used_records,
    )


def _project_bounds(weights: np.ndarray, names: list[str], bounds: dict[str, tuple[float | None, float | None]]) -> np.ndarray:
    out = weights.copy()
    for j, name in enumerate(names):
        lo, hi = bounds.get(name, (None, None))
        if lo is not None and out[j] < lo:
            out[j] = float(lo)
        if hi is not None and out[j] > hi:
            out[j] = float(hi)
    return out


def fit_expression_model(
    records: list[dict],
    *,
    fixed_terms: dict[str, str],
    fixed_coeffs: dict[str, float] | None = None,
    learned_terms: dict[str, str],
    learned_priors: dict[str, float] | None = None,
    bounds: dict[str, tuple[float | None, float | None]] | None = None,
    y_expr: str = "measures.rssi",
    fit_intercept: bool = True,
    tau: float = 0.5,
    l2_to_prior: float = 0.0,
    max_iter: int = 5000,
    lr: float = 0.05,
    tol: float = 1e-7,
) -> ExpressionModel:
    """
    Fit:
        y ~= intercept + fixed_terms@fixed_coeffs + learned_terms@learned_coeffs

    Expressions carry the sign. For example:
        fixed_terms  = {"tx_power": "tx.pw"}
        learned_terms = {"fspl": "-features.fspl"}

    Quantile loss:
        tau=0.5  median fit
        tau=0.75 upper-envelope fit for RSSI
    """
    if not 0.0 < tau < 1.0:
        raise ValueError("tau must be between 0 and 1")

    fixed_coeffs = fixed_coeffs or {name: 1.0 for name in fixed_terms}
    learned_priors = learned_priors or {name: 0.0 for name in learned_terms}
    bounds = bounds or {}

    names = list(learned_terms.keys())
    y, base, X, used_records = _build_matrix(
        records,
        fixed_terms=fixed_terms,
        fixed_coeffs=fixed_coeffs,
        learned_terms=learned_terms,
        y_expr=y_expr,
    )

    w = np.asarray([float(learned_priors.get(name, 0.0)) for name in names], dtype=float)
    w = _project_bounds(w, names, bounds)

    if fit_intercept:
        intercept = float(np.quantile(y - base - X @ w, tau))
    else:
        intercept = 0.0

    last_obj = float("inf")

    for it in range(1, max_iter + 1):
        pred = intercept + base + X @ w
        residual = y - pred

        # derivative of pinball loss wrt prediction
        d_pred = np.where(residual >= 0.0, -tau, 1.0 - tau)

        grad_w = (X.T @ d_pred) / len(y)
        if l2_to_prior > 0:
            prior_vec = np.asarray([float(learned_priors.get(name, 0.0)) for name in names], dtype=float)
            grad_w += 2.0 * l2_to_prior * (w - prior_vec)

        grad_b = float(np.mean(d_pred)) if fit_intercept else 0.0

        step = lr / math.sqrt(it)
        w_new = _project_bounds(w - step * grad_w, names, bounds)
        b_new = intercept - step * grad_b if fit_intercept else 0.0

        pred_new = b_new + base + X @ w_new
        res_new = y - pred_new
        pinball = np.where(res_new >= 0.0, tau * res_new, (tau - 1.0) * res_new)
        obj = float(np.mean(pinball))
        if l2_to_prior > 0:
            prior_vec = np.asarray([float(learned_priors.get(name, 0.0)) for name in names], dtype=float)
            obj += float(l2_to_prior * np.sum((w_new - prior_vec) ** 2))

        if abs(last_obj - obj) < tol:
            w = w_new
            intercept = b_new
            last_obj = obj
            break

        w = w_new
        intercept = b_new
        last_obj = obj

    return ExpressionModel(
        fixed_terms=dict(fixed_terms),
        fixed_coeffs={k: float(v) for k, v in fixed_coeffs.items()},
        learned_terms=dict(learned_terms),
        learned_coeffs={name: float(value) for name, value in zip(names, w)},
        intercept=float(intercept),
        y_expr=y_expr,
        training_info={
            "n_training_records": int(len(y)),
            "n_input_records": int(len(records)),
            "tau": float(tau),
            "fit_intercept": bool(fit_intercept),
            "l2_to_prior": float(l2_to_prior),
            "iterations": int(it),
            "objective": float(last_obj),
            "optimizer": "projected_subgradient",
        },
    )


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------

def residuals(model: ExpressionModel, records: list[dict]) -> np.ndarray:
    vals: list[float] = []
    for rec in records:
        r = model.residual(rec)
        if r is not None and is_number(r):
            vals.append(float(r))
    return np.asarray(vals, dtype=float)


def evaluate_model(model: ExpressionModel, records: list[dict]) -> dict[str, float]:
    y_true: list[float] = []
    y_pred: list[float] = []

    for rec in records:
        y = eval_expr(rec, model.y_expr)
        p = model.predict(rec)
        if y is None or p is None:
            continue
        y_true.append(y)
        y_pred.append(p)

    if not y_true:
        return {"n": 0}

    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    e = y - p

    mae = float(np.mean(np.abs(e)))
    rmse = float(np.sqrt(np.mean(e * e)))
    bias = float(np.mean(e))
    ss_res = float(np.sum((y - p) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "n": int(len(y)),
        "mae": mae,
        "rmse": rmse,
        "bias": bias,
        "r2": r2,
    }


def describe_residuals(model: ExpressionModel, records: list[dict]) -> pd.Series:
    r = residuals(model, records)
    if len(r) == 0:
        return pd.Series(dtype=float)
    return pd.Series(r, name="residual").describe(
        percentiles=[0.10, 0.25, 0.50, 0.75, 0.90]
    )


def add_model_field(
    records: list[dict],
    *,
    field_name: str,
    fn: Callable[[dict], float | None],
    container: str = "_model",
) -> None:
    for rec in records:
        value = fn(rec)
        if value is None or not is_number(value):
            continue
        rec.setdefault(container, {})
        rec[container][field_name] = float(value)
