"""
Restored diffraction models (Bullington, Bullington Smooth, Spherical Earth)
ported literally from old rfModel but adapted to the new architecture.

All external inputs are in meters.
Internal computations follow the old model exactly, using km where required.
"""

import numpy as np
import math
from cisei_lib.core.rf.features import v_intrusions


# ================================================================
# Helpers
# ================================================================

def _to_km(dist_m):
    return np.asarray(dist_m, dtype=float) / 1000.0

def _wavelength_m(freq_mhz: float) -> float:
    """Return wavelength (m) from frequency (MHz)."""
    C = 299792458.0
    return C / (freq_mhz * 1e6)



# ================================================================
# BULLINGTON  
# Classical Bullington diffraction model.
# Assumes a single dominant effective obstacle.
# Replaces the terrain by one equivalent knife-edge located at the maximum obstruction point.
# ================================================================

def bullington(elevations, distances, h_tx, h_rx, freq_mhz):
    """
    Literal port of old rfModel.Bullington(), preserving all logic.

    elevations: terrain heights (m)
    distances: distances (m)
    h_tx, h_rx: terminal heights above ground (m)
    wavelength_m: wavelength (m)
    """

    wavelength_m = _wavelength_m(freq_mhz)
    hn = np.asarray(elevations, float)
    dn_km = _to_km(distances)
    d_km = dn_km[-1]

    # absolute heights
    hts = hn[0] + h_tx
    hrs = hn[-1] + h_rx

    # constants
    re = 8500  # km (k = 4/3 built-in)
    Ce = 1 / re
    sizedn = len(dn_km)

    # -----------------------------------------
    # LOS TEST
    # -----------------------------------------
    STIM = [0.0] * (sizedn - 1)
    v = [0.0] * (sizedn - 1)

    for i in range(1, sizedn - 1):
        STIM[i] = (hn[i] + (500 * Ce * dn_km[i] * (d_km - dn_km[i])) - hts) / dn_km[i]

    STIM.pop(0)
    Stim = max(STIM)
    Str = (hrs - hts) / d_km

    # -----------------------------------------
    # PATH IS LOS
    # -----------------------------------------
    if Stim < Str:
        for i in range(1, sizedn - 1):
            var = ((hts * (d_km - dn_km[i])) + (hrs * dn_km[i])) / d_km
            v[i] = (
                (hn[i] + (500 * Ce * dn_km[i] * (d_km - dn_km[i])) - var)
                * math.sqrt((0.002 * d_km) / (wavelength_m * dn_km[i] * (d_km - dn_km[i])))
            )
        v.pop(0)
        vmax = max(v)
        if vmax > -0.78:
            j = 6.9 + 20 * math.log10(math.sqrt((vmax - 0.1)**2 + 1) + vmax - 0.1)
        else:
            j = 0
        Luc = j
        Lb = Luc + ((1 - math.exp(-Luc / 6)) * (10 + (0.02 * d_km)))

    # -----------------------------------------
    # PATH IS TRANSHORIZON
    # -----------------------------------------
    else:
        SRIM = [0.0] * (sizedn - 1)
        for i in range(1, sizedn - 1):
            SRIM[i] = (
                hn[i] + (500 * Ce * dn_km[i] * (d_km - dn_km[i])) - hrs
            ) / (d_km - dn_km[i])

        SRIM.pop(0)
        Srim = max(SRIM)

        Db = (hrs - hts + (Srim * d_km)) / (Stim + Srim)
        vb = (
            hts + (Stim * Db) - (((hts * (d_km - Db)) + (hrs * Db)) / d_km)
        ) * math.sqrt((0.002 * d_km) / (wavelength_m * Db * (d_km - Db)))

        if vb > -0.78:
            j = 6.9 + 20 * math.log10(math.sqrt((vb - 0.1)**2 + 1) + vb - 0.1)
        else:
            j = 0

        Luc = j
        Lb = Luc + ((1 - math.exp(-Luc / 6)) * (10 + (0.02 * d_km)))

    return Lb


# ================================================================
# BULLINGTON SMOOTH
# Smooth-Earth Bullington model.
# Uses a smoothed profile to compute diffraction and derive effective terminal heights.
# Represents terrain as statistically smoothed curvature.
# ================================================================

def bullington_smooth(elevations, distances, h_tx, h_rx, freq_mhz):
    """
    Literal port of old rfModel.Bullington_Smooth()
    returning (LBs, h_ts, h_rs)
    """
    hn = np.asarray(elevations, float)
    dn_km = _to_km(distances)
    d_km = dn_km[-1]
    sizehn = len(hn)

    # absolute heights (same as original code)
    hts = hn[0] + h_tx
    hrs = hn[-1] + h_rx

    # -----------------------------------------
    # V1, V2 integrals
    # -----------------------------------------
    V1 = [0] * sizehn
    V2 = [0] * sizehn
    for i in range(1, sizehn):
        V1[i] = (dn_km[i] - dn_km[i - 1]) * (hn[i] + hn[i - 1])
        V2[i] = (dn_km[i] - dn_km[i - 1]) * (
            (hn[i] * ((2 * dn_km[i]) + dn_km[i - 1]))
            + (hn[i - 1] * (dn_km[i] + (2 * dn_km[i - 1])))
        )

    V1.pop(0)
    V2.pop(0)
    v1 = sum(V1)
    v2 = sum(V2)

    hstip = ((2 * v1 * d_km) - v2) / (d_km**2)
    hsrip = (v2 - (v1 * d_km)) / (d_km**2)

    # -----------------------------------------
    # obstruction terms
    # -----------------------------------------
    hobi = [0] * sizehn
    A_obt = [0] * sizehn
    A_obr = [0] * sizehn

    for i in range(1, sizehn - 1):
        hobi[i] = hn[i] - (((hts * (d_km - dn_km[i])) + (hrs * dn_km[i])) / d_km)
        A_obt[i] = hobi[i] / dn_km[i]
        A_obr[i] = hobi[i] / (d_km - dn_km[i])

    hobi.pop(0)
    A_obt.pop(0)
    A_obr.pop(0)

    hobs = max(hobi)
    a_obt = max(A_obt)
    a_obr = max(A_obr)

    if hobs <= 0:
        hstp = hstip
        hsrp = hsrip
    else:
        gt = a_obt / (a_obt + a_obr)
        gr = a_obr / (a_obt + a_obr)
        hstp = hstip - (hobs * gt)
        hsrp = hsrip - (hobs * gr)

    hst = hn[0] if hstp > hn[0] else hstp
    hsr = hn[-1] if hsrp > hn[-1] else hsrp

    h_ts = hts - hst
    h_rs = hrs - hsr

    # -----------------------------------------
    # LBs (smooth Bullington) using flat hn_ = 0
    # -----------------------------------------
    hn_flat = np.zeros_like(hn)
    LBs = bullington(hn_flat, distances, h_ts, h_rs, freq_mhz)

    return LBs, h_ts, h_rs


# ================================================================
# OLD SPHERICAL-EARTH DIFFRACTION (literal port)
# ================================================================

def earth_diffraction(d_km, ae, h1, h2, Kv, freq_mhz):
    '''
    Influence of the electrical characteristics of the surface of the Earth
    First-term spherical-Earth diffraction loss 
    '''

    beta = 1
    X = 2.188*beta*(freq_mhz**(1/3))*(ae**(-2/3))*d_km
    Y1 = 9.575*(10**(-3))*beta*(freq_mhz**(2/3))*(ae**(-1/3))*h1
    Y2 = 9.575*(10**(-3))*beta*(freq_mhz**(2/3))*(ae**(-1/3))*h2
    if (X > 1.6):
        F = 11 + (10*(math.log10(X)))- (17.6*X)
    else:
        F = (-20*(math.log10(X)))- (5.6488*(X**1.425))
    
    B1 = beta*Y1
    B2=beta*Y2
    if (B1 > 2):
        G1 = (17.6*((B1-1.1)**(1/2)))-(5*(math.log10(B1-1.1)))-8
    else:
        G1 = 20*(math.log10(B1+(0.1*(B1**3))))
    
    if (B2 > 2):
        G2 = (17.6*((B2-1.1)**(1/2)))-(5*(math.log10(B2-1.1)))-8
    else:
        G2 = 20*(math.log10(B2+(0.1*(B2**3))))
    
    termo = 2 + (20*(math.log10(Kv)))
    if (G1 < termo):
        G1 = 2 + (20*(math.log10(Kv)))
    if (G2 < termo):
        G2 = 2 + (20*(math.log10(Kv)))

    return F,G1,G2

def spherical_earth_diffraction(distances, h_ts, h_rs, freq_mhz):

    '''
    Spherical Earth diffraction loss  
    ae = effective Earth radius (km)
    ep = effective relative permittivity
    delta = effective conductivity (S/m)
    A = Spherical-Earth diffraction loss 
    h = smallest clearance height between the curved-Earth path
    hreq = required clearance for zero diffraction loss
    ED = First-term spherical-Earth diffraction loss
    '''     
    if h_ts == h_rs:
        return 0   
    d_km = distances[-1]/1000
    lambda_ = _wavelength_m(freq_mhz)
    
    h1=h_ts
    h2=h_rs
    ae = 8500 #effective Earth radius (km)
    ep = 22 #effective relative permittivity
    delta = 0.003 #effective conductivity (S/m)
    d_los = math.sqrt(2*ae)*(math.sqrt(0.001*h1)+math.sqrt(0.001*h2))

    if (d_km >= d_los):
        Kh = 0.036*((ae*freq_mhz)**(-1/3))*((((ep - 1)**2)+((18*delta/freq_mhz)**2))**(-1/4))
        Kv = Kh*((ep**2) + ((18*delta/freq_mhz)**(1/2)))
        if  (Kv >= 0.001):
            F,G1,G2 = earth_diffraction(d_km, ae, h1, h2, Kv, freq_mhz); #First-term spherical-Earth diffraction loss
            A = -F-G1-G2    
            Lsph = A #Spherical-Earth diffraction loss
        else:
            A = 0
            Lsph = A #Spherical-Earth diffraction loss
    else: 
        m = (250*(d_km**2))/(ae*(h1+h2))
        c = (h1-h2)/(h1+h2)
        b = 2*math.sqrt((m+1)/(3*m))*math.cos((math.pi/3)+ ((1/3)*math.acos((3*c/2)*math.sqrt((3*m)/((m+1)**3)))));
        d1 = (d_km/2)*(1+b)
        d2 = d_km - d1
        h = (((h1-(500*(d1**2)/ae))*d2)+((h2-(500*(d2**2)/ae))*d1))/d_km #smallest clearance height between the curved-Earth path
        hreq = 17.456*math.sqrt((d1*d2*lambda_)/d_km) #required clearance for zero diffraction loss
        if (h > hreq):
            A = 0 
            Lsph = A #Spherical-Earth diffraction loss
        else:
            aem = 500*((d_km/(math.sqrt(h1)+math.sqrt(h2)))**2)
            Khm = 0.036*((aem*freq_mhz)**(-1/3))*((((ep - 1)**2)+((18*delta/freq_mhz)**2))**(-1/4))
            Kvm = Khm*((ep**2) + ((18*delta/freq_mhz)**(1/2)))
            if (Kvm > 0.001):
                F,G1,G2 = earth_diffraction(d_km, aem, h1, h2, Kvm, freq_mhz) #First-term spherical-Earth diffraction loss
                Ah = -F-G1-G2
                if (Ah < 0):
                    A = 0
                    Lsph = A #Spherical-Earth diffraction loss
                else:
                    A = (1-(h/hreq))*Ah
                    Lsph = A #Spherical-Earth diffraction loss
            else:
                A = 0
                Lsph = A #Spherical-Earth diffraction loss
    return Lsph



# ================================================================
# DELTA BULLINGTON
# ITU Δ-Bullington combined diffraction model.
# Blends classical Bullington, smooth-Bullington, and spherical-Earth diffraction.
# Recommended general-purpose diffraction model.
# ================================================================


def delta_bullington(elevations, distances, h_tx, h_rx, freq_mhz):
    """
    Use EXACT old Bullington, old Bullington Smooth, old Spherical Earth.
    """
    
    Lb = bullington(elevations, distances, h_tx, h_rx, freq_mhz)
    LBs, h_ts, h_rs = bullington_smooth(elevations, distances, h_tx, h_rx, freq_mhz)

    Lsph = spherical_earth_diffraction(distances, h_ts, h_rs, freq_mhz)

    return Lb + max(0.0, Lsph - LBs)


# ================================================================
# THREE PEAK DEGAULT
# ITU Δ-Bullington combined diffraction model.
# ================================================================

def deygout_three_peak(elevations, distances, h_tx, h_rx, freq_mhz):
    v_list = v_intrusions(elevations, distances, h_tx, h_rx, freq_mhz)
    v_list = [ v[1] for v in v_list ]

    if not v_list:
        return 0.0
    
    # strongest three intrusions
    v_top = sorted(v_list, reverse=True)[:3]

    def L_knife(v):
        if v <= -0.78:
            return 0.0
        return 6.9 + 20*np.log10(np.sqrt((v - 0.1)**2 + 1) + v - 0.1)

    return float(sum(L_knife(v) for v in v_top))
