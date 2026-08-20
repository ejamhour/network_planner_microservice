import re
from collections.abc import Iterable
from importlib.resources import files
from math import hypot, isfinite

import networkx as nx

import cisei_lib.planners.metric_compiler as mc
from cisei_lib.planners.planner_classes import PlanningNode

from pyproj import Transformer

# Author: Edgard Jamhour
# Base class for RPL-based planning; agnostic to radio model
# Designed for single-round use within a multi-round algorithm that adds repeaters iteratively
# Builds a single-path topology connecting new nodes to fixed nodes (border routers)
# Radio-specific parameters should be defined in self.context

class GeoRPL:
    
    # Initialize base RPL planning context, radio model, and internal graph state
    def __init__(
        self,
        input_crs: str = "EPSG:4326",
        planning_crs: str = "EPSG:31982",
        **context,
    ):
        self.input_crs = input_crs
        self.planning_crs = planning_crs

        self.transformer = Transformer.from_crs(
            input_crs,
            planning_crs,
            always_xy=True,
        )
    
        self.G = nx.Graph()
        self.G_res = nx.Graph()

        self.nodes: dict[str, PlanningNode] = {}
        self.positions_utm: dict[str, tuple[float, float]] = {}

        self.edge_features: dict[tuple[str, str], dict] = {}

        self.context = context

        metric_name = context.get("metric_spec", "default")

        try:
            metric_spec = self.load_metric_spec(metric_name)
            self.metric_function = mc.compile_metric_spec(metric_spec)
        except Exception as error:
            raise RuntimeError(
                f"Invalid metric specification: {metric_name}"
            ) from error


    # Enable geoRPL to be used as a context manager        
    def __enter__(self):
        return self 
    
    # No cleanup logic needed for context manager exit
    def __exit__(self, exc_type, exc_value, traceback):
        pass                          

    @staticmethod
    def _edge_key(node_a: str, node_b: str) -> tuple[str, str]:
        if node_a == node_b:
            raise ValueError("An edge cannot connect a node to itself")

        return tuple(sorted((node_a, node_b)))

    @staticmethod
    def load_metric_spec(name: str = "default") -> str:
        return (
            files("cisei_lib")
            .joinpath(
                "resources",
                "metrics",
                f"{name}_metric.toml",
            )
            .read_text(encoding="utf-8")
        )

    # Define fixed and new nodes for RPL planning
    # Fixed nodes have precalculated ranks; new nodes will be connected by propagation
    def set_nodes(self, nodes: Iterable[PlanningNode]) -> None:
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

        normalized_nodes = {}

        for node in nodes:
            if (
                not isinstance(node.pos_utm, (tuple, list))
                or len(node.pos_utm) != 2
            ):
                raise ValueError(
                    f"Invalid UTM position for node {node.node_id}"
                )

            x, y = node.pos_utm

            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                raise TypeError(
                    f"UTM coordinates must be numeric for node {node.node_id}"
                )

            if not isfinite(x) or not isfinite(y):
                raise ValueError(
                    f"UTM coordinates must be finite for node {node.node_id}"
                )

            if node.fixed:
                if not isfinite(node.rank):
                    raise ValueError(
                        f"Fixed node {node.node_id} must have a finite rank"
                    )
            elif node.rank != float("inf"):
                raise ValueError(
                    f"Floating node {node.node_id} must start with infinite rank"
                )

            normalized_nodes[node.node_id] = RPLNode(
                node_id=node.node_id,
                pos_utm=(float(x), float(y)),
                fixed=node.fixed,
                relay=node.relay,
                rank=float(node.rank),
                extra=node.extra.copy(),
            )

        if not any(node.fixed for node in normalized_nodes.values()):
            raise ValueError("GeoRPL requires at least one fixed node")

        self.nodes = normalized_nodes

    # define the edges quality to avoid recaculation
    # -- parent class may include a rank key to overhidde ETX calculation based on rssi
    def set_edge_features(
        self,
        edges: dict[tuple[str, str], dict],
        update: bool = True,
    ) -> None:

        if not update:
            self.edge_features = {}

        for edge, features in edges.items():
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise TypeError(
                    "Edge keys must be (node_a, node_b) tuples"
                )

            node_a, node_b = edge

            if node_a not in self.nodes:
                raise KeyError(f"Unknown node: {node_a}")

            if node_b not in self.nodes:
                raise KeyError(f"Unknown node: {node_b}")

            if not isinstance(features, dict):
                raise TypeError(
                    f"Features for edge {edge} must be a dictionary"
                )

            key = self._edge_key(node_a, node_b)
            self.edge_features[key] = features.copy()

    def get_edge_features(
        self,
        node_a: str,
        node_b: str,
    ) -> dict:

        key = self._edge_key(node_a, node_b)

        try:
            return self.edge_features[key]
        except KeyError as error:
            raise KeyError(
                f"Missing features for edge {node_a}-{node_b}"
            ) from error
        
    # Create a graph from nodes dicts and edges list o tuples
    def _build_graph(self, nodes, edges):
        G = nx.Graph()
        G.add_nodes_from(nodes.keys())               
        nx.set_node_attributes(G, nodes)
        G.add_edges_from(edges)
        return G

    # Create a graph connecting floating nodes to fixed nodes
    def create_graph(self, degree: int) -> None:
        if degree <= 0:
            raise ValueError("degree must be greater than zero")

        if not self.nodes:
            raise RuntimeError("No RPL nodes were added")

        graph_nodes = {
            node.node_id: {
                "node_id": node.node_id,
                "pos": node.pos_utm,
                "fixed": node.fixed,
                "relay": node.relay,
                "rank": node.rank,
                "extra": node.extra.copy(),
            }
            for node in self.nodes.values()
        }

        relay_candidates = [
            node
            for node in self.nodes.values()
            if node.relay
        ]

        edges = []

        for node in self.nodes.values():
            if node.fixed:
                continue

            neighbors = self.find_neighbors(
                target=node,
                candidates=relay_candidates,
                n=degree,
            )

            edges.extend(
                (node.node_id, neighbor.node_id)
                for neighbor in neighbors
            )

        self.G = self._build_graph(graph_nodes, edges)
     
    # Calculate the rank
    def calculate_rank(self, parent: str, child: str) -> float:
        features = self.get_edge_features(parent, child)

        edge_metric = float(self.metric_function(features))

        if edge_metric < 0:
            raise ValueError(
                f"Negative edge metric for {parent}-{child}: {edge_metric}"
            )

        parent_rank = self.G.nodes[parent]["rank"]

        return parent_rank + edge_metric

    # Execute one round of RPL propagation from fixed nodes over the virtual link graph
    # Builds a single-path topology (DODAG) and saves the result in G_res
    def run_RPL(self) -> None:
        if self.G.number_of_nodes() == 0:
            raise RuntimeError("Candidate graph was not created")

        # Reset previous propagation results.
        for _, attributes in self.G.nodes(data=True):
            attributes.pop("parent", None)

            if not attributes["fixed"]:
                attributes["rank"] = float("inf")

        nodes_to_process = {
            node_id
            for node_id, attributes in self.G.nodes(data=True)
            if attributes["fixed"]
            and isfinite(attributes["rank"])
        }

        if not nodes_to_process:
            raise RuntimeError(
                "GeoRPL requires at least one fixed node with finite rank"
            )

        while nodes_to_process:
            parent = nodes_to_process.pop()

            for child in self.G.neighbors(parent):
                child_data = self.G.nodes[child]

                # Fixed nodes are roots and never select another parent.
                if child_data["fixed"]:
                    continue

                candidate_rank = self.calculate_rank(parent, child)

                if candidate_rank >= child_data["rank"]:
                    continue

                child_data["rank"] = candidate_rank
                child_data["parent"] = parent

                # Non-relay nodes may receive a parent but do not propagate.
                if child_data["relay"] and isfinite(candidate_rank):
                    nodes_to_process.add(child)

        self.G_res = nx.Graph()
        self.G_res.add_nodes_from(self.G.nodes(data=True))

        parent_edges = [
            (node_id, attributes["parent"])
            for node_id, attributes in self.G.nodes(data=True)
            if "parent" in attributes
        ]

        self.G_res.add_edges_from(parent_edges)
        self.G_res.remove_nodes_from(list(nx.isolates(self.G_res)))
    
    # Show the network in mathplotlib (use G_res for results)
    def show_network(self, layout='pos', G=None):
        import matplotlib.pyplot as plt

        if G is None: G = self.G 

        def label_function(s):
            match = re.search(r'(\d+)\D*$', s)
            return match.group(1) if match else s[0]+s[-3:]
        
        def color_function(node):
            if node['fixed']:
                return 'gray' if node['relay'] else 'black'
            else:                
                if node['relay']: 
                    return 'lightgreen' if node.get('rank', float('inf')) < float('inf') else 'green'                    
                else: 
                    return 'lightblue' if node.get('rank', float('inf')) < float('inf') else 'blue'                    
               

        if layout == 'pos':
            pos = nx.get_node_attributes(G, 'pos')
        elif layout == 'spiral':
            pos = nx.spiral_layout(G)
        elif layout == 'circular':
            pos = nx.circular_layout(G)
        else:
            pos = nx.spring_layout(G)
        
        node_colors = [color_function(node[1]) for node in G.nodes(data=True) ]        
        nx.draw(G, pos=pos, node_color=node_colors, with_labels=False)   
        labels = {node: label_function(node) for node in G.nodes}
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=8, font_color="white")     
        # nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels)        
        plt.show()

        # Locate n-neighbors for a point (geometry) in gdf (projected)
    
    # Compute the n-nearest neighbors using an UTM projection
    def find_neighbors(
        self,
        target: RPLNode,
        candidates: Iterable[RPLNode],
        n: int,
    ) -> list[RPLNode]:

        if n <= 0:
            return []

        tx, ty = target.pos_utm

        neighbors = [
            candidate
            for candidate in candidates
            if candidate.node_id != target.node_id
        ]

        neighbors.sort(
            key=lambda candidate: hypot(
                candidate.pos_utm[0] - tx,
                candidate.pos_utm[1] - ty,
            )
        )

        return neighbors[:n]

if __name__ == "__main__":

    nodes = [
        RPLNode(
            node_id="root_a",
            pos_utm=(670000.0, 7185000.0),
            fixed=True,
            relay=True,
            rank=0.0,
        ),
        RPLNode(
            node_id="root_b",
            pos_utm=(671000.0, 7185000.0),
            fixed=True,
            relay=True,
            rank=0.0,
        ),
        RPLNode(
            node_id="node_1",
            pos_utm=(670200.0, 7185100.0),
        ),
        RPLNode(
            node_id="node_2",
            pos_utm=(670500.0, 7185100.0),
        ),
        RPLNode(
            node_id="node_3",
            pos_utm=(670800.0, 7185100.0),
        ),
        RPLNode(
            node_id="leaf",
            pos_utm=(670500.0, 7185300.0),
            relay=False,
        ),
    ]

    planner = GeoRPL(metric_spec="default")

    # 1. Load nodes.
    planner.set_nodes(nodes)

    # 2. Create the proximity candidate graph.
    planner.create_graph(degree=2)

    # 3. Supply externally evaluated features for every candidate edge.
    edge_features = {
        ("root_a", "node_1"): {
            "terrain_peaks_vv": [
                {"v_v": 0.1, "d_norm": 0.5},
            ],
        },
        ("node_1", "node_2"): {
            "terrain_peaks_vv": [
                {"v_v": 0.7, "d_norm": 0.5},
            ],
        },
        ("node_2", "node_3"): {
            "terrain_peaks_vv": [
                {"v_v": 0.3, "d_norm": 0.5},
            ],
        },
        ("node_3", "root_b"): {
            "terrain_peaks_vv": [
                {"v_v": 0.2, "d_norm": 0.5},
            ],
        },
        ("leaf", "node_2"): {
            "terrain_peaks_vv": [
                {"v_v": 0.5, "d_norm": 0.5},
            ],
        },
        ("leaf", "node_1"): {
            "terrain_peaks_vv": [
                {"v_v": 1.2, "d_norm": 0.5},
            ],
        },
    }

    planner.set_edge_features(edge_features)

    # 4. Propagate rank from the fixed roots.
    planner.run_RPL()

    # 5. Show the selected parent and cumulative rank.
    for node_id, attributes in planner.G_res.nodes(data=True):
        print(
            f"{node_id:8s} "
            f"parent={attributes.get('parent')} "
            f"rank={attributes['rank']:.2f}"
        )

    print("\nSelected edges:")

    for child, parent in planner.G_res.edges:
        print(f"{child} <-> {parent}")

    # Optional visualization:
    # planner.show_network(G=planner.G_res)    



  