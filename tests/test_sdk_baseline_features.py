import asyncio
from copy import deepcopy
import json

import pytest

from cisei_planning_sdk.baseline_features import enrich_cache, training_records, validate_cache
from app.feature_cache import FeatureCache, CachedGeo


def documents():
    request = {"tx": [-25, -53], "rx": [-25.1, -53.1], "tx_ha": 7,
               "rx_ha": 9, "freq_mhz": 900, "options": {}}
    entry = {"source": "http://features:8080", "request": request, "features": {"fspl": 100}}
    cache = {"format": "cisei-feature-cache", "version": 1, "entries": [entry]}
    sample = {"id": "sample-1", "source": entry["source"], "request": deepcopy(request),
              "tx": {"name": "A", "pw": 30, "ant_gain": 8},
              "rx": {"name": "B", "ant_gain": 14}, "measures": {"rssi": -70}}
    measures = {"format": "cisei-link-measurements", "version": 1, "records": [sample]}
    return cache, measures


def test_preserves_raw_features_and_cache_reuse_without_geographic_requests():
    cache, measures = documents()
    original = deepcopy(cache)
    enriched, report = enrich_cache(cache, measures)
    assert cache == original
    assert report["added"] == 1
    for key in ("source", "request", "features"):
        assert enriched["entries"][0][key] == original["entries"][0][key]
    restored = FeatureCache.from_document(enriched)
    assert restored.export()["entries"][0]["observations"] == enriched["entries"][0]["observations"]
    class Runtime:
        async def get_geo_pool(self, **kwargs):
            pytest.fail("An enriched matching cache must not extract features")
    async def exercise():
        geo = CachedGeo(Runtime(), restored, base_url="http://features:8080")
        assert await geo.get_features((-25,-53),(-25.1,-53.1),tx_ha=7,rx_ha=9,freq_mhz=900) == {"fspl":100}
    asyncio.run(exercise())


def test_multiple_observations_idempotence_and_explicit_replacement():
    cache, measurements = documents()
    second = deepcopy(measurements["records"][0])
    second["id"] = "sample-2"
    second["measures"]["rssi"] = -90
    measurements["records"].append(second)
    enriched, report = enrich_cache(cache, measurements)
    assert report["added"] == 2 and len(enriched["entries"]) == 1
    records = training_records(enriched)
    assert [r["measures"]["rssi"] for r in records] == [-70, -90]
    assert records[0]["tx"]["pos"] == [-25,-53]
    assert records[0]["rx"]["ant_height"] == 9
    again, report = enrich_cache(enriched, measurements)
    assert again == enriched and report["unchanged"] == 2
    measurements["records"][0]["tx"]["pw"] = 20
    with pytest.raises(ValueError, match="replace=True"):
        enrich_cache(enriched, measurements)
    changed, report = enrich_cache(enriched, measurements, replace=True)
    assert report["updated"] == 1
    assert changed["entries"][0]["features"] == cache["entries"][0]["features"]


@pytest.mark.parametrize("field,value", [("freq_mhz",915),("tx_ha",8),("rx_ha",10),
    ("tx",[-25.01,-53]),("options",{"different":True})])
def test_mismatched_extraction_cannot_acquire_a_measurement(field,value):
    cache, measurements = documents()
    measurements["records"][0]["request"][field] = value
    original = deepcopy(cache)
    with pytest.raises(ValueError, match="no matching feature entry"):
        enrich_cache(cache, measurements)
    assert cache == original


def test_source_direction_duplicates_and_missing_fields_rejected():
    cache, measurements = documents()
    row = measurements["records"][0]
    row["source"] = "http://another-source"
    with pytest.raises(ValueError, match="no matching"):
        enrich_cache(cache, measurements)
    row["source"] = cache["entries"][0]["source"]
    row["request"]["tx"], row["request"]["rx"] = row["request"]["rx"], row["request"]["tx"]
    with pytest.raises(ValueError, match="no matching"):
        enrich_cache(cache, measurements)
    cache, measurements = documents()
    measurements["records"].append(deepcopy(measurements["records"][0]))
    with pytest.raises(ValueError, match="duplicate measurement id"):
        enrich_cache(cache, measurements)
    cache, measurements = documents()
    del measurements["records"][0]["tx"]["pw"]
    with pytest.raises(ValueError, match="observation.tx.pw"):
        enrich_cache(cache, measurements)


def test_invalid_measurement_and_conflicting_geometry_rejected():
    for value in [None, True, float("nan"), float("inf"), "-70"]:
        cache, measures = documents()
        measures["records"][0]["measures"]["rssi"] = value
        with pytest.raises(ValueError):
            enrich_cache(cache, measures)
    cache, measures = documents()
    measures["records"][0]["tx"]["ant_height"] = 1
    with pytest.raises(ValueError, match="conflicts"):
        enrich_cache(cache, measures)
    with pytest.raises(ValueError, match="no training observations"):
        training_records(cache)
    cache["entries"].append(deepcopy(cache["entries"][0]))
    with pytest.raises(ValueError, match="duplicate extraction"):
        validate_cache(cache)

def test_enrichment_failure_preserves_existing_features(tmp_path):
    from cisei_planning_sdk import BaselineProject
    cache, measurements = documents()
    folder = tmp_path / "projects/demo"
    folder.mkdir(parents=True)
    recipe = {"version": 1, "data": "projects/demo/features.json", "model": "metrics/model.json",
              "metric": "metrics/model.toml"}
    (folder / "baseline.json").write_text(json.dumps(recipe))
    feature_path = folder / "features.json"
    feature_path.write_text(json.dumps(cache))
    original = feature_path.read_bytes()
    bad = json.loads(json.dumps(measurements["records"][0]))
    bad["id"] = "bad"
    bad["request"]["freq_mhz"] = 450
    measurements["records"].append(bad)
    project = BaselineProject(folder)
    with pytest.raises(ValueError, match="records\\[1\\]"):
        project.enrich_features(measurements=measurements)
    assert feature_path.read_bytes() == original
