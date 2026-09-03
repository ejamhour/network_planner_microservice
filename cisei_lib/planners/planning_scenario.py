from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from copy import deepcopy
from math import inf, isfinite
from pathlib import Path
from typing import Any

from cisei_lib.planners.planner_classes import (
    AntennaSpec,
    Device,
    RadioInterface,
    Site,
    SiteNode,
    SitePattern,
    make_device_id,
    make_interface_id,
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

    Scenario structure accepted by ``from_config_dict`` / ``from_toml``:

    ``[scenario]``:
        Optional. ``working_crs`` is the projected CRS used by planning, such
        as ``"EPSG:31982"``. If site instances are provided with ``x``/``y``
        only, callers must supply or serialize a working CRS.

    ``[antennas.<profile_id>]``:
        Optional profile catalog. Required fields are intentionally minimal.
        ``kind`` defaults to ``"omni"``, ``gain_dbi`` defaults to ``0.0`` and
        ``height_m`` defaults to ``7.0``. Optional recognized fields include
        ``model_id`` for the antenna library id, ``description`` for a
        human-readable note, ``azimuth_deg``, ``downtilt_deg``,
        ``beamwidth_deg`` and side-pole shadow fields. Extra keys are
        preserved in ``AntennaSpec.extra``.

    ``[interfaces.<profile_id>]``:
        Interface profile catalog. Required fields: ``tech``, ``freq_mhz``,
        ``tx_power_dbm`` and ``antenna_id``. Optional fields include
        ``can_relay`` and ``medium``. Extra keys are preserved.

    ``[devices.<profile_id>]``:
        Device profile catalog. Required field: ``interfaces`` as a list of
        interface profile ids. Optional fields include ``connected``,
        ``can_route``, ``rank``, ``mount_height_m`` and arbitrary extra keys.

    ``[metrics.<tech>]``:
        Optional metric spec mapping by technology. The value can be a mapping
        such as ``{spec = "lte_tower_node"}``, a formula/spec string, or any
        object later understood by GraphPlanner.

    ``[instances]``:
        Optional companion instance table metadata. ``source`` points to a CSV
        file, normally beside the TOML file. ``profile_column`` defaults to
        ``"device_profile"``. If the TOML has this section and the CSV exists,
        ``from_toml(..., load_instances=True)`` imports the rows and creates
        concrete site nodes from the referenced device profiles.

    ``[[net.connectivity]]``:
        Optional automatic candidate-generation rules. GraphPlanner will
        interpret these later. Typical fields are ``kind``, ``tech``,
        ``source``, ``destination``, ``degree`` and ``limit``.

    ``[[net.candidate_edges]]``:
        Optional manual candidate edges. Required fields: ``src`` and ``dst``,
        both interface node ids such as ``"torre:d0:i1"``. Extra fields become
        edge attributes. ``origin`` defaults to ``"manual"`` when added through
        the Python API.

    ``[[sites]]``:
        Optional concrete instances. Required field: ``site_id`` plus either
        ``lat``/``lon`` or ``x``/``y`` if positions should be usable by
        planning. Each site may contain ``[[sites.devices]]`` and
        ``[[sites.interfaces]]`` arrays with concrete device/interface records.
        Concrete interface records require ``interface_id``, ``device_id``,
        ``tech``, ``freq_mhz``, ``tx_power_dbm`` and ``antenna_id``.

    Typical notebook flow:
        Create profiles, instantiate sites from a DataFrame using
        ``add_node_instances(...)`` or add explicit ``SiteNode`` objects,
        optionally add manual candidate edges, export to TOML/CSV for review,
        then pass the scenario to GraphPlanner once that integration is added.
    """

    def __init__(self, *, working_crs: str | None = None) -> None:
        """
        Create an empty scenario.

        Parameters
        ----------
        working_crs:
            Optional projected CRS used for all generated or validated
            positions. Use values such as ``"EPSG:31982"``. If omitted, callers
            may still define profiles and unresolved site records, but projected
            distance-based planning will need a CRS later.
        """
        self.working_crs = working_crs
        self.antenna_profiles: dict[str, AntennaSpec] = {}
        self.interface_profiles: dict[str, dict[str, Any]] = {}
        self.device_profiles: dict[str, dict[str, Any]] = {}
        self.site_nodes: dict[str, SiteNode] = {}
        self.connectivity_rules: dict[str, dict[str, Any]] = {}
        self.metric_specs_by_tech: dict[str, Any] = {}
        self.candidate_edges: list[tuple[str, str]] = []
        self.candidate_edge_attrs: dict[tuple[str, str], dict[str, Any]] = {}
        self.instance_source: dict[str, Any] = {}

    @classmethod
    def from_config_dict(
        cls,
        config: Mapping[str, Any],
        *,
        working_crs: str | None = None,
    ) -> "PlanningScenario":
        """
        Build a scenario from a standard Python configuration dictionary.

        This method is the canonical loader. TOML loading parses the file to a
        dictionary and delegates here, so a notebook, API request, JSON payload
        or TOML file all follow the same validation path.

        Parameters
        ----------
        config:
            Mapping with optional top-level sections ``scenario``, ``antennas``,
            ``interfaces``, ``devices``, ``metrics``, ``net`` and ``sites``.
            Required fields inside each section are documented in the class
            docstring.
        working_crs:
            Optional override for ``config["scenario"]["working_crs"]``.
        """
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

        if "instances" in config:
            scenario.set_instance_source(config["instances"])

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
        load_instances: bool = True,
    ) -> "PlanningScenario":
        """
        Load a scenario from TOML.

        TOML is not a separate execution model. The file is parsed with
        ``tomlkit`` and then passed to ``from_config_dict``.

        Parameters
        ----------
        path:
            TOML file path.
        working_crs:
            Optional CRS override for the value serialized in the file.
        load_instances:
            If true, and the TOML declares ``[instances]`` without embedded
            ``[[sites]]``, load the companion CSV when it exists.
        """
        import tomlkit

        path = Path(path)
        config = tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()
        scenario = cls.from_config_dict(config, working_crs=working_crs)
        if load_instances and not scenario.site_nodes:
            scenario.load_instance_source(path.parent)
        return scenario

    def add_antenna_profile(
        self,
        profile_id: str,
        antenna: AntennaSpec | Mapping[str, Any],
        *,
        update: bool = True,
    ) -> None:
        """
        Add or replace an antenna profile.

        Parameters
        ----------
        profile_id:
            Stable id referenced by interface profiles and concrete interfaces,
            for example ``"lte_client_omni"`` or ``"lte_sector_120"``.
        antenna:
            Either an ``AntennaSpec`` or a mapping accepted by ``AntennaSpec``.
            Recognized keys: ``kind``, ``model_id``, ``description``,
            ``gain_dbi``,
            ``height_m``, ``azimuth_deg``, ``downtilt_deg``,
            ``beamwidth_deg``, ``shadow_azimuth_deg``, ``shadow_width_deg``
            and ``shadow_loss_db``. All other keys are preserved as extra
            metadata.
        update:
            If false, adding an existing profile id raises ``ValueError``.
        """
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
        """
        Remove an antenna profile and return the removed ``AntennaSpec``.

        By default, removal is blocked if an interface profile or concrete
        interface still references the antenna. Use ``force=True`` only when
        the caller is intentionally leaving the scenario temporarily invalid.
        """
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
        """
        Add or replace an interface profile.

        Required profile fields:
        ``tech``:
            Technology identifier used later to select candidate rules and
            metric functions, for example ``"lte"``, ``"wisun"`` or ``"n2n"``.
        ``freq_mhz``:
            RF frequency in MHz. Candidate builders should not connect
            interfaces with different frequencies unless a specialized model
            explicitly supports that.
        ``tx_power_dbm``:
            Transmit power in dBm.
        ``antenna_id``:
            Antenna profile id.

        Optional profile fields:
        ``can_relay``:
            Whether this interface may forward within the RPL graph for its
            own technology. Defaults are interpreted later by consumers.
        ``max_links``:
            Maximum simultaneous planned links for this interface. Omit it for
            unlimited capacity.
        ``medium``:
            Link medium, usually ``"rf"``.

        Extra fields are kept and exported. ``validate()`` will report unknown
        antenna references.
        """
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
        if "max_links" in profile:
            profile["max_links"] = self._max_links_value(profile["max_links"])
        profile["tech"] = str(profile["tech"])
        profile["antenna_id"] = str(profile["antenna_id"])
        self.interface_profiles[profile_id] = profile

    def remove_interface_profile(
        self,
        profile_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Remove an interface profile and return its stored mapping.

        By default, removal is blocked while any device profile references this
        interface profile. Use ``force=True`` only for deliberate intermediate
        edits.
        """
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
        """
        Add or replace a device profile.

        Required profile fields:
        ``interfaces``:
            Non-empty list of interface profile ids. One device may contain one
            or more interfaces.

        Optional profile fields:
        ``connected``:
            True when the device/site is already connected to the backbone or
            to an accepted existing network.
        ``can_route``:
            True when the device can route between its interfaces. Graph
            builders may use this to create internal interface links.
        ``rank``:
            Initial RPL rank. Connected devices normally use finite rank such
            as ``0.0``; unconnected devices normally remain infinite.
        ``mount_height_m``:
            Installation height above terrain/model reference in meters.

        Extra fields are preserved and exported. ``validate()`` will report
        unknown interface profile references.
        """
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
        """
        Remove a device profile and return its stored mapping.

        By default, removal is blocked if concrete devices created from this
        profile still exist and retained their ``device_profile`` metadata.
        """
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
        """
        Register the metric specification used by a technology.

        Parameters
        ----------
        tech:
            Technology key, matching interface ``tech`` values.
        spec:
            Metric declaration. Current GraphPlanner accepts resource names
            such as ``"lte_tower_node"``, inline TOML/formula strings, or
            callables when passed programmatically.
        update:
            If false, adding a metric for an existing technology raises.
        """
        tech = self._clean_id(tech, "technology")
        if tech in self.metric_specs_by_tech and not update:
            raise ValueError(f"Metric spec already exists for tech: {tech}")
        self.metric_specs_by_tech[tech] = deepcopy(spec)

    def remove_metric_spec(self, tech: str) -> Any:
        """Remove and return the metric spec for ``tech``."""
        tech = self._clean_id(tech, "technology")
        return self.metric_specs_by_tech.pop(tech)

    def clear_metric_specs(self) -> None:
        """Remove all technology-to-metric mappings from the scenario."""
        self.metric_specs_by_tech.clear()

    def add_connectivity_rule(
        self,
        rule: Mapping[str, Any],
        *,
        rule_id: str | None = None,
        update: bool = True,
    ) -> str:
        """
        Add an automatic candidate-generation rule.

        The scenario stores the rule; GraphPlanner or a specialization will
        interpret it later.

        Common rule fields:
        ``kind``:
            Usually ``"rf"`` for radio candidate links or ``"internal"`` for
            links between interfaces in the same device.
        ``tech``:
            Required for RF rules. Selects source/destination interfaces by
            technology.
        ``source`` and ``destination``:
            Selectors such as ``"connected"``, ``"unconnected"``, ``"relay"``,
            ``"non_relay"`` or ``"any"``.
        ``degree``:
            Optional nearest-neighbor count.
        ``limit``:
            Optional maximum distance in meters.

        Parameters
        ----------
        rule_id:
            Optional stable id. If omitted, ``connectivity_N`` is generated.
        update:
            If false, adding an existing rule id raises.
        """
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
        """Remove and return one connectivity rule by id."""
        rule_id = self._clean_id(rule_id, "connectivity rule id")
        return self.connectivity_rules.pop(rule_id)

    def clear_connectivity_rules(self) -> None:
        """Remove all automatic candidate-generation rules."""
        self.connectivity_rules.clear()

    def add_site_node(
        self,
        node: SiteNode,
        *,
        update: bool = False,
    ) -> None:
        """
        Add one concrete site node.

        A ``SiteNode`` contains:
        ``site``:
            Physical position and site metadata.
        ``devices``:
            One or more concrete devices installed at the site.
        ``interfaces``:
            Concrete interfaces belonging to the devices. Interface ids are the
            nodes that RPL will later see.

        Use this method when the caller already constructed objects directly.
        For CSV/DataFrame-style rows, prefer ``add_site_records`` with a
        ``SitePattern``.
        """
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
        """Add multiple concrete site nodes."""
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

        Required row fields:
        ``site_id`` / ``position_id`` / ``id`` / ``name``:
            Optional. The first available value becomes the site id. If none is
            present, ``id_prefix_N`` is generated.
        ``lat`` and ``lon`` or ``x`` and ``y``:
            Required when ``resolve=True`` or when later distance-based planning
            will use the site. ``x``/``y`` must already be normalized to the
            scenario ``working_crs``.

        Parameters
        ----------
        pattern:
            ``SitePattern`` that defines the concrete device/interface layout
            created at each row.
        resolve:
            If true, positions are resolved immediately using ``working_crs``.
        validate:
            If true and both geographic and projected coordinates exist, the
            projected values are checked against the CRS within ``tolerance_m``.
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

    def add_site_records_from_profile(
        self,
        records: Iterable[Mapping[str, Any]] | Any,
        *,
        device_profile: str,
        defaults: Mapping[str, Any] | None = None,
        overrides_by_id: Mapping[str, Mapping[str, Any]] | None = None,
        id_prefix: str = "site",
        resolve: bool = True,
        validate: bool = False,
        tolerance_m: float = 1.0,
        update: bool = False,
    ) -> list[SiteNode]:
        """
        Instantiate and add site nodes from records using a device profile.

        This is the preferred high-level notebook/API method. The caller first
        registers antenna, interface and device profiles, then supplies rows
        containing positions and optional per-site overrides.

        Required row fields:
        ``site_id`` / ``position_id`` / ``id`` / ``name``:
            Optional. The first available value becomes the site id. If none is
            present, ``id_prefix_N`` is generated.
        ``lat`` and ``lon`` or ``x`` and ``y``:
            Required when ``resolve=True`` or when the site will be used in
            distance-based planning. ``x``/``y`` must be normalized to
            ``working_crs``.

        Parameters
        ----------
        device_profile:
            Registered device profile id. Its ``interfaces`` list defines the
            concrete interfaces created for every record.
        defaults:
            Optional values applied to every row before row values and
            overrides are merged.
        overrides_by_id:
            Optional mapping keyed by site id. Use it to override profile-level
            fields such as ``connected``, ``can_route``, ``rank``,
            ``mount_height_m`` or ``kind`` for specific sites.
        update:
            If true, existing site ids are replaced.
        """
        device_profile = self._clean_id(device_profile, "device profile id")
        if device_profile not in self.device_profiles:
            raise ValueError(f"Unknown device profile: {device_profile}")

        records = self._records(records)
        defaults = dict(defaults or {})
        overrides_by_id = {
            self._clean_id(site_id, "site id"): dict(override)
            for site_id, override in dict(overrides_by_id or {}).items()
        }

        nodes = []
        used_ids = set()
        generated_count = 0
        for record in records:
            site_id = self._record_site_id(record)
            if site_id is None:
                generated_count += 1
                site_id = f"{id_prefix}_{generated_count}"

            site_id = self._clean_id(site_id, "site id")
            if site_id in used_ids:
                raise ValueError(f"Duplicated site ID: {site_id}")
            used_ids.add(site_id)

            merged = {}
            merged.update(defaults)
            merged.update(record)
            merged.update(overrides_by_id.get(site_id, {}))

            node = self._site_node_from_profile_record(
                site_id,
                merged,
                device_profile,
                resolve=resolve,
                validate=validate,
                tolerance_m=tolerance_m,
            )
            nodes.append(node)

        self.add_site_nodes(nodes, update=update)
        return nodes

    def add_node_instances(
        self,
        records: Iterable[Mapping[str, Any]] | Any,
        *,
        profile_column: str | None = None,
        defaults: Mapping[str, Any] | None = None,
        overrides_by_id: Mapping[str, Mapping[str, Any]] | None = None,
        id_prefix: str = "site",
        resolve: bool = True,
        validate: bool = False,
        tolerance_m: float = 1.0,
        update: bool = False,
    ) -> list[SiteNode]:
        """
        Instantiate site nodes from rows that carry their own device profile.

        This is the standard method for one CSV/DataFrame containing mixed
        site types. Each row must include the profile column, which defaults to
        ``"device_profile"`` or to ``instance_source["profile_column"]`` when
        configured. The profile value must match a registered device profile.

        Required row fields:
        ``device_profile``:
            Device profile id, unless a different ``profile_column`` is passed.
        ``site_id`` / ``position_id`` / ``id`` / ``name``:
            Optional stable site id. If missing, ``id_prefix_N`` is generated.
        ``lat`` and ``lon`` or ``x`` and ``y``:
            Required when positions are resolved for planning.

        Row values may override profile defaults for ``kind``,
        ``mount_height_m``, ``connected``, ``can_route`` and ``rank``. Unknown
        row fields are preserved in ``Site.extra``.
        """
        profile_column = self._instance_profile_column(profile_column)
        records = self._records(records)
        defaults = dict(defaults or {})
        overrides_by_id = {
            self._clean_id(site_id, "site id"): dict(override)
            for site_id, override in dict(overrides_by_id or {}).items()
        }

        nodes = []
        used_ids = set()
        generated_count = 0
        for record in records:
            site_id = self._record_site_id(record)
            if site_id is None:
                generated_count += 1
                site_id = f"{id_prefix}_{generated_count}"

            site_id = self._clean_id(site_id, "site id")
            if site_id in used_ids:
                raise ValueError(f"Duplicated site ID: {site_id}")
            used_ids.add(site_id)

            merged = {}
            merged.update(defaults)
            merged.update(record)
            merged.update(overrides_by_id.get(site_id, {}))

            device_profile = merged.get(profile_column)
            if self._is_blank(device_profile):
                raise ValueError(
                    f"Node instance {site_id!r} requires column "
                    f"{profile_column!r}"
                )

            nodes.append(
                self._site_node_from_profile_record(
                    site_id,
                    merged,
                    self._clean_id(device_profile, "device profile id"),
                    resolve=resolve,
                    validate=validate,
                    tolerance_m=tolerance_m,
                )
            )

        self.add_site_nodes(nodes, update=update)
        return nodes

    def set_instance_source(
        self,
        source: str | Mapping[str, Any] | None = None,
        *,
        format: str = "csv",
        profile_column: str = "device_profile",
    ) -> None:
        """
        Store companion instance-table metadata for TOML serialization.

        ``source`` is normally a CSV filename beside the TOML file. The scenario
        does not need this metadata to hold concrete nodes, but it makes a
        saved TOML bundle reproducible: ``from_toml`` can reload the companion
        CSV and instantiate rows from the registered profiles.
        """
        if isinstance(source, Mapping):
            config = dict(source)
        else:
            config = {}
            if source is not None:
                config["source"] = source
            config["format"] = format
            config["profile_column"] = profile_column

        if not config:
            self.instance_source = {}
            return

        source_value = config.get("source")
        if self._is_blank(source_value):
            raise ValueError("instances.source cannot be empty")

        table_format = str(config.get("format", "csv")).strip().lower()
        if table_format != "csv":
            raise ValueError("Only CSV instance sources are supported")

        profile_column = self._clean_id(
            config.get("profile_column", "device_profile"),
            "instances.profile_column",
        )
        self.instance_source = {
            "source": str(source_value),
            "format": table_format,
            "profile_column": profile_column,
        }

    def load_instance_source(
        self,
        base_dir: str | Path = ".",
        *,
        resolve: bool = True,
        validate: bool = False,
        tolerance_m: float = 1.0,
        update: bool = False,
    ) -> list[SiteNode]:
        """
        Load the configured companion CSV if it exists and instantiate nodes.

        Missing files are treated as "no serialized instances available" and
        return an empty list. Invalid files or invalid rows raise errors.
        """
        if not self.instance_source:
            return []

        source = Path(str(self.instance_source["source"]))
        if not source.is_absolute():
            source = Path(base_dir) / source
        if not source.exists():
            return []

        records = self._read_csv_records(source)
        return self.add_node_instances(
            records,
            profile_column=str(self.instance_source.get("profile_column")),
            resolve=resolve,
            validate=validate,
            tolerance_m=tolerance_m,
            update=update,
        )

    def to_instance_records(self) -> list[dict[str, Any]]:
        """
        Export profile-based node instances as rows suitable for CSV.

        The method serializes the current concrete nodes back to one row per
        site. It expects each site to contain one concrete device created from
        a device profile, which is the standard profile/CSV workflow. More
        complex hand-built sites should be exported with embedded ``[[sites]]``
        instead.
        """
        rows = []
        for node in self.site_nodes.values():
            if len(node.devices) != 1:
                raise ValueError(
                    f"Site {node.site_id!r} cannot be exported as one "
                    "profile-based instance row because it has "
                    f"{len(node.devices)} devices"
                )

            device = node.devices[0]
            device_profile = device.extra.get("device_profile")
            if self._is_blank(device_profile):
                raise ValueError(
                    f"Device {device.device_id!r} does not record a "
                    "device_profile"
                )

            row = {
                "site_id": node.site.site_id,
                "device_profile": str(device_profile),
                "kind": node.site.kind,
                "lat": node.site.lat,
                "lon": node.site.lon,
                "x": node.site.x,
                "y": node.site.y,
                "mount_height_m": device.mount_height_m,
                "connected": device.connected,
                "can_route": device.can_route,
                "rank": device.rank,
            }
            row.update(node.site.extra)
            rows.append(self._drop_none(row))

        return rows

    def instance_table(self):
        """Return profile-based node instances as a pandas DataFrame if available."""
        return self._table(self.to_instance_records())

    def to_instance_csv(self, path: str | Path) -> None:
        """Write profile-based node instances to a CSV file."""
        self._write_csv_records(Path(path), self.to_instance_records())

    def save_bundle(
        self,
        path: str | Path,
        *,
        include_sites: bool = False,
        instance_filename: str | None = None,
    ) -> tuple[Path, Path | None]:
        """
        Save a TOML scenario plus an optional companion instance CSV.

        If the scenario has concrete nodes, they are written to
        ``instance_filename`` or to ``<toml-stem>.nodes.csv`` beside the TOML
        file, and the TOML receives an ``[instances]`` section pointing to that
        CSV. If no nodes exist, only the TOML is written. Set
        ``include_sites=True`` only when you also want full concrete
        ``[[sites]]`` records embedded in the TOML.
        """
        path = Path(path)
        instance_path = None
        if self.site_nodes:
            instance_name = instance_filename or f"{path.stem}.nodes.csv"
            instance_path = path.with_name(instance_name)
            self.to_instance_csv(instance_path)
            self.set_instance_source(
                instance_path.name,
                format="csv",
                profile_column="device_profile",
            )

        self.to_toml(path, include_sites=include_sites)
        return path, instance_path

    def remove_site_node(self, site_id: str) -> SiteNode:
        """
        Remove one concrete site node and any manual candidate edges touching it.

        Candidate edges are interface-to-interface. Removing a site invalidates
        all edges connected to any interface installed at that site, so those
        edges are dropped automatically.
        """
        site_id = self._clean_id(site_id, "site id")
        removed = self.site_nodes.pop(site_id)
        self._drop_candidate_edges_for_site(removed)
        return removed

    def remove_site_nodes(self, site_ids: Iterable[str]) -> list[SiteNode]:
        """Remove multiple site nodes and return them in removal order."""
        return [self.remove_site_node(site_id) for site_id in site_ids]

    def add_candidate_edge(
        self,
        src: str,
        dst: str,
        *,
        attrs: Mapping[str, Any] | None = None,
        update: bool = True,
    ) -> None:
        """
        Add one manual candidate edge.

        Required parameters:
        ``src`` and ``dst``:
            Interface node ids, not site ids. Example:
            ``"torre:d0:i1"`` to ``"cliente:d0:i0"``.

        Optional ``attrs``:
            Edge metadata exported under ``[[net.candidate_edges]]``. Common
            fields are ``kind``, ``tech``, ``rule``, ``distance_m`` or any
            application-specific annotation. ``origin`` defaults to
            ``"manual"``.

        This edge means RPL may use the link. It does not force RPL to choose
        it in the final planned network.
        """
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
        """
        Add multiple manual candidate edges.

        ``default_attrs`` are applied to every edge. ``attrs_by_edge`` can
        override or extend attributes per edge. Undirected duplicate lookup is
        used, so ``("a", "b")`` and ``("b", "a")`` refer to the same
        candidate edge.
        """
        attrs_by_edge = attrs_by_edge or {}
        default_attrs = dict(default_attrs or {})

        for src, dst in edges:
            attrs = dict(default_attrs)
            attrs.update(attrs_by_edge.get((src, dst), {}))
            attrs.update(attrs_by_edge.get((dst, src), {}))
            self.add_candidate_edge(src, dst, attrs=attrs, update=update)

    def remove_candidate_edge(self, src: str, dst: str) -> tuple[str, str]:
        """Remove one manual candidate edge and return its stored orientation."""
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
        """Remove multiple manual candidate edges."""
        return [self.remove_candidate_edge(src, dst) for src, dst in edges]

    def clear_candidate_edges(self) -> None:
        """Remove all manual candidate edges and their attributes."""
        self.candidate_edges.clear()
        self.candidate_edge_attrs.clear()

    def validate(self) -> list[str]:
        """
        Return configuration problems without raising.

        Validation checks cross references:
        - interface profiles reference known antenna profiles;
        - device profiles reference known interface profiles;
        - concrete interfaces reference known antenna profiles;
        - manual candidate edge endpoints reference concrete interface ids.

        An empty list means the scenario is internally consistent for the
        checks implemented here. GraphPlanner may still perform stricter checks
        when it computes candidates or metrics.
        """
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
        """
        Export the scenario as a plain Python configuration dictionary.

        Parameters
        ----------
        include_sites:
            When true, concrete site/device/interface instances are included
            under ``sites``. When false, only reusable profiles, metrics,
            connectivity rules and manual candidate edges are exported.
        """
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
        if self.instance_source:
            config["instances"] = deepcopy(self.instance_source)

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
        """
        Serialize the scenario to TOML and optionally write it to disk.

        The returned TOML is intended to be readable by users and by a future
        custom GPT that generates planning scenarios. Empty top-level catalogs
        are omitted.
        """
        import tomlkit

        text = tomlkit.dumps(self.to_config_dict(include_sites=include_sites))
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    def profile_table(self, kind: str):
        """
        Return profile catalogs as a pandas DataFrame when pandas is available.

        ``kind`` accepts ``"antennas"``, ``"interfaces"`` or ``"devices"``
        and singular aliases. Without pandas, the method returns a list of
        dictionaries.
        """
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
        """
        Return concrete site/device/interface instances as an inspection table.

        Each row represents one concrete interface with its parent site and
        device fields expanded. This is useful before exporting or handing the
        scenario to GraphPlanner.
        """
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
        """
        Return manual candidate edges and attributes as an inspection table.

        This table only contains scenario-defined candidate edges. Automatic
        candidate edges generated later by GraphPlanner are not stored here.
        """
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

    def _site_node_from_profile_record(
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
        connected = self._bool_value(
            record.get("connected", profile.get("connected")),
            False,
        )
        can_route = self._bool_value(
            record.get("can_route", profile.get("can_route")),
            False,
        )
        rank_value = record.get("rank", profile.get("rank"))
        rank = float(rank_value) if not self._is_blank(rank_value) else (
            0.0 if connected else inf
        )

        mount_height_m = self._optional_float(
            self._first_value(record, "mount_height_m", "ant_h", "antenna_height_m")
        )
        if mount_height_m is None:
            mount_height_m = self._optional_float(profile.get("mount_height_m"))

        known_record_keys = {
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
            "connected",
            "can_route",
            "rank",
            "device_profile",
        }
        site = Site(
            site_id=site_id,
            lat=self._optional_float(record.get("lat")),
            lon=self._optional_float(record.get("lon")),
            x=self._optional_float(record.get("x")),
            y=self._optional_float(record.get("y")),
            kind=str(record.get("kind", profile.get("kind", "field"))),
            extra={
                key: value
                for key, value in record.items()
                if key not in known_record_keys
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
        for index, interface_profile_id in enumerate(profile["interfaces"]):
            if interface_profile_id not in self.interface_profiles:
                raise ValueError(
                    f"Device profile {profile_id!r} references unknown "
                    f"interface profile {interface_profile_id!r}"
                )

            interface_profile = self.interface_profiles[interface_profile_id]
            interfaces.append(
                RadioInterface(
                    interface_id=make_interface_id(site_id, 0, index),
                    device_id=device_id,
                    site_id=site_id,
                    tech=str(interface_profile["tech"]),
                    freq_mhz=float(interface_profile["freq_mhz"]),
                    tx_power_dbm=float(interface_profile["tx_power_dbm"]),
                    antenna_id=str(interface_profile["antenna_id"]),
                    can_relay=self._bool_value(
                        interface_profile.get("can_relay"),
                        False,
                    ),
                    medium=str(interface_profile.get("medium", "rf")),
                    max_links=self._max_links_value(
                        interface_profile.get("max_links")
                    ),
                    extra={"interface_profile": interface_profile_id},
                )
            )

        node = SiteNode(site=site, devices=[device], interfaces=interfaces)
        if resolve:
            node.resolve_position(
                self.working_crs,
                validate=validate,
                tolerance_m=tolerance_m,
            )
        return node

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
                "max_links",
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
                    max_links=PlanningScenario._max_links_value(
                        interface_config.get("max_links")
                    ),
                    extra={
                        key: value
                        for key, value in interface_config.items()
                        if key not in known_interface_keys
                    },
                )
            )

        return SiteNode(site=site, devices=devices, interfaces=interfaces)

    def _instance_profile_column(self, profile_column: str | None) -> str:
        if profile_column is None:
            profile_column = self.instance_source.get(
                "profile_column",
                "device_profile",
            )
        return self._clean_id(profile_column, "profile column")

    @staticmethod
    def _antenna_from_config(config: Mapping[str, Any]) -> AntennaSpec:
        config = dict(config)
        known_keys = {
            "kind",
            "model_id",
            "description",
            "gain_dbi",
            "height_m",
            "azimuth_deg",
            "downtilt_deg",
            "beamwidth_deg",
            "shadow_azimuth_deg",
            "shadow_width_deg",
            "shadow_loss_db",
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
                and not (
                    key == "max_links"
                    and isinstance(item, (int, float))
                    and not isfinite(float(item))
                )
            }
        if isinstance(value, list):
            return [PlanningScenario._drop_none(item) for item in value]
        return value

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
    def _read_csv_records(path: Path) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    @staticmethod
    def _write_csv_records(path: Path, records: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        preferred = [
            "site_id",
            "device_profile",
            "kind",
            "lat",
            "lon",
            "x",
            "y",
            "mount_height_m",
            "connected",
            "can_route",
            "rank",
        ]
        fieldnames = [
            key
            for key in preferred
            if any(key in record for record in records)
        ]
        for record in records:
            for key in record:
                if key not in fieldnames:
                    fieldnames.append(key)

        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                writer.writerow(
                    {
                        key: "" if record.get(key) is None else record.get(key)
                        for key in fieldnames
                    }
                )

    @staticmethod
    def _record_site_id(record: Mapping[str, Any]) -> str | None:
        for key in ("site_id", "position_id", "id", "name"):
            value = record.get(key)
            if not PlanningScenario._is_blank(value):
                return str(value).strip()
        return None

    @staticmethod
    def _first_value(record: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = record.get(key)
            if not PlanningScenario._is_blank(value):
                return value
        return None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if PlanningScenario._is_blank(value):
            return None
        return PlanningScenario._finite_float(value, "numeric field")

    @staticmethod
    def _max_links_value(value: Any) -> float:
        if PlanningScenario._is_blank(value):
            return inf
        if isinstance(value, str) and value.strip().lower() in {
            "inf",
            "+inf",
            "infinity",
            "+infinity",
            "unlimited",
        }:
            return inf
        if isinstance(value, (int, float)) and not isfinite(float(value)):
            if float(value) > 0:
                return inf
            raise ValueError("max_links must be positive or unlimited")

        max_links = PlanningScenario._finite_float(value, "max_links")
        if max_links <= 0:
            raise ValueError("max_links must be positive or unlimited")
        return max_links

    @staticmethod
    def _bool_value(value: Any, default: bool = False) -> bool:
        if PlanningScenario._is_blank(value):
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
