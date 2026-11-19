"""Cell planning calls for a server-managed project."""
from ._execution import NetworkExecution


class CellPlanning(NetworkExecution):
    kind = "cell"

    def build_candidate_edges(self, *, preserve_existing=True, primary_tech="lte",
                              sector_aware=True, limit_m=None, degree=None):
        """Generate cell/client candidates remotely with the Cell-specific options."""
        return self._build_candidate_edges(
            preserve_existing=preserve_existing, primary_tech=primary_tech,
            sector_aware=sector_aware, limit_m=limit_m, degree=degree)

    def tune_sectors(self, *, primary_tech="lte", site_ids=None, apply_rotation=True,
                     prune_empty=False, step_deg=5.0, uncovered_weight=10.0,
                     imbalance_weight=1.0, sector_aware=True, limit_m=None, degree=None):
        """Tune only profiles permitting auto_tune; preview with apply_rotation=False.

        The server applies effective per-interface placements. No confirmation,
        local search, file expansion or input-file rewriting occurs in the SDK.
        """
        return self._request("post", "cell/sectors/tune", json={
            "primary_tech": primary_tech, "site_ids": site_ids,
            "apply_rotation": apply_rotation, "prune_empty": prune_empty,
            "step_deg": step_deg, "uncovered_weight": uncovered_weight,
            "imbalance_weight": imbalance_weight, "sector_aware": sector_aware,
            "limit_m": limit_m, "degree": degree})
