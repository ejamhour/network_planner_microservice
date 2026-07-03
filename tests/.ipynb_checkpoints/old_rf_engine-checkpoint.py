# tests/test_rf_engine.py

import json
import os
from pathlib import Path
import pytest
from cisei_lib.core.rf.rf_engine import RFEngine
from cisei_lib.io.minio_access import MinioAccess
from pathlib import Path
import psutil
from datetime import datetime


minio = MinioAccess()

BASE_TRAINING = "luppi/copel1"                              # "luppi"
BASE_HOME     = os.environ["MINIO_HOME_FOLDER"]            # "home"

REQUIRED_FILE = "bhns_geo.json"

@pytest.fixture
def list_networks():
    """
    Lists subfolders under: home/<training_set>/
    Returns: ["AMP-S", "BEI-P", ...]
    """
    prefix = f"{BASE_HOME}/{BASE_TRAINING}/"
    objs = minio.client.list_objects(
        minio.bucket,
        prefix=prefix,
        recursive=True
    )

    networks = set()
    for obj in objs:
        # strip prefix
        rel = obj.object_name[len(prefix):]
        parts = rel.split("/", 1)
        if len(parts) > 1:
            networks.add(parts[0])
    return sorted(networks)

@pytest.fixture
def load_test_files(network):
    """
    Downloads and loads bhns_geo.json for one network
    from MinIO based on environment variables.
    """
    base = f"{BASE_TRAINING}/{network}"
    key = f"{base}/{REQUIRED_FILE}"

    local_path = minio.download(key)

    with open(local_path, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture
def extract_link_parameters(bhns_geo):
    """
    Robust extractor for bhns_geo.json (GeoJSON).
    Skips malformed nodes and malformed links silently.
    Returns a list of flat dicts.
    """

    nodes = {}  # name → node-data

    # ---- 1. Parse nodes safely ----
    for feat in bhns_geo.get("features", []):
        try:
            props = feat["properties"]
            geom  = feat["geometry"]

            name = props["name"]
            host = props.get("host_router")

            coords = geom.get("coordinates")
            if not coords or coords[0] is None or coords[1] is None:
                continue  # malformed coordinates → skip

            lon = float(coords[0])
            lat = float(coords[1])

            tx_pw     = float(props.get("GE RF Power", 0) or 0)
            gain      = float(props.get("ant_dB", 0) or 0)
            ant_type  = props.get("ant_type", "") or ""
            lqi = props.get("Down Stream Avg LQI")
            rssi = props.get("Down Stream Avg RSSI")
            snr = props.get("Down Stream Avg SNR")
            ant_height = props.get("Loc Altitude", 7) or 7

            nodes[name] = {
                "lat": lat,
                "lon": lon,
                "host": host,
                "tx_pw": tx_pw,
                "gain": gain,
                "ant_type": ant_type,
                "ant_height" : ant_height,
                "lqi" : lqi,
                "rssi" : rssi, 
                "snr" : snr
            }

        except Exception:
            # Any unexpected malformed node → skip
            continue

    # ---- 2. Build links parent → child safely ----
    results = []

    for name, nd in nodes.items():
        parent = nd.get("host")
        if not parent or parent == "None":
            continue
        if parent not in nodes:
            continue  # skip links where parent is missing

        try:
            tx = nodes[parent]
            rx = nodes[name]

            entry = {}

            # prefix all TX keys
            for k, v in tx.items():
                entry[f"tx_{k}"] = v

            # prefix all RX keys
            for k, v in rx.items():
                entry[f"rx_{k}"] = v

            results.append(entry)

        except Exception:
            continue


        except Exception:
            # malformed parent/child → skip
            continue

    return results

@pytest.fixture
def collect_test_cases():
    tests = []
    for network in list_networks():
        geo = load_test_files(network)
        tests.extend(extract_link_parameters(geo))
    return tests

@pytest.fixture
def test_collect_cases():
    cases = collect_test_cases()
    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)

    out_path = out_dir / "links.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)
    assert len(cases) >= 0   # we only want to ensure it runs without errors

@pytest.mark.parametrize("params", collect_test_cases())
def test_diffraction_estimation(params):

    engine = RFEngine(freq_hz=920e6)
    engine.load_profile(
        (params["tx_lat"], params["tx_lon"]),
        (params["rx_lat"], params["rx_lon"])
    )

    rssi_est = engine.rssi(
        params["tx_pw"],
        params["tx_gain"],
        params["rx_gain"]
    )

    # placeholder assertion until you define target behavior
    assert True


def test_fresnel_features():
    """
    Process ALL links in tests/data/links.json,
    compute Fresnel geometry features,
    save to tests/data/links_with_features.json,
    and print memory usage during execution.
    """

    data_dir = Path(__file__).parent / "data"
    in_file  = data_dir / "links.json"
    out_file = data_dir / "links_with_features.json"

    # Load input
    with open(in_file, "r", encoding="utf-8") as f:
        cases = json.load(f)

    enriched = []
    proc = psutil.Process(os.getpid())
    total = len(cases)

    # eng = RFEngine(freq_hz=case.get("freq_hz", 920e6))    
    eng = RFEngine(freq_hz= 920e6)    
    
    for i, case in enumerate(cases):
        tx = (case["tx_lat"], case["tx_lon"])
        rx = (case["rx_lat"], case["rx_lon"])
  
        eng.load_profile(tx, rx)
        fres = eng.fresnel_features()

        row = dict(case)
        row.update(fres)
        enriched.append(row)

        # progress + memory
        if i % 10 == 0 or i == total - 1:
            mem = proc.memory_info().rss / (1024 * 1024)
            print(f"{datetime.now().isoformat()}  {i+1}/{total}  mem={mem:.1f} MB")

    # Save enriched results
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    # Basic tests
    assert out_file.exists()
    assert len(enriched) == len(cases)
