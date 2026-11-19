"""HTTP plumbing for project-bound planner interfaces; no planning logic."""


class PlanningOperations:
    kind: str  # Bound by each concrete planner interface; no implicit planner.

    def __init__(self, project):
        self._project = project

    def _request(self, method, path, **kwargs):
        project = self._project
        return project._client._worker_request(
            method, f"/projects/{project.project_id}/{path}",
            headers={"X-Project-Token": project._recovery_token}, **kwargs,
        )["data"]

    def _build_candidate_edges(self, **options):
        return self._request("post", "candidate_edges/build",
                             json={"planner": self.kind, **options})

    def get_candidate_edges(self):
        """Inspect this planner's current candidates; never generate them."""
        return self._request("get", "candidate_edges", params={"planner": self.kind})
