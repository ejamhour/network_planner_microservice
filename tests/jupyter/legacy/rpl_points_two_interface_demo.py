import math
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from cisei_lib.planners.graph_planner import GraphPlanner
import cisei_lib.planners.planner_reports as reports


CONFIG_PATH = REPO_ROOT / "tests/jupyter/rpl_points_two_interface.toml"
POINTS_PATH = REPO_ROOT / "tests/jupyter/points.csv"


def distance_metric(planner, edge):
    src, dst = edge
    attrs = planner._edge_attrs(src, dst)

    if attrs.get("kind") == "internal":
        return float(attrs.get("metric", 0.0))

    rpl_by_id = {node.node_id: node for node in planner.rpl_nodes()}
    src_node = rpl_by_id[src]
    dst_node = rpl_by_id[dst]
    distance_m = math.hypot(
        dst_node.pos_utm[0] - src_node.pos_utm[0],
        dst_node.pos_utm[1] - src_node.pos_utm[1],
    )

    if attrs.get("tech") == "lte":
        return max(1.0, distance_m / 1000.0)
    if attrs.get("tech") == "n2n":
        return max(1.0, distance_m / 500.0)

    raise ValueError(f"Unknown edge technology: {attrs}")


def build_planner():
    planner = GraphPlanner.from_toml(CONFIG_PATH)
    points = pd.read_csv(POINTS_PATH)
    points["id"] = points["id"].astype(str).str.strip()

    overrides = {
        site_id: {"device_profile": "dual_leaf"}
        for site_id in points["id"]
    }
    for site_id in ["itallia", "torre"]:
        overrides[site_id] = {"device_profile": "lte_root"}
    for site_id in ["deneka", "saturno"]:
        overrides[site_id] = {"device_profile": "dual_router"}

    planner.add_records(points.to_dict("records"), overrides_by_id=overrides)
    planner.build_candidate_edges_from_rules()
    planner.edge_metrics = {
        edge: round(distance_metric(planner, edge), 3)
        for edge in planner.candidate_edges
    }
    planner.run_rpl()
    return planner


if __name__ == "__main__":
    planner = build_planner()

    print("Configured metric techs:", sorted(planner.metric_functions_by_tech))
    print("Sites:", len(planner.nodes))
    print("RPL nodes:", len(planner.rpl_nodes()))
    print("Candidate edges:", len(planner.candidate_edges))
    print("Result counts:", planner.rpl_result_counts())

    print("\nInterfaces by site:")
    print(
        reports.config_table(planner, "interfaces")
        .groupby("site_id")["interface_profile"]
        .agg(list)
        .to_string()
    )

    print("\nCandidate edge counts:")
    print(
        reports.edge_table(planner, "candidate")
        .groupby(["kind", "src_tech", "dst_tech"])
        .size()
        .to_string()
    )

    print("\nResult nodes:")
    node_columns = [
        "node_id",
        "site_id",
        "parent_site_id",
        "connected",
        "rpl_relay",
        "rank",
        "interface_can_relay",
        "can_relay",
    ]
    print(
        reports.node_table(planner, "result")[node_columns]
        .to_string(index=False)
    )

    print("\nResult edges:")
    edge_columns = [
        "src_site",
        "dst_site",
        "kind",
        "src_tech",
        "dst_tech",
        "metric",
    ]
    print(
        reports.edge_table(planner, "result")[edge_columns]
        .to_string(index=False)
    )
