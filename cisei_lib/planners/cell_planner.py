from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import atan2, degrees, inf, sqrt
from typing import Any

from cisei_lib.planners.graph_planner import GraphPlanner
from cisei_lib.planners.planning_scenario import PlanningScenario


class CellPlanner:
    """
    Tower/cell based planning specialization over GraphPlanner.

    The first implemented stage is direct cell planning: connected cell sites
    offer candidate links to client sites for one primary technology. Optional
    tower selection and relay recovery are intentionally left as later stages.

    Typical usage:
    1. Build the planner from a complete ``PlanningScenario``.
    2. Build the geometric candidate graph with ``build_primary_candidates``.
    3. Optionally inspect/adjust sectors with ``sector_candidate_table``,
       ``suggest_sector_rotation`` and ``prune_empty_sectors``.
    4. Compute edge metrics with ``compute_metrics``.
    5. Run RPL with ``run_rpl`` and inspect service with ``service_table``.

    The wrapped GraphPlanner remains the source of truth for sites, devices,
    interfaces, candidate edges, metrics and RPL state. CellPlanner only adds
    cell-specific construction policy such as sector-aware edge selection.
    """

    # Construction -----------------------------------------------------------

    def __init__(
        self,
        scenario: PlanningScenario | GraphPlanner,
        *,
        primary_tech: str = "lte",
    ) -> None:
        """
        Create a cell planner around a complete planning scenario.

        ``scenario`` may be a ``PlanningScenario`` or an already initialized
        ``GraphPlanner``. The scenario is expected to include site instances
        with their final ``device_profile`` assignments. CellPlanner does not
        load TOML, read CSV files or assign profiles.
        """
        self.graph = (
            scenario
            if isinstance(scenario, GraphPlanner)
            else GraphPlanner(scenario)
        )
        self.primary_tech = primary_tech
        self.sector_rotation_by_site: dict[str, float] = {}

    # Candidate graph construction -----------------------------------------

    def build_primary_candidates(
        self,
        *,
        sector_aware: bool = True,
        limit_m: float | None = None,
        degree: int | None = None,
        rule: str = "cell_primary",
    ) -> list[tuple[str, str]]:
        """
        Build direct cell-to-client candidate edges for the primary technology.

        Sources are connected interfaces using ``primary_tech``. Destinations
        are unconnected interfaces using the same technology. When
        ``sector_aware`` is true, sector antenna azimuth and beamwidth are used
        as a hard geometric filter before any expensive geo/metric evaluation.

        ``limit_m`` optionally rejects long links. ``degree`` optionally keeps
        only the nearest N candidate clients per source interface.
        """
        if degree is not None and degree < 0:
            raise ValueError("degree must be >= 0")
        if limit_m is not None and limit_m < 0:
            raise ValueError("limit_m must be >= 0")

        sources = [
            node
            for node in self.graph.rpl_nodes
            if node.connected and node.extra.get("tech") == self.primary_tech
        ]
        destinations = [
            node
            for node in self.graph.rpl_nodes
            if not node.connected and node.extra.get("tech") == self.primary_tech
        ]

        edges = []
        attrs = {}

        for source in sources:
            candidates = []
            for destination in destinations:
                if not self.graph._compatible(source, destination):
                    continue

                distance_m = self.graph._distance_m(source, destination)
                if limit_m is not None and distance_m > limit_m:
                    continue

                bearing_deg = self._bearing_deg(source.pos_utm, destination.pos_utm)
                if sector_aware and not self._inside_antenna(source, bearing_deg):
                    continue

                candidates.append((distance_m, bearing_deg, destination))

            candidates.sort(key=lambda item: (item[0], item[2].node_id))
            if degree is not None:
                candidates = candidates[:degree]

            for distance_m, bearing_deg, destination in candidates:
                edge = (source.node_id, destination.node_id)
                edges.append(edge)
                attrs[edge] = {
                    "kind": "rf",
                    "tech": self.primary_tech,
                    "rule": rule,
                    "distance_m": distance_m,
                    "bearing_deg": bearing_deg,
                }

        return self.graph.set_candidate_edges(edges, attrs_by_edge=attrs)

    # Sector tuning ----------------------------------------------------------

    def tune_sectors(
        self,
        *,
        site_ids: Iterable[str] | None = None,
        apply_rotation: bool = False,
        prune_empty: bool = False,
        step_deg: float = 5.0,
        uncovered_weight: float = 10.0,
        imbalance_weight: float = 1.0,
        sector_aware: bool = True,
        limit_m: float | None = None,
        degree: int | None = None,
    ) -> dict[str, Any]:
        """
        Run the standard pre-metric sector tuning workflow.

        The method is designed for notebooks and API handlers that need a
        compact, repeatable tuning step before expensive geo/metric evaluation.
        It computes a rotation suggestion, optionally applies that rotation,
        reports empty sectors, optionally prunes them, rebuilds candidates when
        a mutation is applied, and returns the final sector report.

        ``site_ids`` filters the operation to selected cell sites. It does not
        define which sites are cells. Cell sites are always inferred from the
        scenario's connected primary-tech interfaces.

        ``sector_aware``, ``limit_m`` and ``degree`` are passed to
        ``build_primary_candidates`` when the candidate graph must be rebuilt.
        """
        rotation = self.suggest_sector_rotation(
            site_ids=site_ids,
            step_deg=step_deg,
            uncovered_weight=uncovered_weight,
            imbalance_weight=imbalance_weight,
            apply=apply_rotation,
        )

        if apply_rotation:
            self.build_primary_candidates(
                sector_aware=sector_aware,
                limit_m=limit_m,
                degree=degree,
            )

        prune = self.prune_empty_sectors(site_ids=site_ids, apply=False)
        if prune_empty:
            self.prune_empty_sectors(site_ids=site_ids, apply=True)
            self.build_primary_candidates(
                sector_aware=sector_aware,
                limit_m=limit_m,
                degree=degree,
            )
            prune = self.prune_empty_sectors(site_ids=site_ids, apply=False)

        return {
            "rotation": rotation,
            "prune": prune,
            "sectors": self.sector_candidate_table(),
            "candidate_edges": len(self.graph.candidate_edges),
        }

    def suggest_sector_rotation(
        self,
        *,
        site_ids: Iterable[str] | None = None,
        step_deg: float = 5.0,
        uncovered_weight: float = 10.0,
        imbalance_weight: float = 1.0,
        apply: bool = False,
    ):
        """
        Search a common azimuth rotation for each selected cell site's sectors.

        ``site_ids`` is an optional operation filter. It does not define which
        sites are cells; cell sites come from the scenario's connected
        primary-tech interfaces.

        The score is:
        ``uncovered_weight * uncovered_ratio + imbalance_weight * imbalance_ratio``.
        ``uncovered_ratio`` is the fraction of clients not covered by any
        sector from that site. ``imbalance_ratio`` is the sector load standard
        deviation divided by mean load. Empty sectors are reported but are not
        directly penalized; use ``prune_empty_sectors`` to remove them.

        When ``apply=False`` the method only returns a report. When
        ``apply=True`` it stores the suggested rotation, after which the
        candidate graph should be rebuilt with ``build_primary_candidates``.
        """
        if step_deg <= 0:
            raise ValueError("step_deg must be > 0")
        if uncovered_weight < 0:
            raise ValueError("uncovered_weight must be >= 0")
        if imbalance_weight < 0:
            raise ValueError("imbalance_weight must be >= 0")

        rows = []
        for site_id in self._selected_site_ids(site_ids):
            sectors = self._cell_sector_nodes(site_id)
            if len(sectors) < 2:
                continue

            clients = self._primary_client_nodes()
            if not clients:
                continue

            current_rotation = self.sector_rotation_by_site.get(site_id, 0.0)
            current = self._rotation_score(
                sectors,
                clients,
                current_rotation,
                uncovered_weight=uncovered_weight,
                imbalance_weight=imbalance_weight,
            )

            best = current
            steps = int(360.0 / step_deg)
            for index in range(steps):
                rotation = (index * step_deg) % 360.0
                candidate = self._rotation_score(
                    sectors,
                    clients,
                    rotation,
                    uncovered_weight=uncovered_weight,
                    imbalance_weight=imbalance_weight,
                )
                if candidate["score"] < best["score"]:
                    best = candidate

            if apply:
                self.sector_rotation_by_site[site_id] = best["rotation_deg"]

            rows.append(
                {
                    "cell_site_id": site_id,
                    "uncovered_weight": uncovered_weight,
                    "imbalance_weight": imbalance_weight,
                    "current_rotation_deg": current_rotation,
                    "suggested_rotation_deg": best["rotation_deg"],
                    "applied": apply,
                    "score_before": current["score"],
                    "score_after": best["score"],
                    "uncovered_ratio_before": current["uncovered_ratio"],
                    "uncovered_ratio_after": best["uncovered_ratio"],
                    "imbalance_ratio_before": current["imbalance_ratio"],
                    "imbalance_ratio_after": best["imbalance_ratio"],
                    "covered_before": current["covered_clients"],
                    "covered_after": best["covered_clients"],
                    "uncovered_before": current["uncovered_clients"],
                    "uncovered_after": best["uncovered_clients"],
                    "empty_sectors_before": current["empty_sectors"],
                    "empty_sectors_after": best["empty_sectors"],
                    "loads_before": current["loads"],
                    "loads_after": best["loads"],
                    "azimuths_before": current["azimuths"],
                    "azimuths_after": best["azimuths"],
                }
            )

        return self._table(rows)

    def prune_empty_sectors(
        self,
        *,
        site_ids: Iterable[str] | None = None,
        apply: bool = False,
    ):
        """
        Report, and optionally remove, sector interfaces with no candidates.

        The method uses the current candidate graph. Call
        ``build_primary_candidates`` first, then inspect this report. With
        ``apply=True``, empty sector interfaces are removed from their device
        definitions and stale candidate/metric/RPL state is cleared.

        ``site_ids`` is an optional operation filter. It does not define which
        sites are cells.
        """
        candidate_by_source = self._candidate_edges_by_source()
        selected_site_ids = set(self._selected_site_ids(site_ids))

        rows = []
        sector_node_ids = []
        for sector in self._primary_cell_nodes():
            site_id = sector.extra.get("site_id")
            if site_id not in selected_site_ids:
                continue

            candidate_count = len(candidate_by_source.get(sector.node_id, []))
            remove = candidate_count == 0
            if remove:
                sector_node_ids.append(sector.node_id)

            rows.append(
                {
                    "cell_site_id": site_id,
                    "sector_node_id": sector.node_id,
                    "antenna_id": sector.extra.get("antenna_id"),
                    "azimuth_deg": self._effective_azimuth(sector),
                    "candidate_clients": candidate_count,
                    "remove": remove,
                    "applied": apply and remove,
                }
            )

        if apply and sector_node_ids:
            self.remove_sector_interfaces(sector_node_ids)

        return self._table(rows)

    def remove_sector_interfaces(
        self,
        sector_node_ids: Iterable[str],
    ) -> list[str]:
        """
        Remove specific sector interface nodes from the underlying graph model.

        ``sector_node_ids`` are interface node ids such as ``torre:d0:i1``.
        This is a manual editing helper for notebooks and specialized planners.
        It does not try to infer replacement sectors. If anything is removed,
        candidate edges, metrics, extracted features and RPL results are cleared
        because they may reference the removed interfaces.
        """
        remove_ids = {str(node_id) for node_id in sector_node_ids}
        removed = []

        for site_node in self.graph.nodes.values():
            kept = []
            for interface in site_node.interfaces:
                if interface.interface_id in remove_ids:
                    removed.append(interface.interface_id)
                else:
                    kept.append(interface)
            site_node.interfaces = kept

        if removed:
            self.graph.candidate_edges = []
            self.graph.candidate_edge_attrs = {}
            self.graph.edge_metrics = {}
            self.graph.edge_features = {}
            self.graph.rpl = None

        return removed

    def apply_sector_rotation(
        self,
        rotations_by_site: Mapping[str, float],
    ) -> None:
        """
        Manually set sector rotation offsets by site id.

        The values are degrees added to every sector antenna azimuth at the
        site. Rebuild the candidate graph after changing rotations.
        """
        for site_id, rotation in rotations_by_site.items():
            self.sector_rotation_by_site[GraphPlanner._clean_id(site_id)] = (
                float(rotation) % 360.0
            )

    def clear_sector_rotations(
        self,
        site_ids: Iterable[str] | None = None,
    ) -> None:
        """
        Clear manually or automatically applied sector rotations.

        With no arguments all rotations are cleared. With ``site_ids``, only
        those sites are reset. Rebuild candidates after clearing.
        """
        if site_ids is None:
            self.sector_rotation_by_site.clear()
            return

        for site_id in site_ids:
            self.sector_rotation_by_site.pop(
                GraphPlanner._clean_id(site_id),
                None,
            )

    # Reports ----------------------------------------------------------------

    def sector_candidate_table(self):
        """
        Return a per-sector report from the current candidate graph.

        Use this immediately after ``build_primary_candidates`` to inspect
        sector coverage, empty sectors, active rotations and candidate client
        site ids before running geo feature extraction.
        """
        rows = []
        edge_table = self._candidate_edges_by_source()

        for sector in self._primary_cell_nodes():
            antenna = self.graph.antenna_catalog.get(sector.extra.get("antenna_id"))
            site_id = sector.extra.get("site_id")
            candidates = edge_table.get(sector.node_id, [])
            rows.append(
                {
                    "cell_site_id": site_id,
                    "sector_node_id": sector.node_id,
                    "antenna_id": sector.extra.get("antenna_id"),
                    "antenna_kind": (
                        antenna.kind if antenna is not None else None
                    ),
                    "antenna_model_id": (
                        antenna.model_id if antenna is not None else None
                    ),
                    "antenna_description": (
                        antenna.description if antenna is not None else None
                    ),
                    "antenna_gain_dbi": (
                        antenna.gain_dbi if antenna is not None else None
                    ),
                    "antenna_height_m": (
                        antenna.height_m if antenna is not None else None
                    ),
                    "azimuth_deg": self._effective_azimuth(sector),
                    "base_azimuth_deg": (
                        antenna.azimuth_deg if antenna is not None else None
                    ),
                    "rotation_deg": self.sector_rotation_by_site.get(site_id, 0.0),
                    "beamwidth_deg": (
                        antenna.beamwidth_deg if antenna is not None else None
                    ),
                    "freq_mhz": sector.extra.get("freq_mhz"),
                    "tx_power_dbm": sector.extra.get("tx_power_dbm"),
                    "candidate_clients": len(candidates),
                    "candidate_sites": [
                        edge["dst_site"]
                        for edge in candidates
                    ],
                }
            )

        return self._table(rows)

    # Execution --------------------------------------------------------------

    async def compute_metrics(
        self,
        geo,
        *,
        metric_specs_by_tech: Mapping[str, Any] | None = None,
        manage_geo: bool = False,
    ):
        """
        Compute edge metrics for the current candidate graph.

        ``geo`` is usually an AsyncGeoServicePool or compatible object supplied
        by the notebook/API layer. CellPlanner delegates metric compilation to
        GraphPlanner, preserving the generic tech-to-metric mapping.
        """
        return await self.graph.compute_edge_metrics(
            geo,
            metric_specs_by_tech=metric_specs_by_tech,
            manage_geo=manage_geo,
        )

    def run_rpl(self):
        """
        Run RPL over the current metric graph and return the RPL result object.

        Call this after candidate edges have metrics. Connected cell interfaces
        become the available backbone targets for the DODAG.
        """
        return self.graph.run_rpl()

    def service_table(self):
        """
        Return the direct service result for primary-tech client interfaces.

        The table reports each client interface, whether it was served, its
        selected parent interface/site, the selected edge metric and final rank.
        """
        rows = []
        rpl = self.graph.rpl
        graph = rpl.G_res if rpl is not None else None

        for node in self.graph.rpl_nodes:
            if node.connected or node.extra.get("tech") != self.primary_tech:
                continue

            parent = None
            metric = None
            rank = inf
            if graph is not None and node.node_id in graph:
                data = graph.nodes[node.node_id]
                parent = data.get("parent")
                rank = data.get("rank", inf)
                if parent is not None and graph.has_edge(node.node_id, parent):
                    metric = graph.edges[node.node_id, parent].get("metric")

            parent_site_id = None
            parent_tech = None
            if parent is not None and graph is not None and parent in graph:
                parent_extra = graph.nodes[parent].get("extra", {})
                parent_site_id = parent_extra.get("site_id")
                parent_tech = parent_extra.get("tech")

            rows.append(
                {
                    "site_id": node.extra.get("site_id"),
                    "node_id": node.node_id,
                    "tech": node.extra.get("tech"),
                    "served": parent is not None,
                    "parent": parent,
                    "parent_site_id": parent_site_id,
                    "parent_tech": parent_tech,
                    "metric": metric,
                    "rank": rank,
                }
            )

        return self._table(rows)

    # Internal sector helpers ------------------------------------------------

    def _inside_antenna(self, node, bearing_deg: float) -> bool:
        """
        Return whether a bearing is accepted by a cell interface.

        Non-sector antennas accept every bearing. Sector antennas use their
        effective azimuth, including any site rotation, and beamwidth as a
        geometric pre-filter before expensive geo feature extraction.
        """
        # Only sector antennas constrain candidate visibility for now.
        antenna_id = node.extra.get("antenna_id")
        antenna = self.graph.antenna_catalog.get(antenna_id)
        if antenna is None or str(antenna.kind).lower() != "sector":
            return True
        if antenna.azimuth_deg is None or antenna.beamwidth_deg is None:
            return True

        delta = abs((bearing_deg - self._effective_azimuth(node) + 180.0) % 360.0 - 180.0)
        return delta <= antenna.beamwidth_deg / 2.0

    def _effective_azimuth(self, node) -> float | None:
        """
        Return the antenna azimuth after applying the site rotation offset.

        The base azimuth lives in the antenna profile. CellPlanner stores
        rotations separately so sectors can be tuned without rewriting the
        scenario definition.
        """
        antenna_id = node.extra.get("antenna_id")
        antenna = self.graph.antenna_catalog.get(antenna_id)
        if antenna is None or antenna.azimuth_deg is None:
            return None

        site_id = node.extra.get("site_id")
        rotation = self.sector_rotation_by_site.get(site_id, 0.0)
        return (float(antenna.azimuth_deg) + rotation) % 360.0

    def _selected_site_ids(
        self,
        site_ids: Iterable[str] | None,
    ) -> list[str]:
        """
        Normalize an optional operation filter to cleaned cell site ids.

        With no explicit selection, all cell sites inferred from the scenario
        are returned.
        """
        if site_ids is None:
            return sorted(self._infer_cell_site_ids())
        return sorted(GraphPlanner._clean_id(site_id) for site_id in site_ids)

    def _infer_cell_site_ids(self) -> set[str]:
        """
        Infer cell sites from connected interfaces using the primary technology.

        A site is considered a cell site when at least one interface node is
        connected and uses ``self.primary_tech``.
        """
        return {
            str(node.extra.get("site_id"))
            for node in self.graph.rpl_nodes
            if node.connected
            and node.extra.get("tech") == self.primary_tech
            and node.extra.get("site_id") is not None
        }

    def _primary_cell_nodes(self):
        """
        Return connected RPL interface nodes using the primary technology.

        These nodes are candidate sources for direct cell service.
        """
        return [
            node
            for node in self.graph.rpl_nodes
            if node.connected and node.extra.get("tech") == self.primary_tech
        ]

    def _primary_client_nodes(self):
        """
        Return unconnected RPL interface nodes using the primary technology.

        These nodes are candidate destinations for direct cell service.
        """
        return [
            node
            for node in self.graph.rpl_nodes
            if not node.connected and node.extra.get("tech") == self.primary_tech
        ]

    def _cell_sector_nodes(self, site_id: str):
        """
        Return primary connected interfaces belonging to one cell site.

        The method does not require ``kind == "sector"`` because fallback tests
        may use omni interfaces in a cell profile.
        """
        return [
            node
            for node in self._primary_cell_nodes()
            if node.extra.get("site_id") == site_id
        ]

    def _rotation_score(
        self,
        sectors,
        clients,
        rotation_deg: float,
        *,
        uncovered_weight: float,
        imbalance_weight: float,
    ) -> dict[str, Any]:
        """
        Score one common sector rotation against all primary client nodes.

        Coverage is based on geometry and interface compatibility. The score
        combines uncovered-client ratio and sector-load imbalance using the
        weights supplied by ``suggest_sector_rotation``.
        """
        loads = [0 for _ in sectors]
        covered_clients = set()
        azimuths = []

        for sector_index, sector in enumerate(sectors):
            antenna = self.graph.antenna_catalog.get(sector.extra.get("antenna_id"))
            if antenna is None or antenna.azimuth_deg is None:
                azimuths.append(None)
                continue

            azimuth = (float(antenna.azimuth_deg) + rotation_deg) % 360.0
            azimuths.append(azimuth)

            for client in clients:
                if not self.graph._compatible(sector, client):
                    continue
                bearing = self._bearing_deg(sector.pos_utm, client.pos_utm)
                if self._bearing_inside(azimuth, antenna.beamwidth_deg, bearing):
                    loads[sector_index] += 1
                    covered_clients.add(client.node_id)

        uncovered = len(clients) - len(covered_clients)
        empty = sum(1 for load in loads if load == 0)
        imbalance = self._stdev(loads)
        uncovered_ratio = uncovered / len(clients) if clients else 0.0
        mean_load = sum(loads) / len(loads) if loads else 0.0
        imbalance_ratio = imbalance / mean_load if mean_load > 0 else 0.0
        score = (
            uncovered_weight * uncovered_ratio
            + imbalance_weight * imbalance_ratio
        )

        return {
            "rotation_deg": rotation_deg,
            "score": score,
            "loads": loads,
            "azimuths": azimuths,
            "covered_clients": len(covered_clients),
            "uncovered_clients": uncovered,
            "uncovered_ratio": uncovered_ratio,
            "imbalance_ratio": imbalance_ratio,
            "empty_sectors": empty,
        }

    # Internal geometry and statistics helpers -------------------------------

    @staticmethod
    def _bearing_inside(
        azimuth_deg: float | None,
        beamwidth_deg: float | None,
        bearing_deg: float,
    ) -> bool:
        """
        Return whether ``bearing_deg`` falls inside an azimuth/beamwidth sector.

        Missing azimuth or beamwidth means the interface is unconstrained for
        candidate selection.
        """
        if azimuth_deg is None or beamwidth_deg is None:
            return True

        delta = abs((bearing_deg - azimuth_deg + 180.0) % 360.0 - 180.0)
        return delta <= beamwidth_deg / 2.0

    @staticmethod
    def _stdev(values: list[int]) -> float:
        """
        Return the population standard deviation for sector load counts.
        """
        if not values:
            return 0.0

        mean = sum(values) / len(values)
        return sqrt(sum((value - mean) ** 2 for value in values) / len(values))

    def _candidate_edges_by_source(self) -> dict[str, list[dict[str, Any]]]:
        """
        Group current candidate edges by source node id for sector reports.

        Each grouped edge includes the destination site id plus the edge
        attributes stored by GraphPlanner.
        """
        by_source: dict[str, list[dict[str, Any]]] = {}
        rpl_by_id = {node.node_id: node for node in self.graph.rpl_nodes}

        for src, dst in self.graph.candidate_edges:
            attrs = self.graph._edge_attrs(src, dst)
            dst_node = rpl_by_id[dst]
            by_source.setdefault(src, []).append(
                {
                    "dst": dst,
                    "dst_site": dst_node.extra.get("site_id"),
                    **attrs,
                }
            )

        return by_source

    @staticmethod
    def _bearing_deg(
        source_pos: tuple[float, float] | None,
        destination_pos: tuple[float, float] | None,
    ) -> float:
        """
        Return azimuth bearing in degrees from source to destination.

        Positions are projected ``(x, y)`` coordinates in the planner working
        CRS. The convention is 0 degrees north and positive clockwise.
        """
        if source_pos is None or destination_pos is None:
            raise ValueError("Projected positions are required")

        dx = destination_pos[0] - source_pos[0]
        dy = destination_pos[1] - source_pos[1]
        if dx == 0 and dy == 0:
            return 0.0

        return (degrees(atan2(dx, dy)) + 360.0) % 360.0

    # Internal record adapters -----------------------------------------------

    @staticmethod
    def _table(rows: list[dict[str, Any]]):
        """
        Return a pandas DataFrame when pandas is available, else raw rows.
        """
        try:
            import pandas as pd
        except ImportError:
            return rows

        return pd.DataFrame(rows)
