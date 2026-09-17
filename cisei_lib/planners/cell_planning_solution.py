from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from cisei_lib.planners.planning_solution import PlanningSolution


class CellPlanningSolution(PlanningSolution):
    """
    Cell-planning report wrapper around a serialized planning result.

    The base ``PlanningSolution`` owns reports that apply to any solved network.
    This specialization adds reports whose meaning is specific to tower/cell
    planning: client service assignment and load per serving cell interface.

    The class consumes the same JSON-ready result artifact as
    ``PlanningSolution``. It does not rebuild candidate edges, recompute
    metrics or rerun RPL.
    """

    @classmethod
    def from_result(cls, result: Mapping[str, Any]) -> "CellPlanningSolution":
        """Create a cell solution wrapper from an exported result dictionary."""
        cls._validate_result(result)
        return cls(result=result)

    def evaluation_payload(
        self,
        *,
        rank_threshold: float,
        primary_tech: str = "lte",
    ) -> dict[str, Any]:
        """
        Return generic evaluation plus cell-specific final reports.

        ``primary_tech`` selects the cell access technology to report. The
        payload keeps the generic ``service`` key for compatibility and adds a
        ``cell`` block for cell-specific reports.
        """
        payload = super().evaluation_payload(
            rank_threshold=rank_threshold,
            primary_tech=primary_tech,
        )
        payload["cell"] = {
            "client_service": self.client_service_table(
                primary_tech=primary_tech,
            ),
            "cell_load": self.cell_load_table(primary_tech=primary_tech),
        }
        return payload

    def client_service_table(
        self,
        *,
        primary_tech: str = "lte",
    ) -> list[dict[str, Any]]:
        """
        Return one final service row per unconnected cell-client interface.

        This is the cell-planning vocabulary for the generic service table.
        Each row reports the client site/interface, whether it is served, the
        selected serving cell interface/site, and the final path rank.
        """
        rows = []
        for row in self.service_table(primary_tech=primary_tech):
            rows.append(
                {
                    "client_site_id": row.get("site_id"),
                    "client_node_id": row.get("node_id"),
                    "client_tech": row.get("tech"),
                    "served": row.get("served"),
                    "cell_node_id": row.get("parent"),
                    "cell_site_id": row.get("parent_site_id"),
                    "cell_tech": row.get("parent_tech"),
                    "selected_metric": row.get("metric"),
                    "rank": row.get("rank"),
                }
            )
        return rows

    def cell_load_table(
        self,
        *,
        primary_tech: str = "lte",
    ) -> list[dict[str, Any]]:
        """
        Return final served-client counts per connected cell interface.

        Connected interfaces using ``primary_tech`` are included even when no
        clients selected them, making empty sectors/towers visible in final
        reports.
        """
        node_records = self._merged_node_records()
        client_rows = self.client_service_table(primary_tech=primary_tech)
        load_by_cell = Counter(
            row["cell_node_id"]
            for row in client_rows
            if row.get("served") and row.get("cell_node_id") is not None
        )

        rows = []
        for node_id, record in node_records.items():
            if not record.get("connected") or record.get("tech") != primary_tech:
                continue
            rows.append(
                {
                    "cell_site_id": record.get("site_id"),
                    "cell_node_id": node_id,
                    "cell_tech": record.get("tech"),
                    "antenna_id": record.get("antenna_id"),
                    "freq_mhz": record.get("freq_mhz"),
                    "served_clients": load_by_cell.get(node_id, 0),
                }
            )

        rows.sort(key=lambda row: (str(row["cell_site_id"]), str(row["cell_node_id"])))
        return rows
