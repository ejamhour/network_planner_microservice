import asyncio
from types import SimpleNamespace

import pytest

from app.session import UserRuntime
from cisei_lib.io import async_geo_service_pool as pool_module
from cisei_lib.io.geo_service_client import APIResult


@pytest.fixture
def workers(monkeypatch):
    registry = {}
    attempts = []
    failures = set()
    registrations = []

    class Client:
        user_id = None
        token = None

        def __init__(self, **kwargs):
            pass

        def register(self, user_id):
            registrations.append(user_id)
            if len(registrations) in failures:
                raise RuntimeError("registration failed")
            self.user_id = user_id
            self.token = f"credential-{len(registrations)}"
            registry[user_id] = self.token
            return APIResult("json", {"status": "ok"})

        def close_worker(self):
            attempts.append((self.user_id, self.token))
            if registry.get(self.user_id) != self.token:
                return APIResult("http_error", {"status": "error", "data": "403 Invalid token"})
            del registry[self.user_id]
            return APIResult("json", {"status": "stopped"})

    monkeypatch.setattr(pool_module, "GeoServiceClient", Client)
    return SimpleNamespace(registry=registry, attempts=attempts, failures=failures, registrations=registrations)


def test_sessions_and_pools_have_isolated_workers_and_reuse_credentials(workers):
    async def exercise():
        first, second = UserRuntime(), UserRuntime()
        options = dict(base_url="http://features", user_prefix="planning-sdk", pool_size=2, timeout=120)
        a, reused = await asyncio.gather(first.get_geo_pool(**options), first.get_geo_pool(**options))
        assert a is reused
        b = await first.get_geo_pool(**{**options, "timeout": 300})
        c = await second.get_geo_pool(**options)
        assert len(workers.registrations) == 6
        assert len(set(workers.registrations)) == 6
        assert a.pool_number == 0 and b.pool_number == 1 and c.pool_number == 0
        assert a.session_id != c.session_id
        assert all(f"pool-{a.pool_number}-worker-" in worker for worker in a.worker_ids)
        credentials = dict(workers.registry)
        status = first.geo_pool_status()
        assert status[0]["worker_ids"] == a.worker_ids
        assert "credential-" not in str(status)
        await first.close_geo_pools()
        assert set(workers.registry) == set(c.worker_ids)
        assert all(credentials[worker] == token for worker, token in workers.attempts)
        await second.close_geo_pools()
        assert not workers.registry
        assert not first.geo_pools and not second.geo_pools

    asyncio.run(exercise())


def test_cleanup_failure_is_visible_retained_and_retryable(workers):
    async def exercise():
        runtime = UserRuntime()
        pool = await runtime.get_geo_pool(base_url="http://features", user_prefix="planning-sdk", pool_size=2, timeout=120)
        failed_worker = pool.worker_ids[0]
        credentials = workers.registry[failed_worker]
        workers.registry[failed_worker] = "unexpected-token"
        with pytest.raises(RuntimeError, match="403 Invalid token"):
            await runtime.close_geo_pools()
        assert pool.worker_ids == [failed_worker]
        assert runtime.geo_pool_status()[0]["cleanup_errors"]
        assert set(workers.registry) == {failed_worker}
        workers.registry[failed_worker] = credentials
        await runtime.close_geo_pools()
        assert not workers.registry and not runtime.geo_pools
        assert [w for w, _ in workers.attempts].count(failed_worker) == 2

    asyncio.run(exercise())


def test_partial_startup_closes_registered_workers_and_uses_new_pool_number(workers):
    async def exercise():
        runtime = UserRuntime()
        options = dict(base_url="http://features", user_prefix="planning-sdk", pool_size=2, timeout=120)
        workers.failures.add(2)
        with pytest.raises(RuntimeError, match="registration failed"):
            await runtime.get_geo_pool(**options)
        assert not workers.registry and not runtime.geo_pools
        pool = await runtime.get_geo_pool(**options)
        assert pool.pool_number == 1
        await runtime.close_geo_pools()
        assert not workers.registry

    asyncio.run(exercise())


def test_standalone_pools_are_also_isolated(workers):
    async def exercise():
        a = pool_module.AsyncGeoServicePool("http://features", "same-prefix", pool_size=1)
        b = pool_module.AsyncGeoServicePool("http://features", "same-prefix", pool_size=1)
        await asyncio.gather(a.start(), b.start())
        assert a.worker_ids != b.worker_ids
        await a.close()
        assert set(workers.registry) == set(b.worker_ids)
        await b.close()

    asyncio.run(exercise())


def test_close_waits_for_in_flight_extraction_and_rejects_new_work(workers):
    async def exercise():
        pool = pool_module.AsyncGeoServicePool("http://features", "planning-sdk", pool_size=1)
        await pool.start()
        finished = asyncio.Event()
        task = asyncio.create_task(finished.wait())
        pool._in_flight[("test",)] = task
        cleanup = asyncio.create_task(pool.close())
        await asyncio.sleep(0)
        assert not workers.attempts
        with pytest.raises(RuntimeError):
            await pool.get_features((1, 2), (3, 4), tx_ha=7, rx_ha=7)
        finished.set()
        await cleanup
        assert not workers.registry

    asyncio.run(exercise())


def test_worker_delete_uses_its_registration_token(monkeypatch):
    from cisei_lib.io import geo_service_client as client_module

    calls = []

    class Response:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"user_id": "owned-worker", "token": "private-token"}

    monkeypatch.setattr(client_module.requests, "post", lambda *args, **kwargs: Response())

    def delete(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(client_module.requests, "delete", delete)
    client = client_module.GeoServiceClient("http://features", timeout=30)
    client.register("owned-worker")
    client.close_worker()
    assert calls == [("http://features/workers/owned-worker", {"headers": {"X-Token": "private-token"}, "timeout": 30})]
