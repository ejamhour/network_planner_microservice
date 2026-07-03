# Refactored features.py (with restored diffraction/geometry primitives)
# Core Fresnel decomposition and per-tile vegetation attenuation

import numpy as np


# ---------------------------------------------------------------------------
# Fresnel functions
# ---------------------------------------------------------------------------

def fresnel_radii(distances, freq_mhz):
    """
    Compute the first Fresnel zone radius along the path.

    distances : 1D array of cumulative distances [m]
    freq_mhz  : frequency in MHz
    Returns   : array of Fresnel radii [m], endpoints = 0
    """
    C = 299_792_458.0  # m/s
    lambda_m = C / (freq_mhz * 1e6)

    d1 = distances
    d2 = distances[-1] - distances

    # Avoid division by zero at endpoints
    with np.errstate(divide='ignore', invalid='ignore'):
        fr = np.sqrt(lambda_m * d1 * d2 / (d1 + d2))

    # Endpoints should be exactly zero
    eps = 1e-9
    fr[0] = eps
    fr[-1] = eps

    return fr

def get_fresnel_offsets(distances, target_v, freq_mhz):
    """
    Computes the lateral dh offsets for a specific horizontal Fresnel band.
    
    distances         : 1D array of absolute path distances [m]
    target_v          : The horizontal band scaling (-1.0 to 1.0)
    freq_mhz          : Frequency for wavelength calculation
    
    Returns           : 1D array of dh offsets [m]
    """
    # fr represents the radius (R_h or R_v) at each point
    fr = fresnel_radii(distances, freq_mhz)
    
    # Scale radius by the target band (e.g., -0.6 * radius)
    # Negative values shift to the Left, positive to the Right
    dh = target_v * fr
    
    return dh

def los_curve(elevations, distances, htx, hrx):
    """
    Linear line-of-sight height between endpoints.

    elevations : ground profile [m]
    distances  : cumulative distances [m]
    htx/hrx    : antenna heights above ground [m]
    Returns    : LOS height at each sample [m]
    """
    h_tx_abs = elevations[0] + htx
    h_rx_abs = elevations[-1] + hrx
    return h_tx_abs + (distances / distances[-1]) * (h_rx_abs - h_tx_abs)

def v_intrusions(elevations, distances, h_tx, h_rx, freq_mhz):
    """
    List Fresnel intrusions from intermediate terrain points.

    Returns tuples (index, v) for v > 0.
    """
    fr  = fresnel_radii(distances, freq_mhz)
    los = los_curve(elevations, distances, h_tx, h_rx)

    v_list = []
    for i in range(1, len(elevations) - 1):
        if fr[i] > 0:
            v = (elevations[i] - los[i]) / fr[i]
            if v > 0:
                v_list.append((i, v))
    return v_list


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Fresnel decomposition: terrain / vegetation / building / free
# ---------------------------------------------------------------------------

def fresnel_decomposition(elevations, covers, los, fresnel_r,
                          veg_class=10, bldg_class=50):
    """
    Fresnel decomposition over an effective surface profile (DSM-like).

    All quantities refer to geometric interaction between the radio path
    and an effective surface (terrain + vegetation + buildings).

    Returns per-sample features:

      v_surface_intrusion :
          Normalized Fresnel intrusion of the surface (v-parameter, clipped at 0).

      h_surface_intrusion :
          Vertical penetration depth of the surface into the Fresnel zone [m].
          Positive values indicate intrusion; negative values indicate clearance.

      h_surface_missing_100 :
          Remaining vertical margin before the Fresnel zone becomes fully blocked [m].

      h_veg_surface_missing_100 :
          Same as h_surface_missing_100, but only where land-cover is vegetation.

      h_bldg_surface_missing_100 :
          Same as h_surface_missing_100, but only where land-cover is built-up.
    """

    # Normalized surface intrusion (v-parameter, clipped)
    v_raw = (elevations - los) / fresnel_r
    v_surface_intrusion = np.maximum(v_raw, 0.0)

    # Absolute surface intrusion into Fresnel zone (meters)
    h_surface_intrusion = elevations - (los + fresnel_r)

    # Remaining margin to full Fresnel blockage
    h_surface_missing_100 = np.maximum((los + fresnel_r) - elevations, 0.0)

    # Conditional margins by surface class
    h_veg_surface_missing_100 = np.where(
        covers == veg_class, h_surface_missing_100, 0.0
    )

    h_bldg_surface_missing_100 = np.where(
        covers == bldg_class, h_surface_missing_100, 0.0
    )

    return (
        v_surface_intrusion,
        h_surface_intrusion,
        h_surface_missing_100,
        h_veg_surface_missing_100,
        h_bldg_surface_missing_100,
    )
 

# ---------------------------------------------------------------------------
# Per-tile vegetation attenuation
# ---------------------------------------------------------------------------

def vegetation_loss_per_tile(v_veg, dx, tau, density_factor):
    """
    Per-tile vegetation attenuation using effective depth.

    v_veg : obstruction fraction per tile
    dx    : tile length [m]
    tau   : extinction coefficient [Np/m]
    density_factor : canopy density scale
    Returns total loss [dB], array per tile.
    """
    d_eff = dx * density_factor * v_veg
    L_tile = 8.686 * tau * d_eff
    return float(np.sum(L_tile)), L_tile

# ---------------------------------------------------------------------------
# Built environment metrics
# ---------------------------------------------------------------------------

def building_crossing_segments(building_mask, distances):
    """
    Find contiguous building segments along path.

    building_mask : boolean-like array
    distances     : cumulative distances [m]
    Returns list of lengths and index segments.
    """
    mask = np.array(building_mask, dtype=bool)
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return [], []

    segments = []
    start = idx[0]

    for i in range(1, len(idx)):
        if idx[i] != idx[i - 1] + 1:
            segments.append((start, idx[i - 1]))
            start = idx[i]

    segments.append((start, idx[-1]))

    step = (distances[-1] - distances[0]) / (len(distances) - 1)
    lengths = [distances[j] - distances[i] + step for i, j in segments]

    return lengths, segments


def builtup_length(dm_m, cover, built_class=50):
    """
    Sum path length where cover equals built_class.

    dm_m  : cumulative distances [m]
    cover : class codes
    Returns total built-up length [m].
    """
    total = 0.0
    for i in range(len(cover) - 1):
        if cover[i] == built_class:
            total += dm_m[i + 1] - dm_m[i]
    return total


def urbanization_level(dm_m, cover, built_class=50):
    """
    Fraction of path inside built_class.

    Returns (ratio, built_length_m).
    """
    total = dm_m[-1] - dm_m[0]
    crossed = 0.0
    for i in range(len(cover) - 1):
        if cover[i] == built_class:
            crossed += dm_m[i + 1] - dm_m[i]
    return crossed / total, crossed
