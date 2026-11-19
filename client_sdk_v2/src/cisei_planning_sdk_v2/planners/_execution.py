"""Reusable HTTP/IO methods for planners with staged network execution.

Graph and Cell opt in as sibling interfaces. Mesh keeps its own preparation
interface until its execution stages are migrated. No algorithm lives here.
"""
from ._base import PlanningOperations


class NetworkExecution(PlanningOperations):
    def compute_metrics(self, *, edge_metrics=None, geo_base_url=None, batch_offset=0,
                        batch_size=None, bidirectional=False, assume_symmetric_features=False,
                        geo_user_prefix="planning-api", geo_pool_size=1, geo_timeout=120.0):
        """Compute one metric batch remotely, or supply explicit candidate costs.

        With batch_size, use the returned batch.next_offset for the next call.
        Features and metric evaluation stay on the server; solving is explicit.
        """
        return self._request("post", f"{self.kind}/metrics/compute", json={
            "edge_metrics": edge_metrics, "geo_base_url": geo_base_url,
            "batch_offset": batch_offset, "batch_size": batch_size,
            "bidirectional": bidirectional, "assume_symmetric_features": assume_symmetric_features,
            "geo_user_prefix": geo_user_prefix, "geo_pool_size": geo_pool_size, "geo_timeout": geo_timeout})

    def get_metrics(self):
        """Inspect computed costs without fetching raw feature caches."""
        return self._request("get", f"{self.kind}/metrics")

    def solve(self, *, max_edge_metric=None, parent_set_size=1):
        """Solve this planner's complete metrics and persist output on the server."""
        return self._request("post", f"{self.kind}/solution/solve", json={
            "max_edge_metric": max_edge_metric, "parent_set_size": parent_set_size})

    def get_solution(self):
        """Inspect the solution for this worker's current execution."""
        return self._request("get", f"{self.kind}/solution")

    def get_result(self, *, parameter_hash=None):
        """Retrieve saved server output without starting or activating a worker."""
        project = self._project
        return project._client._request("get", f"/projects/{project.project_id}/{self.kind}/result",
            headers={"X-Project-Token": project._recovery_token},
            params={} if parameter_hash is None else {"parameter_hash": parameter_hash})["data"]

    def save_result(self, path=None, *, parameter_hash=None):
        """Download and atomically save result.json locally; return stage feedback.

        An invalid retrieval report is returned without writing a file. Success
        adds local_path to that report. This never exports the raw feature cache.
        """
        from ..artifacts import write_json_artifact
        report = self.get_result(parameter_hash=parameter_hash)
        if report["valid"]:
            saved = write_json_artifact(path if path is not None else self._project.path / "result.json", report["result"])
            report = {**report, "local_path": str(saved)}
        return report
