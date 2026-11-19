"""Mesh network calls; future Mesh link operations belong to this interface."""
from ._base import PlanningOperations


class MeshPlanning(PlanningOperations):
    kind = "mesh"

    def add_repeaters(self, *, geo_base_url=None, **options):
        """Run Mesh network repeater addition with server-side poles/settings."""
        return self._request("post", "mesh/add_repeaters",
                             json={"geo_base_url": geo_base_url, **options})

    def build_candidate_edges(self, *, preserve_existing=True):
        """Generate Mesh candidates after the explicit repeater-addition stage."""
        return self._build_candidate_edges(preserve_existing=preserve_existing)
