"""Network execution state and orchestration over existing server implementations.

No HTTP routing or local SDK behavior lives here. Candidates are prepared by the
existing GraphPlanner; metrics and solve use its existing methods. This adapter
tracks stage readiness and result provenance, not another planning algorithm.
"""
from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite

from app.feature_cache import FeatureCache
from app.routes_planning import _compute_or_assign_metrics, _metrics_response, _table_records
from cisei_lib.project_io.placements import effective_placements
from cisei_lib.planners.cell.cell_planning_solution import CellPlanningSolution
from cisei_lib.project_io.planning_results import load_planning_result, save_planning_result
from cisei_lib.project_io.diagnostics import ProjectIssue, ProjectValidationError


@dataclass
class ExecutionState:
    metric_requests: list = field(default_factory=list)
    metrics_ready: bool = False
    solution_hash: str | None = None


def _fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def compute_metrics(runtime, project_id, body):
    planner = runtime.planning_planners[project_id]
    state = runtime.planning_runs.setdefault(project_id, ExecutionState())
    planner.rpl = None
    state.solution_hash = None
    state.metrics_ready = False
    if body.edge_metrics is not None:
        # Validate supplied costs before assignment; do not allow phantom links.
        supplied = []
        candidates = {frozenset(edge): edge for edge in planner.candidate_edges}
        seen = set()
        for record in body.edge_metrics:
            src, dst, cost = str(record["src"]), str(record["dst"]), float(record["metric"])
            key = frozenset((src, dst))
            if key not in candidates or key in seen or not isfinite(cost) or cost < 0:
                raise ValueError("Each supplied metric must be a unique generated candidate with a finite nonnegative cost.")
            seen.add(key)
            a, b = candidates[key]
            supplied.append({"src": a, "dst": b, "metric": cost})
        body = body.model_copy(update={"edge_metrics": supplied})
        planner.edge_features = {}
    await _compute_or_assign_metrics(planner, body, runtime=runtime,
        cache=runtime.feature_caches.setdefault(project_id, FeatureCache()))
    if body.edge_metrics is not None or body.batch_size is None or body.batch_offset == 0:
        state.metric_requests.clear()
    state.metric_requests.append({**body.model_dump(exclude={"edge_metrics", "include_features"}),
                                  "supplied_metrics": body.edge_metrics is not None})
    state.metrics_ready = all(edge in planner.edge_metrics for edge in planner.candidate_edges)
    data = _metrics_response(planner, include_features=False)["data"]
    data["complete"] = state.metrics_ready
    if body.batch_size is not None:
        total = len(planner.candidate_edges)
        data["batch"] = {"offset": body.batch_offset,
            "next_offset": min(body.batch_offset + body.batch_size, total),
            "total_links": total, "complete": state.metrics_ready}
    return data


def solve_execution(runtime, store, identity, options, *, planner_kind):
    """Reuse an exact saved execution or solve and atomically persist its result."""
    project_id = identity["project_id"]
    planner = runtime.planning_planners[project_id]
    state = runtime.planning_runs[project_id]
    placements = effective_placements(planner.scenario)
    parameters = {
        "planner": planner_kind,
        "effective_placements_hash": _fingerprint(placements),
        "candidates": runtime.candidate_options[project_id].model_dump(),
        "candidate_hash": _fingerprint(sorted([list(edge) for edge in planner.candidate_edges])),
        "metric_hash": _fingerprint(sorted([[a, b, value] for (a, b), value in planner.edge_metrics.items()])),
        "metric_requests": state.metric_requests,
        "solve": options.model_dump(),
    }
    parameter_hash = _fingerprint(parameters)
    saved = load_planning_result(store, identity, parameter_hash=parameter_hash, planner=planner_kind)
    if {key: saved[key] for key in ("project_id", "input_hash", "input_revision")} != runtime.active_project:
        raise ProjectValidationError([ProjectIssue("result.json", "active_project_outdated",
            "Inputs changed during planning. Rebuild and activate again.")], stage=f"{planner_kind}_solve")
    reused = saved["valid"]
    if reused:
        result = saved["result"]
    else:
        if planner_kind == "cell":
            runtime.cell_planners[project_id].run_rpl(**options.model_dump())
        else:
            planner.run_rpl(**options.model_dump())
        result = planner.to_result_dict(include_features=False)
        result["antenna_placements"] = placements
        if planner_kind == "cell":
            cell = runtime.cell_planners[project_id]
            solution = CellPlanningSolution.from_result(result)
            result["cell"] = {
                "primary_tech": cell.primary_tech,
                "sectors": _table_records(cell.sector_candidate_table()),
                "client_service": solution.client_service_table(primary_tech=cell.primary_tech),
                "cell_load": solution.cell_load_table(primary_tech=cell.primary_tech)}
        result["project_run"] = {"planner": planner_kind, "inputs": dict(runtime.active_project),
            "parameter_hash": parameter_hash, "parameters": parameters}
        save_planning_result(store, identity, runtime.active_project, result)
    state.solution_hash = parameter_hash
    return {**solution_payload(result), "parameter_hash": parameter_hash,
            "saved_result": "result.json", "reused": reused}


def solution_payload(result):
    """Public solution tables plus the effective installation snapshot."""
    return {key: result[key] for key in ("counts", "planned_nodes", "planned_edges", "cell", "antenna_placements")
            if key in result}
