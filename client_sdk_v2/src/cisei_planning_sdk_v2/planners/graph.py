"""Static Graph planning calls for a server-managed project."""
from ._execution import NetworkExecution


class GraphPlanning(NetworkExecution):
    kind = "graph"

    def build_candidate_edges(self, *, preserve_existing=True):
        """Generate static candidates from scenario connectivity rules remotely."""
        return self._build_candidate_edges(preserve_existing=preserve_existing)
