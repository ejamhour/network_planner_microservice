from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from importlib.resources import files
from math import hypot, inf, isfinite
from pathlib import Path
from typing import Any

import cisei_lib.planners.metric_compiler as mc
from cisei_lib.planners.geo_rpl_agnostic import GeoRPL
from cisei_lib.planners.planner_classes import (
    AntennaSpec,
    Device,
    FieldSite,
    RadioInterface,
    RPLNode,
    Site,
    SiteNode,
    make_device_id,
    make_interface_id,
)


def is_blank(value: Any) -> bool:
    if value is None:
        return True

    try:
        if value != value:
            return True
    except TypeError:
        pass

    return isinstance(value, str) and not value.strip()


def optional_value(
    record: Mapping[str, Any],
    key: str,
    default: Any = None,
) -> Any:
    value = record.get(key, default)
    return default if is_blank(value) else value


def required_value(record: Mapping[str, Any], key: str) -> Any:
    value = optional_value(record, key)
    if is_blank(value):
        raise ValueError(f"Missing required record field: {key}")
    return value


def first_value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if not is_blank(value):
            return value
    return None


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
    Generic planning-data preparation helper.

    File reading stays outside this class. Notebooks, APIs, or callers prepare
    rows as records; this class normalizes them into planning objects.
    """

    DEFAULT_RECORD_KEYS = {
        "site_id",
        "position_id",
        "id",
        "name",
        "lat",
        "lon",
        "x",
        "y",
        "kind",
        "mount_height_m",
        "ant_h",
        "antenna_height_m",
        "tech",
        "freq_mhz",
        "tx_power_dbm",
        "antenna_id",
        "connected",
        "can_route",
        "can_relay",
        "medium",
        "rank",
        "device_profile",
    }

    def __init__(
        self,
        *,
        working_crs: str | None = None,
        antenna_catalog: Mapping[str, AntennaSpec] | None = None,
    ) -> None:
        self.working_crs = working_crs
        self.antenna_catalog: dict[str, AntennaSpec] = dict(
            antenna_catalog or {}
        )
        self.nodes: dict[str, SiteNode] = {}
        self.candidate_edges: list[tuple[str, str]] = []
        self.edge_metrics: dict[tuple[str, str], float] = {}
        self.edge_features: dict[tuple[str, str], dict[str, Any]] = {}
        self.candidate_edge_attrs: dict[tuple[str, str], dict[str, Any]] = {}
        self.metric_functions_by_tech: dict[str, Callable[[Mapping[str, Any]], float]] = {}
        self.interface_profiles: dict[str, dict[str, Any]] = {}
        self.device_profiles: dict[str, dict[str, Any]] = {}
        self.connectivity_rules: dict[str, dict[str, Any]] = {}
        self.profile_config: dict[str, Any] = {}
        self.rpl: GeoRPL | None = None

    def add_antenna(self, antenna_id: str, antenna: AntennaSpec) -> None:
        self.add_antennas({antenna_id: antenna})

    def add_antennas(
        self,
        antennas: Mapping[str, AntennaSpec],
        *,
        update: bool = True,
    ) -> None:
        if not update:
            conflicts = set(self.antenna_catalog).intersection(antennas)
            if conflicts:
                raise ValueError(
                    f"Antennas already exist: {sorted(conflicts)}"
                )

        self.antenna_catalog.update(antennas)

    @classmethod
    def from_profiles(
        cls,
        config: Mapping[str, Any],
        *,
        working_crs: str | None = None,
    ) -> "GraphPlanner":
        planner = cls(working_crs=working_crs)
        planner.configure_profiles(config)
        return planner

    @classmethod
    def from_toml(
        cls,
        path: str | Path,
        *,
        working_crs: str | None = None,
    ) -> "GraphPlanner":
        return cls.from_profiles(
            cls.load_toml(path),
            working_crs=working_crs,
        )

    @staticmethod
    def load_toml(path: str | Path) -> dict[str, Any]:
        import tomlkit

        document = tomlkit.parse(Path(path).read_text(encoding="utf-8"))
        return document.unwrap()

    def configure_profiles(self, config: Mapping[str, Any]) -> None:
        config = deepcopy(dict(config))
        self.profile_config = deepcopy(config)

        antennas = {}
        for antenna_id, antenna_config in config.get("antennas", {}).items():
            antennas[str(antenna_id)] = self._antenna_from_config(
                antenna_config
            )

        if antennas:
            self.add_antennas(antennas)

        self.interface_profiles = {
            str(profile_id): dict(profile)
            for profile_id, profile in config.get("interfaces", {}).items()
        }
        self.device_profiles = {
            str(profile_id): dict(profile)
            for profile_id, profile in config.get("devices", {}).items()
        }
        self.connectivity_rules = self._connectivity_rules_from_config(config)

        metric_specs = {}
        for tech, metric_config in config.get("metrics", {}).items():
            if isinstance(metric_config, str):
                metric_specs[str(tech)] = metric_config
                continue

            metric_config = dict(metric_config)
            spec = metric_config.get("spec", metric_config.get("formula"))
            if spec is None:
                raise ValueError(
                    f"Metric profile {tech!r} requires 'spec' or 'formula'"
                )
            metric_specs[str(tech)] = spec

        if metric_specs:
            self.compile_metric_specs_by_tech(metric_specs)

    def to_config_dict(self) -> dict[str, Any]:
        if self.profile_config:
            return deepcopy(self.profile_config)

        return {
            "antennas": {
                antenna_id: antenna.to_dict()
                for antenna_id, antenna in self.antenna_catalog.items()
            },
            "interfaces": deepcopy(self.interface_profiles),
            "devices": deepcopy(self.device_profiles),
            "net": {"connectivity": list(deepcopy(self.connectivity_rules).values())},
        }

    @staticmethod
    def _connectivity_rules_from_config(
        config: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        net_config = dict(config.get("net", {}))
        rules = net_config.get("connectivity", [])

        if rules:
            if not isinstance(rules, list):
                raise TypeError("net.connectivity must be a list of tables")

            result = {}
            for index, rule in enumerate(rules):
                rule = dict(rule)
                rule_id = f"connectivity_{index}"
                result[rule_id] = rule
            return result

        return {}

    def add_node(self, node: SiteNode) -> None:
        self.add_nodes([node])

    def add_nodes(self, nodes: Iterable[SiteNode]) -> None:
        nodes = list(nodes)
        if not nodes:
            return

        node_ids = [node.node_id for node in nodes]
        duplicated = {
            node_id
            for node_id in node_ids
            if node_ids.count(node_id) > 1
        }

        if duplicated:
            raise ValueError(
                f"Duplicated site IDs in batch: {sorted(duplicated)}"
            )

        conflicts = set(self.nodes).intersection(node_ids)
        if conflicts:
            raise ValueError(f"Sites already exist: {sorted(conflicts)}")

        for node in nodes:
            self.nodes[node.node_id] = node

    def add_records(
        self,
        records: Iterable[Mapping[str, Any]] | Any,
        *,
        defaults: Mapping[str, Any] | None = None,
        overrides_by_id: Mapping[str, Mapping[str, Any]] | None = None,
        id_prefix: str = "site",
        resolve: bool = True,
        validate: bool = False,
        tolerance_m: float = 1.0,
    ) -> list[SiteNode]:
        normalized_records = self._records(records)
        defaults = dict(defaults or {})
        overrides_by_id = dict(overrides_by_id or {})

        nodes = []
        used_ids = set()
        generated_count = 0

        for record in normalized_records:
            site_id = self._record_site_id(record)
            if site_id is None:
                generated_count += 1
                site_id = f"{id_prefix}_{generated_count}"

            site_id = self._clean_id(site_id)
            if site_id in used_ids:
                raise ValueError(f"Duplicated site ID: {site_id}")
            used_ids.add(site_id)

            merged = {}
            merged.update(defaults)
            merged.update(record)
            merged.update(overrides_by_id.get(site_id, {}))

            device_profile = optional_value(merged, "device_profile")
            if not is_blank(device_profile):
                node = self._node_from_profile_record(
                    site_id,
                    merged,
                    str(device_profile),
                    resolve=resolve,
                    validate=validate,
                    tolerance_m=tolerance_m,
                )
            else:
                node = self._node_from_record(
                    site_id,
                    merged,
                    resolve=resolve,
                    validate=validate,
                    tolerance_m=tolerance_m,
                )
            nodes.append(node)

        self.add_nodes(nodes)
        return nodes

    def rpl_nodes(self) -> list[RPLNode]:
        nodes = []
        for node in self.nodes.values():
            nodes.extend(node.rpl_nodes())
        return nodes

    def set_candidate_edges(
        self,
        edges: Iterable[tuple[str, str]],
        *,
        attrs_by_edge: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
        default_attrs: Mapping[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
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
        rpl_by_id = {node.node_id: node for node in self.rpl_nodes()}
        attrs_by_edge = attrs_by_edge or {}
        default_attrs = dict(default_attrs or {})

        edge_keys = {
            frozenset(edge)
            for edge in self.candidate_edges
        }

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

            self.candidate_edges.append((src, dst))
            self.candidate_edge_attrs[(src, dst)] = attrs
            edge_keys.add(key)

        return list(self.candidate_edges)

    def build_candidate_edges_from_rules(self) -> list[tuple[str, str]]:
        if not self.connectivity_rules:
            raise RuntimeError("No connectivity rules were configured")

        rpl_nodes = self.rpl_nodes()
        edge_keys = set()
        edges = []
        edge_attrs = {}

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
            source_selector = str(rule.get("source", "any")).lower()
            destination_selector = str(rule.get("destination", "any")).lower()
            source_nodes = self._select_rule_nodes(
                rpl_nodes,
                tech=tech,
                selector=source_selector,
            )
            destination_nodes = self._select_rule_nodes(
                rpl_nodes,
                tech=tech,
                selector=destination_selector,
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

                for _, destination in candidates:
                    add_edge(
                        source,
                        destination,
                        {
                            "kind": "rf",
                            "rule": rule_id,
                            "tech": tech,
                        },
                    )

        self.candidate_edges = edges
        self.candidate_edge_attrs = edge_attrs
        return list(self.candidate_edges)

    def candidate_graph(
        self,
        candidate_edges: Iterable[tuple[str, str]] | None = None,
    ):
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
            attrs = self._edge_attrs(src, dst)
            graph.add_edge(src, dst, **attrs)

        return graph

    def node_by_site(self) -> dict[str, SiteNode]:
        return dict(self.nodes)

    def compile_metric_specs_by_tech(
        self,
        metric_specs_by_tech: Mapping[str, str | Callable[[Mapping[str, Any]], float]],
    ) -> dict[str, Callable[[Mapping[str, Any]], float]]:
        compiled = {}

        for tech, spec in metric_specs_by_tech.items():
            if callable(spec):
                compiled[tech] = spec
                continue

            spec_text = self._metric_spec_text(spec)
            compiled[tech] = mc.compile_metric_spec(spec_text)

        self.metric_functions_by_tech = compiled
        return dict(compiled)

    async def compute_edge_metrics(
        self,
        geo,
        candidate_edges: Iterable[tuple[str, str]] | None = None,
        *,
        metric_specs_by_tech: Mapping[str, str | Callable[[Mapping[str, Any]], float]] | None = None,
        manage_geo: bool = False,
    ) -> dict[tuple[str, str], float]:
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

                metric = float(metric_function(metric_record))
                edge_metrics[(src, dst)] = metric
                edge_features[(src, dst)] = metric_record

        finally:
            if manage_geo:
                await geo.close()

        self.candidate_edges = candidate_edges
        self.edge_metrics = edge_metrics
        self.edge_features = edge_features
        return dict(edge_metrics)

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
                src_antenna,
                include_power=True,
            ),
            "rx": self._metric_terminal_record(
                dst_node,
                dst_antenna,
                include_power=False,
            ),
            "features": dict(features),
        }

    @staticmethod
    def _metric_terminal_record(
        node: RPLNode,
        antenna: AntennaSpec,
        *,
        include_power: bool,
    ) -> dict[str, Any]:
        record = {
            "ant_gain": float(antenna.gain_dbi),
            "ant_height": float(node.extra["mount_height_m"]),
            "ant_type": str(antenna.kind),
        }

        if antenna.model is not None:
            record["ant_name"] = str(antenna.model)

        if include_power:
            record["pw"] = float(node.extra["tx_power_dbm"])

        return record

    def run_rpl(
        self,
        edge_metrics: Mapping[tuple[str, str], float] | None = None,
    ) -> GeoRPL:
        if edge_metrics is None:
            edge_metrics = self.edge_metrics

        if not edge_metrics:
            raise RuntimeError("No edge metrics were computed")

        rpl = GeoRPL()
        rpl.set_nodes(self.rpl_nodes())
        rpl.set_edge_metrics(edge_metrics)
        rpl.run_RPL()

        self.rpl = rpl
        return rpl

    def rpl_result_counts(self) -> dict[str, int]:
        if self.rpl is None:
            raise RuntimeError("RPL was not run")

        return {
            "nodes": self.rpl.G_res.number_of_nodes(),
            "edges": self.rpl.G_res.number_of_edges(),
        }

    def _node_from_profile_record(
        self,
        site_id: str,
        record: Mapping[str, Any],
        profile_id: str,
        *,
        resolve: bool,
        validate: bool,
        tolerance_m: float,
    ) -> SiteNode:
        if profile_id not in self.device_profiles:
            raise ValueError(f"Unknown device profile: {profile_id}")

        profile = self.device_profiles[profile_id]
        connected = parse_bool(profile.get("connected"), False)
        can_route = parse_bool(profile.get("can_route"), False)
        rank = float(profile.get("rank", 0.0 if connected else inf))
        mount_height_m = optional_float(
            first_value(
                record,
                "mount_height_m",
                "ant_h",
                "antenna_height_m",
            )
        )
        if mount_height_m is None:
            mount_height_m = optional_float(profile.get("mount_height_m"))

        site = Site(
            site_id=site_id,
            lat=optional_float(optional_value(record, "lat")),
            lon=optional_float(optional_value(record, "lon")),
            x=optional_float(optional_value(record, "x")),
            y=optional_float(optional_value(record, "y")),
            kind=str(record.get("kind", profile.get("kind", "field"))),
            extra={
                key: value
                for key, value in record.items()
                if key not in self.DEFAULT_RECORD_KEYS
            },
        )

        device_id = make_device_id(site_id, 0)
        device = Device(
            device_id=device_id,
            site_id=site_id,
            connected=connected,
            can_route=can_route,
            rank=rank,
            mount_height_m=mount_height_m,
            extra={"device_profile": profile_id},
        )

        interfaces = []
        for interface_index, interface_profile_id in enumerate(
            profile.get("interfaces", [])
        ):
            if not isinstance(interface_profile_id, str):
                raise TypeError(
                    f"Device profile {profile_id!r} interfaces must be "
                    "profile names"
                )
            if interface_profile_id not in self.interface_profiles:
                raise ValueError(
                    f"Unknown interface profile: {interface_profile_id}"
                )

            interface_profile = self.interface_profiles[interface_profile_id]
            antenna_id = str(required_value(interface_profile, "antenna_id"))
            if antenna_id not in self.antenna_catalog:
                raise ValueError(
                    f"Unknown antenna_id for interface profile "
                    f"{interface_profile_id}: {antenna_id}"
                )

            interfaces.append(
                RadioInterface(
                    interface_id=make_interface_id(
                        site_id,
                        0,
                        interface_index,
                    ),
                    device_id=device_id,
                    site_id=site_id,
                    tech=str(required_value(interface_profile, "tech")),
                    freq_mhz=float(
                        required_value(interface_profile, "freq_mhz")
                    ),
                    tx_power_dbm=float(
                        required_value(interface_profile, "tx_power_dbm")
                    ),
                    antenna_id=antenna_id,
                    can_relay=parse_bool(
                        interface_profile.get("can_relay"),
                        False,
                    ),
                    medium=str(interface_profile.get("medium", "rf")),
                    extra={"interface_profile": interface_profile_id},
                )
            )

        if not interfaces:
            raise ValueError(
                f"Device profile {profile_id!r} requires at least one "
                "interface"
            )

        node = SiteNode(
            site=site,
            devices=[device],
            interfaces=interfaces,
        )

        if resolve:
            node.resolve_position(
                self.working_crs,
                validate=validate,
                tolerance_m=tolerance_m,
            )

        return node

    def _node_from_record(
        self,
        site_id: str,
        record: Mapping[str, Any],
        *,
        resolve: bool,
        validate: bool,
        tolerance_m: float,
    ) -> SiteNode:
        connected = parse_bool(
            optional_value(record, "connected"),
            False,
        )
        can_route = parse_bool(optional_value(record, "can_route"), False)
        can_relay = parse_bool(
            optional_value(record, "can_relay"),
            connected,
        )
        rank = float(optional_value(record, "rank", 0.0 if connected else inf))
        mount_height_m = optional_float(
            first_value(
                record,
                "mount_height_m",
                "ant_h",
                "antenna_height_m",
            )
        )

        node = FieldSite.with_default_interface(
            site_id=site_id,
            lat=optional_float(optional_value(record, "lat")),
            lon=optional_float(optional_value(record, "lon")),
            x=optional_float(optional_value(record, "x")),
            y=optional_float(optional_value(record, "y")),
            tech=str(required_value(record, "tech")),
            freq_mhz=float(required_value(record, "freq_mhz")),
            tx_power_dbm=float(required_value(record, "tx_power_dbm")),
            mount_height_m=mount_height_m,
            antenna_id=str(required_value(record, "antenna_id")),
            connected=connected,
            can_route=can_route,
            can_relay=can_relay,
            rank=rank,
            working_crs=self.working_crs,
            resolve=resolve,
            validate=validate,
            tolerance_m=tolerance_m,
            extra={
                key: value
                for key, value in record.items()
                if key not in self.DEFAULT_RECORD_KEYS
            },
        )

        if node.interfaces[0].antenna_id not in self.antenna_catalog:
            raise ValueError(
                f"Unknown antenna_id for {site_id}: "
                f"{node.interfaces[0].antenna_id}"
            )

        return node

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
                parse_bool(
                    node.extra.get("device_can_route"),
                    False,
                )
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
    def _antenna_from_config(config: Mapping[str, Any]) -> AntennaSpec:
        config = dict(config)
        known_keys = {
            "kind",
            "model",
            "gain_dbi",
            "height_m",
            "azimuth_deg",
            "beamwidth_deg",
            "max_links",
        }
        kwargs = {
            key: config[key]
            for key in known_keys
            if key in config
        }
        kwargs["extra"] = {
            key: value
            for key, value in config.items()
            if key not in known_keys
        }
        return AntennaSpec(**kwargs)

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
    def _records(records: Iterable[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
        if hasattr(records, "to_dict"):
            records = records.to_dict("records")

        return [
            dict(record)
            if isinstance(record, Mapping)
            else dict(record._asdict())
            if hasattr(record, "_asdict")
            else dict(vars(record))
            for record in records
        ]

    @staticmethod
    def _record_site_id(record: Mapping[str, Any]) -> str | None:
        for key in ("site_id", "position_id", "id", "name"):
            value = record.get(key)
            if not is_blank(value):
                return str(value).strip()
        return None

    @staticmethod
    def _clean_id(value: Any) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("ID cannot be empty")
        return value


