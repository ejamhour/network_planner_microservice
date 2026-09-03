from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from importlib.resources import files
from math import hypot, isfinite
from pathlib import Path
from typing import Any

import cisei_lib.planners.metric_compiler as mc
from cisei_lib.planners.antenna_planner import AntennaPlanner
from cisei_lib.planners.geo_rpl_agnostic import GeoRPL
from cisei_lib.planners.planner_classes import AntennaSpec, RPLNode, SiteNode
from cisei_lib.planners.planning_scenario import PlanningScenario


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if value != value:
            return True
    except TypeError:
        pass
    return isinstance(value, str) and not value.strip()


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


def parse_bool(value: Any, default: bool = False) -> bool:
    if is_blank(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not isfinite(float(value)):
            raise ValueError("Boolean numeric fields must be finite")
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y"}:
            return True
        if normalized in {"0", "false", "f", "no", "n"}:
            return False
    raise ValueError(f"Invalid boolean value: {value!r}")


class GraphPlanner:
    """
    Generic graph/RPL planner backed by a ``PlanningScenario``.

    ``PlanningScenario`` owns user-facing preparation: antenna profiles,
    interface profiles, device profiles, node instances, manual candidate
    edges and TOML/CSV serialization. ``GraphPlanner`` consumes that scenario
    and owns computational state: generated candidate edges, extracted edge
    features, compiled metric functions and the RPL result.
    """

    def __init__(self, scenario: PlanningScenario) -> None:
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
        self.connectivity_rules: dict[str, dict[str, Any]] = (
            scenario.connectivity_rules
        )
        self.candidate_edges: list[tuple[str, str]] = list(
            scenario.candidate_edges
        )
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
        self.rpl: GeoRPL | None = None

        if scenario.metric_specs_by_tech:
            self.compile_metric_specs_by_tech(scenario.metric_specs_by_tech)

    @classmethod
    def from_scenario(cls, scenario: PlanningScenario) -> "GraphPlanner":
        """Create a graph planner from an already prepared scenario."""
        return cls(scenario)

    @classmethod
    def from_toml(
        cls,
        path: str | Path,
        *,
        working_crs: str | None = None,
        load_instances: bool = True,
    ) -> "GraphPlanner":
        """Load a ``PlanningScenario`` from TOML and create a graph planner."""
        return cls(
            PlanningScenario.from_toml(
                path,
                working_crs=working_crs,
                load_instances=load_instances,
            )
        )

    def to_config_dict(self, *, include_sites: bool = True) -> dict[str, Any]:
        """Return the backing scenario as a plain configuration dictionary."""
        return self.scenario.to_config_dict(include_sites=include_sites)

    def rpl_nodes(self) -> list[RPLNode]:
        """Return all concrete interfaces as RPL nodes."""
        nodes = []
        for node in self.nodes.values():
            nodes.extend(node.rpl_nodes())
        return nodes

    def node_by_site(self) -> dict[str, SiteNode]:
        """Return concrete site nodes keyed by site id."""
        return dict(self.nodes)

    def set_candidate_edges(
        self,
        edges: Iterable[tuple[str, str]],
        *,
        attrs_by_edge: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
        default_attrs: Mapping[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
        """Replace the current candidate graph with explicit interface edges."""
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
        """Add candidate edges without duplicating undirected pairs."""
        rpl_by_id = {node.node_id: node for node in self.rpl_nodes()}
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
        """
        if not self.connectivity_rules:
            raise RuntimeError("No connectivity rules were configured")

        rpl_nodes = self.rpl_nodes()
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

            if degree is not None and degree < 0:
                raise ValueError(f"Rule {rule_id}: degree must be >= 0")
            if limit_m is not None and limit_m < 0:
                raise ValueError(f"Rule {rule_id}: limit must be >= 0")

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
                    if limit_m is not None and distance_m > limit_m:
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
        return list(self.candidate_edges)

    def candidate_graph(
        self,
        candidate_edges: Iterable[tuple[str, str]] | None = None,
    ):
        """Return a NetworkX graph containing the candidate edges."""
        import networkx as nx

        rpl_nodes = self.rpl_nodes()
        if candidate_edges is None:
            candidate_edges = (
                self.candidate_edges
                if self.candidate_edges
                else self.build_candidate_edges_from_rules()
            )
        candidate_edges = list(candidate_edges)

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

    def compile_metric_specs_by_tech(
        self,
        metric_specs_by_tech: Mapping[
            str,
            str | Mapping[str, Any] | Callable[[Mapping[str, Any]], float],
        ],
    ) -> dict[str, Callable[[Mapping[str, Any]], float]]:
        """Compile metric formulas/resources keyed by interface technology."""
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
    ) -> dict[tuple[str, str], float]:
        """Extract geo features and compute one metric per candidate edge."""
        if metric_specs_by_tech is not None:
            self.compile_metric_specs_by_tech(metric_specs_by_tech)

        if not self.metric_functions_by_tech:
            raise RuntimeError("No metric functions were registered")

        if candidate_edges is None:
            candidate_edges = (
                self.candidate_edges
                if self.candidate_edges
                else self.build_candidate_edges_from_rules()
            )
        candidate_edges = list(candidate_edges)

        rpl_by_id = {node.node_id: node for node in self.rpl_nodes()}
        node_by_site = self.node_by_site()
        edge_metrics = {}
        edge_features = {}

        if manage_geo:
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

                src_site = node_by_site[src_node.extra["site_id"]].site
                dst_site = node_by_site[dst_node.extra["site_id"]].site
                features = await geo.get_features(
                    src_site.geo_pos,
                    dst_site.geo_pos,
                    tx_ha=src_height,
                    rx_ha=dst_height,
                    freq_mhz=src_freq_mhz,
                )

                features = dict(features)
                features.setdefault("freq_mhz", src_freq_mhz)

                metric_record = self._metric_record(
                    src_node,
                    dst_node,
                    features,
                )
                edge_metrics[(src, dst)] = float(metric_function(metric_record))
                edge_features[(src, dst)] = metric_record

        finally:
            if manage_geo:
                await geo.close()

        self.candidate_edges = candidate_edges
        self.edge_metrics = edge_metrics
        self.edge_features = edge_features
        return dict(edge_metrics)

    def run_rpl(
        self,
        edge_metrics: Mapping[tuple[str, str], float] | None = None,
    ) -> GeoRPL:
        """Run RPL over the current metric graph."""
        if edge_metrics is None:
            edge_metrics = self.edge_metrics
        if not edge_metrics:
            raise RuntimeError("No edge metrics were computed")

        self.edge_metrics = {
            (str(src), str(dst)): float(metric)
            for (src, dst), metric in edge_metrics.items()
        }

        rpl = GeoRPL()
        rpl.set_nodes(self.rpl_nodes())
        rpl.set_edge_metrics(self.edge_metrics)
        rpl.run_RPL()

        self.rpl = rpl
        return rpl

    def rpl_result_counts(self) -> dict[str, int]:
        """Return node/edge counts for the last RPL result."""
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
        """
        result: dict[str, Any] = {
            "working_crs": self.working_crs,
            "counts": {
                "sites": len(self.nodes),
                "rpl_nodes": len(self.rpl_nodes()),
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
            "rpl_nodes": self._rpl_node_records(self.rpl_nodes()),
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
        """Write ``to_result_dict`` output as an indented JSON file."""
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

    def _add_internal_edges_from_rule(
        self,
        rpl_nodes: list[RPLNode],
        rule_id: str,
        rule: Mapping[str, Any],
        add_edge: Callable[[RPLNode, RPLNode, Mapping[str, Any]], None],
    ) -> None:
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
        attrs = self.candidate_edge_attrs.get((src, dst))
        if attrs is not None:
            return dict(attrs)

        attrs = self.candidate_edge_attrs.get((dst, src))
        if attrs is not None:
            return dict(attrs)

        return {}

    def _clear_results(self) -> None:
        self.edge_metrics = {}
        self.edge_features = {}
        self.rpl = None

    def _edge_record(
        self,
        src: str,
        dst: str,
        *,
        metric: float | None = None,
    ) -> dict[str, Any]:
        rpl_by_id = {node.node_id: node for node in self.rpl_nodes()}
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
        records = []
        for node in nodes:
            x = y = None
            if node.pos_utm is not None:
                x, y = node.pos_utm
            records.append(
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

    def _metric_record(
        self,
        src_node: RPLNode,
        dst_node: RPLNode,
        features: Mapping[str, Any],
    ) -> dict[str, Any]:
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

    @staticmethod
    def _select_rule_nodes(
        nodes: Iterable[RPLNode],
        *,
        tech: str,
        selector: str,
    ) -> list[RPLNode]:
        selector = selector.lower()
        if selector not in {
            "any",
            "connected",
            "unconnected",
            "relay",
            "non_relay",
        }:
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
        if "\n" in spec or "=" in spec or "{" in spec:
            return spec

        return (
            files("cisei_lib")
            .joinpath("resources", "metrics", f"{spec}_metric.toml")
            .read_text(encoding="utf-8")
        )

    @staticmethod
    def _compatible(left: RPLNode, right: RPLNode) -> bool:
        left_extra = left.extra
        right_extra = right.extra
        return (
            left_extra.get("tech") == right_extra.get("tech")
            and left_extra.get("freq_mhz") == right_extra.get("freq_mhz")
        )

    @staticmethod
    def _clean_id(value: Any) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("ID cannot be empty")
        return value

    @staticmethod
    def _json_value(value: Any) -> Any:
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
