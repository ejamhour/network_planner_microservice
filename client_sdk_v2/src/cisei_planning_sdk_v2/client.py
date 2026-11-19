"""HTTP transport and standard project opening, without local compilation."""
from pathlib import Path

import requests

from .project import open_project
from .errors import ActiveProjectExists


class PlanningClient:
    def __init__(self, base_url: str, *, workspace: str | Path, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise FileNotFoundError("Workspace directory does not exist")
        self.timeout = timeout
        self.project = None
        self.user_id = None
        self.token = None
        self.session = requests.Session()

    def open_project(self, folder: str | Path, *, mode=None):
        """Create/resume project storage; never sync, validate, compile or plan."""
        if self.user_id is not None:
            active = self._worker_request("get", "/projects/active")["data"]["active_project"]
            if active is not None:
                raise ActiveProjectExists({"code": "active_project_exists",
                    "message": "Close the active project before opening another.",
                    "active_project_id": active["project_id"]})
        return open_project(self, folder, mode=mode)

    def connect(self):
        """Create a worker session lazily; never reset an existing session."""
        if self.user_id is None:
            response = self.session.request("post", self.base_url + "/register",
                                            timeout=self.timeout, allow_redirects=False)
            response.raise_for_status()
            data = response.json()
            self.user_id, self.token = data["user_id"], data["token"]
        return self.user_id

    def _worker_request(self, method, path, **kwargs):
        self.connect()
        headers = {**kwargs.pop("headers", {}), "X-Token": self.token}
        return self._request(method, f"/{self.user_id}/{path.lstrip('/')}", headers=headers, **kwargs)

    def _request(self, method, path, **kwargs):
        kwargs.setdefault("allow_redirects", False)
        response = self.session.request(
            method, self.base_url + "/" + path.lstrip("/"),
            timeout=kwargs.pop("timeout", self.timeout), **kwargs,
        )
        if response.status_code == 409:
            detail = response.json().get("detail", {})
            if isinstance(detail, dict) and detail.get("code") == "active_project_exists":
                raise ActiveProjectExists(detail, response=response)
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise RuntimeError("Unexpected redirect from project service")
        payload = response.json()
        if str(payload.get("status", "")).lower() != "ok":
            raise RuntimeError(payload.get("data", payload))
        return payload
