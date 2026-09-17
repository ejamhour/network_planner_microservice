from __future__ import annotations

from typing import Any

from cisei_lib.io.async_geo_service_pool import AsyncGeoServicePool


class UserRuntime:
    """
    Per-worker planning runtime.

    The hub creates one worker process per user/session. This object stores the
    in-memory planning state owned by that worker: prepared scenarios, the
    latest planner execution results, and reusable feature-service client pools.

    The pools do not create local RF/GDAL state. They keep HTTP workers and an
    edge-feature cache alive while this planning worker lives, so repeated
    planning runs can avoid recalculating features for unchanged links.
    """

    def __init__(self):
        self.planning_scenarios = {}
        self.planning_planners = {}
        self.geo_pools: dict[tuple[Any, ...], AsyncGeoServicePool] = {}

    async def get_geo_pool(
        self,
        *,
        base_url: str,
        user_prefix: str,
        pool_size: int,
        timeout: float,
    ) -> AsyncGeoServicePool:
        """
        Return a started reusable geo-service pool for these connection params.

        A different key creates a different pool because the feature-service
        workers and local cache are tied to base URL, user prefix, pool size and
        request timeout.
        """
        key = (base_url, user_prefix, pool_size, float(timeout))
        pool = self.geo_pools.get(key)
        if pool is None:
            pool = AsyncGeoServicePool(
                base_url=base_url,
                user_prefix=user_prefix,
                pool_size=pool_size,
                timeout=timeout,
            )
            await pool.start()
            self.geo_pools[key] = pool
        return pool

    async def close_geo_pools(self) -> None:
        """Close all reusable geo-service pools owned by this runtime."""
        errors = []
        for pool in list(self.geo_pools.values()):
            try:
                await pool.close()
            except Exception as exc:
                errors.append(exc)
        self.geo_pools.clear()
        if errors:
            raise RuntimeError(
                "Failed to close one or more geo pools: "
                + "; ".join(str(error) for error in errors)
            )

    def geo_pool_status(self) -> list[dict[str, Any]]:
        """Return cache/status metadata for active geo-service pools."""
        rows = []
        for (base_url, user_prefix, pool_size, timeout), pool in self.geo_pools.items():
            rows.append(
                {
                    "base_url": base_url,
                    "user_prefix": user_prefix,
                    "pool_size": pool_size,
                    "timeout": timeout,
                    "cache_size": pool.cache_size(),
                    "in_flight": pool.in_flight_count(),
                }
            )
        return rows
