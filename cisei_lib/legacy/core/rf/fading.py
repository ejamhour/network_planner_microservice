   
import numpy as np

def nakagami_fading(m=1.0, omega=1.0, size=1, db=False, seed=None):
    """
    Generate Nakagami-m fading samples.

    Parameters:
        m (float): Fading shape parameter (>= 0.5). m = 1 is Rayleigh.
        omega (float): Mean power (E[R^2]) in linear scale.
        size (int): Number of samples to generate.
        db (bool): If True, return result in dB.
        seed (int): Optional random seed.

    Returns:
        np.ndarray: Array of fading gains (linear or dB).
    """
    if seed is not None:
        np.random.seed(seed)

    # Nakagami-m is a scaled gamma distribution of R^2
    power = np.random.gamma(shape=m, scale=omega/m, size=size)
    gain = np.sqrt(power)

    if db:
        return 20 * np.log10(gain)
    return gain

