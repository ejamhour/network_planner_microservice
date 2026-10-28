"""Workspace workflow for fitting technology models from measured links.

Uses a separate copy of the raw-feature cache, enriched with measurements. This
module does not convert legacy data or own geographic extraction sessions.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile


def _save(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


class BaselineProject:
    """A baseline project with a baseline.json recipe in projects/<name>.

    Paths in the recipe are relative to the workspace (the project's parent's
    parent by default). numpy is needed only for training and evaluation.
    Expressions are user-provided trusted configuration, as in the RF core.
    """

    def __init__(self, path, *, workspace=None):
        self.path = Path(path).expanduser().resolve()
        self.workspace = (Path(workspace).expanduser().resolve() if workspace
                          is not None else self.path.parent.parent)
        if not self.path.is_relative_to(self.workspace):
            raise ValueError("Project must be inside the workspace")
        self.recipe = json.loads((self.path / "baseline.json").read_text(encoding="utf-8"))
        if self.recipe.get("version") != 1:
            raise ValueError("Unsupported baseline recipe version")
        self.features_path = self._file(self.recipe["data"])
        self.enriched_features_path = (self._file(self.recipe["enriched"])
                                      if "enriched" in self.recipe
                                      else self.path / "enriched_features.json")
        self.data_path = self.enriched_features_path
        self.measurements_path = (self._file(self.recipe["measurements"])
                                  if self.recipe.get("measurements") else None)
        self.model_path = self._file(self.recipe["model"])
        self.metric_path = self._file(self.recipe["metric"])
        if len({self.features_path, self.enriched_features_path, self.model_path, self.metric_path,
                self.path / "training_result.json", self.path / "evaluation.json"}) != 6:
            raise ValueError("Baseline inputs and outputs must use distinct paths")
        if self.measurements_path in {self.features_path, self.data_path, self.model_path, self.metric_path,
                                       self.path / "training_result.json", self.path / "evaluation.json"}:
            raise ValueError("Measurements must use a distinct input path")

    def _file(self, name):
        relative = Path(name)
        path = (self.workspace / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(self.workspace):
            raise ValueError("Baseline paths must remain inside the workspace")
        return path

    def _records(self):
        from .rssi_model import filter_records_by_many
        from .baseline_features import training_records
        if not self.enriched_features_path.exists():
            raise ValueError("Missing enriched_features.json; call enrich_features() first")
        records = training_records(json.loads(self.enriched_features_path.read_text(encoding="utf-8")))
        selected = filter_records_by_many(records, self.recipe.get("filters", []))
        return records, selected

    def enrich_features(self, *, measurements=None, replace=False):
        """Write observations to enriched_features.json; never modify raw cache.

        measurements is a workspace-relative JSON path or a parsed measurement
        document; omitted uses the recipe's measurements path. All associations
        are validated before an atomic save. Repeating unchanged data is safe.
        """
        from copy import deepcopy
        from .baseline_features import enrich_cache, validate_cache, request_key
        if measurements is None:
            if self.measurements_path is None:
                raise ValueError("Define measurements in baseline.json or provide measurements=")
            measurements = json.loads(self.measurements_path.read_text(encoding="utf-8"))
        elif isinstance(measurements, (str, Path)):
            measurements = json.loads(self._file(measurements).read_text(encoding="utf-8"))
        document = json.loads(self.features_path.read_text(encoding="utf-8"))
        validate_cache(document)
        previous = None
        if self.enriched_features_path.exists():
            previous = json.loads(self.enriched_features_path.read_text(encoding="utf-8"))
            validate_cache(previous)
            observations = {request_key(e["source"], e["request"]): e.get("observations", [])
                            for e in previous["entries"]}
            for entry in document["entries"]:
                entry.pop("observations", None)
                samples = observations.get(request_key(entry["source"], entry["request"]))
                if samples:
                    entry["observations"] = deepcopy(samples)
        enriched, report = enrich_cache(document, measurements, replace=replace)
        if enriched != previous:
            _save(self.enriched_features_path, enriched)
        return {**report, "features": str(self.features_path),
                "enriched_features": str(self.enriched_features_path)}

    def train(self):
        """Fit configured stages, then atomically save the final model and report."""
        from .rssi_model import fit_expression_model, filter_records_by_many, evaluate_model
        records, selected = self._records()
        models, reports = {}, []
        stages = self.recipe.get("stages", [])
        if not stages:
            raise ValueError("Baseline recipe requires training stages")
        for stage in stages:
            name = stage["name"]
            if name in models:
                raise ValueError(f"Duplicate stage: {name}")
            settings = dict(stage["fit"])
            for key in ("fixed_coeffs", "learned_priors"):
                settings[key] = {
                    term: (models[value.split(".")[0]].learned_coeffs[value.split(".")[1]]
                           if isinstance(value, str) else value)
                    for term, value in settings.get(key, {}).items()
                }
            for key in ("max_iter", "lr", "tol"):
                value = settings.get(key, {"max_iter": 5000, "lr": .05, "tol": 1e-7}[key])
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"{key} must be positive and finite")
            if not isinstance(settings.get("max_iter", 5000), int):
                raise ValueError("max_iter must be an integer")
            subset = filter_records_by_many(selected, stage.get("filters", []))
            model = fit_expression_model(subset, **settings)
            models[name] = model
            reports.append({"name": name, "training": model.training_info,
                            "coefficients": model.learned_coeffs,
                            "evaluation": evaluate_model(model, subset)})
        model = models[stages[-1]["name"]]
        report = {"input_records": len(records), "selected_records": len(selected),
                  "feature_entries": len(json.loads(self.enriched_features_path.read_text(encoding="utf-8"))["entries"]),
                  "data_sha256": hashlib.sha256(self.data_path.read_bytes()).hexdigest(),
                  "stages": reports, "model": self.recipe["model"]}
        _save(self.model_path, model.to_dict())
        report["model_sha256"] = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        _save(self.path / "training_result.json", report)
        return report

    def evaluate(self, *, rssi_threshold_dbm, save=True):
        """Compare measured/predicted RSSI using an explicit engineering threshold."""
        from .rssi_model import ExpressionModel, eval_expr, evaluate_model, classify_rssi
        if isinstance(rssi_threshold_dbm, bool) or not isinstance(rssi_threshold_dbm, (int, float)) or not math.isfinite(rssi_threshold_dbm):
            raise ValueError("RSSI threshold must be a finite number")
        _, records = self._records()
        model = ExpressionModel.load(self.model_path)
        links = []
        counts = dict(investigate=0, satisfactory=0, measured_satisfactory=0, review_design=0)
        for index, record in enumerate(records):
            predicted = model.predict(record)
            measured = eval_expr(record, model.y_expr)
            if predicted is None or measured is None:
                continue
            category = classify_rssi(predicted, measured, rssi_threshold_dbm)
            counts[category] += 1
            links.append({"index": index, "id": record["id"], "link_id": record.get("link_id"), "tx": record.get("tx", {}).get("name", ""),
                          "rx": record.get("rx", {}).get("name", ""),
                          "predicted": predicted, "measured": measured,
                          "residual": measured - predicted, "category": category,
                          "explanation": model.explain(record)})
        report = {"threshold_dbm": rssi_threshold_dbm, "summary": evaluate_model(model, records),
                  "selected_records": len(records), "excluded_predictions": len(records)-len(links),
                  "categories": counts, "links": links,
                  "data_sha256": hashlib.sha256(self.data_path.read_bytes()).hexdigest(),
                  "model_sha256": hashlib.sha256(self.model_path.read_bytes()).hexdigest()}
        if save:
            _save(self.path / "evaluation.json", report)
        return report

    def export_metric(self, *, required_rssi_dbm, db_per_cost=10.0):
        """Save a planning metric TOML with predicted RSSI and positive path cost.

        Cost is max(1, 2 - (predicted RSSI - required RSSI)/db_per_cost).
        Required RSSI and scale are explicit user decisions, not learned values.
        """
        from .rssi_model import ExpressionModel
        import tomlkit
        if (isinstance(required_rssi_dbm, bool) or not isinstance(required_rssi_dbm, (int, float))
                or not math.isfinite(required_rssi_dbm)):
            raise ValueError("Required RSSI must be finite")
        if isinstance(db_per_cost, bool) or not isinstance(db_per_cost, (int, float)) or not math.isfinite(db_per_cost) or db_per_cost <= 0:
            raise ValueError("db_per_cost must be positive and finite")
        model = ExpressionModel.load(self.model_path)
        terms = [repr(model.intercept)]
        for expressions, coefficients in ((model.fixed_terms, model.fixed_coeffs),
                                           (model.learned_terms, model.learned_coeffs)):
            for name, expression in expressions.items():
                # The example's math.log10 is log10 in the metric compiler.
                expression = expression.replace("math.log10(", "log10(")
                if "math." in expression or "np." in expression:
                    raise ValueError("Metric export supports arithmetic expressions and math.log10; rewrite other math/np functions for the metric compiler")
                terms.append(f"({coefficients.get(name, 1.0 if expressions is model.fixed_terms else 0.0)!r}) * ({expression})")
        spec = {"version": 1, "result": "metric", "expressions": {
            "predicted_rssi_dbm": " + ".join(terms),
            "metric": "max(1.0, 2.0 - (predicted_rssi_dbm - params.required_rssi_dbm) / params.db_per_cost)"},
            "parameters": {"required_rssi_dbm": float(required_rssi_dbm), "db_per_cost": float(db_per_cost)}}
        self.metric_path.parent.mkdir(parents=True, exist_ok=True)
        self.metric_path.write_text(tomlkit.dumps(spec), encoding="utf-8")
        return self.metric_path

    def show_training(self):
        from .baseline_view import training_view
        return self._show(training_view, "training_result.json", "training.html")

    def show_evaluation(self):
        from .baseline_view import evaluation_view
        return self._show(evaluation_view, "evaluation.json", "evaluation.html")

    def inspect_divergent_links(self, geo, *, freq_mhz, min_abs_error_db=10.0,
                                direction="both", limit=50, minimum_height_m=None):
        """Inspect largest RSSI residuals using an existing features client.

        direction: both, below_expected (negative residual), above_expected.
        Frequency and optional height floor must match the feature extraction.
        Construction displays controls but makes no geographic requests.
        """
        from .baseline_inspection import BaselineInspectionPanel, finite
        error = finite(min_abs_error_db, "Minimum absolute error")
        if error < 0:
            raise ValueError("Minimum absolute error must be nonnegative")
        if direction not in {"both", "below_expected", "above_expected"}:
            raise ValueError("Unknown residual direction")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Limit must be a positive integer")
        report = json.loads((self.path / "evaluation.json").read_text(encoding="utf-8"))
        self._check_result(report)
        _, records = self._records()
        selected = [row for row in report["links"] if abs(row["residual"]) >= error
                    and (direction == "both" or (row["residual"] < 0 if direction == "below_expected"
                                                 else row["residual"] > 0))]
        selected.sort(key=lambda row: (-abs(row["residual"]), row["index"]))
        edges = [{**row, "src": f"measured_{row['index']}:tx",
                  "dst": f"measured_{row['index']}:rx", "src_site": row["tx"],
                  "dst_site": row["rx"], "record": records[row["index"]]}
                 for row in selected[:limit]]
        panel = BaselineInspectionPanel(geo, edges, freq_mhz=freq_mhz,
                                        minimum_height_m=minimum_height_m)
        panel.show()
        return panel

    def _check_result(self, report):
        if report.get("data_sha256") != hashlib.sha256(self.data_path.read_bytes()).hexdigest():
            raise ValueError("Measured data changed; rerun training or evaluation before displaying results")
        if report.get("model_sha256") != hashlib.sha256(self.model_path.read_bytes()).hexdigest():
            raise ValueError("Model changed; rerun training or evaluation before displaying results")

    def _show(self, render, filename, htmlname):
        from IPython.display import HTML, display
        report = json.loads((self.path / filename).read_text(encoding="utf-8"))
        self._check_result(report)
        html = render(report)
        (self.path / htmlname).write_text(html, encoding="utf-8")
        view = HTML(html)
        display(view)
        return view
