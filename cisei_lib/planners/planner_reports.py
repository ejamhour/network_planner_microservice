from __future__ import annotations

from math import isfinite
from typing import Any


def config_table(planner, kind: str = "nodes"):
    kind = kind.lower()

    if kind == "sites":
        rows = []
        for node in planner.nodes.values():
            row = node.site.to_record()
            row.pop("geometry", None)
            row.pop("pos", None)
            rows.append(row)
        return _table(rows)

    if kind == "devices":
        return _table(
            [
                device.to_dict()
                for node in planner.nodes.values()
                for device in node.devices
            ]
        )

    if kind == "interfaces":
        return _table(
            [
                interface.to_dict()
                for node in planner.nodes.values()
                for interface in node.interfaces
            ]
        )

    if kind == "antennas":
        rows = []
        for antenna_id, antenna in planner.antenna_catalog.items():
            row = {"antenna_id": antenna_id}
            row.update(antenna.to_dict())
            rows.append(row)
        return _table(rows)

    if kind != "nodes":
        raise ValueError(
            "kind must be 'nodes', 'sites', 'devices', "
            "'interfaces', or 'antennas'"
        )

    rows = []
    for node in planner.nodes.values():
        site_record = node.site.to_record()
        site_record.pop("geometry", None)
        site_record.pop("pos", None)

        devices = {device.device_id: device for device in node.devices}

        for interface in node.interfaces:
            device = devices.get(interface.device_id)
            antenna = planner.antenna_catalog.get(interface.antenna_id)

            row = {}
            row.update(site_record)
            if device is not None:
                row.update(device.to_dict())
            row.update(interface.to_dict())

            if antenna is not None:
                row.update(
                    {
                        "antenna_kind": antenna.kind,
                        "antenna_model_id": antenna.model_id,
                        "antenna_description": antenna.description,
                        "antenna_gain_dbi": antenna.gain_dbi,
                        "antenna_azimuth_deg": antenna.azimuth_deg,
                        "antenna_downtilt_deg": antenna.downtilt_deg,
                        "antenna_beamwidth_deg": antenna.beamwidth_deg,
                        "antenna_shadow_azimuth_deg": antenna.shadow_azimuth_deg,
                        "antenna_shadow_width_deg": antenna.shadow_width_deg,
                        "antenna_shadow_loss_db": antenna.shadow_loss_db,
                    }
                )

            rows.append(row)

    return _table(rows)


def node_table(planner, kind: str = "rpl"):
    kind = kind.lower()
    if kind == "result":
        return node_index_table(planner)
    if kind != "rpl":
        raise ValueError("kind must be 'rpl' or 'result'")

    rows = []
    for node in planner.rpl_nodes:
        x = None
        y = None
        if node.pos_utm is not None:
            x, y = node.pos_utm

        rows.append(
            {
                "node_id": node.node_id,
                "site_id": node.extra.get("site_id"),
                "device_id": node.extra.get("device_id"),
                "tech": node.extra.get("tech"),
                "freq_mhz": node.extra.get("freq_mhz"),
                "tx_power_dbm": node.extra.get("tx_power_dbm"),
                "antenna_id": node.extra.get("antenna_id"),
                "mount_height_m": node.extra.get("mount_height_m"),
                "max_links": node.extra.get("max_links"),
                "interface_can_relay": node.extra.get(
                    "interface_can_relay",
                    node.rpl_relay,
                ),
                "connected": node.connected,
                "rpl_relay": node.rpl_relay,
                "rank": node.rank,
                "x": x,
                "y": y,
            }
        )

    return _table(rows)


def node_index_table(planner, G=None):
    if planner.rpl is None:
        raise RuntimeError("RPL was not run")

    graph = G
    if graph is None:
        graph = (
            planner.rpl.G_res
            if planner.rpl.G_res.number_of_nodes()
            else planner.rpl.G
        )

    interface_can_relay = {
        node_id: attributes.get("extra", {}).get("interface_can_relay")
        for node_id, attributes in graph.nodes(data=True)
    }

    table = planner.rpl.node_index_table(G=G)

    try:
        if "interface_can_relay" not in table:
            table = table.copy()
            table["interface_can_relay"] = table["node_id"].map(
                interface_can_relay
            )
        if "can_relay" not in table:
            table = table.copy()
            table["can_relay"] = table["interface_can_relay"]
        return table
    except TypeError:
        rows = []
        for row in table:
            row = dict(row)
            row.setdefault(
                "interface_can_relay",
                interface_can_relay.get(row.get("node_id")),
            )
            row.setdefault("can_relay", row.get("interface_can_relay"))
            rows.append(row)
        return rows


def edge_table(planner, kind: str = "candidate"):
    kind = kind.lower()
    rpl_by_id = {node.node_id: node for node in planner.rpl_nodes}
    rows = []

    if kind == "candidate":
        edges = (
            planner.candidate_edges
            if planner.candidate_edges
            else planner.build_candidate_edges_from_rules()
        )
        metric_by_edge = {}
    elif kind == "metric":
        edges = list(planner.edge_metrics)
        metric_by_edge = planner.edge_metrics
    elif kind == "result":
        if planner.rpl is None:
            raise RuntimeError("RPL was not run")
        edges = list(planner.rpl.G_res.edges)
        metric_by_edge = {
            edge: planner.rpl.G_res.edges[edge].get("metric")
            for edge in edges
        }
    else:
        raise ValueError("kind must be 'candidate', 'metric', or 'result'")

    for src, dst in edges:
        src_node = rpl_by_id[src]
        dst_node = rpl_by_id[dst]
        edge_attrs = _edge_attrs(planner, src, dst)
        row = {
            "src": src,
            "dst": dst,
            "kind": edge_attrs.get("kind", "rf"),
            "rule": edge_attrs.get("rule"),
            "src_site": src_node.extra["site_id"],
            "dst_site": dst_node.extra["site_id"],
            "src_tech": src_node.extra["tech"],
            "dst_tech": dst_node.extra["tech"],
            "src_freq_mhz": src_node.extra["freq_mhz"],
            "dst_freq_mhz": dst_node.extra["freq_mhz"],
            "src_max_links": src_node.extra.get("max_links"),
            "dst_max_links": dst_node.extra.get("max_links"),
            "src_mount_height_m": src_node.extra.get("mount_height_m"),
            "dst_mount_height_m": dst_node.extra.get("mount_height_m"),
        }
        if kind in {"metric", "result"}:
            row["metric"] = metric_by_edge.get((src, dst))
            if row["metric"] is None:
                row["metric"] = metric_by_edge.get((dst, src))
        rows.append(row)

    if kind == "metric":
        rows.sort(key=lambda row: row["metric"])

    return _table(rows)


def antenna_gain_table(planner):
    """
    Return one row per metric edge with the antenna gains used by the planner.

    This report reads ``planner.edge_features`` already produced by
    ``GraphPlanner.compute_edge_metrics``. It does not call the geo service or
    recompute metrics. Interface metadata such as frequency and antenna ids are
    joined from ``planner.rpl_nodes`` because the metric record keeps only the
    normalized fields consumed by the metric compiler.
    """
    rpl_by_id = {node.node_id: node for node in planner.rpl_nodes}
    rows = []

    for (src, dst), record in planner.edge_features.items():
        src_node = rpl_by_id[src]
        dst_node = rpl_by_id[dst]
        tx = record.get("tx", {})
        rx = record.get("rx", {})
        features = record.get("features", {})

        rows.append(
            {
                "src": src,
                "dst": dst,
                "dist_m": features.get("dist_m"),
                "src_site": src_node.extra.get("site_id"),
                "dst_site": dst_node.extra.get("site_id"),
                "src_tech": src_node.extra.get("tech"),
                "dst_tech": dst_node.extra.get("tech"),
                "src_freq_mhz": src_node.extra.get("freq_mhz"),
                "dst_freq_mhz": dst_node.extra.get("freq_mhz"),
                "tx_power_dbm": tx.get("pw"),
                "tx_antenna_id": src_node.extra.get("antenna_id"),
                "tx_model_id": tx.get("ant_model_id"),
                "tx_antenna_name": tx.get("ant_name"),
                "tx_type": tx.get("ant_type"),
                "tx_height_m": tx.get("ant_height"),
                "tx_gain_dbi": tx.get("ant_gain"),
                "rx_antenna_id": dst_node.extra.get("antenna_id"),
                "rx_model_id": rx.get("ant_model_id"),
                "rx_antenna_name": rx.get("ant_name"),
                "rx_type": rx.get("ant_type"),
                "rx_height_m": rx.get("ant_height"),
                "rx_gain_dbi": rx.get("ant_gain"),
                "total_ant_gain_dbi": (
                    tx.get("ant_gain", 0.0) + rx.get("ant_gain", 0.0)
                ),
                "metric": planner.edge_metrics.get((src, dst)),
            }
        )

    return _table(rows)


def planning_quality_table(
    planner,
    *,
    rank_threshold: float,
    G=None,
):
    """
    Return a final planning success report based on node rank.

    Connected nodes are treated as backbone/source nodes. Unconnected nodes
    with a finite rank are classified as ``good`` when
    ``rank <= rank_threshold`` and ``poor`` otherwise. Unconnected nodes without
    finite rank are classified as ``unserved``.
    """
    if rank_threshold < 0:
        raise ValueError("rank_threshold must be >= 0")
    if planner.rpl is None:
        raise RuntimeError("RPL was not run")

    graph = G
    if graph is None:
        graph = planning_result_graph(planner, include_all_nodes=True)

    rows = []
    for node_id, attributes in graph.nodes(data=True):
        extra = attributes.get("extra", {})
        rank = attributes.get("rank")
        connected = bool(attributes.get("connected"))
        parent = attributes.get("parent")
        finite_rank = rank is not None and isfinite(float(rank))

        if connected:
            quality = "connected"
            planned = True
        elif not finite_rank:
            quality = "unserved"
            planned = False
        elif float(rank) <= rank_threshold:
            quality = "good"
            planned = True
        else:
            quality = "poor"
            planned = True

        rows.append(
            {
                "node_index": attributes.get("node_index"),
                "node_id": node_id,
                "site_id": extra.get("site_id"),
                "device_id": extra.get("device_id"),
                "tech": extra.get("tech"),
                "connected": connected,
                "planned": planned,
                "quality": quality,
                "rank": rank,
                "rank_threshold": rank_threshold,
                "parent": parent,
                "parent_site_id": (
                    graph.nodes[parent].get("extra", {}).get("site_id")
                    if parent is not None and parent in graph
                    else None
                ),
            }
        )

    return _table(rows)


def planning_result_graph(planner, *, include_all_nodes: bool = True):
    """
    Return the final planned graph, optionally preserving unserved nodes.

    ``RPL.G_res`` contains the selected result edges and served nodes. For a
    final planning report, unserved interfaces must remain visible too. With
    ``include_all_nodes=True``, this helper copies ``G_res`` and adds any node
    from the full RPL graph that did not receive a planned edge.
    """
    if planner.rpl is None:
        raise RuntimeError("RPL was not run")

    graph = planner.rpl.G_res.copy()
    if not include_all_nodes:
        return graph

    for node_id, attributes in planner.rpl.G.nodes(data=True):
        if node_id not in graph:
            graph.add_node(node_id, **attributes)

    return graph


def planning_quality_summary(
    planner,
    *,
    rank_threshold: float,
    G=None,
) -> dict[str, Any]:
    """
    Return aggregate success counts for ``planning_quality_table``.
    """
    table = planning_quality_table(
        planner,
        rank_threshold=rank_threshold,
        G=G,
    )
    try:
        counts = table["quality"].value_counts().to_dict()
        total_targets = int((table["quality"] != "connected").sum())
        good_targets = int((table["quality"] == "good").sum())
        unserved_targets = int((table["quality"] == "unserved").sum())
        poor_targets = int((table["quality"] == "poor").sum())
    except TypeError:
        counts = {}
        total_targets = 0
        good_targets = 0
        poor_targets = 0
        unserved_targets = 0
        for row in table:
            quality = row["quality"]
            counts[quality] = counts.get(quality, 0) + 1
            if quality != "connected":
                total_targets += 1
            if quality == "good":
                good_targets += 1
            elif quality == "poor":
                poor_targets += 1
            elif quality == "unserved":
                unserved_targets += 1

    return {
        "rank_threshold": rank_threshold,
        "success": total_targets > 0
        and poor_targets == 0
        and unserved_targets == 0,
        "total_targets": total_targets,
        "good_targets": good_targets,
        "poor_targets": poor_targets,
        "unserved_targets": unserved_targets,
        "counts": counts,
    }


def draw_network_graph(
    G,
    *,
    label_mode: str | None | bool = "index",
    color_mode: str = "role",
    rank_threshold: float | None = None,
    figsize=(10, 7),
    positions=None,
    pos_attr: str = "pos",
    spread_same_position: bool = False,
    spread_radius: float = 20.0,
):
    import matplotlib.pyplot as plt
    import networkx as nx

    if label_mode == "index":
        labels = {
            node_id: str(attributes.get("node_index", ""))
            for node_id, attributes in G.nodes(data=True)
        }
    elif label_mode in {"site", "position"}:
        labels = {
            node_id: attributes.get("extra", {}).get("site_id", node_id)
            for node_id, attributes in G.nodes(data=True)
        }
    elif label_mode in {None, "none", False}:
        labels = None
    elif label_mode == "node":
        labels = {node_id: node_id for node_id in G.nodes}
    elif callable(label_mode):
        labels = {
            node_id: str(label_mode(node_id, attributes))
            for node_id, attributes in G.nodes(data=True)
        }
    else:
        raise ValueError(
            "label_mode must be 'index', 'site', 'position', 'node', "
            "'none', None, or a callable"
        )

    colors = [
        _node_color(
            attributes,
            color_mode=color_mode,
            rank_threshold=rank_threshold,
        )
        for _, attributes in G.nodes(data=True)
    ]

    if positions is None:
        positions = nx.get_node_attributes(G, pos_attr)
    if len(positions) != G.number_of_nodes() or any(
        pos is None for pos in positions.values()
    ):
        positions = nx.spring_layout(G)
    elif spread_same_position:
        positions = _spread_same_positions(positions, radius=spread_radius)

    plt.figure(figsize=figsize)
    nx.draw(
        G,
        pos=positions,
        node_color=colors,
        edge_color="lightgray",
        with_labels=False,
    )
    if labels is not None:
        nx.draw_networkx_labels(
            G,
            positions,
            labels=labels,
            font_size=8,
            font_color="black",
        )
    plt.axis("equal")
    plt.show()


def _spread_same_positions(positions, *, radius: float):
    import math

    groups = {}
    for node_id, pos in positions.items():
        x, y = pos
        key = (round(float(x), 6), round(float(y), 6))
        groups.setdefault(key, []).append(node_id)

    display_positions = {
        node_id: (float(pos[0]), float(pos[1]))
        for node_id, pos in positions.items()
    }

    for key, node_ids in groups.items():
        if len(node_ids) == 1:
            continue

        cx, cy = key
        count = len(node_ids)
        for index, node_id in enumerate(sorted(node_ids)):
            angle = 2.0 * math.pi * index / count
            display_positions[node_id] = (
                cx + radius * math.cos(angle),
                cy + radius * math.sin(angle),
            )

    return display_positions


def _node_color(
    attributes: dict[str, Any],
    *,
    color_mode: str,
    rank_threshold: float | None,
) -> str:
    color_mode = color_mode.lower()
    if color_mode == "role":
        if attributes.get("connected"):
            return "gray"
        if attributes.get("rpl_relay"):
            return "lightgreen"
        return "lightblue"

    if color_mode != "rank_quality":
        raise ValueError("color_mode must be 'role' or 'rank_quality'")

    if attributes.get("connected"):
        return "gray"

    rank = attributes.get("rank")
    if rank is None or not isfinite(float(rank)):
        return "tomato"

    if rank_threshold is None:
        return "lightgreen"

    return "lightgreen" if float(rank) <= rank_threshold else "gold"


def _table(rows: list[dict[str, Any]]):
    try:
        import pandas as pd
    except ImportError:
        return rows

    return pd.DataFrame(rows)


def _edge_attrs(planner, src: str, dst: str) -> dict[str, Any]:
    attrs = planner.candidate_edge_attrs.get((src, dst))
    if attrs is not None:
        return dict(attrs)

    attrs = planner.candidate_edge_attrs.get((dst, src))
    if attrs is not None:
        return dict(attrs)

    return {}
