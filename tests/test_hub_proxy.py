import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app import main_hub as hub


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
