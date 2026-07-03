# tests/test_rf_engine.py

import json
import os
from pathlib import Path
import pytest
from cisei_lib.core.rf.rf_engine import RFEngine
from cisei_lib.io.minio_access import MinioAccess
from math import hypot
from cisei_lib.core.rf.geo_supervisor import GeoSupervisor

# Constants
BASE_TRAINING = "luppi/copel1"
BASE_HOME = os.environ.get("MINIO_HOME_FOLDER", "home")
REQUIRED_FILE = "bhns_geo.json"


@pytest.fixture(scope="session")
def minio():
    """Provides a shared MinIO client instance."""
    return MinioAccess()
    
@pytest.fixture
def list_networks(minio):
    """Lists subfolders under: home/<training_set>/"""
    prefix = f"{BASE_HOME}/{BASE_TRAINING}/"
    objs = minio.client.list_objects(
        minio.bucket,
        prefix=prefix,
        recursive=True
    )

    networks = set()
    for obj in objs:
        rel = obj.object_name[len(prefix):]
        parts = rel.split("/", 1)
        if len(parts) > 1:
            networks.add(parts[0])
    return sorted(networks)

@pytest.fixture
def build_test_cases(minio, list_networks):
    """
    Downloads and prepares all test cases. 
    This only runs when a test requesting it is actually executed.
    """

    def _to_float(v, default=None):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    all_cases = []
    
    for network in list_networks:
        # Download
        base = f"{BASE_TRAINING}/{network}"
        key = f"{base}/{REQUIRED_FILE}"
        local_path = minio.download(key)

        # Parse JSON
        with open(local_path, "r", encoding="utf-8") as f:
            bhns_geo = json.load(f)

        # Extract parameters (Logic moved from standalone function to fixture flow)
        nodes = {}
        for feat in bhns_geo.get("features", []):
            try:
                props = feat["properties"]
                geom = feat["geometry"]
                name = props["name"]
                coords = geom.get("coordinates")
                
                if not coords or None in coords: continue

                nodes[name] = {
                    "lat": _to_float(coords[1]),
                    "lon": _to_float(coords[0]),
                    "host": props.get("host_router"),

                    "tx_pw": _to_float(props.get("GE RF Power"), 0.0),
                    "gain": _to_float(props.get("ant_dB"), 0.0),
                    "ant_height": _to_float(props.get("Loc Altitude"), 7.0),

                    "ant_type": props.get("ant_type", "OMNI"),
                    "sensitiviy": props.get("GE LNA State", "high-sensitivity"),

                    "lqi": _to_float(props.get("Down Stream Avg LQI"), 0.0),
                    "rssi": _to_float(props.get("Down Stream Avg RSSI"), 0.0),
                    "snr": _to_float(props.get("Down Stream Avg SNR"), 0.0),
                }
            except (KeyError, ValueError, TypeError):
                continue

        # Build link pairs
        for name, nd in nodes.items():
            parent = nd.get("host")
            if parent in nodes:
                tx, rx = nodes[parent], nodes[name]
                entry = {f"tx_{k}": v for k, v in tx.items()}
                entry.update({f"rx_{k}": v for k, v in rx.items()})
                all_cases.append(entry)
                
    return all_cases

@pytest.fixture
def all_test_cases():
    data_dir = Path(__file__).parent / "data"
    in_file = data_dir / "links.json"

    with open(in_file, "r", encoding="utf-8") as f:
        cases = json.load(f)
    
    return cases

def test_build_links_json(build_test_cases):
    """
    Integration test.
    Builds a frozen snapshot of links from MinIO data.
    """

    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)

    out_file = data_dir / "links.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(build_test_cases, f, indent=2, ensure_ascii=False)

    assert out_file.exists()
    assert out_file.stat().st_size > 0


def test_diffraction_estimation(all_test_cases):
    """
    Runs the engine on all discovered links. 
    By looping INSIDE the test, discovery remains fast.
    """
    engine = RFEngine(freq_hz=920e6)
    
    for params in all_test_cases:
        engine.load_profile(
            (params["tx_lat"], params["tx_lon"]),
            (params["rx_lat"], params["rx_lon"])
        )

        rssi_est = engine.rssi(
            params["tx_pw"],
            params["tx_gain"],
            params["rx_gain"]
        )
        assert rssi_est is not None

def test_fresnel_features(all_test_cases):
   
    def _f(v, default):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


    def _coord(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    out_file = data_dir / "links_with_features2.json"

    sup = GeoSupervisor(
        num_workers=4,
        max_links_per_worker=25,
        freq_hz=920e6,
    )

    enriched = []
    pending = 0

    try:
        for i, case in enumerate(all_test_cases):

            tx_lat = _coord(case.get("tx_lat"))
            tx_lon = _coord(case.get("tx_lon"))
            rx_lat = _coord(case.get("rx_lat"))
            rx_lon = _coord(case.get("rx_lon"))

            # Skip case if any coordinate is missing
            if None in (tx_lat, tx_lon, rx_lat, rx_lon):
                continue

            # Skip b2b links
            if hypot(tx_lat - rx_lat, tx_lon - rx_lon) < 1e-5:
                continue
            payload = {
                "link_parameters": {
                    "tx": {
                        "lat": tx_lat,
                        "lon": tx_lon,
                        "tx_pw": _f(case.get("tx_pw"), 30.0),
                        "gain": _f(case.get("tx_gain"), 0.0),
                        "ant_height": _f(case.get("tx_ant_height"), 7.0),
                    },
                    "rx": {
                        "lat": rx_lat,
                        "lon": rx_lon,
                        "gain": _f(case.get("rx_gain"), 0.0),
                        "ant_height": _f(case.get("rx_ant_height"), 7.0),
                    },
                },
                "requested_evaluation": {
                    "fresnel_features": True,
                    "diffraction_loss": True,
                    "path_loss" : True
                }
            }

            sup.submit(i, payload)
            pending += 1

        while pending:
            item = sup.collect(timeout=1)
            if item is None:
                continue
            
            req_id, result, worker_id = item

            if "error" in result:
                print("SKIP ERROR", req_id)
                print(all_test_cases[req_id])
                pending -= 1
                continue

            row = dict(all_test_cases[req_id])
            if "fresnel_features" in result:
                row.update(result["fresnel_features"])

            if "diffraction_loss" in result:
                row.update(result["diffraction_loss"])

            if "path_loss" in result:
                row.update(result["path_loss"])
          
            enriched.append(row)
            pending -= 1

    finally:
        sup.shutdown()

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    assert out_file.exists()


