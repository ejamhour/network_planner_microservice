import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from cisei_lib.planners import PlanningScenario, CellPlanner
from cisei_planning_sdk import load_scenario_bundle, ScenarioSession


def test_accumulation_matches_full_run_and_preserves_graph():
    async def exercise():
        config = load_scenario_bundle(Path(__file__).resolve().parents[1] / "examples/projects/example_1_LTE_planning_and_link_inspection/scenario.toml")
        def planner():
            p = CellPlanner(PlanningScenario.from_config_dict(config))
            p.build_primary_candidates()
            p.graph.metric_functions_by_tech = {"lte": lambda record: record["features"]["dist_m"] / 1000}
            return p.graph
        class Geo:
            async def get_features(self, *args, **kwargs):
                return {"dist_m": 1000.0, "terrain_peak": {"vv_0": 0.0}}
        full, batched = planner(), planner()
        edges = list(batched.candidate_edges)
        await full.compute_edge_metrics(Geo())
        for offset in range(0, len(edges), 2):
            await batched.compute_edge_metrics(Geo(), edges[offset:offset+2], accumulate=True)
            assert batched.candidate_edges == edges
        assert batched.edge_metrics == full.edge_metrics
        assert batched.edge_features == full.edge_features
        await batched.compute_edge_metrics(Geo(), edges[:2], accumulate=True)
        assert batched.edge_metrics == full.edge_metrics
        class FailedGeo:
            async def get_features(self, *args, **kwargs):
                raise RuntimeError("extraction failed")
        with pytest.raises(RuntimeError, match="extraction failed"):
            await batched.compute_edge_metrics(FailedGeo(), edges[:2], accumulate=True)
        assert batched.edge_metrics == full.edge_metrics
        assert batched.candidate_edges == edges
    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [None, "save", "request"])
def test_sdk_saves_before_next_batch_and_stops_on_failure(failure):
    events = []
    def request(method, path, **kwargs):
        offset = kwargs["json"]["batch_offset"]
        events.append(("request", offset))
        if failure == "request" and offset == 2:
            raise RuntimeError("request failed")
        return {"data": {"batch": {"next_offset": min(offset+2, 5), "complete": offset+2 >= 5, "total_links": 5}}}
    session = ScenarioSession(SimpleNamespace(_request=request), "test", "cell", "test", project=object())
    def save():
        events.append(("save", session.metrics["batch"]["next_offset"]))
        if failure == "save":
            raise OSError("disk full")
    session.save_feature_cache = save
    if failure:
        with pytest.raises((OSError, RuntimeError)):
            session.compute_metrics(batch_size=2)
        assert events == ([("request", 0), ("save", 2)] if failure == "save" else [("request", 0), ("save", 2), ("request", 2)])
    else:
        progress = []
        result = session.compute_metrics(batch_size=2, on_batch=progress.append)
        assert result["batch"]["complete"]
        assert events == [("request", 0), ("save", 2), ("request", 2), ("save", 4), ("request", 4), ("save", 5)]
        assert len(progress) == 3


@pytest.mark.parametrize("size", [0, -1, True, 1.5])
def test_invalid_batch_size_is_rejected_before_request(size):
    session = ScenarioSession(None, "test", "cell", "test", project=object())
    with pytest.raises(ValueError):
        session.compute_metrics(batch_size=size)
