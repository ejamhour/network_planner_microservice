from __future__ import annotations

import json
import networkx as nx
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from importlib.resources import files
from math import hypot, isfinite
from pathlib import Path
from typing import Any

import cisei_lib.planners.shared.metric_compiler as mc
from cisei_lib.planners.shared.antenna_planner import AntennaPlanner, antenna_models_from_config
from cisei_lib.planners.graph.geo_rpl_agnostic import GeoRPL
from cisei_lib.planners.shared.planner_classes import AntennaSpec, RPLNode, SiteNode
from cisei_lib.planners.shared.planning_scenario import PlanningScenario
from cisei_lib.planners.shared.input_values import is_blank, connectivity_rule_issues, RULE_SELECTORS, parse_bool


def required_value(record: Mapping[str, Any], key: str) -> Any:
    value = record.get(key)
    if is_blank(value):
        raise ValueError(f"Missing required field: {key}")
    return value


def optional_float(value: Any) -> float | None:
    if is_blank(value):
        return None
    value = float(value)
    if not isfinite(value):
        raise ValueError("Numeric fields must be finite")
    return value




class GraphPlanner:
    """
    Generic graph/RPL planner backed by a ``PlanningScenario``.

    ``PlanningScenario`` owns user-facing preparation: antenna profiles,
    interface profiles, device profiles, node instances, manual candidate
    edges and TOML/CSV serialization. ``GraphPlanner`` consumes that scenario
    and owns computational state: generated candidate edges, extracted edge
    features, compiled metric functions and the RPL result.
    """

    # Construction and state -------------------------------------------------

    def __init__(self, scenario: PlanningScenario) -> None:
        """
        Initialize a planner from a prepared ``PlanningScenario``.

        The constructor validates scenario cross references, copies scenario
        input into computational state, compiles metric specs when they are
        already present, and prepares an ``AntennaPlanner`` for dynamic
        library-backed antenna gain. It does not build edges, call the geo
        service, or run RPL.
        """
        if not isinstance(scenario, PlanningScenario):
            raise TypeError("GraphPlanner requires a PlanningScenario instance")

        errors = scenario.validate()
        if errors:
            raise ValueError(
                "Invalid planning scenario:\n" + "\n".join(f"- {e}" for e in errors)
            )

        self.scenario = scenario
        self.working_crs = scenario.working_crs
        self.antenna_catalog: dict[str, AntennaSpec] = scenario.antenna_profiles
        self.nodes: dict[str, SiteNode] = scenario.site_nodes
        self.rpl_nodes: list[RPLNode] = self._build_rpl_nodes()
        self._interface_state = self._interface_signature()
        self.connectivity_rules: dict[str, dict[str, Any]] = (
            scenario.connectivity_rules
        )
        self.candidate_edges: list[tuple[str, str]] = list(
            scenario.candidate_edges
        )
        self._candidates_built = bool(self.candidate_edges)
        self.candidate_edge_attrs: dict[tuple[str, str], dict[str, Any]] = {
            edge: dict(attrs)
            for edge, attrs in scenario.candidate_edge_attrs.items()
        }
        self.edge_metrics: dict[tuple[str, str], float] = {}
        self.edge_features: dict[tuple[str, str], dict[str, Any]] = {}
        self.metric_functions_by_tech: dict[
            str,
            Callable[[Mapping[str, Any]], float],
        ] = {}
        self.antenna_planner = AntennaPlanner()
        self.antenna_planner.models.update(antenna_models_from_config(scenario.antenna_library))
        self.rpl: GeoRPL | None = None
        self._validate_active_edges(self.candidate_edges)

        if scenario.metric_specs_by_tech:
            self.compile_metric_specs_by_tech(scenario.metric_specs_by_tech)

    # Candidate graph construction ------------------------------------------

    def set_candidate_edges(
        self,
        edges: Iterable[tuple[str, str]],
        *,
        attrs_by_edge: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
        default_attrs: Mapping[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
        """
        Replace the current candidate graph with explicit interface edges.

        Parameters are interface node ids such as ``"torre:d0:i0"``. Existing
        generated/manual edges in the planner are discarded, then the supplied
        edges are validated and added through ``add_candidate_edges``.
        """
        self._sync_interfaces()
        edges = self._validate_active_edges(edges)
        self.candidate_edges = []
        self.candidate_edge_attrs = {}
        return self.add_candidate_edges(
            edges,
            attrs_by_edge=attrs_by_edge,
            default_attrs=default_attrs,
        )

    def add_candidate_edges(
        self,
        edges: Iterable[tuple[str, str]],
        *,
        attrs_by_edge: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
        default_attrs: Mapping[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
        """
        Add candidate edges without duplicating undirected pairs.

        ``default_attrs`` are copied to every edge. ``attrs_by_edge`` may add
        or override attributes for specific ``(src, dst)`` or ``(dst, src)``
        pairs. Adding candidates clears prior metrics and RPL results because
        they no longer describe the active candidate graph.
        """
        self._sync_interfaces()
        edges = self._validate_active_edges(edges)
        rpl_by_id = {node.node_id: node for node in self.rpl_nodes}
        attrs_by_edge = attrs_by_edge or {}
        default_attrs = dict(default_attrs or {})
        edge_keys = {frozenset(edge) for edge in self.candidate_edges}

        for edge in edges:
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise TypeError("Candidate edges must be (src, dst) tuples")

            src, dst = edge
            if src not in rpl_by_id:
                raise KeyError(f"Unknown candidate edge source: {src}")
            if dst not in rpl_by_id:
                raise KeyError(f"Unknown candidate edge destination: {dst}")

            key = frozenset((src, dst))
            if len(key) != 2 or key in edge_keys:
                continue

            attrs = dict(default_attrs)
            attrs.update(attrs_by_edge.get((src, dst), {}))
            attrs.update(attrs_by_edge.get((dst, src), {}))

            stored_edge = (src, dst)
            self.candidate_edges.append(stored_edge)
            self.candidate_edge_attrs[stored_edge] = attrs
            edge_keys.add(key)

        self._clear_results()
        self._candidates_built = True
        return list(self.candidate_edges)

    def build_candidate_edges_from_rules(
        self,
        *,
        preserve_existing: bool = True,
    ) -> list[tuple[str, str]]:
        """
        Build candidate edges from scenario connectivity rules.

        Manual candidate edges loaded from the scenario are kept by default.
        Generated edges are not written back to ``PlanningScenario`` because
        they are computational state, not scenario input.

        Supported rule kinds:
        - ``rf``: connect compatible interfaces using the rule selectors,
          optional distance ``limit`` and optional nearest-neighbor ``degree``.
        - ``internal``: connect interfaces installed in the same routing-capable
          device, normally with metric zero.
        """
        self._sync_interfaces()
        if not self.connectivity_rules:
            return self.set_candidate_edges(
                self.candidate_edges if preserve_existing else [],
                attrs_by_edge=self.candidate_edge_attrs,
            )

        rpl_nodes = self.rpl_nodes
        edges = list(self.candidate_edges) if preserve_existing else []
        edge_attrs = (
            {edge: dict(self._edge_attrs(*edge)) for edge in edges}
            if preserve_existing
            else {}
        )
        edge_keys = {frozenset(edge) for edge in edges}

        def add_edge(
            left: RPLNode,
            right: RPLNode,
            attrs: Mapping[str, Any],
        ) -> None:
            key = frozenset((left.node_id, right.node_id))
            if len(key) != 2 or key in edge_keys:
                return
            edge_keys.add(key)
            edge = (left.node_id, right.node_id)
            edges.append(edge)
            edge_attrs[edge] = dict(attrs)

        for rule_id, rule in self.connectivity_rules.items():
            if problems := connectivity_rule_issues(rule):
                field, message = problems[0]
                raise ValueError(f"Rule {rule_id}, {field}: {message}")
            kind = str(rule.get("kind", "rf")).lower()

            if kind == "internal":
                self._add_internal_edges_from_rule(
                    rpl_nodes,
                    rule_id,
                    rule,
                    add_edge,
                )
                continue

            if kind != "rf":
                raise ValueError(f"Unknown connectivity rule kind: {kind}")

            tech = str(required_value(rule, "tech"))
            source_nodes = self._select_rule_nodes(
                rpl_nodes,
                tech=tech,
                selector=str(rule.get("source", "any")).lower(),
            )
            destination_nodes = self._select_rule_nodes(
                rpl_nodes,
                tech=tech,
                selector=str(rule.get("destination", "any")).lower(),
            )
            degree = (
                int(rule["degree"])
                if not is_blank(rule.get("degree"))
                else None
            )
            limit_m = optional_float(rule.get("limit"))

            for source in source_nodes:
                candidates = []
                for destination in destination_nodes:
                    if destination.node_id == source.node_id:
                        continue
                    if not self._compatible(source, destination):
                        continue
                    if not source.rpl_relay and not destination.rpl_relay:
                        continue

                    distance_m = self._distance_m(source, destination)
                    # Colocated sites must not create zero-length RF paths.
                    if distance_m <= 0.0:
                        continue
                    if limit_m is not None and distance_m > limit_m:
                        continue
                    if parse_bool(rule.get("sector_aware"), False):
                        from cisei_lib.planners.cell.cell_planner import CellPlanner
                        sector_filter = CellPlanner(self, primary_tech=tech)
                        bearing = sector_filter._bearing_deg(source.pos_utm, destination.pos_utm)
                        if not sector_filter._inside_antenna(source, bearing):
                            continue
                    candidates.append((distance_m, destination))

                candidates.sort(key=lambda item: (item[0], item[1].node_id))
                if degree is not None:
                    candidates = candidates[:degree]

                for distance_m, destination in candidates:
                    add_edge(
                        source,
                        destination,
                        {
                            "kind": "rf",
                            "rule": rule_id,
                            "tech": tech,
                            "distance_m": distance_m,
                        },
                    )

        self.candidate_edges = edges
        self.candidate_edge_attrs = edge_attrs
        self._clear_results()
        self._candidates_built = True
        return list(self.candidate_edges)

    def candidate_graph(
        self,
        candidate_edges: Iterable[tuple[str, str]] | None = None,
    ):
        """
        Return a NetworkX graph containing the candidate edges.

        The graph is intended for inspection/visualization before metric
        computation. Nodes include stable ``node_index`` labels, interface
        metadata in ``extra`` and projected coordinates in ``pos``.
        """
        import networkx as nx

        self._sync_interfaces()
        rpl_nodes = self.rpl_nodes
        if candidate_edges is None:
            candidate_edges = self._candidate_edges_or_build()
        candidate_edges = list(candidate_edges)
        self._validate_active_edges(candidate_edges)

        graph = nx.Graph()
        for node_index, node in enumerate(rpl_nodes):
            graph.add_node(
                node.node_id,
                node_id=node.node_id,
                node_index=node_index,
                connected=node.connected,
                rpl_relay=node.rpl_relay,
                rank=node.rank,
                pos=node.pos_utm,
                extra=node.extra.copy(),
            )

        for src, dst in candidate_edges:
            if src not in graph:
                raise KeyError(f"Unknown candidate edge source: {src}")
            if dst not in graph:
                raise KeyError(f"Unknown candidate edge destination: {dst}")
            graph.add_edge(src, dst, **self._edge_attrs(src, dst))

        return graph

    # Metric computation -----------------------------------------------------

    def compile_metric_specs_by_tech(
        self,
        metric_specs_by_tech: Mapping[
            str,
            str | Mapping[str, Any] | Callable[[Mapping[str, Any]], float],
        ],
    ) -> dict[str, Callable[[Mapping[str, Any]], float]]:
        """
        Compile metric formulas/resources keyed by interface technology.

        Values may be callables, inline metric specs/formulas, or mappings with
        ``spec``/``formula``. Short names such as ``"default"`` are loaded from
        ``cisei_lib/resources/metrics/<name>_metric.toml``.
        """
        compiled = {}
        for tech, spec in metric_specs_by_tech.items():
            if callable(spec):
                compiled[str(tech)] = spec
                continue

            if isinstance(spec, Mapping):
                spec = spec.get("spec", spec.get("formula"))
                if spec is None:
                    raise ValueError(
                        f"Metric profile {tech!r} requires 'spec' or 'formula'"
                    )

            spec_text = self._metric_spec_text(str(spec))
            compiled[str(tech)] = mc.compile_metric_spec(spec_text)

        self.metric_functions_by_tech = compiled
        return dict(compiled)

    async def compute_edge_metrics(
        self,
        geo,
        candidate_edges: Iterable[tuple[str, str]] | None = None,
        *,
        metric_specs_by_tech: Mapping[
            str,
            str | Mapping[str, Any] | Callable[[Mapping[str, Any]], float],
        ] | None = None,
        manage_geo: bool = False,
        accumulate: bool = False,
        bidirectional: bool = False,
        assume_symmetric_features: bool = False,
    ) -> dict[tuple[str, str], float]:
        """
        Extract geo features and compute one metric per candidate edge.

        For RF edges this method validates compatible technology/frequency,
        calls the supplied geo service, builds the metric input record, applies
        the compiled metric for that technology and stores both final scalar
        metrics and the expanded metric records. Internal edges use their rule
        metric directly and do not call the geo service.

        ``assume_symmetric_features=True`` reuses raw features for the reverse
        score while still exchanging the transmitter and receiver inputs.
        ``bidirectional=True`` evaluates both RF directions and uses the
        greater cost. The default preserves the original one-direction policy.

        Set ``manage_geo=True`` when this method should call ``geo.start()``
        and ``geo.close()``. Leave it false when the caller manages a reused
        ``AsyncGeoServicePool``.
        """
        self._sync_interfaces()
        if metric_specs_by_tech is not None:
            self.compile_metric_specs_by_tech(metric_specs_by_tech)

        if candidate_edges is None:
            candidate_edges = self._candidate_edges_or_build()
        candidate_edges = list(candidate_edges)
        self._validate_active_edges(candidate_edges)
        if candidate_edges and not self.metric_functions_by_tech:
            raise RuntimeError("No metric functions were registered")

        rpl_by_id = {node.node_id: node for node in self.rpl_nodes}
        edge_metrics = {}
        edge_features = {}

        if manage_geo and candidate_edges:
            await geo.start()

        try:
            for src, dst in candidate_edges:
                src_node = rpl_by_id[src]
                dst_node = rpl_by_id[dst]
                edge_attrs = self._edge_attrs(src, dst)

                if edge_attrs.get("kind") == "internal":
                    metric = float(edge_attrs.get("metric", 0.0))
                    edge_metrics[(src, dst)] = metric
                    edge_features[(src, dst)] = {
                        "kind": "internal",
                        "rule": edge_attrs.get("rule"),
                    }
                    continue

                src_tech = src_node.extra["tech"]
                dst_tech = dst_node.extra["tech"]
                if src_tech != dst_tech:
                    raise ValueError(
                        f"Tech mismatch for edge {src} -> {dst}: "
                        f"{src_tech} != {dst_tech}"
                    )

                metric_function = self.metric_functions_by_tech.get(src_tech)
                if metric_function is None:
                    raise ValueError(
                        f"No metric function registered for tech: {src_tech}"
                    )

                src_freq_mhz = src_node.extra["freq_mhz"]
                dst_freq_mhz = dst_node.extra["freq_mhz"]
                if src_freq_mhz != dst_freq_mhz:
                    raise ValueError(
                        f"Frequency mismatch for edge {src} -> {dst}: "
                        f"{src_freq_mhz} != {dst_freq_mhz}"
                    )

                src_antenna_id = src_node.extra["antenna_id"]
                dst_antenna_id = dst_node.extra["antenna_id"]
                if src_antenna_id not in self.antenna_catalog:
                    raise ValueError(
                        f"Unknown antenna_id for {src}: {src_antenna_id}"
                    )
                if dst_antenna_id not in self.antenna_catalog:
                    raise ValueError(
                        f"Unknown antenna_id for {dst}: {dst_antenna_id}"
                    )

                src_height = src_node.extra.get("mount_height_m")
                dst_height = dst_node.extra.get("mount_height_m")
                if src_height is None:
                    raise ValueError(f"Missing mount height for interface {src}")
                if dst_height is None:
                    raise ValueError(f"Missing mount height for interface {dst}")

                src_site = self.nodes[src_node.extra["site_id"]].site
                dst_site = self.nodes[dst_node.extra["site_id"]].site
                try:
                    features = await geo.get_features(
                        src_site.geo_pos,
                        dst_site.geo_pos,
                        tx_ha=src_height,
                        rx_ha=dst_height,
                        freq_mhz=src_freq_mhz,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Feature extraction failed for {src} -> {dst} "
                        f"(tx={src_site.geo_pos}, rx={dst_site.geo_pos}, "
                        f"frequency={src_freq_mhz} MHz): {exc}"
                    ) from exc

                features = dict(features)
                features.setdefault("freq_mhz", src_freq_mhz)

                metric_record = self._metric_record(
                    src_node,
                    dst_node,
                    features,
                )
                edge_metrics[(src, dst)] = float(metric_function(metric_record))
                if bidirectional:
                    try:
                        reverse = features if assume_symmetric_features else await geo.get_features(
                            dst_site.geo_pos, src_site.geo_pos,
                            tx_ha=dst_height, rx_ha=src_height, freq_mhz=src_freq_mhz)
                    except Exception as exc:
                        raise RuntimeError(f'Feature extraction failed for {dst} -> {src}: {exc}') from exc
                    reverse = dict(reverse)
                    reverse.setdefault('freq_mhz', src_freq_mhz)
                    reverse_record = self._metric_record(dst_node, src_node, reverse)
                    reverse_cost = float(metric_function(reverse_record))
                    if not isfinite(reverse_cost) or not isfinite(edge_metrics[(src, dst)]):
                        raise ValueError(f'Nonfinite bidirectional metric for {src} -> {dst}')
                    edge_metrics[(src, dst)] = max(edge_metrics[(src, dst)], reverse_cost)
                    metric_record['reverse'] = reverse_record
                    if assume_symmetric_features:
                        metric_record['reverse_features_reused'] = True
                edge_features[(src, dst)] = metric_record

        finally:
            if manage_geo and candidate_edges:
                await geo.close()

        if accumulate:
            self.edge_metrics.update(edge_metrics)
            self.edge_features.update(edge_features)
        else:
            self.candidate_edges = candidate_edges
            self.edge_metrics = edge_metrics
            self.edge_features = edge_features
        self.rpl = None
        return dict(edge_metrics)

    # RPL execution ----------------------------------------------------------

    def run_rpl(
        self,
        edge_metrics: Mapping[tuple[str, str], float] | None = None,
        *,
        max_edge_metric: float | None = None,
        parent_set_size: int = 1,
    ) -> GeoRPL:
        """
        Run RPL over the current metric graph.

        ``edge_metrics`` maps interface edge tuples to scalar costs. If omitted,
        the method uses metrics previously produced by ``compute_edge_metrics``.
        The resulting ``GeoRPL`` instance is stored on ``self.rpl``.
        """
        if isinstance(parent_set_size, bool) or not isinstance(parent_set_size, int) or parent_set_size < 1:
            raise ValueError("parent_set_size must be a positive integer")
        self._sync_interfaces()
        if edge_metrics is None:
            edge_metrics = self.edge_metrics
        self._validate_active_edges(edge_metrics)
        if not edge_metrics and self.candidate_edges:
            raise RuntimeError("No edge metrics were computed")

        self.edge_metrics = {
            (str(src), str(dst)): float(metric)
            for (src, dst), metric in edge_metrics.items()
        }
        if max_edge_metric is not None and (not isfinite(max_edge_metric) or max_edge_metric < 0):
            raise ValueError('max_edge_metric must be finite and nonnegative')
        if max_edge_metric is not None and any(not isfinite(v) or v < 0 for v in self.edge_metrics.values()):
            raise ValueError('Edge metrics must be finite and nonnegative')
        self.last_max_edge_metric = max_edge_metric
        self.last_parent_set_size = parent_set_size
        usable_metrics = {k: v for k, v in self.edge_metrics.items()
                          if max_edge_metric is None or v <= max_edge_metric}

        rpl = GeoRPL()
        if not self.rpl_nodes:
            self.rpl = rpl
            return rpl
        rpl.set_nodes(self.rpl_nodes)
        if usable_metrics:
            rpl.set_edge_metrics(usable_metrics)
            rpl.run_RPL(parent_set_size=parent_set_size)
        # Isolated external accesses remain part of a valid solution.
        rpl.G_res.add_nodes_from(
            (node, attrs.copy()) for node, attrs in rpl.G.nodes(data=True)
            if attrs["connected"]
        )

        self.rpl = rpl
        return rpl

    # Result export ----------------------------------------------------------

    def score_paths(self, attached_equipment=None, *, metric_spec: str = "default_path",
                    include_all_interfaces: bool = False):
        """Score the preferred paths in the solved graph with a compiled metric."""
        if self.rpl is None:
            raise RuntimeError("Solve the network before scoring paths")
        score = mc.compile_path_metric_spec(self._metric_spec_text(metric_spec))
        return self._score_tree(self.rpl.G_res, attached_equipment, score, include_all_interfaces)[0]

    @staticmethod
    def _score_tree(graph, attached_equipment, score, include_all_interfaces=False):
        demand = defaultdict(Counter)
        for equipment in attached_equipment or ():
            value = equipment.get("traffic_units")
            load = 1.0 if is_blank(value) else float(value)
            if not isfinite(load) or load < 0:
                raise ValueError("Equipment traffic_units must be finite and nonnegative")
            demand[equipment["device_id"]][equipment["equipment_type"]] += load

        endpoints = {}
        for interface, node in sorted(graph.nodes(data=True)):
            device = node["extra"].get("device_id")
            if device is None or (attached_equipment is not None and device not in demand):
                continue
            current = endpoints.get(device)
            if current is None or node["rank"] < graph.nodes[current]["rank"]:
                endpoints[device] = interface

        edge_loads = defaultdict(Counter)
        paths = {}
        for start in graph:
            interface = start
            path = []
            while (parent := graph.nodes[interface].get("parent")) is not None:
                path.append((interface, parent))
                interface = parent
            paths[start] = path
        for device, interface in endpoints.items():
            for edge in paths[interface]:
                edge_loads[edge].update(demand[device])

        scores = []
        interfaces = sorted(graph) if include_all_interfaces else endpoints.values()
        for interface in interfaces:
            segments = [
                {
                    "tech": graph.nodes[src]["extra"].get("tech"),
                    "segment_metric": graph.edges[src, dst]["metric"],
                    "load": [{"app": app, "carga": load}
                             for app, load in sorted(edge_loads[src, dst].items())],
                }
                for src, dst in paths[interface]
            ]
            scores.append({"node_id": interface, "device_id": graph.nodes[interface]["extra"].get("device_id"),
                           "segments": segments, "score": score(segments)})
        segment_scores = [graph.edges[edge]["metric"] * sum(load.values())
                          for edge, load in edge_loads.items()]
        return scores, segment_scores

    @staticmethod
    def _saved_parent_graph(result):
        graph = nx.DiGraph()
        for row in result["planned_nodes"]:
            graph.add_node(row["node_id"], parent=row["parent"], rank=row["rank"],
                           extra={"device_id": row["device_id"], "tech": row.get("tech")})
        if not graph:
            raise ValueError("The saved result has no planned network")
        metrics = {frozenset((edge["src"], edge["dst"])): edge["metric"]
                   for edge in [*result.get("metric_edges", ()), *result["planned_edges"]]}
        alternatives = {}
        for row in result["planned_nodes"]:
            node = row["node_id"]
            alternatives[node] = tuple(sorted(set(row.get("alternate_parents", ()))))
            parents = (row["parent"], *alternatives[node]) if row["parent"] is not None else ()
            for parent in parents:
                cost = metrics.get(frozenset((node, parent)))
                if parent not in graph or cost is None or not isfinite(cost) or cost < 0:
                    raise ValueError(f"Invalid saved parent segment {node} → {parent}")
                graph.add_edge(node, parent, metric=cost)
        if not nx.is_directed_acyclic_graph(graph):
            raise ValueError("The saved parent sets contain a cycle")
        return graph, alternatives

    @staticmethod
    def _score_saved_tree(graph, equipment, path_score, network_score):
        rows, segment_scores = GraphPlanner._score_tree(graph, equipment, path_score)
        scores = {row["node_id"]: row["score"] for row in rows}
        if not scores:
            raise ValueError("Network metric requires at least one served path")
        return {"path_scores": scores, "network_score": network_score(tuple(scores.values()), segment_scores)}

    @staticmethod
    def optimize_saved_parents(result, attached_equipment, *, path_metric, network_metric):
        """Minimize the network score by best improving single-parent changes."""
        if not attached_equipment:
            raise ValueError("Parent optimization requires an attached equipment inventory")
        graph, alternatives = GraphPlanner._saved_parent_graph(result)
        path_score = mc.compile_path_metric_spec(GraphPlanner._metric_spec_text(path_metric))
        network_score = mc.compile_network_metric_spec(GraphPlanner._metric_spec_text(network_metric))
        baseline = current = GraphPlanner._score_saved_tree(graph, attached_equipment, path_score, network_score)
        changes = []
        while True:
            best = current
            choice = None
            for node in sorted(alternatives):
                old = graph.nodes[node]["parent"]
                for parent in sorted(set(alternatives[node]) | {old}) if old is not None else ():
                    if parent == old:
                        continue
                    graph.nodes[node]["parent"] = parent
                    trial = GraphPlanner._score_saved_tree(graph, attached_equipment, path_score, network_score)
                    if trial["network_score"] < best["network_score"]:
                        best, choice = trial, (node, old, parent)
                graph.nodes[node]["parent"] = old
            if choice is None:
                break
            node, old, parent = choice
            graph.nodes[node]["parent"] = parent
            alternatives[node] = tuple(sorted((set(alternatives[node]) | {old}) - {parent}))
            changes.append({"node_id": node, "previous_parent": old, "parent": parent,
                            "network_score": best["network_score"]})
            current = best

        sites = {row["node_id"]: row.get("site_id") for row in result["planned_nodes"]}
        optimized = {key: value for key, value in result.items() if key not in {"evaluation", "network_evaluation"}}
        optimized["planned_nodes"] = [
            dict(row, parent=graph.nodes[node]["parent"],
                 parent_site_id=sites.get(graph.nodes[node]["parent"]),
                 alternate_parents=list(alternatives[node]))
            for row in result["planned_nodes"] for node in (row["node_id"],)
        ]
        optimized["planned_edges"] = [
            {"src": node, "dst": data["parent"], "metric": graph.edges[node, data["parent"]]["metric"]}
            for node, data in graph.nodes(data=True) if data["parent"] is not None
        ]
        paths = {}
        for origin in current["path_scores"]:
            route = [origin]
            while (parent := graph.nodes[route[-1]]["parent"]) is not None:
                route.append(parent)
            paths[origin] = {"nodes": route, "score": current["path_scores"][origin]}
        current = {**current, "paths": paths}
        optimized.update(path_metric=path_metric, path_scores=current["path_scores"],
                         path_origins=list(paths))
        return {"baseline": baseline, "optimized": current, "changes": changes, "result": optimized}

    @staticmethod
    def network_metric(path_scores: Iterable[float], *, metric_spec: str = "default_network") -> float:
        """Score the network using only the scalar scores of its paths."""
        scores = tuple(path_scores)
        if not scores:
            raise ValueError("Network metric requires at least one path score")
        if any(not isfinite(score) for score in scores):
            raise ValueError("Path scores must be finite")
        metric = mc.compile_network_metric_spec(GraphPlanner._metric_spec_text(metric_spec))
        return metric(scores)

    def rpl_result_counts(self) -> dict[str, int]:
        """
        Return node/edge counts for the last RPL result.

        Raises ``RuntimeError`` if RPL has not been run yet.
        """
        if self.rpl is None:
            raise RuntimeError("RPL was not run")
        return {
            "nodes": self.rpl.G_res.number_of_nodes(),
            "edges": self.rpl.G_res.number_of_edges(),
        }

    def to_result_dict(
        self,
        *,
        include_candidates: bool = True,
        include_metrics: bool = True,
        include_features: bool = False,
    ) -> dict[str, Any]:
        """
        Serialize the current graph-planning execution as JSON-ready data.

        The returned object is execution output, not scenario input. It can
        include candidate edges, computed metric records and the final RPL
        planned graph. Expensive raw feature payloads are excluded by default.

        The object is JSON-ready and is suitable for notebooks, API responses
        and persisted planning artifacts.
        """
        self._sync_interfaces()
        inventory = []
        for site in self.nodes.values():
            enabled = {i.interface_id: i.enabled for i in site.interfaces}
            for record in self._rpl_node_records(site.rpl_nodes()):
                record["enabled"] = enabled[record["node_id"]]
                inventory.append(record)
        result: dict[str, Any] = {
            "working_crs": self.working_crs,
            "counts": {
                "sites": len(self.nodes),
                "rpl_nodes": len(self.rpl_nodes),
                "interfaces": len(inventory),
                "disabled_interfaces": sum(not row["enabled"] for row in inventory),
                "candidate_edges": len(self.candidate_edges),
                "metric_edges": len(self.edge_metrics),
                "planned_nodes": (
                    self.rpl.G_res.number_of_nodes()
                    if self.rpl is not None
                    else 0
                ),
                "planned_edges": (
                    self.rpl.G_res.number_of_edges()
                    if self.rpl is not None
                    else 0
                ),
            },
            "rpl_nodes": self._rpl_node_records(self.rpl_nodes),
            "interface_inventory": inventory,
            "antennas": {key: antenna.to_dict() for key, antenna in self.antenna_catalog.items()},
        }

        if include_candidates:
            result["candidate_edges"] = [
                self._edge_record(src, dst)
                for src, dst in self.candidate_edges
            ]

        if include_metrics:
            result["metric_edges"] = [
                self._edge_record(src, dst, metric=metric)
                for (src, dst), metric in self.edge_metrics.items()
            ]

        if include_features:
            result["edge_features"] = [
                {
                    "src": src,
                    "dst": dst,
                    "features": self._json_value(features),
                }
                for (src, dst), features in self.edge_features.items()
            ]

        if self.rpl is not None:
            result["planned_nodes"] = self._graph_node_records(self.rpl.G_res)
            result["planned_edges"] = self._graph_edge_records(self.rpl.G_res)
        else:
            result["planned_nodes"] = []
            result["planned_edges"] = []

        return self._json_value(result)

    def save_results(
        self,
        path: str | Path,
        *,
        include_candidates: bool = True,
        include_metrics: bool = True,
        include_features: bool = False,
    ) -> Path:
        """
        Write ``to_result_dict`` output as an indented JSON file.

        Parent directories are created automatically. The returned path is the
        file that was written.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                self.to_result_dict(
                    include_candidates=include_candidates,
                    include_metrics=include_metrics,
                    include_features=include_features,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    # Internal candidate/state helpers --------------------------------------

    def _add_internal_edges_from_rule(
        self,
        rpl_nodes: list[RPLNode],
        rule_id: str,
        rule: Mapping[str, Any],
        add_edge: Callable[[RPLNode, RPLNode, Mapping[str, Any]], None],
    ) -> None:
        """
        Add same-device interface edges for an ``internal`` connectivity rule.

        This is an implementation helper for ``build_candidate_edges_from_rules``.
        It groups RPL nodes by device id and connects every pair in a device
        when routing is allowed by the rule and by the device metadata.
        """
        enabled_when_can_route = parse_bool(
            rule.get("enabled_when_device_can_route"),
            True,
        )
        metric = float(rule.get("metric", 0.0))
        if metric < 0 or not isfinite(metric):
            raise ValueError(f"Rule {rule_id}: internal metric must be >= 0")

        nodes_by_device: dict[str, list[RPLNode]] = {}
        for node in rpl_nodes:
            device_id = node.extra.get("device_id")
            if device_id is None:
                continue
            nodes_by_device.setdefault(str(device_id), []).append(node)

        for nodes in nodes_by_device.values():
            if len(nodes) < 2:
                continue

            can_route = any(
                parse_bool(node.extra.get("device_can_route"), False)
                for node in nodes
            )
            if enabled_when_can_route and not can_route:
                continue

            for index, left in enumerate(nodes):
                for right in nodes[index + 1:]:
                    add_edge(
                        left,
                        right,
                        {
                            "kind": "internal",
                            "rule": rule_id,
                            "metric": metric,
                        },
                    )

    def _edge_attrs(self, src: str, dst: str) -> dict[str, Any]:
        """
        Return candidate-edge attributes independent of stored edge direction.

        Candidate edges are treated as undirected for lookup, so callers may
        ask for ``(src, dst)`` or ``(dst, src)`` and receive the same attrs.
        """
        attrs = self.candidate_edge_attrs.get((src, dst))
        if attrs is not None:
            return dict(attrs)

        attrs = self.candidate_edge_attrs.get((dst, src))
        if attrs is not None:
            return dict(attrs)

        return {}

    def _clear_results(self) -> None:
        """
        Drop computed metrics, features and RPL result after graph changes.

        Candidate graph mutations invalidate all downstream execution state.
        """
        self.edge_metrics = {}
        self.edge_features = {}
        self.rpl = None

    def _interface_signature(self):
        return tuple(
            (interface.interface_id, interface.enabled)
            for site in self.nodes.values() for interface in site.interfaces
        )

    def _sync_interfaces(self) -> None:
        """Activation changes invalidate computed state, not external raw caches."""
        signature = self._interface_signature()
        if signature != self._interface_state:
            self.rpl_nodes = self._build_rpl_nodes()
            self.candidate_edges = list(self.scenario.candidate_edges)
            self.candidate_edge_attrs = {
                edge: dict(attrs) for edge, attrs in self.scenario.candidate_edge_attrs.items()
            }
            self._candidates_built = bool(self.candidate_edges)
            self._clear_results()
            self._interface_state = signature
        self._validate_active_edges(self.candidate_edges)

    def _candidate_edges_or_build(self):
        self._sync_interfaces()
        if self._candidates_built or self.candidate_edges or not self.connectivity_rules:
            return self.candidate_edges
        return self.build_candidate_edges_from_rules()

    def _validate_active_edges(self, edges):
        edges = list(edges)
        known = {i.interface_id: i.enabled for site in self.nodes.values() for i in site.interfaces}
        for edge in edges:
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise TypeError("Candidate edges must be (src, dst) tuples")
            for iid in edge:
                if iid not in known:
                    raise KeyError(f"Unknown interface: {iid}")
                if not known[iid]:
                    raise ValueError(f"Edge references disabled interface: {iid}")
        return edges

    def _build_rpl_nodes(self) -> list[RPLNode]:
        """
        Convert all site/device/interface instances into interface-level nodes.

        This is the only place where GraphPlanner flattens the physical
        scenario into the RPL representation. It is kept private because users
        should inspect the resulting ``rpl_nodes`` attribute, not request a new
        conversion during normal planning.
        """
        rpl_nodes = []
        for site_node in self.nodes.values():
            enabled = {i.interface_id for i in site_node.interfaces if i.enabled}
            rpl_nodes.extend(node for node in site_node.rpl_nodes() if node.node_id in enabled)
        return rpl_nodes

    # Internal result serialization helpers ---------------------------------

    def _edge_record(
        self,
        src: str,
        dst: str,
        *,
        metric: float | None = None,
    ) -> dict[str, Any]:
        """
        Build one JSON-ready edge record for result serialization.

        The record combines endpoint interface metadata, candidate edge
        attributes and optionally the computed metric.
        """
        rpl_by_id = {node.node_id: node for node in self.rpl_nodes}
        src_node = rpl_by_id[src]
        dst_node = rpl_by_id[dst]
        record = {
            "src": src,
            "dst": dst,
            "src_site": src_node.extra.get("site_id"),
            "dst_site": dst_node.extra.get("site_id"),
            "src_tech": src_node.extra.get("tech"),
            "dst_tech": dst_node.extra.get("tech"),
            "src_freq_mhz": src_node.extra.get("freq_mhz"),
            "dst_freq_mhz": dst_node.extra.get("freq_mhz"),
            "src_max_links": src_node.extra.get("max_links"),
            "dst_max_links": dst_node.extra.get("max_links"),
            "src_mount_height_m": src_node.extra.get("mount_height_m"),
            "dst_mount_height_m": dst_node.extra.get("mount_height_m"),
            **self._edge_attrs(src, dst),
        }
        if metric is not None:
            record["metric"] = metric
        return record

    @staticmethod
    def _rpl_node_records(nodes: Iterable[RPLNode]) -> list[dict[str, Any]]:
        """
        Convert RPL nodes to JSON-ready records.

        These records describe interface-level planning nodes before or after
        metric/RPL execution.
        """
        records = []
        for node in nodes:
            x = y = None
            if node.pos_utm is not None:
                x, y = node.pos_utm
            records.append(
                {
                    "node_id": node.node_id,
                    "enabled": node.extra.get("enabled", True),
                    "site_id": node.extra.get("site_id"),
                    "device_id": node.extra.get("device_id"),
                    "tech": node.extra.get("tech"),
                    "freq_mhz": node.extra.get("freq_mhz"),
                    "tx_power_dbm": node.extra.get("tx_power_dbm"),
                    "antenna_id": node.extra.get("antenna_id"),
                    "mount_height_m": node.extra.get("mount_height_m"),
                    "max_links": node.extra.get("max_links"),
                    "connected": node.connected,
                    "rpl_relay": node.rpl_relay,
                    "rank": node.rank,
                    "x": x,
                    "y": y,
                }
            )
        return records

    @staticmethod
    def _graph_node_records(graph) -> list[dict[str, Any]]:
        """
        Convert a NetworkX RPL graph node view to JSON-ready records.

        Used for serializing the final planned graph. Parent information is
        included when RPL has assigned it.
        """
        records = []
        for node_id, attrs in graph.nodes(data=True):
            extra = attrs.get("extra", {})
            parent = attrs.get("parent")
            parent_extra = graph.nodes[parent].get("extra", {}) if parent else {}
            pos = attrs.get("pos")
            x = y = None
            if pos is not None:
                x, y = pos
            records.append(
                {
                    "node_index": attrs.get("node_index"),
                    "node_id": node_id,
                    "site_id": extra.get("site_id"),
                    "device_id": extra.get("device_id"),
                    "tech": extra.get("tech"),
                    "parent": parent,
                    "parent_site_id": parent_extra.get("site_id"),
                    "connected": attrs.get("connected"),
                    "rpl_relay": attrs.get("rpl_relay"),
                    "rank": attrs.get("rank"),
                    "advertised_rank": attrs.get("advertised_rank", attrs.get("rank")),
                    "alternate_parents": attrs.get("alternate_parents", []),
                    "x": x,
                    "y": y,
                }
            )
        records.sort(
            key=lambda row: (
                row["node_index"] is None,
                row["node_index"],
            )
        )
        return records

    @staticmethod
    def _graph_edge_records(graph) -> list[dict[str, Any]]:
        """
        Convert a NetworkX RPL graph edge view to JSON-ready records.

        Edge attributes are preserved, with ``metric`` promoted to a stable
        top-level field in each record.
        """
        records = []
        for src, dst, attrs in graph.edges(data=True):
            records.append(
                {
                    "src": src,
                    "dst": dst,
                    "metric": attrs.get("metric"),
                    **{
                        key: value
                        for key, value in attrs.items()
                        if key != "metric"
                    },
                }
            )
        return records

    # Internal metric adapters ----------------------------------------------

    def _metric_record(
        self,
        src_node: RPLNode,
        dst_node: RPLNode,
        features: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Build the record consumed by a compiled metric function.

        This adapter combines the transmitting interface, receiving interface,
        antenna profiles and geo feature output into the nested ``tx``/``rx``/
        ``features`` shape expected by ``metric_compiler``.
        """
        src_antenna = self.antenna_catalog[src_node.extra["antenna_id"]]
        dst_antenna = self.antenna_catalog[dst_node.extra["antenna_id"]]

        return {
            "tx": self._metric_terminal_record(
                src_node,
                dst_node,
                src_antenna,
                include_power=True,
            ),
            "rx": self._metric_terminal_record(
                dst_node,
                src_node,
                dst_antenna,
                include_power=False,
            ),
            "features": dict(features),
        }

    def _metric_terminal_record(
        self,
        node: RPLNode,
        peer_node: RPLNode,
        antenna: AntennaSpec,
        *,
        include_power: bool,
    ) -> dict[str, Any]:
        """
        Build the ``tx`` or ``rx`` terminal section of a metric record.

        Dynamic antenna gain is evaluated here because gain depends on link
        direction. If the antenna profile has ``model_id`` the library pattern
        is used; otherwise the fixed ``gain_dbi`` fallback is used.
        """
        ant_gain = self._link_antenna_gain(node, peer_node, antenna)
        record = {
            "ant_gain": ant_gain,
            "ant_height": float(node.extra["mount_height_m"]),
            "ant_type": str(antenna.kind),
        }

        if antenna.model_id is not None:
            record["ant_model_id"] = str(antenna.model_id)

        if antenna.description is not None:
            record["ant_name"] = str(antenna.description)

        if include_power:
            record["pw"] = float(node.extra["tx_power_dbm"])

        return record

    def _link_antenna_gain(
        self,
        node: RPLNode,
        peer_node: RPLNode,
        antenna: AntennaSpec,
    ) -> float:
        """
        Return antenna gain from one interface toward its peer.

        ``model_id`` activates library-backed gain with azimuth, downtilt and
        shadow parameters. Without ``model_id`` this returns the profile's
        fixed ``gain_dbi``.
        """
        if antenna.model_id is None:
            return float(antenna.gain_dbi)

        if node.pos_utm is None:
            raise ValueError(f"Missing position for node {node.node_id}")
        if peer_node.pos_utm is None:
            raise ValueError(f"Missing position for node {peer_node.node_id}")

        return self.antenna_planner.link_gain_dbi(
            antenna.model_id,
            frequency_mhz=float(node.extra["freq_mhz"]),
            src_pos=node.pos_utm,
            dst_pos=peer_node.pos_utm,
            src_height_m=float(node.extra["mount_height_m"]),
            dst_height_m=float(peer_node.extra["mount_height_m"]),
            azimuth_deg=(
                float(antenna.azimuth_deg)
                if antenna.azimuth_deg is not None
                else 0.0
            ),
            downtilt_deg=(
                float(antenna.downtilt_deg)
                if antenna.downtilt_deg is not None
                else 0.0
            ),
            modifier={
                key: value
                for key, value in {
                    "shadow_azimuth_deg": antenna.shadow_azimuth_deg,
                    "shadow_width_deg": antenna.shadow_width_deg,
                    "shadow_loss_db": antenna.shadow_loss_db,
                }.items()
                if value is not None
            },
        )

    # Internal rule and value helpers ----------------------------------------

    @staticmethod
    def _select_rule_nodes(
        nodes: Iterable[RPLNode],
        *,
        tech: str,
        selector: str,
    ) -> list[RPLNode]:
        """
        Select RPL interface nodes for one connectivity-rule endpoint.

        ``selector`` may be ``any``, ``connected``, ``unconnected``, ``relay``
        or ``non_relay``. The technology filter is always applied first.
        """
        selector = selector.lower()
        if selector not in RULE_SELECTORS:
            raise ValueError(
                "Rule source/destination must be 'any', 'connected', "
                "'unconnected', 'relay', or 'non_relay'"
            )

        selected = []
        for node in nodes:
            if node.extra.get("tech") != tech:
                continue
            if selector == "connected" and not node.connected:
                continue
            if selector == "unconnected" and node.connected:
                continue
            if selector == "relay" and not node.rpl_relay:
                continue
            if selector == "non_relay" and node.rpl_relay:
                continue
            selected.append(node)

        return selected

    @staticmethod
    def _distance_m(left: RPLNode, right: RPLNode) -> float:
        """
        Return projected Euclidean distance between two RPL nodes in meters.

        Both nodes must already have ``pos_utm`` in the scenario working CRS.
        """
        if left.pos_utm is None:
            raise ValueError(f"Missing position for node {left.node_id}")
        if right.pos_utm is None:
            raise ValueError(f"Missing position for node {right.node_id}")

        return hypot(
            right.pos_utm[0] - left.pos_utm[0],
            right.pos_utm[1] - left.pos_utm[1],
        )

    @staticmethod
    def _metric_spec_text(spec: str) -> str:
        """
        Resolve a metric spec name or inline spec to TOML/formula text.

        Inline specs are returned unchanged. Otherwise ``spec`` is interpreted
        as a resource name under ``cisei_lib/resources/metrics``.
        """
        if "\n" in spec or "=" in spec or "{" in spec:
            return spec

        return (
            files("cisei_lib")
            .joinpath("resources", "metrics", f"{spec}_metric.toml")
            .read_text(encoding="utf-8")
        )

    @staticmethod
    def _compatible(left: RPLNode, right: RPLNode) -> bool:
        """
        Return true when two interface nodes may form a basic RF edge.

        The generic planner only requires equal technology and equal frequency.
        Specialized planners may apply stricter geometry, sector or capacity
        rules before calling into the generic metric/RPL workflow.
        """
        left_extra = left.extra
        right_extra = right.extra
        return (
            left_extra.get("tech") == right_extra.get("tech")
            and left_extra.get("freq_mhz") == right_extra.get("freq_mhz")
        )

    @staticmethod
    def _clean_id(value: Any) -> str:
        """
        Normalize an identifier and reject blank values.

        This is used for user-facing ids such as interface ids and rule ids.
        """
        value = str(value).strip()
        if not value:
            raise ValueError("ID cannot be empty")
        return value

    @staticmethod
    def _json_value(value: Any) -> Any:
        """
        Convert common Python/numpy values into JSON-compatible values.

        Non-finite floats become ``None`` because JSON has no portable
        representation for infinity or NaN.
        """
        if isinstance(value, Mapping):
            return {
                str(key): GraphPlanner._json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, tuple):
            return [GraphPlanner._json_value(item) for item in value]
        if isinstance(value, list):
            return [GraphPlanner._json_value(item) for item in value]
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value if isfinite(value) else None
        if hasattr(value, "item"):
            return GraphPlanner._json_value(value.item())
        if hasattr(value, "tolist"):
            return GraphPlanner._json_value(value.tolist())
        return str(value)
