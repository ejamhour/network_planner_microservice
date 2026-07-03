from geopy.distance import geodesic
import os
import math
import numpy as np
import json
import pandas as pd

'''
Obs. coordinates are (lat, lon)
'''

VALIDATION_DIR = '../Validation'

def geo_to_distance(ref, coord):
    ''' ref and coord (lat, lon)'''
    x = geodesic(ref, (ref[0], coord[1])).km 
    y = geodesic(ref, (coord[0], ref[1])).km 
    return (x,y)

def dms_to_dd(direction, degrees, minutes, seconds):  
    # more negative lon is to the west and more negative lat is to the south
    dd = degrees + minutes/60 + seconds/3600
    return dd if direction in ['N', 'E'] else -dd 

def calculate_bearing(start_point, end_point):
    lat1, lon1 = start_point
    lat2, lon2 = end_point
    
    d_lon = lon2 - lon1
    y = math.sin(math.radians(d_lon)) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - \
        math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(d_lon))
    
    brng = np.degrees(math.atan2(y, x))
    
    if brng < 0:
        brng += 360
    
    return brng

def points_along_line(start_point, end_point, **kwargs):

    resolution=kwargs.get('resolution', 10)
    min_points=kwargs.get('min_points', 50)
    max_points=kwargs.get('max_points', 1000)
    
    d = geodesic(start_point, end_point).meters
    num_points = int(d/resolution)
    num_points = max( min(num_points, max_points), min_points)

    line_points = []
    
    for i in range(num_points + 2):
        alpha = i / (num_points + 1)
        lat = (1 - alpha) * start_point[0] + alpha * end_point[0]
        lon = (1 - alpha) * start_point[1] + alpha * end_point[1]
        line_points.append((lat, lon))
    
    return line_points

def line_of_sight(d, h, ha_s, ha_d):

    hs = h[0] + ha_s
    hd = h[-1] + ha_d

    x1, y1 = d[0], hs
    x2, y2 = d[-1], hd
    slope = (y2 - y1) / (x2 - x1)
    intercept = y1 - slope * x1 

    f = lambda x : slope * x + intercept

    return [ f(x) for x in d ]

def load_test_profile(number):

    csvfile = os.path.join(VALIDATION_DIR, 'tests.csv')   
    data = pd.read_csv(csvfile, skiprows=1 )
    
    path = data['Path'][number]
    hts = data['hts'][number]
    hrs = data['hrs'][number]
    fmhz = data['f'][number]
    print(path, hts, hrs, fmhz)
    
    csvfile = os.path.join('Validation', f'path{path}.csv')   
    data = pd.read_csv(csvfile, names=['dkm', 'hm'], dtype={'dkm' : float, 'hm' : float}, skiprows=1 )

    dkm = data['dkm'].values.tolist()
    hm = data['hm'].values.tolist()

    return hts, hrs, fmhz, dkm, hm 

def load_measured_link(file, link):

    jsonfile = os.path.join(VALIDATION_DIR, file)   

    with open(jsonfile, 'r') as f:
        res = json.loads(f.read())
    
    try:
        s, d, rssi, pl, lqi, snr, rate = res['links'][link]        
        start, pw, s_ga = res['radios'][s][0:2], res['radios'][s][2], res['radios'][s][3]
        end, _, e_ga = res['radios'][d][0:2], res['radios'][d][2], res['radios'][d][3]
        return f'{s}-{d}', start, end, {'pw': pw, 's_ga': s_ga, 'e_ga': e_ga}, {'rssi': rssi, 'pl' : pl, 'lqi' : lqi}
    
    except Exception as e:
        print(e)

   # We need a dedicated, internal function for the Bresenham logic itself.

def bresenham_line(r1: int, c1: int, r2: int, c2: int) -> list[tuple[int, int]]:
    """
    [INTERNAL] Generates integer (row, col) indices along a line 
    between two integer points (r1, c1) and (r2, c2) using Bresenham's algorithm.
    """
    # Based on a common digital line algorithm implementation
    dr = abs(r2 - r1)
    dc = abs(c2 - c1)
    sr = 1 if r1 < r2 else -1
    sc = 1 if c1 < c2 else -1
    
    # Initialize error term
    err = dr - dc

    # Starting point
    r, c = r1, c1
    points = [(r, c)]
    
    while r != r2 or c != c2:
        e2 = 2 * err
        
        if e2 > -dc:
            err -= dc
            r += sr
        if e2 < dr:
            err += dr
            c += sc
            
        points.append((r, c))

    return points

def interpolate_point(self, A, B, D):
    """Returns the (lat, lon) coordinates for distance D (km) along line A→B."""
    dist_AB = geodesic(A, B).km
    frac = D / dist_AB
    lat = A[0] + frac * (B[0] - A[0])
    lon = A[1] + frac * (B[1] - A[1])
    return (lat, lon)

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

def fresnel_radius(d, D, freq_mhz):
    """
    First Fresnel radius at distance d along a link of length D.
    Works with scalar or array d, unordered.
    """
    import numpy as np

    c = 299_792_458.0
    lam = c / (freq_mhz * 1e6)

    d1 = d
    d2 = D - d

    with np.errstate(divide='ignore', invalid='ignore'):
        R = np.sqrt(lam * d1 * d2 / (d1 + d2))

    R = np.nan_to_num(R, nan=0.0)
    return R

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
