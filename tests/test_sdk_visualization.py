import copy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from cisei_planning_sdk import load_result, plot_network, save_result


def test_loaded_graphs_preserve_unserved_sites_and_collapse_interfaces(tmp_path, monkeypatch):
    import requests
    def no_network(*args, **kwargs):
        pytest.fail("Plotting a saved result must not contact a service")
    monkeypatch.setattr(requests.Session, "request", no_network)
    nodes = [
        {"node_id": "a1", "site_id": "tower", "x": 0, "y": 0, "connected": True},
        {"node_id": "a2", "site_id": "tower", "x": 0, "y": 0, "connected": True},
        {"node_id": "b", "site_id": "served", "x": 100, "y": 100},
        {"node_id": "c", "site_id": "unserved", "x": 200, "y": 0},
    ]
    result = {"rpl_nodes": nodes, "planned_nodes": nodes[:3],
              "candidate_edges": [{"src": "a1", "dst": "b"}, {"src": "a2", "dst": "b"},
                                  {"src": "a1", "dst": "a2"}, {"src": "a1", "dst": "c"}],
              "planned_edges": [{"src": "a1", "dst": "b"}]}
    result["planned_nodes"] = [dict(row, parent="a1" if row["node_id"] == "b" else None)
                               for row in nodes[:3]]
    loaded = load_result(save_result(result, tmp_path / "result.json"))
    original = copy.deepcopy(loaded)
    for kind, expected_arrows in (("candidate", 0), ("final", 1)):
        ax = plot_network(loaded, kind=kind)
        assert sum(len(c.get_offsets()) for c in ax.collections) == 3
        assert sum(t.arrow_patch is not None for t in ax.texts) == expected_arrows
        if kind == "candidate":
            assert len(ax.lines) == 2
        if kind == "final":
            assert "Unserved client / relay" in ax.get_legend_handles_labels()[1]
        ax.figure.canvas.draw()
        plt.close(ax.figure)
    assert loaded == original
