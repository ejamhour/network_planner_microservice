"""Portable raw-feature cache, independent of metric formulas and planner state."""
from copy import deepcopy
import json
from math import isfinite

from cisei_lib.io.async_geo_service_pool import AsyncGeoServicePool, FeatureRequest


class FeatureCache:
    def __init__(self):
        self.records = {}

    @staticmethod
    def key(source, request):
        return (source.rstrip("/"), AsyncGeoServicePool._make_key(request))

    def export(self):
        return {"format": "cisei-feature-cache", "version": 1,
                "entries": deepcopy(list(self.records.values()))}

    @classmethod
    def from_document(cls, document):
        if document.get("format") != "cisei-feature-cache" or document.get("version") != 1:
            raise ValueError("Unsupported feature cache format/version")
        if not isinstance(document.get("entries"), list):
            raise ValueError("Feature cache requires an entries list")
        cache = cls()
        for entry in document["entries"]:
            source = entry["source"]
            if not isinstance(source, str) or not source:
                raise ValueError("Feature source must be a nonempty service URL")
            values = entry["request"]
            for field in ("tx", "rx"):
                if len(values[field]) != 2:
                    raise ValueError(f"{field} requires latitude and longitude")
            numbers = [*values["tx"], *values["rx"], values["tx_ha"], values["rx_ha"], values["freq_mhz"]]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in numbers):
                raise ValueError("Feature coordinates, heights and frequency must be finite numbers")
            if values["freq_mhz"] <= 0:
                raise ValueError("Feature frequency must be positive")
            if not isinstance(values.get("options", {}), dict) or not isinstance(entry["features"], dict):
                raise ValueError("Feature options and features must be objects")
            json.dumps(entry, allow_nan=False)
            request = FeatureRequest(**values)
            key = cls.key(source, request)
            if key in cache.records:
                raise ValueError("Duplicate feature cache key")
            cache.records[key] = deepcopy(entry)
        return cache


class CachedGeo:
    """Start the external pool only when the project's raw cache misses."""
    def __init__(self, runtime, cache, **pool_options):
        self.runtime, self.cache, self.pool_options = runtime, cache, pool_options
        self.pool = None

    async def get_features(self, tx, rx, *, tx_ha, rx_ha, freq_mhz, **options):
        request = FeatureRequest(tuple(tx), tuple(rx), tx_ha, rx_ha, freq_mhz, options)
        source = self.pool_options["base_url"].rstrip("/")
        key = self.cache.key(source, request)
        if key not in self.cache.records:
            if self.pool is None:
                self.pool = await self.runtime.get_geo_pool(**self.pool_options)
            features = await self.pool.get_features(tx, rx, tx_ha=tx_ha, rx_ha=rx_ha,
                                                    freq_mhz=freq_mhz, **options)
            entry = {"source": source, "request": {
                "tx": list(tx), "rx": list(rx), "tx_ha": tx_ha, "rx_ha": rx_ha,
                "freq_mhz": freq_mhz, "options": deepcopy(options)}, "features": deepcopy(features)}
            # Never persist a cache artifact that cannot be imported later.
            FeatureCache.from_document({"format": "cisei-feature-cache", "version": 1, "entries": [entry]})
            self.cache.records[key] = entry
        return deepcopy(self.cache.records[key]["features"])
