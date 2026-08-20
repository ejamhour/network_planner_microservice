from collections.abc import Iterable, Mapping
from math import inf, isfinite
from numbers import Real
from cisei_lib.planners.planner_classes import RPLNode

import networkx as nx


# Author: Edgard Jamhour
# Generic RPL propagation over a weighted candidate graph.
# Radio technology, feature extraction, link planning, and candidate-link
# selection are responsibilities of the caller.

class GeoRPL:

    def __init__(self):
        self.G = nx.Graph()
        self.G_res = nx.Graph()
        self.nodes: dict[str, RPLNode] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def set_nodes(self, nodes: Iterable[RPLNode]) -> None:
        nodes = list(nodes)

        if not nodes:
            raise ValueError("GeoRPL requires at least one node")

        node_ids = [node.node_id for node in nodes]

        duplicated = {
            node_id
            for node_id in node_ids
            if node_ids.count(node_id) > 1
        }

        if duplicated:
            raise ValueError(
                f"Duplicated node IDs: {sorted(duplicated)}"
            )

        normalized_nodes: dict[str, RPLNode] = {}

        for node in nodes:
            if node.connected:
                if not isfinite(node.rank):
                    raise ValueError(
                        f"Connected node {node.node_id} must have a finite rank"
                    )
            elif node.rank != inf:
                raise ValueError(
                    f"Unconnected node {node.node_id} must start with infinite rank"
                )

            pos_utm = None
            if node.pos_utm is not None:
                if (
                    not isinstance(node.pos_utm, (tuple, list))
                    or len(node.pos_utm) != 2
                ):
                    raise ValueError(
                        f"Invalid position for node {node.node_id}"
                    )

                x, y = node.pos_utm
                if not isinstance(x, Real) or not isinstance(y, Real):
                    raise TypeError(
                        f"Coordinates must be numeric for node {node.node_id}"
                    )
                if not isfinite(x) or not isfinite(y):
                    raise ValueError(
                        f"Coordinates must be finite for node {node.node_id}"
                    )

                pos_utm = (float(x), float(y))

            normalized_nodes[node.node_id] = RPLNode(
                node_id=node.node_id,
                connected=bool(node.connected),
                rpl_relay=bool(node.rpl_relay),
                rank=float(node.rank),
                pos_utm=pos_utm,
                extra=node.extra.copy(),
            )

        if not any(node.connected for node in normalized_nodes.values()):
            raise ValueError("GeoRPL requires at least one connected node")

        self.nodes = normalized_nodes
        self.G = nx.Graph()
        self.G_res = nx.Graph()

        for node_index, node in enumerate(self.nodes.values()):
            self.G.add_node(
                node.node_id,
                node_id=node.node_id,
                node_index=node_index,
                connected=node.connected,
                rpl_relay=node.rpl_relay,
                rank=node.rank,
                pos=node.pos_utm,
                extra=node.extra.copy(),
            )

    def set_edge_metrics(
        self,
        edges: Mapping[tuple[str, str], Real],
        update: bool = True,
    ) -> None:
        if not self.nodes:
            raise RuntimeError("No RPL nodes were added")

        if not update:
            self.G.remove_edges_from(list(self.G.edges))

        for edge, metric in edges.items():
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise TypeError(
                    "Edge keys must be (node_a, node_b) tuples"
                )

            node_a, node_b = edge

            if node_a not in self.nodes:
                raise KeyError(f"Unknown node: {node_a}")
            if node_b not in self.nodes:
                raise KeyError(f"Unknown node: {node_b}")

            if not isinstance(metric, Real):
                raise TypeError(
                    f"Metric for edge {edge} must be numeric"
                )

            metric = float(metric)

            if not isfinite(metric):
                raise ValueError(
                    f"Metric for edge {edge} must be finite"
                )
            if metric < 0:
                raise ValueError(
                    f"Negative edge metric for {node_a}-{node_b}: {metric}"
                )

            self.G.add_edge(node_a, node_b, metric=metric)

    def get_edge_metric(self, node_a: str, node_b: str) -> float:
        if not self.G.has_edge(node_a, node_b):
            raise KeyError(f"Missing edge {node_a}-{node_b}")

        try:
            return float(self.G.edges[node_a, node_b]["metric"])
        except KeyError as error:
            raise KeyError(
                f"Missing metric for edge {node_a}-{node_b}"
            ) from error

    def calculate_rank(self, parent: str, child: str) -> float:
        edge_metric = self.get_edge_metric(parent, child)
        parent_rank = self.G.nodes[parent]["rank"]
        return parent_rank + edge_metric

    def run_RPL(self) -> None:
        if self.G.number_of_nodes() == 0:
            raise RuntimeError("RPL nodes were not added")

        if self.G.number_of_edges() == 0:
            raise RuntimeError("Candidate edges were not added")

        for _, attributes in self.G.nodes(data=True):
            attributes.pop("parent", None)

            if not attributes["connected"]:
                attributes["rank"] = inf

        nodes_to_process = {
            node_id
            for node_id, attributes in self.G.nodes(data=True)
            if (
                attributes["connected"]
                and attributes["rpl_relay"]
                and isfinite(attributes["rank"])
            )
        }

        if not nodes_to_process:
            raise RuntimeError(
                "GeoRPL requires at least one connected RPL relay node "
                "with finite rank"
            )

        while nodes_to_process:
            parent = nodes_to_process.pop()

            for child in self.G.neighbors(parent):
                child_data = self.G.nodes[child]

                if child_data["connected"]:
                    continue

                candidate_rank = self.calculate_rank(parent, child)

                if candidate_rank >= child_data["rank"]:
                    continue

                child_data["rank"] = candidate_rank
                child_data["parent"] = parent

                if child_data["rpl_relay"]:
                    nodes_to_process.add(child)

        self.G_res = nx.Graph()
        self.G_res.add_nodes_from(self.G.nodes(data=True))

        parent_edges = [
            (
                node_id,
                attributes["parent"],
                self.G.edges[node_id, attributes["parent"]].copy(),
            )
            for node_id, attributes in self.G.nodes(data=True)
            if "parent" in attributes
        ]

        self.G_res.add_edges_from(parent_edges)
        self.G_res.remove_nodes_from(list(nx.isolates(self.G_res)))

    def node_index_table(self, G=None):
        if G is None:
            G = self.G_res if self.G_res.number_of_nodes() else self.G

        rows = []

        for node_id, attributes in G.nodes(data=True):
            extra = attributes.get("extra", {})
            parent = attributes.get("parent")
            parent_extra = G.nodes[parent].get("extra", {}) if parent else {}

            rows.append(
                {
                    "node_index": attributes.get("node_index"),
                    "node_id": node_id,
                    "site_id": (
                        extra.get("site_id")
                        or extra.get("position_id")
                    ),
                    "device_id": (
                        extra.get("device_id")
                        or extra.get("radio_id")
                    ),
                    "parent": parent,
                    "parent_index": (
                        G.nodes[parent].get("node_index")
                        if parent
                        else None
                    ),
                    "parent_site_id": (
                        parent_extra.get("site_id")
                        or parent_extra.get("position_id")
                    ),
                    "connected": attributes.get("connected"),
                    "rpl_relay": attributes.get("rpl_relay"),
                    "rank": attributes.get("rank"),
                }
            )

        rows.sort(
            key=lambda row: (
                row["node_index"] is None,
                row["node_index"],
            )
        )

        try:
            import pandas as pd
        except ImportError:
            return rows

        return pd.DataFrame(rows)

    def show_network(self, layout="pos", G=None, label_mode="index"):
        import matplotlib.pyplot as plt

        if G is None:
            G = self.G

        def color_function(node):
            if node["connected"]:
                return "gray" if node["rpl_relay"] else "black"
            if node["rpl_relay"]:
                return "lightgreen" if isfinite(node["rank"]) else "green"
            return "lightblue" if isfinite(node["rank"]) else "blue"

        def labels_for_graph():
            if label_mode in {None, "none", False}:
                return None

            if label_mode == "index":
                return {
                    node_id: str(attributes.get("node_index", ""))
                    for node_id, attributes in G.nodes(data=True)
                }

            if label_mode == "position":
                return {
                    node_id: (
                        attributes.get("extra", {}).get("site_id")
                        or attributes.get("extra", {}).get("position_id")
                        or node_id
                    )
                    for node_id, attributes in G.nodes(data=True)
                }

            if label_mode == "node":
                return {node_id: node_id for node_id in G.nodes}

            if callable(label_mode):
                return {
                    node_id: str(label_mode(node_id, attributes))
                    for node_id, attributes in G.nodes(data=True)
                }

            raise ValueError(
                "label_mode must be 'index', 'position', 'node', 'none', "
                "None, or a callable"
            )

        if layout == "pos":
            positions = nx.get_node_attributes(G, "pos")
            if len(positions) != G.number_of_nodes() or any(
                pos is None for pos in positions.values()
            ):
                positions = nx.spring_layout(G)
        elif layout == "spiral":
            positions = nx.spiral_layout(G)
        elif layout == "circular":
            positions = nx.circular_layout(G)
        else:
            positions = nx.spring_layout(G)

        node_colors = [
            color_function(attributes)
            for _, attributes in G.nodes(data=True)
        ]

        nx.draw(
            G,
            pos=positions,
            node_color=node_colors,
            with_labels=False,
        )

        labels = labels_for_graph()
        if labels is not None:
            nx.draw_networkx_labels(
                G,
                positions,
                labels=labels,
                font_size=8,
                font_color="white",
            )
        plt.show()


if __name__ == "__main__":
    nodes = [
        RPLNode("root_a", connected=True, rank=0.0, pos_utm=(670000.0, 7185000.0)),
        RPLNode("root_b", connected=True, rank=0.0, pos_utm=(671000.0, 7185000.0)),
        RPLNode("node_1", pos_utm=(670200.0, 7185100.0)),
        RPLNode("node_2", pos_utm=(670500.0, 7185100.0)),
        RPLNode("node_3", pos_utm=(670800.0, 7185100.0)),
        RPLNode("leaf", rpl_relay=False, pos_utm=(670500.0, 7185300.0)),
    ]

    planner = GeoRPL()
    planner.set_nodes(nodes)

    planner.set_edge_metrics(
        {
            ("root_a", "node_1"): 1.0,
            ("node_1", "node_2"): 2.0,
            ("node_2", "node_3"): 1.5,
            ("node_3", "root_b"): 1.0,
            ("leaf", "node_2"): 1.2,
            ("leaf", "node_1"): 3.0,
        }
    )

    planner.run_RPL()

    for node_id, attributes in planner.G_res.nodes(data=True):
        print(
            f"{node_id:8s} "
            f"parent={attributes.get('parent')} "
            f"rank={attributes['rank']:.2f}"
        )
