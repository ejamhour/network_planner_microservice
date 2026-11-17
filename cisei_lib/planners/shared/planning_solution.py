from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


@dataclass
class PlanningSolution:
    """
    Read-only analysis wrapper around a serialized planning result.

    This class consumes the JSON-ready artifact produced by
    ``GraphPlanner.to_result_dict``. It does not rebuild a ``GraphPlanner`` and
    does not rerun RPL; reports describe the exact serialized solution.
    """

    result: Mapping[str, Any]

    @classmethod
    def from_result(cls, result: Mapping[str, Any]) -> "PlanningSolution":
        """Create a solution wrapper from an exported result dictionary."""
        cls._validate_result(result)
        return cls(result=result)

    def evaluation_payload(
        self,
        *,
        rank_threshold: float,
        primary_tech: str = "lte",
    ) -> dict[str, Any]:
        """Return the standard evaluation payload for this serialized result."""
        return {
            "quality_summary": self.quality_summary(
                rank_threshold=rank_threshold,
            ),
            "quality": self.quality_table(rank_threshold=rank_threshold),
            "service": self.service_table(primary_tech=primary_tech),
            "result_edges": self.result_edge_table(),
        }

    def quality_summary(self, *, rank_threshold: float) -> dict[str, Any]:
        """Return aggregate quality counts for ``quality_table``."""
        rows = self.quality_table(rank_threshold=rank_threshold)
        counts: dict[str, int] = {}
        total_targets = 0
        good_targets = 0
        poor_targets = 0
        unserved_targets = 0

        for row in rows:
            quality = str(row["quality"])
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
            "success": (
                total_targets > 0
                and poor_targets == 0
                and unserved_targets == 0
            ),
            "total_targets": total_targets,
            "good_targets": good_targets,
            "poor_targets": poor_targets,
            "unserved_targets": unserved_targets,
            "counts": counts,
        }

    def quality_table(self, *, rank_threshold: float) -> list[dict[str, Any]]:
        """
        Return per-interface quality rows for the serialized solution.

        All RPL nodes are included. Planned nodes overlay the base RPL node
        records; nodes not present in ``planned_nodes`` are considered unserved
        unless they are connected/backbone interfaces.
        """
        if rank_threshold < 0:
            raise ValueError("rank_threshold must be >= 0")

        rows = []
        node_records = self._merged_node_records()
        for node_id, row in node_records.items():
            rank = row.get("rank")
            connected = bool(row.get("connected"))
            parent = row.get("parent")
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
                    "node_index": row.get("node_index"),
                    "node_id": node_id,
                    "site_id": row.get("site_id"),
                    "device_id": row.get("device_id"),
                    "tech": row.get("tech"),
                    "connected": connected,
                    "planned": planned,
                    "quality": quality,
                    "rank": rank,
                    "rank_threshold": rank_threshold,
                    "parent": parent,
                    "parent_site_id": self._parent_site_id(parent),
                }
            )

        rows.sort(key=self._node_sort_key)
        return rows

    def service_table(self, *, primary_tech: str = "lte") -> list[dict[str, Any]]:
        """
        Return service assignment rows for unconnected primary-tech interfaces.
        """
        rows = []
        node_records = self._merged_node_records()
        edge_metrics = self._planned_edge_metric_map()

        for node_id, row in node_records.items():
            if row.get("connected") or row.get("tech") != primary_tech:
                continue

            parent = row.get("parent")
            parent_record = node_records.get(parent, {}) if parent else {}
            metric = (
                edge_metrics.get((node_id, parent))
                if parent is not None
                else None
            )
            if metric is None and parent is not None:
                metric = edge_metrics.get((parent, node_id))

            rows.append(
                {
                    "site_id": row.get("site_id"),
                    "node_id": node_id,
                    "tech": row.get("tech"),
                    "served": parent is not None,
                    "parent": parent,
                    "parent_site_id": parent_record.get("site_id"),
                    "parent_tech": parent_record.get("tech"),
                    "metric": metric,
                    "rank": row.get("rank"),
                }
            )

        rows.sort(key=self._node_sort_key)
        return rows

    def result_edge_table(self) -> list[dict[str, Any]]:
        """Return planned result edges enriched with endpoint metadata."""
        node_records = self._merged_node_records()
        metric_records = self._metric_edge_record_map()
        rows = []

        for edge in self.result.get("planned_edges", []):
            src = str(edge["src"])
            dst = str(edge["dst"])
            src_node = node_records.get(src, {})
            dst_node = node_records.get(dst, {})
            attrs = metric_records.get((src, dst))
            if attrs is None:
                attrs = metric_records.get((dst, src), {})

            rows.append(
                {
                    "src": src,
                    "dst": dst,
                    "kind": attrs.get("kind", edge.get("kind", "rf")),
                    "rule": attrs.get("rule", edge.get("rule")),
                    "src_site": src_node.get("site_id"),
                    "dst_site": dst_node.get("site_id"),
                    "src_tech": src_node.get("tech"),
                    "dst_tech": dst_node.get("tech"),
                    "src_freq_mhz": src_node.get("freq_mhz"),
                    "dst_freq_mhz": dst_node.get("freq_mhz"),
                    "src_max_links": src_node.get("max_links"),
                    "dst_max_links": dst_node.get("max_links"),
                    "src_mount_height_m": src_node.get("mount_height_m"),
                    "dst_mount_height_m": dst_node.get("mount_height_m"),
                    "metric": edge.get("metric", attrs.get("metric")),
                }
            )

        return rows

    def _merged_node_records(self) -> dict[str, dict[str, Any]]:
        nodes = {
            str(row["node_id"]): dict(row)
            for row in self.result.get("rpl_nodes", [])
        }
        for row in self.result.get("planned_nodes", []):
            node_id = str(row["node_id"])
            merged = dict(nodes.get(node_id, {}))
            merged.update(row)
            nodes[node_id] = merged
        return nodes

    def _planned_edge_metric_map(self) -> dict[tuple[str, str], Any]:
        return {
            (str(row["src"]), str(row["dst"])): row.get("metric")
            for row in self.result.get("planned_edges", [])
        }

    def _metric_edge_record_map(self) -> dict[tuple[str, str], dict[str, Any]]:
        return {
            (str(row["src"]), str(row["dst"])): dict(row)
            for row in self.result.get("metric_edges", [])
        }

    def _parent_site_id(self, parent: str | None) -> Any:
        if parent is None:
            return None
        return self._merged_node_records().get(parent, {}).get("site_id")

    @staticmethod
    def _node_sort_key(row: Mapping[str, Any]) -> tuple[bool, Any, str]:
        node_index = row.get("node_index")
        return (
            node_index is None,
            node_index if node_index is not None else 0,
            str(row.get("node_id", "")),
        )

    @staticmethod
    def _validate_result(result: Mapping[str, Any]) -> None:
        required = {"rpl_nodes", "planned_nodes", "planned_edges"}
        missing = sorted(required - set(result))
        if missing:
            raise ValueError(
                "Planning result is missing required fields: "
                + ", ".join(missing)
            )
