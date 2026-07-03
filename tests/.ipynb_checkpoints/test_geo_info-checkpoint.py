# tests/test_geo_info.py
import os
from pathlib import Path
from cisei_lib.core.profiles.geo_info import geoInfo
from cisei_lib.io.minio_access import MinioAccess
import pytest
from geopy.distance import geodesic
import numpy as np


# Coordinates for the test
start = (-26.178913, -53.072063)
end   = (-26.161617, -53.015026)


# tests/test_geo_info.py
import os
from pathlib import Path
from cisei_lib.core.profiles.geo_info import geoInfo
from cisei_lib.io.minio_access import MinioAccess

def test_geo_info_init():
    """Check that geoInfo initializes correctly and retrieves info.json when missing."""

    # Ensure environment is set externally (source set_minio_env.sh before running pytest)
    assert "MINIO_DEFAULT_COVERAGE" in os.environ
    assert "MINIO_DEFAULT_ELEVATION" in os.environ
    assert "LOCAL_HOME_FOLDER" in os.environ

    g = geoInfo()

    # Must have MinioAccess ready
    assert isinstance(g.repo_access, MinioAccess)

    # Validate configured dataset paths
    assert g.coverage_path.endswith("WorldCover")
    assert g.elevation_path.endswith("URGSData")

    # Check that info.json is cached locally
    local_home = Path(os.environ["LOCAL_HOME_FOLDER"])
    cover_info = local_home / g.coverage_path / "info.json"
    elev_info = local_home / g.elevation_path / "info.json"

    # The files should now exist locally (downloaded if missing)
    assert cover_info.exists(), f"Missing coverage info.json at {cover_info}"
    assert elev_info.exists(), f"Missing elevation info.json at {elev_info}"

    print("geoInfo initialization verified successfully.")

def _compare_profiles(d_ref, v_ref, d_new, v_new, threshold=5):
    d_new = np.array(d_new)
    v_new = np.array(v_new)

    flagged = []
    for idx, (d, v) in enumerate(zip(d_ref, v_ref)):
        i = np.argmin(np.abs(d_new - d))
        err = v_new[i] - v
        if abs(err) > threshold:
            flagged.append((err, i))

    return flagged

def _require_env():
    required = ["MINIO_DEFAULT_COVERAGE", "MINIO_DEFAULT_ELEVATION", "LOCAL_HOME_FOLDER"]
    for var in required:
        if var not in os.environ:
            pytest.skip(f"Skipping: missing environment variable {var}")

def test_find_tiff_valid():
    _require_env()
    g = geoInfo()

    df = g.info_elev
    first = df.iloc[0]
    lat = (first.lat_n + first.lat_s) / 2
    lon = (first.lon_e + first.lon_w) / 2

    res = g.find_tiff(df, lat, lon)
    assert len(res) == 1, f"Expected one match, got {len(res)}"

def test_get_elevation_and_point():
    _require_env()
    g = geoInfo()

    df = g.info_elev
    first = df.iloc[0]
    lat = (first.lat_n + first.lat_s) / 2
    lon = (first.lon_e + first.lon_w) / 2

    elev = g.get_elevation(lat, lon)
    assert isinstance(elev, float)

    point = g.get_point(lat, lon)
    assert isinstance(point, tuple)
    assert len(point) == 2

def test_get_full_profile_and_distance():
    _require_env()
    g = geoInfo()

    df = g.info_elev
    first = df.iloc[0]
    start = ((first.lat_n + first.lat_s) / 2, (first.lon_w + first.lon_e) / 2)
    end = (start[0], start[1] + 0.01)

    d, h, h_t, h_b, c = g.get_full_profile(start, end, step=10)
    assert len(d) == len(h) == len(h_t) == len(h_b) == len(c)
    assert d[0] == 0
    assert d[-1] == pytest.approx(geodesic(start, end).meters, rel=0.05)

def test_highest_obstacle_valid():
    _require_env()
    g = geoInfo()

    df = g.info_elev
    first = df.iloc[0]
    start = ((first.lat_n + first.lat_s) / 2, (first.lon_w + first.lon_e) / 2)
    end = (start[0], start[1] + 0.02)

    result = g.highest_obstacle(start, end, 10, 10)
    assert set(result.keys()) == {"obs_h", "obs_dm", "obs_pos", "len_dm"}
    assert isinstance(result["obs_pos"], tuple)
    assert all(isinstance(v, (int, float)) for v in (result["obs_h"], result["obs_dm"], result["len_dm"]))

def test_profile_cross_multiple_tiles():
    """
    Crazy test: compute an elevation profile that crosses multiple GeoTIFF tiles.
    Ensures geoInfo.load_tiff() seamlessly switches datasets and maintains continuity.
    """

    _require_env()
    g = geoInfo()

    # --- Pick coordinates deliberately large apart ---
    # Adjust to your dataset, these values are inside Brazil (approx Paraná–Santa Catarina)
    start = (-26.5, -53.5)   # southwest
    end   = (-25.0, -51.5)   # northeast

    # This should easily cross multiple tiles if using ~1°x1° tiles
    d, h, h_t, h_b, c = g.get_full_profile(start, end, step=1000)  # step in meters

    # --- Sanity checks ---
    assert len(d) > 20, "Expected many sampling points across tiles"
    assert all(isinstance(x, (int, float)) for x in h)
    assert all(isinstance(y, (int, float)) for y in h_t)
    assert all(isinstance(z, (int, float)) for z in h_b)

    # --- Check cache behavior ---
    used_cover = (g._usage_index_path("cover")).read_text()
    used_elev  = (g._usage_index_path("elev")).read_text()
    print("Tiles accessed (cover):", used_cover[:200], "...")
    print("Tiles accessed (elev) :", used_elev[:200], "...")

    # At least 2 tiles should have been accessed for both datasets
    import json
    cov = len(json.loads(used_cover))
    ele = len(json.loads(used_elev))
    assert cov > 1 or ele > 1, "Expected multiple GeoTIFF tiles to be loaded"

def test_geoinfo_get_point_memory():
    import os, psutil, gc
    # os.environ["GDAL_CACHEMAX"] = "0"
    from cisei_lib.core.profiles.geo_info import geoInfo

    proc = psutil.Process(os.getpid())

    lat, lon = -26.18, -53.07   # fixed point inside one tile
    gi = geoInfo()
    mem0 = proc.memory_info().rss / (1024 * 1024)

    for i in range(1, 2001):
        gi.get_point(lat, lon)
        if i % 100 == 0:
            mem = proc.memory_info().rss / (1024 * 1024)
            print(f"{i} calls  mem={mem - mem0:.1f} MB")

def test_geoinfo_window_path_memory():
    import os, psutil
    from cisei_lib.core.profiles.geo_info import geoInfo
    from cisei_lib.core.profiles.geo_tools import points_along_line

    gi = geoInfo()
    proc = psutil.Process(os.getpid())

    start = (-26.18, -53.07)
    end   = (-26.16, -53.01)

    # generate a realistic path
    points = points_along_line(start, end, n=200)
    P = len(points)
    TOTAL = 2000
    n_iters = max(1, TOTAL // P)

    # ensure tile is loaded
    gi.load_tiff(points[0][0], points[0][1])

    # compute bounding window for all points
    rows, cols = zip(*(gi.src_elev.index(lon, lat) for lat, lon in points))
    r0, r1 = min(rows), max(rows) + 1
    c0, c1 = min(cols), max(cols) + 1

    print(n_iters)

    for i in range(1, n_iters + 1):
        data = gi.src_elev.read(1, window=((r0, r1), (c0, c1)))


        # simulate per-point access in NumPy only
        data2 = [data[r - r0, c - c0] for r, c in zip(rows, cols)]

        print(data2)

        mem = proc.memory_info().rss / (1024 * 1024)
        print(f"{i} path reads  mem={mem:.1f} MB")

def test_path_profile():    
    from cisei_lib.core.profiles.geo_info import geoInfo
    p1 = start = (-26.18, -53.07)
    p2 = end   = (-25.96, -53.01)
    gi = geoInfo()    

    segments = gi._tiles_in_path(p1, p2, gi.info_elev)
    assert segments[0]['p_start'] == start
    assert segments[-1]['p_end'] == end
    src  = gi.load_tiff_by_name(segments[0]['tile'])

    p1_s = segments[0]['p_start']
    p2_s = segments[0]['p_end']

    path = gi._grid_path(src, p1_s, p2_s)
    lon, lat = src.xy(*path[-1])
    assert abs(lat - p2_s[0]) < src.res[1]
    assert abs(lon - p2_s[1]) < src.res[0]
    
    d = gi._grid_distance(path, src)
 
    assert abs(geodesic(p1_s, p2_s).meters - d[-1]) < max(gi._get_tiff_resolution(src))

    profile = gi._sample_tile_profile(src, path)
    assert len(profile) == len(d)
    assert profile[0] == gi.get_elevation(*p1_s)
    assert profile[-1] == gi.get_elevation(*p2_s)

    d1, e1 = gi.profile_by_tiles(p1, p2)
    d2, e2 = gi.get_elevation_profile(p1, p2)

    err = _compare_profiles(d2, e2, d1, e1, 5)
    assert len(err) < len(d2)/10

    dist, elev, cover, *rest = gi.get_full_profile_by_tiles(p1, p2)

    pass

def test_get_full_profile_by_tiles_memory():
    import psutil, os, gc
    from cisei_lib.core.profiles.geo_info import geoInfo

    gi = geoInfo()
    p1 = (-26.18, -53.07)
    p2 = (-26.16, -53.01)

    proc = psutil.Process(os.getpid())

    gc.collect()
    rss0 = proc.memory_info().rss

    for _ in range(20):
        gi.get_full_profile_by_tiles(p1, p2)
        gc.collect()

    rss1 = proc.memory_info().rss

    # allow small fluctuations (e.g. caches, Python allocator)
    assert (rss1 - rss0) < 20 * 1024 * 1024  # 20 MB