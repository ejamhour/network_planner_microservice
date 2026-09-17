from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from cisei_lib.planners import (
    CellPlanningSolution,
    CellPlanner,
    GraphPlanner,
    PlanningScenario,
    PlanningSolution,
)
import cisei_lib.planners.planner_reports as reports


router = APIRouter(prefix="/planning", tags=["planning"])

PlannerKind = Literal["graph", "cell"]


class ScenarioBody(BaseModel):
    """A complete PlanningScenario configuration as JSON."""

    scenario: dict[str, Any] = Field(default_factory=dict)


class DefineScenarioBody(ScenarioBody):
    """Define or replace a stored planning scenario."""

    scenario_id: str | None = None


class CandidateEdgeBuildBody(BaseModel):
    """
    Build the candidate-edge graph, without feature extraction.

    ``planner="graph"`` uses generic connectivity rules from the scenario.
    ``planner="cell"`` uses direct cell-to-client candidate construction,
    optionally applying sector geometry.
    """

    planner: PlannerKind = "graph"
    preserve_existing: bool = True
    primary_tech: str = "lte"
    sector_aware: bool = True
    limit_m: float | None = None
    degree: int | None = None


class CandidateTuneBody(CandidateEdgeBuildBody):
    """
    Tune/review candidates before costly feature extraction.

    Currently implemented for ``planner="cell"``. It can suggest sector
    rotation and optionally apply rotation/prune empty sectors.
    """

    site_ids: list[str] | None = None
    apply_rotation: bool = False
    prune_empty: bool = False
    step_deg: float = 5.0
    uncovered_weight: float = 10.0
    imbalance_weight: float = 1.0


class MetricComputeBody(BaseModel):
    """
    Compute or provide edge metrics for the built candidate graph.

    If ``edge_metrics`` is supplied, no feature service call is made. Otherwise
    the planner uses ``AsyncGeoServicePool`` to call the external features
    service and then collapses features into per-tech metrics.
    """

    edge_metrics: list[dict[str, Any]] | None = None
    include_features: bool = False
    geo_base_url: str | None = None
    geo_user_prefix: str = "planning-api"
    geo_pool_size: int = 1
    geo_timeout: float = 120.0


class ResultEvaluateBody(BaseModel):
    """
    Evaluate a serialized planning result without runtime scenario state.

    ``result`` must be a dictionary previously exported by
    ``/planning/scenarios/{scenario_id}/export/result`` or an equivalent
    ``GraphPlanner.to_result_dict`` payload.
    """

    result: dict[str, Any] = Field(default_factory=dict)
    rank_threshold: float = Field(..., ge=0)
    primary_tech: str = "lte"
    solution_kind: PlannerKind = "graph"


class WorkflowRunBody(
    ScenarioBody,
    CandidateTuneBody,
    MetricComputeBody,
):
    """
    One-shot workflow for MCP or simple notebook calls.

    It executes the staged sequence in memory:
    define scenario -> build candidate edges -> optional tune candidates ->
    compute/provide metrics -> solve network -> evaluate/export result.
    """

    tune_candidates: bool = False
    solve: bool = True
    rank_threshold: float | None = None


@router.get("/status", summary="Planning runtime status")
def status(request: Request):
    runtime = request.app.state.runtime
    return _ok(
        {
            "scenarios": len(runtime.planning_scenarios),
            "planners": len(runtime.planning_planners),
            "geo_pools": runtime.geo_pool_status(),
        }
    )


# Stage 1: Define Scenario --------------------------------------------------


@router.post(
    "/scenarios/define",
    summary="Stage 1 - define a planning scenario",
)
def define_scenario(request: Request, body: DefineScenarioBody):
    try:
        scenario = _scenario_from_dict(body.scenario)
        scenario_id = body.scenario_id or uuid.uuid4().hex

        runtime = request.app.state.runtime
        runtime.planning_scenarios[scenario_id] = scenario
        runtime.planning_planners.pop(scenario_id, None)

        return _ok(
            {
                "scenario_id": scenario_id,
                "summary": _scenario_summary(scenario),
                "validation_errors": scenario.validate(),
                "scenario": scenario.to_json_dict(include_sites=True),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.get(
    "/scenarios",
    summary="Stage 1 - list defined scenarios",
)
def list_scenarios(request: Request):
    runtime = request.app.state.runtime
    return _ok(
        [
            {
                "scenario_id": scenario_id,
                "summary": _scenario_summary(scenario),
                "has_candidate_edges": scenario_id in runtime.planning_planners,
            }
            for scenario_id, scenario in runtime.planning_scenarios.items()
        ]
    )


@router.get(
    "/scenarios/{scenario_id}",
    summary="Stage 1 - inspect a defined scenario",
)
def get_scenario(request: Request, scenario_id: str):
    try:
        scenario = _stored_scenario(request, scenario_id)
        return _ok(
            {
                "scenario_id": scenario_id,
                "summary": _scenario_summary(scenario),
                "validation_errors": scenario.validate(),
                "scenario": scenario.to_json_dict(include_sites=True),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.delete(
    "/scenarios/{scenario_id}",
    summary="Stage 1 - delete a defined scenario",
)
def delete_scenario(request: Request, scenario_id: str):
    runtime = request.app.state.runtime
    removed = scenario_id in runtime.planning_scenarios
    runtime.planning_scenarios.pop(scenario_id, None)
    runtime.planning_planners.pop(scenario_id, None)
    return _ok({"scenario_id": scenario_id, "removed": removed})


@router.post(
    "/scenarios/validate",
    summary="Stage 1 - validate a scenario without storing it",
)
def validate_scenario(body: ScenarioBody):
    try:
        scenario = _scenario_from_dict(body.scenario)
        return _ok(
            {
                "summary": _scenario_summary(scenario),
                "validation_errors": scenario.validate(),
            }
        )
    except Exception as exc:
        return _error(exc)


# Stage 2: Build Candidate Edges -------------------------------------------


@router.post(
    "/scenarios/{scenario_id}/candidate_edges/build",
    summary="Stage 2 - build candidate edges",
)
def build_candidate_edges(
    request: Request,
    scenario_id: str,
    body: CandidateEdgeBuildBody,
):
    try:
        scenario = _stored_scenario(request, scenario_id)
        planner = _build_candidate_edge_planner(scenario, body)
        request.app.state.runtime.planning_planners[scenario_id] = planner
        return _candidate_response(planner, body)
    except Exception as exc:
        return _error(exc)


@router.get(
    "/scenarios/{scenario_id}/candidate_edges",
    summary="Stage 2 - inspect built candidate edges",
)
def get_candidate_edges(
    request: Request,
    scenario_id: str,
):
    try:
        planner = _stored_planner(request, scenario_id)
        body = CandidateEdgeBuildBody(planner="graph")
        return _candidate_response(planner, body)
    except Exception as exc:
        return _error(exc)


# Stage 3: Review / Tune Candidates ----------------------------------------


@router.post(
    "/scenarios/{scenario_id}/candidate_edges/tune",
    summary="Stage 3 - tune candidate edges before metric computation",
)
def tune_candidates(
    request: Request,
    scenario_id: str,
    body: CandidateTuneBody,
):
    try:
        if body.planner != "cell":
            raise ValueError("Candidate tuning is currently implemented for planner='cell'")

        scenario = _stored_scenario(request, scenario_id)
        cell = CellPlanner(scenario, primary_tech=body.primary_tech)
        cell.build_primary_candidates(
            sector_aware=body.sector_aware,
            limit_m=body.limit_m,
            degree=body.degree,
        )
        tuning = cell.tune_sectors(
            site_ids=body.site_ids,
            apply_rotation=body.apply_rotation,
            prune_empty=body.prune_empty,
            step_deg=body.step_deg,
            uncovered_weight=body.uncovered_weight,
            imbalance_weight=body.imbalance_weight,
            sector_aware=body.sector_aware,
            limit_m=body.limit_m,
            degree=body.degree,
        )

        request.app.state.runtime.planning_planners[scenario_id] = cell.graph
        return _ok(
            {
                "candidate_edges": tuning["candidate_edges"],
                "rotation": _table_records(tuning["rotation"]),
                "prune": _table_records(tuning["prune"]),
                "sectors": _table_records(tuning["sectors"]),
            }
        )
    except Exception as exc:
        return _error(exc)


# Stage 4 and 5: Extract Features / Compute Metrics ------------------------


@router.post(
    "/scenarios/{scenario_id}/metrics/compute",
    summary="Stage 4/5 - extract features and compute link metrics",
)
async def compute_metrics(
    request: Request,
    scenario_id: str,
    body: MetricComputeBody,
):
    try:
        planner = _stored_planner(request, scenario_id)
        await _compute_or_assign_metrics(
            planner,
            body,
            runtime=request.app.state.runtime,
        )
        return _metrics_response(planner, include_features=body.include_features)
    except Exception as exc:
        return _error(exc)


@router.get(
    "/scenarios/{scenario_id}/metrics",
    summary="Stage 5 - inspect computed link metrics",
)
def get_metrics(
    request: Request,
    scenario_id: str,
    include_features: bool = False,
):
    try:
        planner = _stored_planner(request, scenario_id)
        return _metrics_response(planner, include_features=include_features)
    except Exception as exc:
        return _error(exc)


# Stage 6: Solve Network ----------------------------------------------------


@router.post(
    "/scenarios/{scenario_id}/solution/solve",
    summary="Stage 6 - solve the network",
)
def solve_network(request: Request, scenario_id: str):
    try:
        planner = _stored_planner(request, scenario_id)
        planner.run_rpl()
        return _solution_response(planner)
    except Exception as exc:
        return _error(exc)


@router.get(
    "/scenarios/{scenario_id}/solution",
    summary="Stage 6 - inspect solved network",
)
def get_solution(request: Request, scenario_id: str):
    try:
        planner = _stored_planner(request, scenario_id)
        return _solution_response(planner)
    except Exception as exc:
        return _error(exc)


# Stage 7: Evaluate Result --------------------------------------------------


@router.get(
    "/scenarios/{scenario_id}/evaluation",
    summary="Stage 7 - evaluate planning result",
)
def evaluate_result(
    request: Request,
    scenario_id: str,
    rank_threshold: float = Query(..., ge=0),
    primary_tech: str = "lte",
    solution_kind: PlannerKind = "graph",
):
    try:
        planner = _stored_planner(request, scenario_id)
        return _evaluation_response(
            planner,
            rank_threshold=rank_threshold,
            primary_tech=primary_tech,
            solution_kind=solution_kind,
        )
    except Exception as exc:
        return _error(exc)


@router.post(
    "/results/evaluate",
    summary="Stage 7 - evaluate a serialized planning result",
)
def evaluate_serialized_result(body: ResultEvaluateBody):
    try:
        solution = _solution_from_result(
            body.result,
            solution_kind=body.solution_kind,
        )
        return _ok(
            solution.evaluation_payload(
                rank_threshold=body.rank_threshold,
                primary_tech=body.primary_tech,
            )
        )
    except Exception as exc:
        return _error(exc)


# Stage 8: Export / Persist -------------------------------------------------


@router.get(
    "/scenarios/{scenario_id}/export/result",
    summary="Stage 8 - export planning result",
)
def export_result(
    request: Request,
    scenario_id: str,
    include_features: bool = False,
    rank_threshold: float | None = None,
    primary_tech: str = "lte",
    solution_kind: PlannerKind = "graph",
):
    try:
        planner = _stored_planner(request, scenario_id)
        result = planner.to_result_dict(include_features=include_features)
        if rank_threshold is not None:
            result["evaluation"] = _evaluation_payload(
                planner,
                rank_threshold=rank_threshold,
                primary_tech=primary_tech,
                solution_kind=solution_kind,
            )
        return _ok(result)
    except Exception as exc:
        return _error(exc)


@router.get(
    "/scenarios/{scenario_id}/export/scenario",
    summary="Stage 8 - export scenario definition",
)
def export_scenario(request: Request, scenario_id: str):
    try:
        scenario = _stored_scenario(request, scenario_id)
        return _ok(scenario.to_json_dict(include_sites=True))
    except Exception as exc:
        return _error(exc)


# Shortcut Workflow ---------------------------------------------------------


@router.post(
    "/workflow/run",
    summary="Shortcut - run a complete planning workflow in one request",
)
async def run_workflow(request: Request, body: WorkflowRunBody):
    try:
        scenario = _scenario_from_dict(body.scenario)
        planner = _build_candidate_edge_planner(scenario, body)

        tuning_payload = None
        if body.tune_candidates:
            if body.planner != "cell":
                raise ValueError("Candidate tuning is currently implemented for planner='cell'")
            cell = CellPlanner(scenario, primary_tech=body.primary_tech)
            cell.graph = planner
            tuning = cell.tune_sectors(
                site_ids=body.site_ids,
                apply_rotation=body.apply_rotation,
                prune_empty=body.prune_empty,
                step_deg=body.step_deg,
                uncovered_weight=body.uncovered_weight,
                imbalance_weight=body.imbalance_weight,
                sector_aware=body.sector_aware,
                limit_m=body.limit_m,
                degree=body.degree,
            )
            planner = cell.graph
            tuning_payload = {
                "candidate_edges": tuning["candidate_edges"],
                "rotation": _table_records(tuning["rotation"]),
                "prune": _table_records(tuning["prune"]),
                "sectors": _table_records(tuning["sectors"]),
            }

        await _compute_or_assign_metrics(
            planner,
            body,
            runtime=request.app.state.runtime,
        )
        if body.solve:
            planner.run_rpl()

        result = planner.to_result_dict(include_features=body.include_features)
        if tuning_payload is not None:
            result["candidate_tuning"] = tuning_payload
        if body.rank_threshold is not None and body.solve:
            result["evaluation"] = _evaluation_payload(
                planner,
                rank_threshold=body.rank_threshold,
                primary_tech=body.primary_tech,
                solution_kind=body.planner,
            )
        return _ok(result)
    except Exception as exc:
        return _error(exc)


def _build_candidate_edge_planner(
    scenario: PlanningScenario,
    body: CandidateEdgeBuildBody,
) -> GraphPlanner:
    if body.planner == "graph":
        planner = GraphPlanner(scenario)
        planner.build_candidate_edges_from_rules(
            preserve_existing=body.preserve_existing,
        )
        return planner

    if body.planner == "cell":
        cell = CellPlanner(scenario, primary_tech=body.primary_tech)
        cell.build_primary_candidates(
            sector_aware=body.sector_aware,
            limit_m=body.limit_m,
            degree=body.degree,
        )
        return cell.graph

    raise ValueError(f"Unsupported planner: {body.planner}")


async def _compute_or_assign_metrics(
    planner: GraphPlanner,
    body: MetricComputeBody,
    *,
    runtime=None,
) -> None:
    if body.edge_metrics is not None:
        planner.edge_metrics = _edge_metric_mapping(body.edge_metrics)
        return

    if runtime is None:
        raise RuntimeError(
            "Reusable runtime is required when edge_metrics are not supplied"
        )

    geo = await runtime.get_geo_pool(
        base_url=_geo_base_url(body.geo_base_url),
        user_prefix=body.geo_user_prefix,
        pool_size=body.geo_pool_size,
        timeout=body.geo_timeout,
    )
    await planner.compute_edge_metrics(geo, manage_geo=False)


def _candidate_response(
    planner: GraphPlanner,
    body: CandidateEdgeBuildBody,
):
    payload = {
        "counts": planner.to_result_dict(
            include_metrics=False,
            include_features=False,
        )["counts"],
        "candidate_edges": _table_records(reports.edge_table(planner, "candidate")),
    }
    if body.planner == "cell":
        cell = CellPlanner(planner, primary_tech=body.primary_tech)
        payload["sectors"] = _table_records(cell.sector_candidate_table())
    return _ok(payload)


def _metrics_response(
    planner: GraphPlanner,
    *,
    include_features: bool,
):
    payload = {
        "counts": planner.to_result_dict(
            include_features=include_features,
        )["counts"],
        "metrics": _table_records(reports.edge_table(planner, "metric")),
    }
    if include_features:
        payload["features"] = planner.to_result_dict(
            include_features=True,
        ).get("edge_features", [])
    return _ok(payload)


def _solution_response(planner: GraphPlanner):
    result = planner.to_result_dict(
        include_candidates=False,
        include_metrics=False,
        include_features=False,
    )
    return _ok(
        {
            "counts": result["counts"],
            "planned_nodes": result["planned_nodes"],
            "planned_edges": result["planned_edges"],
        }
    )


def _evaluation_response(
    planner: GraphPlanner,
    *,
    rank_threshold: float,
    primary_tech: str,
    solution_kind: PlannerKind = "graph",
):
    return _ok(
        _evaluation_payload(
            planner,
            rank_threshold=rank_threshold,
            primary_tech=primary_tech,
            solution_kind=solution_kind,
        )
    )


def _evaluation_payload(
    planner: GraphPlanner,
    *,
    rank_threshold: float,
    primary_tech: str,
    solution_kind: PlannerKind = "graph",
) -> dict[str, Any]:
    result = planner.to_result_dict(
        include_candidates=True,
        include_metrics=True,
        include_features=False,
    )
    solution = _solution_from_result(result, solution_kind=solution_kind)
    return solution.evaluation_payload(
        rank_threshold=rank_threshold,
        primary_tech=primary_tech,
    )


def _solution_from_result(
    result: Mapping[str, Any],
    *,
    solution_kind: PlannerKind,
) -> PlanningSolution:
    if solution_kind == "cell":
        return CellPlanningSolution.from_result(result)
    return PlanningSolution.from_result(result)


def _scenario_from_dict(config: Mapping[str, Any]) -> PlanningScenario:
    return PlanningScenario.from_config_dict(config)


def _stored_scenario(request: Request, scenario_id: str) -> PlanningScenario:
    runtime = request.app.state.runtime
    try:
        return runtime.planning_scenarios[scenario_id]
    except KeyError as exc:
        raise KeyError(f"Unknown planning scenario: {scenario_id}") from exc


def _stored_planner(request: Request, scenario_id: str) -> GraphPlanner:
    runtime = request.app.state.runtime
    try:
        return runtime.planning_planners[scenario_id]
    except KeyError as exc:
        raise KeyError(
            f"No prepared planner for scenario: {scenario_id}. "
            "Run the candidate_edges/build stage first."
        ) from exc


def _edge_metric_mapping(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], float]:
    metrics = {}
    for record in records:
        src = str(record["src"])
        dst = str(record["dst"])
        metrics[(src, dst)] = float(record["metric"])
    return metrics


def _geo_base_url(value: str | None) -> str:
    base_url = (
        value
        or os.getenv("PLANNING_FEATURES_BASE_URL")
        or os.getenv("FEATURES_SERVICE_URL")
        or os.getenv("GEO_BASE_URL")
    )
    if not base_url:
        raise RuntimeError(
            "geo_base_url is required when edge_metrics are not supplied. "
            "Set request.geo_base_url or PLANNING_FEATURES_BASE_URL."
        )
    return base_url


def _scenario_summary(scenario: PlanningScenario) -> dict[str, Any]:
    return {
        "working_crs": scenario.working_crs,
        "antennas": len(scenario.antenna_profiles),
        "interfaces": len(scenario.interface_profiles),
        "devices": len(scenario.device_profiles),
        "sites": len(scenario.site_nodes),
        "connectivity_rules": len(scenario.connectivity_rules),
        "manual_candidate_edges": len(scenario.candidate_edges),
        "metrics": sorted(scenario.metric_specs_by_tech),
    }


def _table_records(table) -> list[dict[str, Any]]:
    if hasattr(table, "to_dict"):
        records = table.to_dict(orient="records")
    else:
        records = list(table)

    return [_json_value(record) for record in records]


def _ok(data: Any, *, kind: str = "json") -> dict[str, Any]:
    return {"status": "OK", "kind": kind, "data": _json_value(data)}


def _error(exc: Exception) -> dict[str, Any]:
    return {"status": "error", "kind": "text", "data": str(exc)}


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return value
    if hasattr(value, "item"):
        return _json_value(value.item())
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    return str(value)
