import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main_hub as hub
from cisei_planning_sdk import PlanningClient


def test_registration_issues_unique_sessions_and_requires_token_to_reset(monkeypatch):
    monkeypatch.setattr(hub, "user_registry", {})
    client = TestClient(hub.hub_app)
    first = client.post("/register", params={"user_id": "label"}).json()
    second = client.post("/register", params={"user_id": first["user_id"]}).json()
    anonymous = client.post("/register").json()
    assert len({entry["user_id"] for entry in (first, second, anonymous)}) == 3
    assert first["user_id"] != "label"
    assert first["token"] != second["token"]
    assert hub.user_registry[first["user_id"]]["label"] == "label"
    path = f"/{first['user_id']}/session/reset"
    assert client.post(path, headers={"X-Token": second["token"]}).status_code == 401
    assert client.post(path, headers={"X-Token": first["token"]}).status_code == 200


def test_sdk_uses_issued_id_and_token_for_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(hub, "user_registry", {})
    (tmp_path / "projects").mkdir()
    client = PlanningClient("http://testserver", workspace=tmp_path, user_id="label", auto_register=False)
    client.session = TestClient(hub.hub_app, base_url="http://testserver")
    client.connect()
    session_id, token = client.user_id, client.token
    assert hub.user_registry[session_id]["label"] == "label"
    client.connect(reset=False)
    client.connect()
    assert (client.user_id, client.token) == (session_id, token)
    assert list(hub.user_registry) == [session_id]


@pytest.mark.parametrize("error,status", [(httpx.ReadTimeout(""), 504), (httpx.ConnectError("worker unavailable"), 502)])
def test_proxy_failure_identifies_cause_and_closes_client(monkeypatch, error, status):
    class Client:
        def __init__(self, **kwargs):
            self.timeout = kwargs["timeout"]
            self.closed = False

        def build_request(self, **kwargs):
            return object()

        async def send(self, request, **kwargs):
            raise error

        async def aclose(self):
            self.closed = True

    client = Client(timeout=1800)
    monkeypatch.setattr(hub.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(hub, "WORKER_REQUEST_TIMEOUT", 1800)
    monkeypatch.setattr(hub, "user_registry", {"test": {"token": "token", "port": 15000, "process": None}})
    request = SimpleNamespace(headers={"X-Token": "token"}, method="POST", query_params={}, stream=lambda: None)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(hub.route_request("test", "planning/scenarios/test/metrics/compute", request))
    assert caught.value.status_code == status
    assert type(error).__name__ in caught.value.detail
    assert client.closed
    if status == 504:
        assert "1800 seconds" in caught.value.detail
        assert "PLANNING_WORKER_REQUEST_TIMEOUT" in caught.value.detail


def test_stream_cleanup_closes_client_even_if_response_close_fails():
    class Response:
        async def aclose(self):
            raise RuntimeError("close failed")

    class Client:
        closed = False

        async def aclose(self):
            self.closed = True

    client = Client()
    with pytest.raises(RuntimeError, match="close failed"):
        asyncio.run(hub._close_proxy(Response(), client))
    assert client.closed
