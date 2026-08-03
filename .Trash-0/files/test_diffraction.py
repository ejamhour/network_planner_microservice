import pandas as pd
import pytest
from cisei_lib.core.rf.features import los_curve, fresnel_radii
# from cisei_lib.core.rf.diffraction2_old import deygout_from_v
from cisei_lib.core.profiles.geo_info import geoInfo
from cisei_lib.core.rf.diffraction import (
    delta_bullington, 
    bullington, 
    bullington_smooth, 
    spherical_earth_diffraction,
    deygout_three_peak )


# ---------------------------------------------------------
# Utilities
# ---------------------------------------------------------

def load_path_csv(path_id):
    """
    Load distances/elevations for pathX.csv with columns:
    'Distance (km)', 'DEM Height (m AMSL)' 
    Convert distances to meters.
    """
    df = pd.read_csv(f"tests/data/path{path_id}.csv")

    # Extract columns by exact name
    dist_km = df["Distance (km)"].astype(float).values
    elevations = df["DEM Height (m AMSL)"].astype(float).values

    # Convert km → meters
    distances = dist_km * 1000.0

    return elevations, distances


def load_reference_table():
    """
    Load the ITU reference rows from data/tests.csv.
    """
    df = pd.read_csv("tests/data/tests.csv", skiprows=1)
    # print(df)
    return df


# ---------------------------------------------------------
# Parameterization: The table contains many rows for paths 1–4.
# ---------------------------------------------------------

ref_table = load_reference_table()

@pytest.mark.parametrize("ref_row", ref_table.to_dict(orient="records"))
def test_delta_bullington_itu(ref_row):
    """
    Full ITU Δ-Bullington validation (old rfModel exact behavior).
    """

    # ITU reference parameters
    path = int(ref_row["Path"])
    h_tx  = float(ref_row["hts"])
    h_rx  = float(ref_row["hrs"])
    freq_mhz = float(ref_row["f"])
    freq_hz = freq_mhz * 1e6
    wavelength = 3e8 / freq_hz

    Lba_ref  = float(ref_row["Lba"])
    Lbs_ref  = float(ref_row["Lbs"])
    Lsph_ref = float(ref_row["Lsph"])
    L_ref    = float(ref_row["L"])

    # Load PathX.csv
    elevations, distances = load_path_csv(path)

    # --- compute old-model results ---
    Lba = bullington(
        elevations=elevations,
        distances=distances,
        h_tx=h_tx,
        h_rx=h_rx,
        freq_mhz=freq_mhz
    )

    Lbs, h_ts, h_rs = bullington_smooth(
        elevations=elevations,
        distances=distances,
        h_tx=h_tx,
        h_rx=h_rx,
        freq_mhz=freq_mhz
    )
    
    Lsph = spherical_earth_diffraction(
        distances=distances,
        h_ts=h_ts,
        h_rs=h_rs,
        freq_mhz=freq_mhz
    )

    Ltot = delta_bullington(elevations, distances, h_tx, h_rx, freq_mhz)
    tol = 0.01

    # Assertions
    assert abs(Lba  - Lba_ref)  < tol, f"Lba mismatch: {Lba} vs {Lba_ref}"
    assert abs(Lbs  - Lbs_ref)  < tol, f"Lbs mismatch: {Lbs} vs {Lbs_ref}"
    assert abs(Lsph - Lsph_ref) < tol, f"Lsph mismatch: {Lsph} vs {Lsph_ref}"
    assert abs(Ltot - L_ref)    < tol, f"L mismatch: {Ltot} vs {L_ref}"

    L_deg = deygout_three_peak(elevations, distances, h_tx, h_rx, freq_mhz)

    tol = 10.0   # very loose, not a correctness test

    assert abs(L_deg - Ltot) < tol, f"Deygout differs too much: {L_deg} vs {Ltot}"



