from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from uuid import uuid4

from cisei_lib.io.geo_service_client import APIResult, GeoServiceClient


LatLon = tuple[float, float]
EdgeKey = tuple[Any, ...]


class GeoServiceError(RuntimeError):
    pass

class GeoServiceBusy(RuntimeError):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after


@dataclass(slots=True)
class FeatureRequest:
    tx: LatLon
    rx: LatLon
    tx_ha: float
    rx_ha: float
    freq_mhz: float = 900.0
    options: dict[str, Any] = field(default_factory=dict)
    tag: str | None = None


@dataclass(slots=True)
class FeatureResult:
    request: FeatureRequest
    features: Any


class AsyncGeoServicePool:
    """Async facade over independent blocking GeoServiceClient instances."""

    def __init__(
        self,
        base_url: str | None,
        user_prefix: str,
        pool_size: int = 3,
        *,
        timeout: float = 120,
        session_id: str | None = None,
        pool_number: int = 0,
    ):
        if pool_size < 1:
            raise ValueError("pool_size must be at least 1")

        if not user_prefix:
            raise ValueError("user_prefix is required")

        self.base_url = base_url
        self.pool_size = pool_size
        self.timeout = timeout
        self.user_prefix = user_prefix
        self.session_id = session_id or uuid4().hex
        self.pool_number = pool_number
        self.worker_prefix = f"{user_prefix}-{self.session_id}-pool-{pool_number}-worker"
        self._owned_clients: list[GeoServiceClient] = []
        self.cleanup_errors: list[str] = []
        self._lifecycle_lock = asyncio.Lock()
        self._closing = False

        self._clients: asyncio.Queue[GeoServiceClient] | None = None
        self._executor: ThreadPoolExecutor | None = None

        self._cache: dict[EdgeKey, Any] = {}
        self._in_flight: dict[EdgeKey, asyncio.Task[Any]] = {}

        self._started = False
        self._closed = False

    @property
    def worker_ids(self) -> list[str]:
        """Owned worker IDs, without exposing authentication tokens."""
        return [client.user_id for client in self._owned_clients]

    async def start(self) -> AsyncGeoServicePool:
        async with self._lifecycle_lock:
            return await self._start()

    async def _start(self) -> AsyncGeoServicePool:
        if self._closed:
            raise RuntimeError("Pool has already been closed")

        if self._started:
            return self

        if self._owned_clients:
            raise GeoServiceError("Pool cleanup is incomplete; retry close() before reuse")

        self._clients = asyncio.Queue(maxsize=self.pool_size)
        self._executor = ThreadPoolExecutor(
            max_workers=self.pool_size,
            thread_name_prefix="geo-service",
        )

        try:
            # Sequential registration avoids concurrent cold-start pressure.
            for index in range(self.pool_size):
                client = GeoServiceClient(
                    base_url=self.base_url,
                    timeout=self.timeout,
                )

                user_id = f"{self.worker_prefix}-{index}"
                await self._run_blocking(client.register, user_id=user_id)

                if not client.user_id or not client.token:
                    raise GeoServiceError(
                        f"GeoService registration failed for client {index}"
                    )

                self._owned_clients.append(client)
                self._clients.put_nowait(client)

        except Exception as startup_error:
            try:
                await self._close()
            except Exception as cleanup_error:
                raise GeoServiceError(
                    f"Pool startup failed: {startup_error}; cleanup failed: {cleanup_error}"
                ) from startup_error
            raise

        self._started = True
        return self

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._close()

    async def _close(self) -> None:
        if self._closed:
            return

        self._closing = True
        try:
            pending = list(self._in_flight.values())
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            clients = list(self._owned_clients)
            # Cleanup uses retained client credentials, including on retries
            # after the extraction executor has been shut down.
            results = await asyncio.gather(
                *(asyncio.to_thread(client.close_worker) for client in clients),
                return_exceptions=True,
            )
            self.cleanup_errors = []
            failed_clients = []
            for client, result in zip(clients, results):
                try:
                    if isinstance(result, BaseException):
                        raise GeoServiceError(str(result))
                    self._validate_result(result, f"close_worker {client.user_id}")
                except Exception as exc:
                    failed_clients.append(client)
                    self.cleanup_errors.append(f"Worker {client.user_id}: {exc}")
            self._owned_clients = failed_clients
            if self._executor is not None:
                self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None
            self._clients = None
            self._started = False
            self._closed = not failed_clients
            if self.cleanup_errors:
                raise GeoServiceError("Pool cleanup failed: " + "; ".join(self.cleanup_errors))
        finally:
            self._closing = False

    async def __aenter__(self) -> AsyncGeoServicePool:
        return await self.start()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    async def get_features(
        self,
        tx: LatLon,
        rx: LatLon,
        *,
        tx_ha: float,
        rx_ha: float,
        freq_mhz: float = 900,
        **options: Any,
    ) -> Any:
        request = FeatureRequest(
            tx=tuple(tx),
            rx=tuple(rx),
            tx_ha=tx_ha,
            rx_ha=rx_ha,
            freq_mhz=freq_mhz,
            options=options,
        )
        return await self.get_request(request)

    async def get_request(self, request: FeatureRequest) -> Any:
        self._require_started()
        key = self._make_key(request)

        if key in self._cache:
            return self._cache[key]

        task = self._in_flight.get(key)
        if task is None:
            # There is no await between lookup and insertion, so this is atomic
            # with respect to other coroutines on the same event-loop thread.
            task = asyncio.create_task(
                self._calculate_and_cache(key, request)
            )
            self._in_flight[key] = task

        # Cancellation of one caller must not cancel a shared extraction.
        return await asyncio.shield(task)

    def submit(self, request: FeatureRequest) -> asyncio.Task[Any]:
        self._require_started()
        return asyncio.create_task(self.get_request(request))

    async def get_many(
        self,
        requests: Iterable[FeatureRequest],
    ) -> list[FeatureResult]:
        async def evaluate(request: FeatureRequest) -> FeatureResult:
            features = await self.get_request(request)
            return FeatureResult(request=request, features=features)

        return await asyncio.gather(
            *(evaluate(request) for request in requests)
        )

    async def as_completed(
        self,
        requests: Iterable[FeatureRequest],
    ) -> AsyncIterator[FeatureResult]:
        async def evaluate(request: FeatureRequest) -> FeatureResult:
            features = await self.get_request(request)
            return FeatureResult(request=request, features=features)

        tasks = [
            asyncio.create_task(evaluate(request))
            for request in requests
        ]

        for task in asyncio.as_completed(tasks):
            yield await task

    async def point_samples(
        self,
        points: list[tuple[float, float]],
        *,
        ds_string: str = "DTM",
        nodata_value: float | None = 0,
    ) -> Any:
        """Run the existing blocking point_samples call using one pooled client."""
        self._require_started()
        assert self._clients is not None

        client = await self._clients.get()
        try:
            result = await self._run_blocking(
                client.point_samples,
                points,
                ds_string=ds_string,
                nodata_value=nodata_value,
            )
            return self._extract_payload(result, "point_samples")
        finally:
            self._clients.put_nowait(client)

    async def _calculate_and_cache(
        self,
        key: EdgeKey,
        request: FeatureRequest,
    ) -> Any:
        try:
            features = await self._extract(request)
            self._cache[key] = features
            return features
        finally:
            self._in_flight.pop(key, None)

    async def _extract(self, request: FeatureRequest) -> Any:
        assert self._clients is not None

        while True:
            client = await self._clients.get()

            try:
                return await self._run_blocking(
                    self._extract_blocking,
                    client,
                    request,
                )

            except GeoServiceBusy as exc:
                retry_after = exc.retry_after

            finally:
                self._clients.put_nowait(client)

            await asyncio.sleep(retry_after)

    def _extract_blocking(
        self,
        client: GeoServiceClient,
        request: FeatureRequest,
    ) -> Any:
        parameters = {
            "tx_ha": request.tx_ha,
            "rx_ha": request.rx_ha,
            "freq_mhz": request.freq_mhz,
            **request.options,
        }

        set_link_result = client.link(
            request.tx,
            request.rx,
            prepare_link=False,
            retry_busy=False, # retry is performed by the pool
            **parameters,
        )

        self._raise_if_busy(set_link_result)
        self._validate_result(set_link_result, "set_link")

        prepare_result = client.request(
            "prepare_profiles",
            method="post",
        )
        self._raise_if_busy(prepare_result)
        self._validate_result(prepare_result, "prepare_profiles")

        feature_result = client.link_features()
        self._raise_if_busy(feature_result)

        return self._extract_payload(
            feature_result,
            "link_features",
        )

    async def _run_blocking(self, function, *args, **kwargs):
        if self._executor is None:
            raise RuntimeError("Pool has not been started")

        loop = asyncio.get_running_loop()
        call = partial(function, *args, **kwargs)
        return await loop.run_in_executor(self._executor, call)

    @staticmethod
    def _raise_if_busy(result: APIResult) -> None:
        if (
            result.response is None
            or result.response.status_code != 503
        ):
            return

        try:
            retry_after = float(
                result.response.headers.get(
                    "Retry-After",
                    "1",
                )
            )
        except (TypeError, ValueError):
            retry_after = 1.0

        raise GeoServiceBusy(retry_after)

    @staticmethod
    def _validate_result(result: APIResult, operation: str) -> None:
        if result.kind in {"http_error", "request_error", "error"}:
            raise GeoServiceError(f"{operation} failed: {result.data}")

        if isinstance(result.data, dict):
            status = str(result.data.get("status", "")).lower()
            if status == "error":
                raise GeoServiceError(f"{operation} failed: {result.data}")

    @classmethod
    def _extract_payload(cls, result: APIResult, operation: str) -> Any:
        cls._validate_result(result, operation)
        data = result.data

        if isinstance(data, dict) and "data" in data:
            return data["data"]

        return data

    @classmethod
    def _make_key(cls, request: FeatureRequest) -> EdgeKey:
        return (
            tuple(request.tx),
            tuple(request.rx),
            request.tx_ha,
            request.rx_ha,
            request.freq_mhz,
            cls._freeze(request.options),
        )

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (key, cls._freeze(item))
                    for key, item in value.items()
                )
            )

        if isinstance(value, (list, tuple)):
            return tuple(cls._freeze(item) for item in value)

        if isinstance(value, set):
            return frozenset(cls._freeze(item) for item in value)

        try:
            hash(value)
        except TypeError as exc:
            raise TypeError(
                f"Cannot use {type(value).__name__} as part of the feature cache key"
            ) from exc

        return value

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_size(self) -> int:
        return len(self._cache)

    def in_flight_count(self) -> int:
        return len(self._in_flight)

    def _require_started(self) -> None:
        if not self._started or self._closing:
            raise RuntimeError(
                "AsyncGeoServicePool.start() must be called first"
            )
