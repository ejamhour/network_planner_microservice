from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

from cisei_planning_sdk.bundle import load_scenario_bundle
from cisei_planning_sdk.session import ScenarioSession


class PlanningClient:
    """
    High-level notebook client for the planning microservice.

    The notebook user should work with this class instead of importing server
    internals. Stage 1 is ``define_scenario(...)``: load a local scenario
    bundle, define it on the server, and return a ``ScenarioSession`` that
    exposes the next planning stages.
    """

    def __init__(
        self,
        base_url: str,
        *,
        user_id: str | None = None,
        token: str | None = None,
        auto_register: bool = True,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id or f"notebook-{uuid4().hex[:12]}"
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        if auto_register and self.token is None:
            self.connect()

    def connect(self, *, reset: bool = True) -> "PlanningClient":
        """
        Register this notebook session with the planning hub.

        The hub returns a per-user token and starts/refreshes the worker on the
        first authenticated planning request. ``reset=True`` keeps the current
        hub behavior: registering the same user id replaces its worker state.
        """
        response = self.session.post(
            f"{self.base_url}/register",
            params={"user_id": self.user_id},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        self.user_id = payload["user_id"]
        self.token = payload["token"]
        return self

    def define_scenario(
        self,
        *,
        name: str,
        scenario: str | Path | dict[str, Any],
        planner: str = "graph",
    ) -> ScenarioSession:
        """
        Define a scenario on the server and return a scenario session.

        ``scenario`` may be either a local TOML bundle path or a complete
        scenario dictionary. When the TOML declares ``[instances].source``, the
        SDK reads the companion CSV locally and embeds concrete ``sites`` in the
        JSON sent to the server.

        Server-side effect:
            Stores/replaces the scenario definition under ``name`` in the
            planning worker. This includes profiles, instantiated sites,
            devices and interfaces. It does not build candidate edges, compute
            metrics, solve, or evaluate.
        """
        scenario_json = self._scenario_json(scenario)
        response = self._request(
            "post",
            "/planning/scenarios/define",
            json={
                "scenario_id": name,
                "scenario": scenario_json,
            },
        )
        data = response["data"]
        return ScenarioSession(
            client=self,
            name=name,
            planner=planner,
            scenario_id=data["scenario_id"],
            summary=data.get("summary", {}),
            validation_errors=data.get("validation_errors", []),
            scenario=data.get("scenario"),
        )

    def evaluate_result(
        self,
        result: dict[str, Any],
        *,
        rank_threshold: float,
        primary_tech: str = "lte",
        solution_kind: str = "graph",
    ) -> dict[str, Any]:
        """
        Evaluate a serialized planning result through the planning API.

        Use this when a notebook has loaded a saved result artifact and wants
        server-side reports without restoring a live scenario session. The SDK
        only sends the JSON result to the API; all planning-solution logic stays
        inside the service.
        """
        response = self._request(
            "post",
            "/planning/results/evaluate",
            json={
                "result": _json_value(result),
                "rank_threshold": rank_threshold,
                "primary_tech": primary_tech,
                "solution_kind": solution_kind,
            },
        )
        return response["data"]

    def _scenario_json(
        self,
        scenario: str | Path | dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(scenario, (str, Path)):
            return load_scenario_bundle(scenario)
        if isinstance(scenario, dict):
            return _json_value(scenario)
        raise TypeError("scenario must be a TOML path or dictionary")

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if self.token is None:
            self.connect()
        if self.token is None:
            raise RuntimeError(
                "PlanningClient is not registered: missing X-Token. "
                "Create it with auto_register=True or call client.connect()."
            )

        url = self._url(path)
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["X-Token"] = self.token

        response = self.session.request(
            method,
            url,
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )
        response.raise_for_status()

        payload = response.json()
        if str(payload.get("status", "")).lower() != "ok":
            raise RuntimeError(payload.get("data", payload))
        return payload

    def _url(self, path: str) -> str:
        path = "/" + path.lstrip("/")
        if self.user_id:
            return f"{self.base_url}/{self.user_id}{path}"
        return f"{self.base_url}{path}"


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in {float("inf"), float("-inf")} else None
    return value
