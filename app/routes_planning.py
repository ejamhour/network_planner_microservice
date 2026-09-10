from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from cisei_lib.io.async_geo_service_pool import AsyncGeoServicePool
from cisei_lib.planners import CellPlanner, GraphPlanner, PlanningScenario
import cisei_lib.planners.planner_reports as reports


router = APIRouter(prefix="/planning", tags=["planning"])


class ScenarioRequest(BaseModel):
    """
    Request body containing a complete scenario configuration dictionary.

    The accepted structure is the same consumed by
    ``PlanningScenario.from_config_dict``: ``scenario``, ``antennas``,
    ``interfaces``, ``devices``, ``metrics``, ``instances``, ``net`` and
    optional ``sites``.
    """

    scenario: dict[str, Any] = Field(default_factory=dict)


class CreateScenarioRequest(ScenarioRequest):
    """Create or replace an in-memory scenario under an optional id."""

    scenario_id: str | None = None


class CandidateRequest(ScenarioRequest):
    """Build generic candidate edges from scenario connectivity rules."""

    preserve_existing: bool = True


class RunGraphRequest(CandidateRequest):
    """
    Run the generic GraphPlanner workflow.

    If ``edge_metrics`` is supplied, the API skips feature extraction and uses
    those scalar edge costs directly. Otherwise it computes metrics by calling
    the external features service through ``AsyncGeoServicePool``.
    """

    edge_metrics: list[dict[str, Any]] | None = None
    include_features: bool = False
    rank_threshold: float | None = None
    geo_base_url: str | None = None
    geo_user_prefix: str = "planning-api"
    geo_pool_size: int = 1
    geo_timeout: float = 120.0


class CellCandidateRequest(ScenarioRequest):
    """Build cell-primary candidate edges with optional sector filtering."""

    primary_tech: str = "lte"
    sector_aware: bool = True
    limit_m: float | None = None
    degree: int | None = None


class CellTuneRequest(CellCandidateRequest):
    """Run the pre-metric sector tuning report/helper."""

    site_ids: list[str] | None = None
    apply_rotation: bool = False
    prune_empty: bool = False
    step_deg: float = 5.0
    uncovered_weight: float = 10.0
    imbalance_weight: float = 1.0


class RunCellRequest(CellCandidateRequest):
    """
    Run the direct cell-planning workflow.

    The endpoint builds primary-tech candidates, computes real metrics unless
    explicit ``edge_metrics`` are supplied, runs RPL and returns result plus
    service/quality reports.
    """

    edge_metrics: list[dict[str, Any]] | None = None
    include_features: bool = False
    rank_threshold: float | None = None
    geo_base_url: str | None = None
    geo_user_prefix: str = "planning-api"
    geo_pool_size: int = 1
    geo_timeout: float = 120.0


@router.get(
    "/status",
    summary="Return planning runtime status",
)
def planning_status(request: Request):
    runtime = request.app.state.runtime
    return _ok(
        {
            "scenarios": len(runtime.planning_scenarios),
            "planners": len(runtime.planning_planners),
        }
    )


@router.post(
    "/scenarios",
    summary="Create or replace an in-memory planning scenario",
)
def create_scenario(
    request: Request,
    payload: CreateScenarioRequest,
):
    try:
        scenario = _scenario_from_payload(payload)
        scenario_id = payload.scenario_id or uuid.uuid4().hex
        runtime = request.app.state.runtime
        runtime.planning_scenarios[scenario_id] = scenario
        runtime.planning_planners.pop(scenario_id, None)
        return _ok(
            {
                "scenario_id": scenario_id,
                "errors": scenario.validate(),
                "summary": _scenario_summary(scenario),
                "scenario": scenario.to_config_dict(include_sites=True),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.get(
    "/scenarios/{scenario_id}",
    summary="Inspect an in-memory planning scenario",
)
def get_scenario(
    request: Request,
    scenario_id: str,
):
    try:
        scenario = _stored_scenario(request, scenario_id)
        return _ok(
            {
                "scenario_id": scenario_id,
                "errors": scenario.validate(),
                "summary": _scenario_summary(scenario),
                "scenario": scenario.to_config_dict(include_sites=True),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.get(
    "/scenarios",
    summary="List in-memory planning scenarios",
)
def list_scenarios(request: Request):
    runtime = request.app.state.runtime
    return _ok(
        [
            {
                "scenario_id": scenario_id,
                "summary": _scenario_summary(scenario),
                "has_planner_result": scenario_id in runtime.planning_planners,
            }
            for scenario_id, scenario in runtime.planning_scenarios.items()
        ]
    )


@router.delete(
    "/scenarios/{scenario_id}",
    summary="Remove an in-memory planning scenario and planner",
)
def delete_scenario(
    request: Request,
    scenario_id: str,
):
    runtime = request.app.state.runtime
    removed = scenario_id in runtime.planning_scenarios
    runtime.planning_scenarios.pop(scenario_id, None)
    runtime.planning_planners.pop(scenario_id, None)
    return _ok({"scenario_id": scenario_id, "removed": removed})


@router.post(
    "/scenarios/validate",
    summary="Validate a planning scenario without storing it",
)
def validate_scenario(payload: ScenarioRequest):
    try:
        scenario = _scenario_from_payload(payload)
        return _ok(
            {
                "errors": scenario.validate(),
                "summary": _scenario_summary(scenario),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.post(
    "/graph/candidates",
    summary="Build generic candidate edges from connectivity rules",
)
def graph_candidates(payload: CandidateRequest):
    try:
        scenario = _scenario_from_payload(payload)
        planner = GraphPlanner(scenario)
        planner.build_candidate_edges_from_rules(
            preserve_existing=payload.preserve_existing
        )
        return _ok(
            {
                "counts": planner.to_result_dict(
                    include_metrics=False,
                    include_features=False,
                )["counts"],
                "candidate_edges": _table_records(
                    reports.edge_table(planner, "candidate")
                ),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.post(
    "/graph/run",
    summary="Run generic graph planning",
)
async def graph_run(
    request: Request,
    payload: RunGraphRequest,
):
    try:
        scenario = _scenario_from_payload(payload)
        planner = GraphPlanner(scenario)
        planner.build_candidate_edges_from_rules(
            preserve_existing=payload.preserve_existing
        )
        await _compute_or_assign_metrics(
            request,
            planner,
            payload.edge_metrics,
            geo_base_url=payload.geo_base_url,
            geo_user_prefix=payload.geo_user_prefix,
            geo_pool_size=payload.geo_pool_size,
            geo_timeout=payload.geo_timeout,
        )
        planner.run_rpl()
        return _planner_response(
            planner,
            include_features=payload.include_features,
            rank_threshold=payload.rank_threshold,
        )
    except Exception as exc:
        return _error(exc)


@router.post(
    "/cell/candidates",
    summary="Build direct cell-planning candidate edges",
)
def cell_candidates(payload: CellCandidateRequest):
    try:
        scenario = _scenario_from_payload(payload)
        cell = CellPlanner(scenario, primary_tech=payload.primary_tech)
        cell.build_primary_candidates(
            sector_aware=payload.sector_aware,
            limit_m=payload.limit_m,
            degree=payload.degree,
        )
        return _ok(
            {
                "counts": cell.graph.to_result_dict(
                    include_metrics=False,
                    include_features=False,
                )["counts"],
                "sectors": _table_records(cell.sector_candidate_table()),
                "candidate_edges": _table_records(
                    reports.edge_table(cell.graph, "candidate")
                ),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.post(
    "/cell/tune_sectors",
    summary="Suggest or apply cell sector rotation/pruning",
)
def cell_tune_sectors(payload: CellTuneRequest):
    try:
        scenario = _scenario_from_payload(payload)
        cell = CellPlanner(scenario, primary_tech=payload.primary_tech)
        cell.build_primary_candidates(
            sector_aware=payload.sector_aware,
            limit_m=payload.limit_m,
            degree=payload.degree,
        )
        tuning = cell.tune_sectors(
            site_ids=payload.site_ids,
            apply_rotation=payload.apply_rotation,
            prune_empty=payload.prune_empty,
            step_deg=payload.step_deg,
            uncovered_weight=payload.uncovered_weight,
            imbalance_weight=payload.imbalance_weight,
            sector_aware=payload.sector_aware,
            limit_m=payload.limit_m,
            degree=payload.degree,
        )
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


@router.post(
    "/cell/run",
    summary="Run direct cell planning",
)
async def cell_run(
    request: Request,
    payload: RunCellRequest,
):
    try:
        scenario = _scenario_from_payload(payload)
        cell = CellPlanner(scenario, primary_tech=payload.primary_tech)
        cell.build_primary_candidates(
            sector_aware=payload.sector_aware,
            limit_m=payload.limit_m,
            degree=payload.degree,
        )
        await _compute_or_assign_metrics(
            request,
            cell.graph,
            payload.edge_metrics,
            geo_base_url=payload.geo_base_url,
            geo_user_prefix=payload.geo_user_prefix,
            geo_pool_size=payload.geo_pool_size,
            geo_timeout=payload.geo_timeout,
        )
        cell.run_rpl()
        response = _planner_response(
            cell.graph,
            include_features=payload.include_features,
            rank_threshold=payload.rank_threshold,
        )
        response["data"]["service"] = _table_records(cell.service_table())
        response["data"]["sectors"] = _table_records(
            cell.sector_candidate_table()
        )
        return response
    except Exception as exc:
        return _error(exc)


@router.post(
    "/stored/{scenario_id}/graph/candidates",
    summary="Build generic candidate edges from a stored scenario",
)
def stored_graph_candidates(
    request: Request,
    scenario_id: str,
    payload: CandidateRequest | None = None,
):
    try:
        if payload is None:
            payload = CandidateRequest()
        scenario = _stored_scenario(request, scenario_id)
        planner = GraphPlanner(scenario)
        planner.build_candidate_edges_from_rules(
            preserve_existing=payload.preserve_existing
        )
        request.app.state.runtime.planning_planners[scenario_id] = planner
        return _ok(
            {
                "counts": planner.to_result_dict(
                    include_metrics=False,
                    include_features=False,
                )["counts"],
                "candidate_edges": _table_records(
                    reports.edge_table(planner, "candidate")
                ),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.post(
    "/stored/{scenario_id}/graph/run",
    summary="Run generic graph planning from a stored scenario",
)
async def stored_graph_run(
    request: Request,
    scenario_id: str,
    payload: RunGraphRequest | None = None,
):
    try:
        if payload is None:
            payload = RunGraphRequest()
        scenario = _stored_scenario(request, scenario_id)
        planner = GraphPlanner(scenario)
        planner.build_candidate_edges_from_rules(
            preserve_existing=payload.preserve_existing
        )
        await _compute_or_assign_metrics(
            request,
            planner,
            payload.edge_metrics,
            geo_base_url=payload.geo_base_url,
            geo_user_prefix=payload.geo_user_prefix,
            geo_pool_size=payload.geo_pool_size,
            geo_timeout=payload.geo_timeout,
        )
        planner.run_rpl()
        request.app.state.runtime.planning_planners[scenario_id] = planner
        return _planner_response(
            planner,
            include_features=payload.include_features,
            rank_threshold=payload.rank_threshold,
        )
    except Exception as exc:
        return _error(exc)


@router.post(
    "/stored/{scenario_id}/cell/candidates",
    summary="Build cell-planning candidate edges from a stored scenario",
)
def stored_cell_candidates(
    request: Request,
    scenario_id: str,
    payload: CellCandidateRequest | None = None,
):
    try:
        if payload is None:
            payload = CellCandidateRequest()
        scenario = _stored_scenario(request, scenario_id)
        cell = CellPlanner(scenario, primary_tech=payload.primary_tech)
        cell.build_primary_candidates(
            sector_aware=payload.sector_aware,
            limit_m=payload.limit_m,
            degree=payload.degree,
        )
        request.app.state.runtime.planning_planners[scenario_id] = cell.graph
        return _ok(
            {
                "counts": cell.graph.to_result_dict(
                    include_metrics=False,
                    include_features=False,
                )["counts"],
                "sectors": _table_records(cell.sector_candidate_table()),
                "candidate_edges": _table_records(
                    reports.edge_table(cell.graph, "candidate")
                ),
            }
        )
    except Exception as exc:
        return _error(exc)


@router.post(
    "/stored/{scenario_id}/cell/tune_sectors",
    summary="Suggest or apply sector tuning for a stored scenario",
)
def stored_cell_tune_sectors(
    request: Request,
    scenario_id: str,
    payload: CellTuneRequest | None = None,
):
    try:
        if payload is None:
            payload = CellTuneRequest()
        scenario = _stored_scenario(request, scenario_id)
        cell = CellPlanner(scenario, primary_tech=payload.primary_tech)
        cell.build_primary_candidates(
            sector_aware=payload.sector_aware,
            limit_m=payload.limit_m,
            degree=payload.degree,
        )
        tuning = cell.tune_sectors(
            site_ids=payload.site_ids,
            apply_rotation=payload.apply_rotation,
            prune_empty=payload.prune_empty,
            step_deg=payload.step_deg,
            uncovered_weight=payload.uncovered_weight,
            imbalance_weight=payload.imbalance_weight,
            sector_aware=payload.sector_aware,
            limit_m=payload.limit_m,
            degree=payload.degree,
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


@router.post(
    "/stored/{scenario_id}/cell/run",
    summary="Run cell planning from a stored scenario",
)
async def stored_cell_run(
    request: Request,
    scenario_id: str,
    payload: RunCellRequest | None = None,
):
    try:
        if payload is None:
            payload = RunCellRequest()
        scenario = _stored_scenario(request, scenario_id)
        cell = CellPlanner(scenario, primary_tech=payload.primary_tech)
        cell.build_primary_candidates(
            sector_aware=payload.sector_aware,
            limit_m=payload.limit_m,
            degree=payload.degree,
        )
        await _compute_or_assign_metrics(
            request,
            cell.graph,
            payload.edge_metrics,
            geo_base_url=payload.geo_base_url,
            geo_user_prefix=payload.geo_user_prefix,
            geo_pool_size=payload.geo_pool_size,
            geo_timeout=payload.geo_timeout,
        )
        cell.run_rpl()
        request.app.state.runtime.planning_planners[scenario_id] = cell.graph
        response = _planner_response(
            cell.graph,
            include_features=payload.include_features,
            rank_threshold=payload.rank_threshold,
        )
        response["data"]["service"] = _table_records(cell.service_table())
        response["data"]["sectors"] = _table_records(
            cell.sector_candidate_table()
        )
        return response
    except Exception as exc:
        return _error(exc)


@router.get(
    "/stored/{scenario_id}/result",
    summary="Return the latest stored planner result",
)
def stored_result(
    request: Request,
    scenario_id: str,
    include_features: bool = False,
    rank_threshold: float | None = None,
):
    try:
        runtime = request.app.state.runtime
        planner = runtime.planning_planners[scenario_id]
        return _planner_response(
            planner,
            include_features=include_features,
            rank_threshold=rank_threshold,
        )
    except KeyError as exc:
        return _error(KeyError(f"No stored planner result: {scenario_id}"))
    except Exception as exc:
        return _error(exc)


async def _compute_or_assign_metrics(
    request: Request,
    planner: GraphPlanner,
    edge_metrics: list[dict[str, Any]] | None,
    *,
    geo_base_url: str | None = None,
    geo_user_prefix: str = "planning-api",
    geo_pool_size: int = 1,
    geo_timeout: float = 120.0,
) -> None:
    if edge_metrics is not None:
        planner.edge_metrics = _edge_metric_mapping(edge_metrics)
        return

    base_url = _geo_base_url(geo_base_url)
    geo = AsyncGeoServicePool(
        base_url=base_url,
        user_prefix=geo_user_prefix,
        pool_size=geo_pool_size,
        timeout=geo_timeout,
    )
    await planner.compute_edge_metrics(geo, manage_geo=True)


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


def _scenario_from_payload(payload: ScenarioRequest) -> PlanningScenario:
    return PlanningScenario.from_config_dict(payload.scenario)


def _stored_scenario(request: Request, scenario_id: str) -> PlanningScenario:
    runtime = request.app.state.runtime
    try:
        return runtime.planning_scenarios[scenario_id]
    except KeyError as exc:
        raise KeyError(f"Unknown planning scenario: {scenario_id}") from exc


def _edge_metric_mapping(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], float]:
    metrics = {}
    for record in records:
        src = str(record["src"])
        dst = str(record["dst"])
        metrics[(src, dst)] = float(record["metric"])
    return metrics


def _planner_response(
    planner: GraphPlanner,
    *,
    include_features: bool,
    rank_threshold: float | None,
):
    result = planner.to_result_dict(include_features=include_features)
    if rank_threshold is not None:
        result["quality_summary"] = reports.planning_quality_summary(
            planner,
            rank_threshold=rank_threshold,
        )
        result["quality"] = _table_records(
            reports.planning_quality_table(
                planner,
                rank_threshold=rank_threshold,
            )
        )

    return _ok(result)


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
