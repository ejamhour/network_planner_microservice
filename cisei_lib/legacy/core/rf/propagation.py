import numpy as np

"""
Propagation models: FSPL, Two-Ray Ground, and unified path-loss selection.
Pure functions. No state stored here.
"""

def fspl(distance, wavelength, correction_db=5):
    """
    Free-space path loss (FSPL), meters everywhere.
    ----------
    distance : Path length (m)
    wavelength : Wavelength (m)
    correction_db : Empirical short/medium distance correction (dB)
    -------
    returns: Path loss (dB)
    """

    # short / medium distance correction
    if distance < 500:
        correction = correction_db
    elif distance < 3000:
        correction = correction_db * (1 - (distance - 500) / 2500)
    else:
        correction = 0

    fspl_db = 20 * np.log10((4 * np.pi * distance) / wavelength)
    return fspl_db + correction


def two_ray_ground(distance, htx, hrx, wavelength, correction_db=5):
    """
    Two-ray ground reflection path loss.
    ----------
    distance : Path length (m)
    htx : Transmitter height above ground (m)
    hrx : Receiver height above ground (m)
    wavelength : Wavelength (m)
    correction_db : FSPL correction for fallback
    -------
    returns: Path loss (dB)
    """

    d_cross = (4 * htx * hrx) / wavelength

    # Beyond crossover → fallback to FSPL
    if distance > d_cross:
        return fspl(distance, wavelength, correction_db)

    return 40 * np.log10(distance) - 20 * np.log10(htx * hrx)


def path_loss(distance, htx, hrx, wavelength, correction_db=5):
    """
    Select FSPL or two-ray automatically.
    ----------
    distance : Path length (m)
    htx : TX height (m)
    hrx : RX height (m)
    wavelength : Wavelength (m)
    correction_db : FSPL correction (dB)
    -------
    returns: Path loss (dB)
    """

    d_cross = (4 * htx * hrx) / wavelength

    if distance < d_cross:
        return two_ray_ground(distance, htx, hrx, wavelength, correction_db)

    return fspl(distance, wavelength, correction_db)
