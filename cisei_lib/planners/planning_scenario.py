from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from math import isfinite
from pathlib import Path
from typing import Any

from cisei_lib.planners.planner_classes import (
    AntennaSpec,
    Device,
    RadioInterface,
    Site,
    SiteNode,
    SitePattern,
)


class PlanningScenario:
    """
    User-facing preparation layer for network planning scenarios.

    The scenario owns editable input state only: profiles, site/device/interface
    instances, connectivity rules, manual candidate edges and metric specs. It
    does not compute geo features, edge metrics, RPL results or planned
    networks.

    TOML is treated as serialization. Loading from TOML calls the same public
    methods available to notebooks and APIs.
    """

    def __init__(self, *, working_crs: str | None = None) -> None:
        self.working_crs = working_crs
        self.antenna_profiles: dict[str, AntennaSpec] = {}
        self.interface_profiles: dict[str, dict[str, Any]] = {}
        self.device_profiles: dict[str, dict[str, Any]] = {}
        self.site_nodes: dict[str, SiteNode] = {}
        self.connectivity_rules: dict[str, dict[str, Any]] = {}
        self.metric_specs_by_tech: dict[str, Any] = {}
        self.candidate_edges: list[tuple[str, str]] = []
        self.candidate_edge_attrs: dict[tuple[str, str], dict[str, Any]] = {}

    @classmethod
    def from_config_dict(
        cls,
        config: Mapping[str, Any],
        *,
        working_crs: str | None = None,
    ) -> "PlanningScenario":
        scenario_config = dict(config.get("scenario", {}))
        scenario = cls(
            working_crs=working_crs or scenario_config.get("working_crs")
        )

        for profile_id, antenna_config in config.get("antennas", {}).items():
            scenario.add_antenna_profile(
                str(profile_id),
                scenario._antenna_from_config(antenna_config),
            )

        for profile_id, profile in config.get("interfaces", {}).items():
            scenario.add_interface_profile(str(profile_id), profile)

        for profile_id, profile in config.get("devices", {}).items():
            scenario.add_device_profile(str(profile_id), profile)

        for tech, metric_config in config.get("metrics", {}).items():
            scenario.add_metric_spec(str(tech), metric_config)

        net_config = dict(config.get("net", {}))
        for index, rule in enumerate(net_config.get("connectivity", [])):
            scenario.add_connectivity_rule(rule, rule_id=f"connectivity_{index}")

        for edge in net_config.get("candidate_edges", []):
            edge = dict(edge)
            src = edge.pop("src")
            dst = edge.pop("dst")
            scenario.add_candidate_edge(src, dst, attrs=edge)

        for site_config in config.get("sites", []):
            scenario.add_site_node(scenario._site_node_from_config(site_config))

        return scenario

    @classmethod
    def from_toml(
        cls,
        path: str | Path,
        *,
        working_crs: str | None = None,
    ) -> "PlanningScenario":
        import tomlkit

        config = tomlkit.parse(Path(path).read_text(encoding="utf-8")).unwrap()
        return cls.from_config_dict(config, working_crs=working_crs)

    def add_antenna_profile(
        self,
        profile_id: str,
        antenna: AntennaSpec | Mapping[str, Any],
        *,
        update: bool = True,
    ) -> None:
        profile_id = self._clean_id(profile_id, "antenna profile id")
        if profile_id in self.antenna_profiles and not update:
            raise ValueError(f"Antenna profile already exists: {profile_id}")

        if not isinstance(antenna, AntennaSpec):
            antenna = self._antenna_from_config(antenna)

        self.antenna_profiles[profile_id] = antenna

    def remove_antenna_profile(
        self,
        profile_id: str,
        *,
        force: bool = False,
    ) -> AntennaSpec:
        profile_id = self._clean_id(profile_id, "antenna profile id")
        if not force:
            users = self._antenna_profile_users(profile_id)
            if users:
                raise ValueError(
                    f"Antenna profile {profile_id!r} is still used by "
                    f"{users}"
                )

        return self.antenna_profiles.pop(profile_id)

    def add_interface_profile(
        self,
        profile_id: str,
        profile: Mapping[str, Any],
        *,
        update: bool = True,
    ) -> None:
        profile_id = self._clean_id(profile_id, "interface profile id")
        if profile_id in self.interface_profiles and not update:
            raise ValueError(f"Interface profile already exists: {profile_id}")

        profile = dict(profile)
        for key in ("tech", "freq_mhz", "tx_power_dbm", "antenna_id"):
            if self._is_blank(profile.get(key)):
                raise ValueError(
                    f"Interface profile {profile_id!r} requires {key!r}"
                )

        profile["freq_mhz"] = self._finite_float(profile["freq_mhz"], "freq_mhz")
        profile["tx_power_dbm"] = self._finite_float(
            profile["tx_power_dbm"],
            "tx_power_dbm",
        )
        profile["tech"] = str(profile["tech"])
        profile["antenna_id"] = str(profile["antenna_id"])
        self.interface_profiles[profile_id] = profile

    def remove_interface_profile(
        self,
        profile_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        profile_id = self._clean_id(profile_id, "interface profile id")
        if not force:
            users = self._interface_profile_users(profile_id)
            if users:
                raise ValueError(
                    f"Interface profile {profile_id!r} is still used by "
                    f"{users}"
                )

        return self.interface_profiles.pop(profile_id)

    def add_device_profile(
        self,
        profile_id: str,
        profile: Mapping[str, Any],
        *,
        update: bool = True,
    ) -> None:
        profile_id = self._clean_id(profile_id, "device profile id")
        if profile_id in self.device_profiles and not update:
            raise ValueError(f"Device profile already exists: {profile_id}")

        profile = dict(profile)
        interfaces = list(profile.get("interfaces", []))
        if not interfaces:
            raise ValueError(
                f"Device profile {profile_id!r} requires at least one interface"
            )
        if not all(isinstance(item, str) and item.strip() for item in interfaces):
            raise TypeError(
                f"Device profile {profile_id!r} interfaces must be profile ids"
            )

        profile["interfaces"] = [str(item).strip() for item in interfaces]
        self.device_profiles[profile_id] = profile

    def remove_device_profile(
        self,
        profile_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        profile_id = self._clean_id(profile_id, "device profile id")
        if not force:
            users = self._device_profile_users(profile_id)
            if users:
                raise ValueError(
                    f"Device profile {profile_id!r} is still used by {users}"
                )

        return self.device_profiles.pop(profile_id)

    def add_metric_spec(
        self,
        tech: str,
        spec: Any,
        *,
        update: bool = True,
    ) -> None:
        tech = self._clean_id(tech, "technology")
        if tech in self.metric_specs_by_tech and not update:
            raise ValueError(f"Metric spec already exists for tech: {tech}")
        self.metric_specs_by_tech[tech] = deepcopy(spec)

    def remove_metric_spec(self, tech: str) -> Any:
        tech = self._clean_id(tech, "technology")
        return self.metric_specs_by_tech.pop(tech)

    def clear_metric_specs(self) -> None:
        self.metric_specs_by_tech.clear()

    def add_connectivity_rule(
        self,
        rule: Mapping[str, Any],
        *,
        rule_id: str | None = None,
        update: bool = True,
    ) -> str:
        rule = dict(rule)
        rule_id = (
            self._clean_id(rule_id, "connectivity rule id")
            if rule_id is not None
            else self._next_rule_id()
        )
        if rule_id in self.connectivity_rules and not update:
            raise ValueError(f"Connectivity rule already exists: {rule_id}")

        self.connectivity_rules[rule_id] = rule
        return rule_id

    def remove_connectivity_rule(self, rule_id: str) -> dict[str, Any]:
        rule_id = self._clean_id(rule_id, "connectivity rule id")
        return self.connectivity_rules.pop(rule_id)

    def clear_connectivity_rules(self) -> None:
        self.connectivity_rules.clear()

    def add_site_node(
        self,
        node: SiteNode,
        *,
        update: bool = False,
    ) -> None:
        if not isinstance(node, SiteNode):
            raise TypeError("node must be a SiteNode")

        site_id = self._clean_id(node.site_id, "site id")
        if site_id in self.site_nodes and not update:
            raise ValueError(f"Site node already exists: {site_id}")

        self.site_nodes[site_id] = node

    def add_site_nodes(
        self,
        nodes: Iterable[SiteNode],
        *,
        update: bool = False,
    ) -> None:
        for node in nodes:
            self.add_site_node(node, update=update)

    def add_site_records(
        self,
        records: Iterable[Mapping[str, Any]] | Any,
        *,
        pattern: SitePattern,
        id_prefix: str | None = None,
        resolve: bool = True,
        validate: bool = False,
        tolerance_m: float = 1.0,
        update: bool = False,
    ) -> list[SiteNode]:
        """
        Instantiate and add site nodes from record rows using a SitePattern.

        File reading stays outside the scenario. A notebook can read CSV into a
        DataFrame and pass it here with the pattern that defines the devices and
        interfaces to install at each position.
        """
        if not isinstance(pattern, SitePattern):
            raise TypeError("pattern must be a SitePattern")

        nodes = pattern.instantiate_many(
            records,
            id_prefix=id_prefix,
            working_crs=self.working_crs,
            resolve=resolve,
            validate=validate,
            tolerance_m=tolerance_m,
        )
        self.add_site_nodes(nodes, update=update)
        return nodes

    def remove_site_node(self, site_id: str) -> SiteNode:
        site_id = self._clean_id(site_id, "site id")
        removed = self.site_nodes.pop(site_id)
        self._drop_candidate_edges_for_site(removed)
        return removed

    def remove_site_nodes(self, site_ids: Iterable[str]) -> list[SiteNode]:
        return [self.remove_site_node(site_id) for site_id in site_ids]

    def add_candidate_edge(
        self,
        src: str,
        dst: str,
        *,
        attrs: Mapping[str, Any] | None = None,
        update: bool = True,
    ) -> None:
        src = self._clean_id(src, "candidate edge source")
        dst = self._clean_id(dst, "candidate edge destination")
        if src == dst:
            raise ValueError("Candidate edge endpoints must be different")

        edge = (src, dst)
        existing = self._candidate_edge_key(src, dst)
        attrs = dict(attrs or {})
        attrs.setdefault("origin", "manual")

        if existing is not None:
            if not update:
                raise ValueError(f"Candidate edge already exists: {existing}")
            self.candidate_edge_attrs[existing].update(attrs)
            return

        self.candidate_edges.append(edge)
        self.candidate_edge_attrs[edge] = attrs

    def add_candidate_edges(
        self,
        edges: Iterable[tuple[str, str]],
        *,
        attrs_by_edge: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
        default_attrs: Mapping[str, Any] | None = None,
        update: bool = True,
    ) -> None:
        attrs_by_edge = attrs_by_edge or {}
        default_attrs = dict(default_attrs or {})

        for src, dst in edges:
            attrs = dict(default_attrs)
            attrs.update(attrs_by_edge.get((src, dst), {}))
            attrs.update(attrs_by_edge.get((dst, src), {}))
            self.add_candidate_edge(src, dst, attrs=attrs, update=update)

    def remove_candidate_edge(self, src: str, dst: str) -> tuple[str, str]:
        src = self._clean_id(src, "candidate edge source")
        dst = self._clean_id(dst, "candidate edge destination")
        edge = self._candidate_edge_key(src, dst)
        if edge is None:
            raise KeyError(f"Candidate edge does not exist: {(src, dst)}")

        self.candidate_edges.remove(edge)
        self.candidate_edge_attrs.pop(edge, None)
        return edge

    def remove_candidate_edges(
        self,
        edges: Iterable[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        return [self.remove_candidate_edge(src, dst) for src, dst in edges]

    def clear_candidate_edges(self) -> None:
        self.candidate_edges.clear()
        self.candidate_edge_attrs.clear()

    def validate(self) -> list[str]:
        errors = []

        for profile_id, profile in self.interface_profiles.items():
            antenna_id = str(profile.get("antenna_id"))
            if antenna_id not in self.antenna_profiles:
                errors.append(
                    f"Interface profile {profile_id!r} references unknown "
                    f"antenna profile {antenna_id!r}"
                )

        for profile_id, profile in self.device_profiles.items():
            for interface_profile_id in profile.get("interfaces", []):
                if interface_profile_id not in self.interface_profiles:
                    errors.append(
                        f"Device profile {profile_id!r} references unknown "
                        f"interface profile {interface_profile_id!r}"
                    )

        antenna_ids = set(self.antenna_profiles)
        interface_ids = set()
        for node in self.site_nodes.values():
            for interface in node.interfaces:
                interface_ids.add(interface.interface_id)
                if interface.antenna_id not in antenna_ids:
                    errors.append(
                        f"Interface {interface.interface_id!r} references "
                        f"unknown antenna profile {interface.antenna_id!r}"
                    )

        for src, dst in self.candidate_edges:
            if src not in interface_ids:
                errors.append(f"Candidate edge source is unknown: {src!r}")
            if dst not in interface_ids:
                errors.append(f"Candidate edge destination is unknown: {dst!r}")

        return errors

    def to_config_dict(self, *, include_sites: bool = True) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if self.working_crs is not None:
            config["scenario"] = {"working_crs": self.working_crs}

        if self.antenna_profiles:
            config["antennas"] = {
                profile_id: self._drop_none(antenna.to_dict())
                for profile_id, antenna in self.antenna_profiles.items()
            }
        if self.interface_profiles:
            config["interfaces"] = deepcopy(self.interface_profiles)
        if self.device_profiles:
            config["devices"] = deepcopy(self.device_profiles)
        if self.metric_specs_by_tech:
            config["metrics"] = deepcopy(self.metric_specs_by_tech)

        net = {}
        if self.connectivity_rules:
            net["connectivity"] = [
                deepcopy(rule)
                for rule in self.connectivity_rules.values()
            ]
        if self.candidate_edges:
            net["candidate_edges"] = [
                {
                    "src": src,
                    "dst": dst,
                    **deepcopy(self.candidate_edge_attrs.get((src, dst), {})),
                }
                for src, dst in self.candidate_edges
            ]
        if net:
            config["net"] = net

        if include_sites and self.site_nodes:
            config["sites"] = [
                self._site_node_to_config(node)
                for node in self.site_nodes.values()
            ]

        return config

    def to_toml(
        self,
        path: str | Path | None = None,
        *,
        include_sites: bool = True,
    ) -> str:
        import tomlkit

        text = tomlkit.dumps(self.to_config_dict(include_sites=include_sites))
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    def profile_table(self, kind: str):
        kind = kind.lower()
        if kind in {"antenna", "antennas"}:
            rows = []
            for profile_id, antenna in self.antenna_profiles.items():
                row = {"profile_id": profile_id}
                row.update(self._drop_none(antenna.to_dict()))
                rows.append(row)
            return self._table(rows)

        if kind in {"interface", "interfaces"}:
            return self._table(
                [
                    {"profile_id": profile_id, **deepcopy(profile)}
                    for profile_id, profile in self.interface_profiles.items()
                ]
            )

        if kind in {"device", "devices"}:
            return self._table(
                [
                    {"profile_id": profile_id, **deepcopy(profile)}
                    for profile_id, profile in self.device_profiles.items()
                ]
            )

        raise ValueError("kind must be 'antennas', 'interfaces', or 'devices'")

    def node_table(self):
        rows = []
        for node in self.site_nodes.values():
            site = node.site.to_record()
            site.pop("geometry", None)
            site.pop("pos", None)
            devices = {device.device_id: device for device in node.devices}
            for interface in node.interfaces:
                device = devices.get(interface.device_id)
                row = dict(site)
                if device is not None:
                    row.update(device.to_dict())
                row.update(interface.to_dict())
                rows.append(row)
        return self._table(rows)

    def candidate_edge_table(self):
        return self._table(
            [
                {
                    "src": src,
                    "dst": dst,
                    **deepcopy(self.candidate_edge_attrs.get((src, dst), {})),
                }
                for src, dst in self.candidate_edges
            ]
        )

    def _next_rule_id(self) -> str:
        index = 0
        while f"connectivity_{index}" in self.connectivity_rules:
            index += 1
        return f"connectivity_{index}"

    def _candidate_edge_key(self, src: str, dst: str) -> tuple[str, str] | None:
        key = frozenset((src, dst))
        for edge in self.candidate_edges:
            if frozenset(edge) == key:
                return edge
        return None

    def _drop_candidate_edges_for_site(self, node: SiteNode) -> None:
        interface_ids = {interface.interface_id for interface in node.interfaces}
        kept = []
        for edge in self.candidate_edges:
            if edge[0] in interface_ids or edge[1] in interface_ids:
                self.candidate_edge_attrs.pop(edge, None)
                continue
            kept.append(edge)
        self.candidate_edges = kept

    def _antenna_profile_users(self, profile_id: str) -> list[str]:
        users = []
        for interface_profile_id, profile in self.interface_profiles.items():
            if str(profile.get("antenna_id")) == profile_id:
                users.append(f"interface profile {interface_profile_id}")

        for node in self.site_nodes.values():
            for interface in node.interfaces:
                if interface.antenna_id == profile_id:
                    users.append(f"interface {interface.interface_id}")

        return users

    def _interface_profile_users(self, profile_id: str) -> list[str]:
        users = []
        for device_profile_id, profile in self.device_profiles.items():
            if profile_id in profile.get("interfaces", []):
                users.append(f"device profile {device_profile_id}")
        return users

    def _device_profile_users(self, profile_id: str) -> list[str]:
        users = []
        for node in self.site_nodes.values():
            for device in node.devices:
                if device.extra.get("device_profile") == profile_id:
                    users.append(f"device {device.device_id}")
        return users

    @staticmethod
    def _site_node_to_config(node: SiteNode) -> dict[str, Any]:
        site = node.site.to_record()
        site.pop("geometry", None)
        site.pop("pos", None)
        return {
            **PlanningScenario._drop_none(site),
            "devices": [
                PlanningScenario._drop_none(device.to_dict())
                for device in node.devices
            ],
            "interfaces": [
                PlanningScenario._drop_none(interface.to_dict())
                for interface in node.interfaces
            ],
        }

    @staticmethod
    def _site_node_from_config(config: Mapping[str, Any]) -> SiteNode:
        config = dict(config)
        devices_config = list(config.pop("devices", []))
        interfaces_config = list(config.pop("interfaces", []))

        known_site_keys = {"site_id", "kind", "lat", "lon", "x", "y"}
        site_id = PlanningScenario._clean_id(
            config.get("site_id"),
            "site id",
        )
        site = Site(
            site_id=site_id,
            kind=str(config.get("kind", "field")),
            lat=(
                float(config["lat"])
                if not PlanningScenario._is_blank(config.get("lat"))
                else None
            ),
            lon=(
                float(config["lon"])
                if not PlanningScenario._is_blank(config.get("lon"))
                else None
            ),
            x=(
                float(config["x"])
                if not PlanningScenario._is_blank(config.get("x"))
                else None
            ),
            y=(
                float(config["y"])
                if not PlanningScenario._is_blank(config.get("y"))
                else None
            ),
            extra={
                key: value
                for key, value in config.items()
                if key not in known_site_keys
            },
        )

        devices = []
        for device_config in devices_config:
            device_config = dict(device_config)
            known_device_keys = {
                "device_id",
                "site_id",
                "connected",
                "can_route",
                "rank",
                "mount_height_m",
            }
            devices.append(
                Device(
                    device_id=PlanningScenario._clean_id(
                        device_config.get("device_id"),
                        "device id",
                    ),
                    site_id=PlanningScenario._clean_id(
                        device_config.get("site_id", site_id),
                        "site id",
                    ),
                    connected=bool(device_config.get("connected", False)),
                    can_route=bool(device_config.get("can_route", False)),
                    rank=float(device_config.get("rank", float("inf"))),
                    mount_height_m=(
                        float(device_config["mount_height_m"])
                        if not PlanningScenario._is_blank(
                            device_config.get("mount_height_m")
                        )
                        else None
                    ),
                    extra={
                        key: value
                        for key, value in device_config.items()
                        if key not in known_device_keys
                    },
                )
            )

        interfaces = []
        for interface_config in interfaces_config:
            interface_config = dict(interface_config)
            known_interface_keys = {
                "interface_id",
                "device_id",
                "site_id",
                "tech",
                "freq_mhz",
                "tx_power_dbm",
                "antenna_id",
                "can_relay",
                "medium",
            }
            interfaces.append(
                RadioInterface(
                    interface_id=PlanningScenario._clean_id(
                        interface_config.get("interface_id"),
                        "interface id",
                    ),
                    device_id=PlanningScenario._clean_id(
                        interface_config.get("device_id"),
                        "device id",
                    ),
                    site_id=PlanningScenario._clean_id(
                        interface_config.get("site_id", site_id),
                        "site id",
                    ),
                    tech=str(interface_config["tech"]),
                    freq_mhz=float(interface_config["freq_mhz"]),
                    tx_power_dbm=float(interface_config["tx_power_dbm"]),
                    antenna_id=str(interface_config["antenna_id"]),
                    can_relay=bool(interface_config.get("can_relay", True)),
                    medium=str(interface_config.get("medium", "rf")),
                    extra={
                        key: value
                        for key, value in interface_config.items()
                        if key not in known_interface_keys
                    },
                )
            )

        return SiteNode(site=site, devices=devices, interfaces=interfaces)

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
    def _drop_none(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: PlanningScenario._drop_none(item)
                for key, item in value.items()
                if item is not None
            }
        if isinstance(value, list):
            return [PlanningScenario._drop_none(item) for item in value]
        return value

    @staticmethod
    def _clean_id(value: Any, name: str) -> str:
        if value is None:
            raise ValueError(f"{name} cannot be empty")
        value = str(value).strip()
        if not value:
            raise ValueError(f"{name} cannot be empty")
        return value

    @staticmethod
    def _finite_float(value: Any, name: str) -> float:
        value = float(value)
        if not isfinite(value):
            raise ValueError(f"{name} must be finite")
        return value

    @staticmethod
    def _is_blank(value: Any) -> bool:
        if value is None:
            return True
        try:
            if value != value:
                return True
        except TypeError:
            pass
        return isinstance(value, str) and not value.strip()

    @staticmethod
    def _table(rows: list[dict[str, Any]]):
        try:
            import pandas as pd
        except ImportError:
            return rows
        return pd.DataFrame(rows)
