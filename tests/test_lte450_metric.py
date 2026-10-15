from pathlib import Path
import pytest
from cisei_lib.planners.metric_compiler import compile_metric_spec

SPEC = Path(__file__).resolve().parents[1] / "examples/metrics/lte450_link_margin.toml"

@pytest.mark.parametrize("loss,expected", [(100, 1), (127, 2), (137, 3), (147, 4)])
def test_bidirectional_budget_and_threshold(loss, expected):
    metric = compile_metric_spec(SPEC.read_text())
    record = {"tx": {"pw": 43, "ant_gain": 11}, "rx": {"ant_gain": 0}, "features": {"fspl": loss, "delta_diffra": 0}}
    assert metric(record) == pytest.approx(expected)
    record["features"]["delta_diffra"] = 10
    assert metric(record) >= expected


def test_downlink_can_be_limiting_and_higher_power_does_not_fix_uplink():
    metric = compile_metric_spec(SPEC.read_text())
    record = {"tx": {"pw": 43, "ant_gain": 11}, "rx": {"ant_gain": 0}, "features": {"fspl": 137, "delta_diffra": 0}}
    assert metric(record) == pytest.approx(3)
    record["tx"]["pw"] = 60
    assert metric(record) == pytest.approx(3)
    record["tx"]["pw"] = 0
    assert metric(record) > 3
