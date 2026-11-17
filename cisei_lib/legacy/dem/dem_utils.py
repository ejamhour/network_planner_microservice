import os
import logging
from pathlib import Path
import json
import math
from math import hypot
import numpy as np
from bisect import bisect_left

import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.merge import merge
from rasterio.plot import show
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from rasterio.warp import reproject
from rasterio.windows import Window
from rasterio.warp import transform


from pyproj import Transformer, Geod
from geopy.distance import geodesic

from shapely.geometry import shape, Point, LineString, box
from shapely.ops import transform as shapely_transform

import matplotlib.pyplot as plt

from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

import requests
from collections import defaultdict

from io import BytesIO
import base64

logger = logging.getLogger(__name__)

class PointNotCoveredError(Exception):
    """Raised when at least one point is not covered by the dataset."""
    pass

# ------------ 1. INDEX CREATION ---------------------------------
def _utm_zone_lon_bounds_from_epsg(epsg: int):
    # EPSG:32601..32660 (north), 32701..32760 (south)
    if epsg is None:
        return None
    if 32601 <= epsg <= 32660:
        zone = epsg - 32600
    elif 32701 <= epsg <= 32760:
        zone = epsg - 32700
    else:
        return None
    west = -180 + (zone - 1) * 6
    east = west + 6
    return west, east, zone

def create_geotiff_index(folder_path, output_file="tile_index.json", points_per_edge=32, clip_to_utm_zone_for_display=True,):

    geod = Geod(ellps="WGS84")
    features = []

    folder_path = os.fspath(folder_path)

    def _apply_affine(T, c, r):
        # T: rasterio.transform.Affine (a,b,c,d,e,f)
        # (col,row) -> (x,y)
        x = T.c + T.a * c + T.b * r
        y = T.f + T.d * c + T.e * r
        return x, y

    files = [f for f in os.listdir(folder_path) if f.lower().endswith((".tif", ".tiff"))]

    for filename in files:
        file_path = os.path.join(folder_path, filename)

        with rasterio.open(file_path) as src:
            if src.crs is None:
                raise ValueError(f"Raster has no CRS: {file_path}")

            b = src.bounds
            is_projected = bool(src.crs and src.crs.is_projected)
            coord_type = "projected" if is_projected else "geographic"

            n = int(points_per_edge)
            if n < 2 or not is_projected:
                n = 2

            W = float(src.width)
            H = float(src.height)

            # Build footprint in pixel-corner space [0..W]x[0..H], then apply affine.
            # This is robust for rotation/shear and avoids the "width/height with center offset" issue.
            cols = np.linspace(0.0, W, n)
            rows = np.linspace(0.0, H, n)
            T = src.transform

            xs = []
            ys = []

            # top edge: row = 0, col: 0 -> W
            c = cols
            r = np.full(n, 0.0)
            x, y = _apply_affine(T, c, r)
            xs.extend(x.tolist()); ys.extend(y.tolist())

            # right edge: col = W, row: 0 -> H (skip first)
            c = np.full(n - 1, W)
            r = rows[1:]
            x, y = _apply_affine(T, c, r)
            xs.extend(x.tolist()); ys.extend(y.tolist())

            # bottom edge: row = H, col: W -> 0 (skip first)
            c = cols[::-1][1:]
            r = np.full(n - 1, H)
            x, y = _apply_affine(T, c, r)
            xs.extend(x.tolist()); ys.extend(y.tolist())

            # left edge: col = 0, row: H -> 0 (skip first)
            c = np.full(n - 1, 0.0)
            r = rows[::-1][1:]
            x, y = _apply_affine(T, c, r)
            xs.extend(x.tolist()); ys.extend(y.tolist())

            # Transform to WGS84 (robust axis order)
            tr = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
            lons, lats = tr.transform(xs, ys)

            lons = list(lons)
            lats = list(lats)

            # Close ring
            if (lons[0], lats[0]) != (lons[-1], lats[-1]):
                lons.append(lons[0])
                lats.append(lats[0])

            # Optional: clamp longitudes to nominal UTM zone span for visualization
            clipped = False
            epsg = src.crs.to_epsg()
            zone_info = _utm_zone_lon_bounds_from_epsg(epsg)

            if clip_to_utm_zone_for_display and zone_info is not None:
                west_zone, east_zone, _zone = zone_info
                lon_min, lon_max = min(lons), max(lons)

                if lon_min < west_zone or lon_max > east_zone:
                    lons = [min(max(lon, west_zone), east_zone) for lon in lons]
                    clipped = True

            # Area on (possibly clipped) polygon (keeps your original behavior)
            poly_area, _ = geod.polygon_area_perimeter(lons, lats)
            area_km2 = abs(poly_area) / 1_000_000

            west, east = min(lons), max(lons)
            south, north = min(lats), max(lats)

            affine_vector = [T.a, T.b, T.c, T.d, T.e, T.f]

            band_count = int(src.count)
            b1_dtype = src.dtypes[0] if src.dtypes and len(src.dtypes) >= 1 else None

            if src.nodatavals and len(src.nodatavals) >= 1:
                b1_nodata = src.nodatavals[0]
            else:
                b1_nodata = src.nodata

            try:
                b1_interp = src.colorinterp[0].name.lower() if src.colorinterp and len(src.colorinterp) >= 1 else None
            except Exception:
                b1_interp = None

            # Resolution (meters) at raster center (geodesic distance between adjacent pixel centers)
            res_m_x = None
            res_m_y = None
            try:
                r0 = int(src.height // 2)
                c0 = int(src.width // 2)
                c1 = min(c0 + 1, src.width - 1)
                r1 = min(r0 + 1, src.height - 1)

                x0, y0 = src.xy(r0, c0, offset="center")
                x1, y1 = src.xy(r0, c1, offset="center")
                x2, y2 = src.xy(r1, c0, offset="center")

                lon0, lat0 = tr.transform(x0, y0)
                lon1, lat1 = tr.transform(x1, y1)
                lon2, lat2 = tr.transform(x2, y2)

                res_m_x = float(geod.inv(lon0, lat0, lon1, lat1)[2])
                res_m_y = float(geod.inv(lon0, lat0, lon2, lat2)[2])
            except Exception:
                res_m_x = None
                res_m_y = None

            feature = {
                "type": "Feature",
                "bbox": [west, south, east, north],
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [list(zip(lons, lats))],
                },
                "properties": {
                    "id_filename": filename,
                    "crs_native": str(src.crs),
                    "px_width": src.width,
                    "px_height": src.height,
                    "geo_west": west,
                    "geo_south": south,
                    "geo_east": east,
                    "geo_north": north,
                    "affine_vector": affine_vector,
                    "band_count": band_count,
                    "b1_dtype": b1_dtype,
                    "b1_nodata": b1_nodata,
                    "b1_interp": b1_interp,
                    "coord_type": coord_type,
                    "res_m_x": None if res_m_x is None else round(res_m_x, 3),
                    "res_m_y": None if res_m_y is None else round(res_m_y, 3),
                    "area_km2": round(area_km2, 4),
                    "native_left": b.left,
                    "native_right": b.right,
                    "native_bottom": b.bottom,
                    "native_top": b.top,
                    "epsg": epsg,
                    "geometry_lon_clamped_for_display": clipped,
                },
            }
            features.append(feature)

    index_geojson = {"type": "FeatureCollection", "features": features}

    out = os.fspath(output_file)
    # If output_file includes a directory (or is absolute), honor it; otherwise write inside folder_path.
    output_path = out if os.path.dirname(out) else os.path.join(folder_path, out)

    with open(output_path, "w") as f:
        json.dump(index_geojson, f, indent=2)

# Create a txt index for the GeoTiff files (generic)
def create_txt_index(folder_path, output_file="tile_index.txt"):
    geod = Geod(ellps="WGS84")

    # Get all .tif files in the directory
    files = [f for f in os.listdir(folder_path) if f.endswith('.tif')]

    with open(output_file, 'w') as index:
        index.write("filename | min_lat | min_lon | max_lat | max_lon | area_sq_km\n")
        index.write("-" * 80 + "\n")

        for filename in files:
            file_path = os.path.join(folder_path, filename)

            with rasterio.open(file_path) as src:
                # 1. Transform bounds to Lat/Lon
                west, south, east, north = transform_bounds(src.crs, 'EPSG:4326', *src.bounds)

                # 2. Calculate area in square kilometers
                # Define the four corners of the tile
                lons = [west, east, east, west, west]
                lats = [south, south, north, north, south]

                # Use polygon_area_perimeter which accepts coordinate lists
                poly_area, _ = geod.polygon_area_perimeter(lons, lats)
                area_km2 = abs(poly_area) / 1_000_000

                output = (f"{filename} | {south:.6f} | {west:.6f} | "
                          f"{north:.6f} | {east:.6f} | {area_km2:.2f} km²\n")

                index.write(output)
                # print(f"Indexed: {filename}")

# ------------- 2. INDEX SEARCH --------------------------------------

# Search a tile in a GeoJSON index
def get_tile_index_info(geojson, tiff_name):

    if isinstance(geojson, dict):
        index_data = geojson
    elif isinstance(geojson, (str, Path)):
        with open(geojson, "r") as f:
            index_data = json.load(f)
    else:
        raise TypeError( f"geojson must be dict, str, or Path; got {type(geojson).__name__}" )

    tile = None
    bounds = {}
    for t in index_data['features']:
        if t['properties']['id_filename'] == tiff_name:
            tile = t
            break
    t = tile['properties']
    bounds = {
        'w':t['geo_west'],
        'e':t['geo_east'],
        's':t['geo_south'],
        'n':t['geo_north']}

    return t, bounds

# Find a tile name based that include the given coordinates (generic)
def find_tile_by_coord(geojson, lat, lon):
    if isinstance(geojson, dict):
        index_data = geojson
    elif isinstance(geojson, (str, Path)):
        with open(geojson, "r") as f:
            index_data = json.load(f)
    else:
        raise TypeError( f"geojson must be dict, str, or Path; got {type(geojson).__name__}" )


    found_for_point = False    

    for feature in index_data.get("features", []):
        props = feature["properties"]

        west = props["geo_west"]
        east = props["geo_east"]
        south = props["geo_south"]
        north = props["geo_north"]

        if (west <= lon <= east) and (south <= lat <= north):            
            found_for_point = True
            break

    if not found_for_point:
        raise PointNotCoveredError(
            f"Point ({lat}, {lon}) is not covered by this dataset"
        )

    return props

# Identifies which TIFF files are needed to cover a set of points
def find_tiles_by_list(geojson, points):
    """
    Identifies which TIFF files are needed to cover a set of points.

    Args:
        geojson_path: Path to the GeoJSON index file.
        points: List of (lat, lon) tuples.

    Returns:
        Sorted list of unique filenames (strings).

    Raises:
        PointNotCoveredError: if any point is not covered by the dataset.
    """

    if isinstance(geojson, dict):
        index_data = geojson
    elif isinstance(geojson, (str, Path)):
        with open(geojson, "r") as f:
            index_data = json.load(f)
    else:
        raise TypeError( f"geojson must be dict, str, or Path; got {type(geojson).__name__}" )

    required_files = set()

    for lat, lon in points:
        found_for_point = False

        for feature in index_data.get("features", []):
            props = feature["properties"]

            west = props["geo_west"]
            east = props["geo_east"]
            south = props["geo_south"]
            north = props["geo_north"]

            if (west <= lon <= east) and (south <= lat <= north):
                required_files.add(props["id_filename"])
                found_for_point = True
                break

        if not found_for_point:
            raise PointNotCoveredError(
                f"Point ({lat}, {lon}) is not covered by this dataset"
            )

    return sorted(required_files)

# Returns the segmented path with the required Tiffs
def find_tiles_by_path(geojson, start_latlon, end_latlon):
    """
    Segment a path into multiple parts, each contained within a single TIFF tile.
    Handles multiple tile crossings (Horizontal, Vertical, and Diagonal).
    """
    segments = []
    current_pt = list(start_latlon)
    target_pt = end_latlon

    # Tolerance to nudge the point into the next tile after a boundary crossing
    EPSILON = 1e-9

    while True:
        # 1. Identify the tile for the current point
        tile_info = find_tile_by_coord(geojson, current_pt[0], current_pt[1])

        if not tile_info:
            # If we fall off the map, we stop.
            # (Optional: return what we have or handle as error)
            break

        # Extract boundaries of the current tile
        tw, ts, te, tn = (tile_info['geo_west'], tile_info['geo_south'],
                          tile_info['geo_east'], tile_info['geo_north'])

        # 2. Check if the target is in the same tile
        if (ts <= target_pt[0] <= tn) and (tw <= target_pt[1] <= te):
            segments.append((tuple(current_pt), target_pt, tile_info['id_filename']))
            break

        # 3. Find where the path exits the current tile
        # Line equation: P(t) = current + t * (target - current)
        d_lat = target_pt[0] - current_pt[0]
        d_lon = target_pt[1] - current_pt[1]

        t_values = []

        # Check intersections with the 4 boundaries of the current rectangle
        if d_lon > 0: # Moving East
            t_values.append((te - current_pt[1]) / d_lon)
        elif d_lon < 0: # Moving West
            t_values.append((tw - current_pt[1]) / d_lon)

        if d_lat > 0: # Moving North
            t_values.append((tn - current_pt[0]) / d_lat)
        elif d_lat < 0: # Moving South
            t_values.append((ts - current_pt[0]) / d_lat)

        # We want the smallest positive t (the first boundary we hit)
        valid_t = [t for t in t_values if t > 0]
        if not valid_t:
            # Should not happen if target is outside tile
            segments.append((tuple(current_pt), target_pt, tile_info['id_filename']))
            break

        t_exit = min(valid_t)

        # Calculate intersection point
        exit_pt = [
            current_pt[0] + t_exit * d_lat,
            current_pt[1] + t_exit * d_lon
        ]

        # Add segment to list
        segments.append((tuple(current_pt), tuple(exit_pt), tile_info['id_filename']))

        # 4. Advance current_pt slightly past the boundary to enter the next tile
        current_pt = [
            exit_pt[0] + (d_lat * EPSILON if d_lat != 0 else 0),
            exit_pt[1] + (d_lon * EPSILON if d_lon != 0 else 0)
        ]

        # Safety break to prevent infinite loops at corners
        if len(segments) > 50:
            break

    return segments

def find_tiles_by_path_with_bboxes(geojson_index, start_latlon, end_latlon, freq_mhz):
    import math
    from pyproj import Transformer
    from shapely.geometry import LineString, box

    # Global Fresnel setup (meters)
    utm_global_epsg = get_utm_epsg(start_latlon[1], start_latlon[0])
    to_metric_global = Transformer.from_crs("EPSG:4326", utm_global_epsg, always_xy=True).transform

    tx_m = to_metric_global(start_latlon[1], start_latlon[0])
    rx_m = to_metric_global(end_latlon[1], end_latlon[0])
    L_km = math.hypot(rx_m[0] - tx_m[0], rx_m[1] - tx_m[1]) / 1000.0
    max_f1 = 17.32 * math.sqrt((L_km / 4) / (freq_mhz / 1000.0))  # meters
    padding = max_f1 * 1.2  # meters

    segments = []

    for tile in geojson_index['features']:
        props = tile['properties']
        native_crs = props['crs_native']

        # Decide a metric "work CRS" for geometric ops (buffer/intersection/bounds)
        is_projected = bool(props.get("coord_type") == "projected")
        if is_projected:
            work_crs = native_crs
        else:
            # Tile is geographic (e.g., EPSG:4326): choose local UTM by tile center
            lon_c = 0.5 * (props['geo_west'] + props['geo_east'])
            lat_c = 0.5 * (props['geo_south'] + props['geo_north'])
            work_crs = get_utm_epsg(lon_c, lat_c)

        to_work = Transformer.from_crs("EPSG:4326", work_crs, always_xy=True).transform
        work_to_geo = Transformer.from_crs(work_crs, "EPSG:4326", always_xy=True).transform

        # 1) Tile metric bounds in work CRS
        if work_crs == native_crs and is_projected:
            # Already metric; your index's native_* are consistent with this CRS
            t_left = props['native_left']
            t_right = props['native_right']
            t_bottom = props['native_bottom']
            t_top = props['native_top']
        else:
            # Native bounds are degrees; project tile corners to work CRS and rebuild min/max
            w = props['native_left']
            e = props['native_right']
            s = props['native_bottom']
            n = props['native_top']

            # corners in lon/lat because native_crs is geographic here
            x1, y1 = to_work(w, s)
            x2, y2 = to_work(w, n)
            x3, y3 = to_work(e, s)
            x4, y4 = to_work(e, n)

            t_left = min(x1, x2, x3, x4)
            t_right = max(x1, x2, x3, x4)
            t_bottom = min(y1, y2, y3, y4)
            t_top = max(y1, y2, y3, y4)

        tile_poly_work = box(t_left, t_bottom, t_right, t_top)

        # 2) Segment in work CRS (meters)
        s_work = to_work(start_latlon[1], start_latlon[0])
        e_work = to_work(end_latlon[1], end_latlon[0])

        # 3) Corridor vs tile box
        line_work = LineString([s_work, e_work])
        corridor_work = line_work.buffer(padding, cap_style=2)

        if not corridor_work.intersects(tile_poly_work):
            continue

        # 4) Boresight entry/exit within tile (for profile endpoints)
        boresight_intersection = line_work.intersection(tile_poly_work)
        if boresight_intersection.is_empty:
            seg_tx_work = s_work
            seg_rx_work = e_work
        else:
            coords = list(boresight_intersection.coords)
            seg_tx_work, seg_rx_work = coords[0], coords[-1]

        # 5) Build bbox in work CRS by intersecting corridor bounds with tile bounds
        c_minx, c_miny, c_maxx, c_maxy = corridor_work.bounds

        xmin = max(t_left, c_minx)
        xmax = min(t_right, c_maxx)
        ymin = max(t_bottom, c_miny)
        ymax = min(t_top, c_maxy)

        # 6) Convert bbox corners back to WGS84
        w_lon, s_lat = work_to_geo(xmin, ymin)
        e_lon, n_lat = work_to_geo(xmax, ymax)

        segments.append({
            'file': props['id_filename'],
            'bbox': (w_lon, s_lat, e_lon, n_lat),
            'tx': work_to_geo(seg_tx_work[0], seg_tx_work[1])[::-1],  # (lat, lon)
            'rx': work_to_geo(seg_rx_work[0], seg_rx_work[1])[::-1],  # (lat, lon)
            'radius': padding
        })

    return segments

# --------- 3. SIMPLE RASTER SAMPLING -------------------------------------

# Returns the values corresponding to a list of points
def get_samples(src, points, band_index=1):
    transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)

    # Transform all points at once for better performance
    lats, lons = zip(*points)
    xs, ys = transformer.transform(lons, lats)
    coords = zip(xs, ys)

    # src.sample returns a generator of arrays (one per band requested)
    # We use [0] because we are only sampling one band_index
    samples = [val[0] for val in src.sample(coords, indexes=[band_index])]

    # Handle NoData and convert to float32
    nodata = src.nodatavals[band_index - 1]
    cleaned_samples = [0 if v == nodata else v for v in samples]

    return np.array(cleaned_samples, dtype="float32")

# Return the tuple dist vs tiff info value for an opened rasterio object
def get_path_data_from_src(src, start_latlon, end_latlon, band_index=1, step=1):
    """
    Returns an array of (distance_from_source, elevation) tuples.
    band_index: The specific band to read (e.g., 2 for Building Height).
    """
    transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
    geod = Geod(ellps="WGS84")

    # Transform from geographic to raster CRS
    start_x, start_y = transformer.transform(start_latlon[1], start_latlon[0])
    end_x, end_y = transformer.transform(end_latlon[1], end_latlon[0])

    # Geodesic distance for the profile axis
    _, _, total_dist = geod.inv(
        start_latlon[1], start_latlon[0],
        end_latlon[1], end_latlon[0]
    )

    # Pixel coordinates in original resolution
    s_row, s_col = src.index(start_x, start_y)
    e_row, e_col = src.index(end_x, end_y)

    # Decimation for performance
    s_row_ds, s_col_ds = s_row // step, s_col // step
    e_row_ds, e_col_ds = e_row // step, e_col // step

    out_shape = (src.height // step, src.width // step)

    # Read requested band at decimated resolution
    data = src.read(
        band_index,
        out_shape=out_shape,
        resampling=Resampling.bilinear
    ).astype("float32")

    nodata = src.nodatavals[band_index - 1]
    if nodata is not None:
        data[data == nodata] = 0

    # Number of sampling points
    num_points = int(np.hypot(e_row_ds - s_row_ds, e_col_ds - s_col_ds))
    if num_points < 2:
        num_points = 2

    rows = np.linspace(s_row_ds, e_row_ds, num_points).astype(int)
    cols = np.linspace(s_col_ds, e_col_ds, num_points).astype(int)

    rows = np.clip(rows, 0, data.shape[0] - 1)
    cols = np.clip(cols, 0, data.shape[1] - 1)

    elevations = data[rows, cols]
    distances = np.linspace(0, total_dist, num_points)

    return np.column_stack((distances, elevations))


# --------- 4. SURFACE DICT CREATION ------------------------------------------

# Auxiliar function to 2D analysis
def get_utm_epsg(lon, lat):
    """
    Correctly calculates the UTM EPSG code for a given WGS84 point.
    Curitiba (~ -49, -25) -> EPSG:32722
    """
    # Zone calculation: 1 + floor((lon + 180) / 6)
    zone = math.floor((lon + 180) / 6) + 1

    # Handle the UTM Special Cases (Optional for most, but good for robust code)
    if lat >= 56.0 and lat < 64.0 and lon >= 3.0 and lon < 12.0:
        zone = 32
    if lat >= 72.0 and lat < 84.0:
        if lon >= 0.0 and lon < 9.0: zone = 31
        elif lon >= 9.0 and lon < 21.0: zone = 33
        elif lon >= 21.0 and lon < 33.0: zone = 35
        elif lon >= 33.0 and lon < 42.0: zone = 37

    # EPSG 326xx is North, 327xx is South
    epsg_base = 32600 if lat >= 0 else 32700
    return f"EPSG:{epsg_base + zone}"

# Check bounds
def check_bounds(src, bbox, tx_latlon, rx_latlon):
    """
    1. Validates that TX/RX are within the provided BBox.
    2. Validates that the BBox corners are strictly within the Raster's pixel grid.
    """
    west, south, east, north = bbox
    tx_lat, tx_lon = tx_latlon
    rx_lat, rx_lon = rx_latlon

    # --- CHECK 1: TX/RX inside the BBox ---
    # Using a small epsilon to handle floating point noise
    eps = 1e-9
    tx_in_bbox = (west - eps <= tx_lon <= east + eps) and (south - eps <= tx_lat <= north + eps)
    rx_in_bbox = (west - eps <= rx_lon <= east + eps) and (south - eps <= rx_lat <= north + eps)

    if not (tx_in_bbox and rx_in_bbox):
        print(f"FAILED: TX/RX segment not contained in provided BBox.")
        return False

    # --- CHECK 2: BBox strictly inside Raster Pixel Space ---
    # We transform the BBox corners to Native (Projected) coordinates
    to_native = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)

    # Check all four corners of the BBox against the raster pixel indices
    corners = [
        (west, south), (east, south), (west, north), (east, north)
    ]

    for lon, lat in corners:
        x_nat, y_nat = to_native.transform(lon, lat)

        # src.index returns the (row, col) for a metric coordinate
        row, col = src.index(x_nat, y_nat)

        # If any index is negative or >= dimensions, the BBox is out of bounds
        if not (0 <= row < src.height and 0 <= col < src.width):
            print(f"FAILED: BBox corner ({lon}, {lat}) maps to pixel ({row}, {col}), "
                  f"which is outside raster {src.height}x{src.width}")
            return False

    return True

# Prepare a bouding box for window extraction (obsolete)
def compute_corridor_bbox(tx_lat, tx_lon, rx_lat, rx_lon, radius_m, resolution=30.0):
    """
    Computes a BBox with safety padding to prevent edge clipping during rotation.
    'resolution' should be the pixel size of your raster (e.g., 30 for SRTM).
    """
    mid_lon, mid_lat = (tx_lon + rx_lon) / 2, (tx_lat + rx_lat) / 2
    utm_epsg = get_utm_epsg(mid_lon, mid_lat)

    to_metric = Transformer.from_crs("EPSG:4326", utm_epsg, always_xy=True)
    to_geo    = Transformer.from_crs(utm_epsg, "EPSG:4326", always_xy=True)

    tx_x, tx_y = to_metric.transform(tx_lon, tx_lat)
    rx_x, rx_y = to_metric.transform(rx_lon, rx_lat)

    dx, dy = rx_x - tx_x, rx_y - tx_y
    L = math.hypot(dx, dy)
    px, py = -dy / L, dx / L

    # --- THE IMPROVEMENT: PADDING ---
    # 1. Add a 10% safety buffer to the radius
    # 2. Add 2x the raster resolution to ensure interpolation has 'look-ahead' pixels
    padded_radius = (radius_m * 1.1) + (2 * resolution)

    corners_x = [
        tx_x + px * padded_radius, tx_x - px * padded_radius,
        rx_x + px * padded_radius, rx_x - px * padded_radius
    ]
    corners_y = [
        tx_y + py * padded_radius, tx_y - py * padded_radius,
        rx_y + py * padded_radius, rx_y - py * padded_radius
    ]

    # Calculate min/max and apply an additional linear buffer to the ends (TX/RX tips)
    # This prevents the TX/RX antennas from being right on the edge of the array
    xmin, xmax = min(corners_x) - resolution, max(corners_x) + resolution
    ymin, ymax = min(corners_y) - resolution, max(corners_y) + resolution

    west, south = to_geo.transform(xmin, ymin)
    east, north = to_geo.transform(xmax, ymax)

    return west, south, east, north

# Create a 2D dict with longitudinal and orthogonal distances vs value along the path
def extract_and_profile_legacy(src, west, south, east, north, tx_latlon, rx_latlon, radius_m = None, band_index=1, key_decimals=1):
    """
    Extract raster values in a geographic window and project them into a 1-D TX–RX–aligned corridor profile,
    grouping samples by distance along the link (meters) with lateral offsets limited to radius_m.
    src: rasterio dataset; west,south,east,north: WGS84 bounds; tx_latlon,rx_latlon: (lat,lon);
    radius_m: safety guard corridor half-width; band_index: raster band; key_decimals: distance bin rounding
    """
    # 1. Determine local UTM CRS
    mid_lon, mid_lat = (west + east) / 2, (south + north) / 2
    # Ensure get_utm_epsg is available in your scope
    metric_crs = get_utm_epsg(mid_lon, mid_lat)

    # 2. Setup Transformers
    # to_native: Geo -> Raster's Internal CRS
    # to_metric: Raster's Internal CRS -> Local UTM
    to_native = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
    to_metric = Transformer.from_crs(src.crs, metric_crs, always_xy=True)

    # 3. Define and Read Window
    w_nat, s_nat = to_native.transform(west, south)
    e_nat, n_nat = to_native.transform(east, north)
    window = rasterio.windows.from_bounds(w_nat, s_nat, e_nat, n_nat, src.transform)
    window = window.intersection(rasterio.windows.Window( 0, 0, src.width, src.height))
    window = window.round_offsets().round_lengths()

    data = src.read(band_index, window=window)
    H, W = data.shape

    # --- ADD THIS GUARD CLAUSE ---
    if H == 0 or W == 0:
        logger.error(f'{tx_latlon} or {rx_latlon} are outside bounds')
        # Return an empty dict so merge_corridors_dicts doesn't crash
        return defaultdict(list)
        
    # 4. Generate Metric 1D Vectors with Spatial Context
    rows = np.arange(window.row_off, window.row_off + H)
    cols = np.arange(window.col_off, window.col_off + W)

    # Get the native coordinates for the horizontal axis (X)
    # We pass the same row_off for all columns to get a single 1D span
    xs_native, _ = rasterio.transform.xy(src.transform, [window.row_off] * W, cols, offset='center')

    # Get the native coordinates for the vertical axis (Y)
    # We pass the same col_off for all rows to get a single 1D span
    _, ys_native = rasterio.transform.xy(src.transform, rows, [window.col_off] * H, offset='center')

    # Convert to numpy arrays for the transformer
    xs_native = np.array(xs_native)
    ys_native = np.array(ys_native)

    # Context for 1D transforms (the center of the opposite axis)
    center_x_native = xs_native[len(xs_native) // 2]
    center_y_native = ys_native[len(ys_native) // 2]

    # Transform 1D arrays into UTM (EPSG:32722)
    # This uses the context to avoid the 1.2-million-meter offset
    x_m_1d, _ = to_metric.transform(xs_native, np.full_like(xs_native, center_y_native))
    _, y_m_1d = to_metric.transform(np.full_like(ys_native, center_x_native), ys_native)

    # 5. Transform TX/RX into the exact same Metric space
    tx_nat_x, tx_nat_y = to_native.transform(tx_latlon[1], tx_latlon[0])
    tx_x, tx_y = to_metric.transform(tx_nat_x, tx_nat_y)

    rx_nat_x, rx_nat_y = to_native.transform(rx_latlon[1], rx_latlon[0])
    rx_x, rx_y = to_metric.transform(rx_nat_x, rx_nat_y)

    # 6. Rotate and Build the Profile
    LOS_dx, LOS_dy = rx_x - tx_x, rx_y - tx_y
    L = math.hypot(LOS_dx, LOS_dy)
    ux, uy = LOS_dx / L, LOS_dy / L

    # Vectorized relative calculation (Transmitter becomes 0,0)
    rel_x = x_m_1d[np.newaxis, :] - tx_x
    rel_y = y_m_1d[:, np.newaxis] - tx_y

    # d_raw = distance from TX along link
    # dh    = perpendicular distance from link
    d_raw = (rel_x * ux) + (rel_y * uy)
    dh    = (-rel_x * uy) + (rel_y * ux)

    # 7. Final Dictionary Assembly (with Filtering)

    flat_d = d_raw.ravel()
    flat_dh = dh.ravel()
    flat_v = data.ravel()

    if x_m_1d.size < 2:
        bin_size = L / max(flat_d.size - 1, 1)
    else:
        bin_size = abs(x_m_1d[1] - x_m_1d[0])

    result = defaultdict(list)

    if radius_m is None:
        radius_m = max(abs(v[1] - v[0]) if v.size >= 2 else 0.0 for v in (x_m_1d, y_m_1d))

    for i in range(len(flat_v)):
        if abs(flat_dh[i]) <= radius_m*2.5:
            # Group into 1D distance buckets
            d_key = round(round(flat_d[i] / bin_size) * bin_size, key_decimals)
            result[d_key].append((round(flat_dh[i], 1), flat_v[i]))

    return result

def extract_and_profile(src, west, south, east, north, tx_latlon, rx_latlon, radius_m=None, band_index=1, key_decimals=1,):

    # 1. CRS normalization
    to_native = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)

    if src.crs.is_projected:
        to_metric = None
        mid_lon, mid_lat = (west + east) / 2, (south + north) / 2
        logger.debug(get_utm_epsg(mid_lon, mid_lat), src.crs)
    else:
        mid_lon, mid_lat = (west + east) / 2, (south + north) / 2
        metric_crs = get_utm_epsg(mid_lon, mid_lat)
        to_metric = Transformer.from_crs(src.crs, metric_crs, always_xy=True)

    # Transform points to Native
    tx_nat_x, tx_nat_y = to_native.transform(tx_latlon[1], tx_latlon[0])
    rx_nat_x, rx_nat_y = to_native.transform(rx_latlon[1], rx_latlon[0])
    w_nat, s_nat = to_native.transform(west, south)
    e_nat, n_nat = to_native.transform(east, north)

    # Check if the native transform of the TX point matches the raster's metric system
    tx_nat_x, tx_nat_y = to_native.transform(tx_latlon[1], tx_latlon[0])
    logger.debug(f"TX Native: {tx_nat_x}, {tx_nat_y}")
    logger.debug(f"Raster Bounds: {src.bounds}")

    # Check the pixel index of the TX point
    row, col = src.index(tx_nat_x, tx_nat_y)
    logger.debug(f"TX Pixel Row/Col: {row}, {col}")

    # 2. Bound ordering and Invariant enforcement
    # Some CRSs have inverted axes; we must ensure min/max logic
    left = min(w_nat, e_nat, tx_nat_x, rx_nat_x)
    right = max(w_nat, e_nat, tx_nat_x, rx_nat_x)
    bottom = min(s_nat, n_nat, tx_nat_y, rx_nat_y)
    top = max(s_nat, n_nat, tx_nat_y, rx_nat_y)

    # 1. Map TX/RX and BBox to Pixel Space
    tx_row, tx_col = src.index(tx_nat_x, tx_nat_y)
    rx_row, rx_col = src.index(rx_nat_x, rx_nat_y)

    # Map BBox corners to pixel space
    row_start, col_start = src.index(left, top)     # Top-left of bbox
    row_stop, col_stop   = src.index(right, bottom) # Bottom-right of bbox

    # 2. Define the Window (ensuring min/max order)
    c_min, c_max = min(col_start, col_stop, tx_col, rx_col), max(col_start, col_stop, tx_col, rx_col)
    r_min, r_max = min(row_start, row_stop, tx_row, rx_row), max(row_start, row_stop, tx_row, rx_row)

    # 3. Intersection & Abort Check
    # We only care about the overlap between our target and the available pixels

    min_w = 8
    min_h = 8

    # existing clamped window
    win_left   = max(0, int(c_min))
    win_top    = max(0, int(r_min))
    win_right  = min(src.width,  int(c_max) + 1)
    win_bottom = min(src.height, int(r_max) + 1)

    # LOS midpoint in pixel coords (use your already computed tx_row/tx_col, rx_row/rx_col)
    c_ctr = int(round((tx_col + rx_col) / 2))
    r_ctr = int(round((tx_row + rx_row) / 2))

    # Ensure min width centered on LOS
    cur_w = win_right - win_left
    if cur_w < min_w:
        half = min_w // 2
        win_left  = max(0, c_ctr - half)
        win_right = min(src.width, win_left + min_w)
        win_left  = max(0, win_right - min_w)

    # Ensure min height centered on LOS
    cur_h = win_bottom - win_top
    if cur_h < min_h:
        half = min_h // 2
        win_top    = max(0, r_ctr - half)
        win_bottom = min(src.height, win_top + min_h)
        win_top    = max(0, win_bottom - min_h)


    final_window = Window(win_left, win_top, win_right - win_left, win_bottom - win_top)

    if final_window.width <= 0 or final_window.height <= 0:
        logger.error("Aborting: Requested corridor is entirely outside the raster coverage.")
        return defaultdict(list)

    # --- INCONSISTENCY CHECK ---
    if final_window.width <= 0 or final_window.height <= 0:
        logger.error(f"Aborting: Inconsistent Window. Window dims: {final_window.width}x{final_window.height}")
        logger.error(f"Native Bounds: L:{left}, B:{bottom}, R:{right}, T:{top}")
        return defaultdict(list)    
       
    data = src.read(band_index, window=final_window, masked = True)
    H, W = data.shape

    # 4. Generate Native Coordinate Grid
    rows = np.arange(final_window.row_off, final_window.row_off + H)
    cols = np.arange(final_window.col_off, final_window.col_off + W)

    xs_1d, _ = rasterio.transform.xy(src.transform, [final_window.row_off]*W, cols, offset='center')
    _, ys_1d = rasterio.transform.xy(src.transform, rows, [final_window.col_off]*H, offset='center')

    X_nat, Y_nat = np.meshgrid(xs_1d, ys_1d)

    # 5. Native -> Metric Conversion
    if to_metric is None:
        X_m, Y_m = X_nat, Y_nat
        tx_x, tx_y, rx_x, rx_y = tx_nat_x, tx_nat_y, rx_nat_x, rx_nat_y
        res_x, res_y = src.res
    else:
        X_m, Y_m = to_metric.transform(X_nat, Y_nat)
        tx_x, tx_y = to_metric.transform(tx_nat_x, tx_nat_y)
        rx_x, rx_y = to_metric.transform(rx_nat_x, rx_nat_y)


        res_x = math.hypot(X_m[0,1] - X_m[0,0], Y_m[0,1] - Y_m[0,0])
        res_y = math.hypot(X_m[1,0] - X_m[0,0], Y_m[1,0] - Y_m[0,0])


    # 6. TX–RX Frame Rotation
    dx, dy = rx_x - tx_x, rx_y - tx_y
    L = math.hypot(dx, dy)
    if L == 0: return defaultdict(list)
    ux, uy = dx / L, dy / L

    rel_x, rel_y = X_m - tx_x, Y_m - tx_y
    d_raw = rel_x * ux + rel_y * uy
    dh    = -rel_x * uy + rel_y * ux

    # 7. Binning
    
    flat_d  = d_raw.ravel()
    flat_dh = dh.ravel()
    flat_v  = data.ravel()

    bin_size = res_x
    diag = math.hypot(res_x, res_y)

    result = defaultdict(list)

    for i in range(flat_v.size):
        if np.ma.is_masked(flat_v[i]):
            continue

        v = float(flat_v[i])
        if not np.isfinite(v):
            continue

        d = float(flat_d[i])
        dh_i = float(flat_dh[i])

        if d < 0.0 or d > L:
            continue

        if radius_m is not None and abs(dh_i) > (radius_m + diag):
            continue

        d_key = round(round(d / bin_size) * bin_size, key_decimals)
        result[d_key].append((round(dh_i, 1), v))

    return result

# Merge 2D dictionaries for segmented paths
def merge_corridors_dicts(dicts, seg_endpoints):
    """
    Stitches multiple path segments into a single continuous profile.

    Args:
        dicts: [res0, res1, ...] (Relative dictionaries from extraction)
        seg_endpoints: [(tx0, rx0), (tx1, rx1), ...] in (lat, lon)
    """
    # 1. Get the master UTM zone based on the starting point of the link
    first_tx = seg_endpoints[0][0]
    utm_epsg = get_utm_epsg(first_tx[1], first_tx[0]) # Using your helper

    # 2. Setup the global metric transformer
    to_metric = Transformer.from_crs("EPSG:4326", utm_epsg, always_xy=True)

    merged = defaultdict(list)

    # 3. Define the Global Origin (Master TX)
    m_tx_x, m_tx_y = to_metric.transform(first_tx[1], first_tx[0])

    for i, corr in enumerate(dicts):
        # 4. Find where THIS segment's transmitter sits on the global ruler
        curr_tx = seg_endpoints[i][0]
        c_tx_x, c_tx_y = to_metric.transform(curr_tx[1], curr_tx[0])

        # Calculate the absolute distance from the Master TX to this segment's start
        global_start_offset = hypot(c_tx_x - m_tx_x, c_tx_y - m_tx_y)

        # 5. Shift local distances (d) into global distances
        for local_d, points in corr.items():
            # local_d is relative to curr_tx; global_d is relative to master_tx
            global_d = round(local_d + global_start_offset, 1)

            # Using extend handles overlapping pixels between tiles
            merged[global_d].extend(points)

    return dict(merged)


# ------------5. SURFACE DICT USAGE  ----------------------------------------

# Extract 1D profile from dict (nearest key to path)
def surface_to_1D(surface_dict):
    merged_res = surface_dict
    centerline_profile = []
    sorted_keys = sorted(merged_res.keys())

    for d in sorted_keys:
        points = merged_res[d]
        # Find the tuple (dh, v) where abs(dh) is minimum
        best_point = min(points, key=lambda p: abs(p[0]))
        centerline_profile.append((d, best_point[1]))

    return centerline_profile

 # Return horizontal interpolation across path

def sample_dict_bilienar(d_targets, dtm_dict, dh_targets=None):
    """
    Interpolates DTM heights at specific (d, dh) coordinate pairs.

    Args:
        d_targets: List or array of longitudinal distances.
        dtm_dict: The merged DTM dictionary {d: [(dh, v), ...]}
        dh_targets: Vector of perpendicular offsets corresponding to d_targets.
    """
    # 1. Handle dimension checks and defaults
    if d_targets is None or len(d_targets) == 0:
        return np.array([])

    if dh_targets is None:
        # Default to LOS (dh=0) for all points
        dh_targets = np.zeros(len(d_targets))

    # Ensure we are working with arrays to avoid list-related attribute errors
    d_targets = np.atleast_1d(d_targets)
    dh_targets = np.atleast_1d(dh_targets)

    if len(d_targets) != len(dh_targets):
        raise ValueError(f"Dimension mismatch: d_targets({len(d_targets)}) != dh_targets({len(dh_targets)})")

    sorted_keys = sorted(dtm_dict.keys())
    sampled_heights = []

    # Iterate through each specific (d_t, dh_t) pair
    # This prevents the "ambiguous truth value" error
    for d_t, dh_t in zip(d_targets, dh_targets):

        # 2. Find longitudinal neighbors (d1, d2)
        idx = bisect_left(sorted_keys, d_t)

        if idx == 0:
            d1 = d2 = sorted_keys[0]
        elif idx == len(sorted_keys):
            d1 = d2 = sorted_keys[-1]
        else:
            d1, d2 = sorted_keys[idx-1], sorted_keys[idx]

        # Helper to interpolate dh within a single distance slice
        def get_v_at_dh(d_key, target_dh):
            # Points are [(dh, v), ...]
            points = sorted(dtm_dict[d_key])
            dhs = [p[0] for p in points]
            vs = [p[1] for p in points]

            # Find orthogonal neighbors for the specific target_dh (scalar)
            dh_idx = bisect_left(dhs, target_dh)

            if dh_idx == 0:
                return vs[0]
            if dh_idx == len(dhs):
                return vs[-1]

            dh_a, dh_b = dhs[dh_idx-1], dhs[dh_idx]
            v_a, v_b = vs[dh_idx-1], vs[dh_idx]

            # Linear interpolation in DH
            weight = (target_dh - dh_a) / (dh_b - dh_a)
            return v_a + (v_b - v_a) * weight

        # 3. Get heights at the specific dh_t for both distance slices
        v_d1 = get_v_at_dh(d1, dh_t)
        v_d2 = get_v_at_dh(d2, dh_t)

        # 4. Longitudinal bridge (interpolation in D)
        if d1 == d2:
            sampled_heights.append(v_d1)
        else:
            weight_d = (d_t - d1) / (d2 - d1)
            v_final = v_d1 + (v_d2 - v_d1) * weight_d
            sampled_heights.append(v_final)

    return np.array(sampled_heights)

def sample_dict_axial_nearest(d_targets, dsm_dict, dh_targets=None):
    """
    Professional DSM Sampler: Uses Nearest-Neighbor logic to maintain
    sharp edges for trees/buildings and avoid artificial ramps.
    """
    if d_targets is None or len(d_targets) == 0:
        return np.array([])

    if dh_targets is None:
        dh_targets = np.zeros(len(d_targets))

    sorted_keys = sorted(dsm_dict.keys())
    sampled_heights = []

    for d_t, dh_t in zip(d_targets, dh_targets):
        # 1. Longitudinal: Snap to the single closest distance slice
        idx = bisect_left(sorted_keys, d_t)
        if idx == 0:
            best_d = sorted_keys[0]
        elif idx == len(sorted_keys):
            best_d = sorted_keys[-1]
        else:
            d1, d2 = sorted_keys[idx-1], sorted_keys[idx]
            # Snap to whichever slice is physically closer
            best_d = d1 if (d_t - d1) < (d2 - d_t) else d2

        # 2. Lateral: Snap to the single closest point in that slice
        points = dsm_dict[best_d]
        # This finds the nearest dh without a 'clumsy' window_m
        closest_p = min(points, key=lambda p: abs(p[0] - dh_t))

        sampled_heights.append(closest_p[1])

    return np.array(sampled_heights)

def sample_dict_max_max(d_targets, dtm_dict, dh_targets=None, search_radius_m=30.0):
    """
    v2: MAX NEIGHBORHOOD SAMPLER.
    Returns the maximum elevation found in the local longitudinal and
    lateral neighborhood to ensure peaks are never missed.
    """
    if d_targets is None or len(d_targets) == 0:
        return np.array([])

    if dh_targets is None:
        dh_targets = np.zeros(len(d_targets))

    d_targets = np.atleast_1d(d_targets)
    dh_targets = np.atleast_1d(dh_targets)

    sorted_keys = sorted(dtm_dict.keys())
    sampled_heights = []

    for d_t, dh_t in zip(d_targets, dh_targets):
        # 1. Identify neighboring distance slices
        idx = bisect_left(sorted_keys, d_t)

        # We look at the slice before and the slice after (or just the nearest if at edges)
        if idx == 0:
            relevant_d_keys = [sorted_keys[0]]
        elif idx == len(sorted_keys):
            relevant_d_keys = [sorted_keys[-1]]
        else:
            relevant_d_keys = [sorted_keys[idx-1], sorted_keys[idx]]

        # 2. Collect all heights within the lateral search_radius_m
        local_candidates = []

        for d_k in relevant_d_keys:
            points = dtm_dict[d_k] # List of (dh, v)
            for dh_p, v_p in points:
                # Check if the point is within the lateral search window
                if abs(dh_p - dh_t) <= search_radius_m:
                    local_candidates.append(v_p)

        # 3. Apply Max Logic
        if local_candidates:
            v_final = max(local_candidates)
        else:
            # Fallback to the single nearest neighbor if the window is empty
            points = dtm_dict[relevant_d_keys[0]]
            closest_p = min(points, key=lambda p: abs(p[0] - dh_t))
            v_final = closest_p[1]

        sampled_heights.append(v_final)

    return np.array(sampled_heights)

def sample_dict_linear_max(d_targets, dtm_dict, dh_targets=None, search_radius_m=30.0):
    """
    v5: LINEAR MAX INTERPOLATION.
    Interpolates between local maximums to eliminate horizontal steps.
    """
    if d_targets is None or len(d_targets) == 0:
        return np.array([])

    if dh_targets is None:
        dh_targets = np.zeros(len(d_targets))

    d_targets = np.atleast_1d(d_targets)
    dh_targets = np.atleast_1d(dh_targets)

    sorted_keys = sorted(dtm_dict.keys())
    sampled_heights = []

    for d_t, dh_t in zip(d_targets, dh_targets):
        # 1. Identify surrounding longitudinal neighbors
        idx = bisect_left(sorted_keys, d_t)

        if idx == 0:
            d1 = d2 = sorted_keys[0]
        elif idx == len(sorted_keys):
            d1 = d2 = sorted_keys[-1]
        else:
            d1, d2 = sorted_keys[idx-1], sorted_keys[idx]

        def get_slice_max(d_key, target_dh):
            """Finds the max value in a lateral window for a specific distance slice."""
            points = dtm_dict[d_key]
            # Filter points in the lateral corridor
            candidates = [v for dh, v in points if abs(dh - target_dh) <= search_radius_m]

            if not candidates:
                # Fallback to absolute nearest point if corridor is empty
                return min(points, key=lambda p: abs(p[0] - target_dh))[1]
            return max(candidates)

        # 2. Get local maximums for both surrounding slices
        v_max1 = get_slice_max(d1, dh_t)
        v_max2 = get_slice_max(d2, dh_t)

        # 3. Linear Interpolation between the two maximums
        if d1 == d2:
            v_final = v_max1
        else:
            # Interpolate based on where d_t sits between the two raw pixel centers
            weight = (d_t - d1) / (d2 - d1)
            v_final = v_max1 + (v_max2 - v_max1) * weight

        sampled_heights.append(v_final)

    return np.array(sampled_heights)

# Return nearest lulc ids along the path
def sample_lulc_ids(d_targets, dh_targets, lulc_dict):
    """
    Retrieves categorical LULC IDs for a specific ribbon in the 3D space.
    """
    if len(d_targets) != len(dh_targets):
        raise ValueError("d_targets and dh_targets must have the same length.")

    sorted_keys = sorted(lulc_dict.keys())
    sampled_ids = []

    for d_t, dh_t in zip(d_targets, dh_targets):
        # 1. FIND NEAREST LONGITUDINAL KEY (d)
        idx = bisect_left(sorted_keys, d_t)
        if idx == 0:
            d_key = sorted_keys[0]
        elif idx == len(sorted_keys):
            d_key = sorted_keys[-1]
        else:
            d1, d2 = sorted_keys[idx-1], sorted_keys[idx]
            d_key = d1 if (d_t - d1) < (d2 - d_t) else d2

        # 2. FIND NEAREST ORTHOGONAL KEY (dh)
        # points is [(dh, class_id), ...]
        points = lulc_dict[d_key]

        # We need the dh values to perform a search
        dhs_in_slice = [p[0] for p in points]

        dh_idx = bisect_left(dhs_in_slice, dh_t)

        if dh_idx == 0:
            best_id = points[0][1]
        elif dh_idx == len(dhs_in_slice):
            best_id = points[-1][1]
        else:
            dh1, dh2 = dhs_in_slice[dh_idx-1], dhs_in_slice[dh_idx]
            # Select ID of the closest lateral neighbor
            if (dh_t - dh1) < (dh2 - dh_t):
                best_id = points[dh_idx-1][1]
            else:
                best_id = points[dh_idx][1]

        sampled_ids.append(best_id)

    return np.array(sampled_ids)

# Create a clutter = DSM - DTM dict
def create_clutter_height_dict(dsm_dict, dtm_dict):
    clutter_dict = {}

    for d_key, dsm_points in dsm_dict.items():
        # 1. Prepare vectors for the entire slice at d_key
        dhs = [p[0] for p in dsm_points]
        vs_dsm = [p[1] for p in dsm_points]

        # Vector of d_key with same length as dhs
        d_targets = [d_key] * len(dhs)

        # 2. Single vectorized call to DTM surface
        # Using the new dh_targets parameter
        vs_dtm = sample_dict_axial_nearest(d_targets, dtm_dict, dh_targets=dhs)

        # 3. Compute absolute clutter height (DSM - DTM)
        clutter_points = [
            (dh, max(0, v_dsm - v_dtm))
            for dh, v_dsm, v_dtm in zip(dhs, vs_dsm, vs_dtm)
        ]

        clutter_dict[d_key] = clutter_points

    return clutter_dict

# Interpolate the clutter dict
def sample_clutter_surface(d_targets, dh_targets, clutter_dict):
    sorted_keys = sorted(clutter_dict.keys())
    sampled_heights = []

    for d_t, dh_t in zip(d_targets, dh_targets):
        # 1. Longitudinal: Find the two nearest distance slices
        idx = bisect_left(sorted_keys, d_t)
        if idx == 0:
            slices = [sorted_keys[0]]
        elif idx == len(sorted_keys):
            slices = [sorted_keys[-1]]
        else:
            slices = [sorted_keys[idx-1], sorted_keys[idx]]

        # 2. Lateral: Find the closest height sample in those slices
        neighbor_heights = []
        for d_key in slices:
            points = clutter_dict[d_key] # List of (dh, height)
            # Find the point with the minimum lateral distance to target_dh
            closest_point = min(points, key=lambda p: abs(p[0] - dh_t))
            neighbor_heights.append(closest_point[1])

        # 3. The Step: Use the maximum of the nearest longitudinal neighbors
        v_final = max(neighbor_heights)
        sampled_heights.append(v_final)

    return np.array(sampled_heights)

# Return a list of tuples (begin_dist, end_dist, cover_id)
def get_path_segments(d_targets, dh_targets, lulc_dict, min_segment_m=20.0):
    """
    Identifies contiguous environmental segments along a specific ribbon (d, dh).
    Returns a list of: (start_dist, end_dist, lulc_id)
    """
    # 1. Generate the LULC vector using our new sampler
    # This ensures we are looking at the EXACT orthogonal points
    lulc_vector = sample_lulc_ids(d_targets, dh_targets, lulc_dict)

    segments = []
    if len(lulc_vector) == 0:
        return segments

    # 2. State Machine for Segment Detection
    current_id = lulc_vector[0]
    start_dist = d_targets[0]

    for i in range(1, len(lulc_vector)):
        # If the environment changes, close the current segment
        if lulc_vector[i] != current_id:
            end_dist = d_targets[i-1]

            # 3. Filter out "Noise" (micro-segments)
            if (end_dist - start_dist) >= min_segment_m:
                segments.append((start_dist, end_dist, current_id))

            # Start new segment
            start_dist = d_targets[i]
            current_id = lulc_vector[i]

    # Close the final segment
    segments.append((start_dist, d_targets[-1], current_id))

    return segments


# --------- 6. PLOT PROFILES -----------------------------------------------------

# Plot multi-profile
def plot_combined_profiles(res_dtm=None, res_dsm=None, res_lulc=None, lulc_heights=None, title="Elevation Profile Comparison"):
    """
    Plots DTM as filled base, DSM as dotted line, and LULC as DTM + Fixed Height.

    Args:
        res_dtm: Array of [distance, elevation]
        res_dsm: Array of [distance, elevation]
        res_lulc: Array of [distance, cover_code]
        lulc_heights: Dict mapping {cover_code: height_in_meters}
    """

    plt.figure(figsize=(15, 7))

    y_values = []

    # 1. Process DTM (Base Ground Layer)
    if res_dtm is not None:
        res_dtm = np.asarray(res_dtm)
        dist_dtm, h_dtm = res_dtm[:, 0], res_dtm[:, 1]

        # Determine a safe bottom for filling (slightly below min elevation)
        y_min_plot = np.min(h_dtm) - 5
        y_values.extend(h_dtm)

        plt.fill_between(dist_dtm, h_dtm, y_min_plot, color='brown', alpha=0.2, label='DTM (Ground)')
        plt.plot(dist_dtm, h_dtm, color='brown', linewidth=1.5, label='DTM Line')

    # 2. Process DSM (Surface dotted line)
    if res_dsm is not None:
        res_dsm = np.asarray(res_dsm)
        y_values.extend(res_dsm[:, 1])
        plt.plot(res_dsm[:, 0], res_dsm[:, 1], color='blue', alpha=0.6,
                 linestyle=':', linewidth=1.2, label='DSM (Surface)')

    # 3. Process LULC (Coverage added to DTM)
    if res_lulc is not None and lulc_heights is not None:
        res_lulc = np.asarray(res_lulc)
        dist_lulc, codes = res_lulc[:, 0], res_lulc[:, 1]

        # Map codes to fixed heights (default to 0 if code not in dict)
        fixed_heights = np.array([lulc_heights.get(int(c), 0) for c in codes])

        if res_dtm is not None:
            # Sync DTM ground to LULC distance points
            h_dtm_interp = np.interp(dist_lulc, dist_dtm, h_dtm)
            h_combined = h_dtm_interp + fixed_heights

            y_values.extend(h_combined)
            plt.plot(dist_lulc, h_combined, color='green', linewidth=1.5, label='DTM + Coverage')
        else:
            # Fallback if DTM isn't provided
            plt.plot(dist_lulc, fixed_heights, color='green', label='Relative Coverage Height')

    # Scaling and Formatting
    if y_values:
        plt.ylim(np.min(y_values) - 2, np.max(y_values) + 10)
    plt.title(title)
    plt.xlabel("Distance from Source (meters)")
    plt.ylabel("Elevation (m ASL)")
    plt.legend(loc='upper right', frameon=True)
    plt.grid(True, linestyle='--', alpha=0.4)
    plt.tight_layout()
    plt.show()

def plot_advanced_profiles(res_dtm=None, res_dsm=None, res_lulc=None, res_chm=None, title="Path Profile Audit: 10m LULC vs 1m CHM"):
    """
    Final Fix: Plots DTM as a smooth, independent baseline.
    Adds relative heights to the DTM without modifying the DTM source.
    """
    plt.figure(figsize=(15, 7))

    # 1. Extract DTM first - this is our "Fixed Earth"
    if res_dtm is not None and len(res_dtm) > 0:
        d_dtm = np.array([p[0] for p in res_dtm])
        h_dtm = np.array([p[1] for p in res_dtm])

        # Plot the Shaded Ground - Using the original h_dtm directly
        y_min_plot = h_dtm.min() - 20
        plt.fill_between(d_dtm, h_dtm, y_min_plot, color='brown', alpha=0.15, label='DTM (Ground)')
        plt.plot(d_dtm, h_dtm, color='brown', linewidth=1.5, alpha=0.6)

        # 2. LULC (Add to DTM)
        if res_lulc is not None and len(res_lulc) > 0:
            d_lulc = np.array([p[0] for p in res_lulc])
            h_lulc_rel = np.array([p[1] for p in res_lulc])
            # Use 'extrapolate' logic to prevent the 0-value drop at edges
            h_dtm_at_lulc = np.interp(d_lulc, d_dtm, h_dtm, left=h_dtm[0], right=h_dtm[-1])
            plt.plot(d_lulc, h_dtm_at_lulc + h_lulc_rel, color='green', linewidth=1.5, label='LULC Assumption (10m)')

        # 3. 1m Tree Audit (Add to DTM)
        if res_chm is not None and len(res_chm) > 0:
            d_chm = np.array([p[0] for p in res_chm])
            h_chm_rel = np.array([p[1] for p in res_chm])
            # Same extrapolation logic to protect the profile edges
            h_dtm_at_chm = np.interp(d_chm, d_dtm, h_dtm, left=h_dtm[0], right=h_dtm[-1])
            plt.plot(d_chm, h_dtm_at_chm + h_chm_rel, color='red', linewidth=1.0, label='1m Tree Audit', alpha=0.8)

    # 4. DSM (Satellite Surface) - Plotted independently
    if res_dsm is not None and len(res_dsm) > 0:
        d_dsm = [p[0] for p in res_dsm]
        h_dsm = [p[1] for p in res_dsm]
        plt.plot(d_dsm, h_dsm, color='blue', alpha=0.4, linestyle=':', label='DSM')

    plt.title(title)
    plt.xlabel("Distance from Source (meters)")
    plt.ylabel("Elevation (m ASL)")
    plt.legend(loc='upper right')
    plt.grid(True, alpha=0.3)
    plt.show()

# This function requires raw dsm profile
def apply_sharpened_canopy_tuples(res_dsm, res_dtm, window_size=3):
    """
    Sharpens only the vegetation layer (DSM-DTM) using a sliding window max,
    then adds it back to the ground (DTM).

    Args:
        res_dsm: List of (dist, elevation) tuples
        res_dtm: List of (dist, elevation) tuples
    Returns:
        List of (dist, sharpened_elevation) tuples
    """
    # 1. Convert to numpy for math
    data_dsm = np.asarray(res_dsm)
    data_dtm = np.asarray(res_dtm)

    dist = data_dsm[:, 0]
    h_dsm = data_dsm[:, 1]

    # 2. Interpolate DTM to the DSM points to get accurate delta
    h_dtm_interp = np.interp(dist, data_dtm[:, 0], data_dtm[:, 1])

    # 3. Isolate the Canopy Height (Height Above Ground)
    hag = h_dsm - h_dtm_interp

    # 4. Apply sliding max to the HAG only
    # This 'pushes' the tree height into the neighboring 'climb' pixels
    hag_sharpened = np.copy(hag)
    half_w = window_size // 2

    for i in range(half_w, len(hag) - half_w):
        # Local Max: borrows height from neighbors to fix satellite 'smear'
        hag_sharpened[i] = np.max(hag[i - half_w : i + half_w + 1])

    # 5. Add sharpened canopy back to the REAL ground
    h_final = h_dtm_interp + hag_sharpened

    return list(zip(dist, h_final))

# Plot Surface dicts
def plot_surface_dict_old(data, legend_dict=None, **kwargs):
    plot_data = []
    for d, points in data.items():
        for dh, v in points:
            plot_data.append((d, dh, v))

    ds, dhs, vs = zip(*plot_data)

    figsize = kwargs.get('figsize',(15, 7))
    fig = plt.figure(figsize=figsize)
    
    if legend_dict:
        # --- LULC MODE ---
        # 1. Create a colormap from the legend JSON
        # Values in LULC can be non-sequential (1, 2, 4, 7...), so we map them
        unique_vals = sorted([int(k) for k in legend_dict.keys()])
        colors = [np.array(legend_dict[str(v)]["color"]) / 255.0 for v in unique_vals]
        cmap = mcolors.ListedColormap(colors)

        # Create boundaries so each class ID gets the correct discrete color
        norm = mcolors.BoundaryNorm(unique_vals + [max(unique_vals)+1], cmap.N)

        scatter = plt.scatter(ds, dhs, c=vs, cmap=cmap, norm=norm, s=2)

        # Create a legend with class names
        patches = [plt.plot([],[], marker="s", ms=10, ls="",
                   color=colors[i], label=legend_dict[str(v)]["name"])[0]
                   for i, v in enumerate(unique_vals)]
        plt.legend(handles=patches, bbox_to_anchor=(1.05, 1), loc='upper left', borderaxespad=0.)

        title = "Corridor Inspection (LULC Classes)"
        label = "Class ID"
    else:
        # --- HEIGHT MODE ---
        scatter = plt.scatter(ds, dhs, c=vs, cmap='terrain', s=2)
        plt.colorbar(scatter, label="Elevation/Clutter Height")
        title = "Corridor Inspection (Elevation)"
        label = "Meters"

    return_base64 = kwargs.get('base64', False)
    if return_base64:
        buf = BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)
        return encoded
    else:
        plt.axhline(0, color='red', linestyle='--', alpha=0.5)
        plt.title(title)
        plt.xlabel("Distance from TX (m)")
        plt.ylabel("Perpendicular Offset (m)")
        plt.tight_layout()
        plt.show()

def plot_surface_dict(data, legend_dict=None, **kwargs):
    import base64
    from io import BytesIO

    import numpy as np
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    plot_data = []
    for d, points in data.items():
        for dh, v in points:
            plot_data.append((d, dh, v))

    if not plot_data:
        return None

    ds, dhs, vs = zip(*plot_data)

    figsize = kwargs.get("figsize", (15, 7))
    point_size = kwargs.get("point_size", 6)
    title = kwargs.get("title")
    xlabel = kwargs.get("xlabel", "Distance from TX (m)")
    ylabel = kwargs.get("ylabel", "Perpendicular Offset (m)")
    return_base64 = kwargs.get("base64", False)

    fig, ax = plt.subplots(figsize=figsize)

    if legend_dict:
        unique_vals = sorted(int(k) for k in legend_dict.keys())
        colors = [np.array(legend_dict[str(v)]["color"]) / 255.0 for v in unique_vals]
        cmap = mcolors.ListedColormap(colors)
        norm = mcolors.BoundaryNorm(unique_vals + [max(unique_vals) + 1], cmap.N)

        ax.scatter(ds, dhs, c=vs, cmap=cmap, norm=norm, s=point_size)

        handles = [
            plt.Line2D(
                [0], [0],
                marker="s",
                linestyle="",
                markersize=10,
                markerfacecolor=colors[i],
                markeredgecolor=colors[i],
                label=legend_dict[str(v)]["name"],
            )
            for i, v in enumerate(unique_vals)
        ]
        ax.legend(handles=handles, bbox_to_anchor=(1.05, 1), loc="upper left", borderaxespad=0.0)

        if title is None:
            title = "Corridor Inspection (LULC Classes)"
    else:
        scatter = ax.scatter(ds, dhs, c=vs, cmap="terrain", s=point_size)
        fig.colorbar(scatter, ax=ax, label="Elevation/Clutter Height")

        if title is None:
            title = "Corridor Inspection (Elevation)"

    ax.axhline(0, color="red", linestyle="--", alpha=0.5)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    fig.tight_layout()

    if return_base64:
        buf = BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)
        return encoded

    plt.show()

# Plot several lists of tuples (d,v) in the same plot
def plot_data_tuples(*dv_tuples_list, labels=None, **kwargs):
    """
    Plot multiple data series.

    Parameters:
        dv_tuples_list: One or more lists of (distance, value) tuples
        labels: Optional list of strings for legend
        colors: Optional list of color strings
        title: Plot title
        xlabel: X-axis label
        ylabel: Y-axis label
        figsize: Figure size (width, height)
        grid_alpha: Grid transparency
    """
   
    colors = kwargs.get('colors', None)
    title = kwargs.get('title' , 'Values along Distance')
    xlabel = kwargs.get('xlabel',"Distance from TX (m)") 
    ylabel = kwargs.get('ylabel',"Value (m)") 
    figsize = kwargs.get('figsize',(15, 7))
    dpi = kwargs.get('dpi',100)
    grid_alpha = kwargs.get('grid_alpha',0.3)

    print(figsize)
    fig = plt.figure(figsize=figsize, dpi=dpi)

    # Generate default labels if needed
    labels = labels or [f"Series {i+1}" for i in range(len(dv_tuples_list))]

    for i, dv_tuples in enumerate(dv_tuples_list):
        if len(dv_tuples) == 0 or dv_tuples is None:
            continue

        dist_coords, heights = zip(*dv_tuples)
        color = colors[i] if colors and i < len(colors) else None

        plt.plot(dist_coords, heights, label=labels[i], color=color, alpha=grid_alpha)

    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.legend()
    plt.grid(True, alpha=grid_alpha)
    
    return_base64 = kwargs.get('base64', False)
    if return_base64:
        buf = BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05)        
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)
        return encoded
    else:
        plt.tight_layout()
        plt.show()

# Wrapper for plot_data_tuples with dict
def plot_data_tuples_dict(data_dict, **kwargs):
    """
    Plot multiple data series from a dictionary.

    Parameters:
    -----------
    data_dict : dict
        Dictionary with format: {"label1": [(dist1, val1), ...], "label2": [...], ...}

    **kwargs : additional arguments passed to plot_data_tuples
    """
    # Extract data and labels from dictionary
    data_series = list(data_dict.values())
    labels = list(data_dict.keys())

    # Call the original function
    return plot_data_tuples(*data_series, labels=labels, **kwargs)

# Show LULC with fresnel
def show_lulc_fresnel_from_srcs(
    src_list,
    geo_bbox,          # (west, south, east, north)
    legend,
    start_latlon,
    end_latlon,
    freq_ghz,
    downscale=1,
    **kwargs
):
    import numpy as np
    import math
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from rasterio.warp import reproject, Resampling
    from pyproj import Geod
    from affine import Affine
    import base64
    from io import BytesIO

    if not src_list:
        raise ValueError("src_list is empty")

    # 1. Geometry - Fresnel & Bounding Box expansion
    geod = Geod(ellps="WGS84")
    _, _, dist_m = geod.inv(start_latlon[1], start_latlon[0], end_latlon[1], end_latlon[0])
    fresnel_r1 = 17.32 * math.sqrt((dist_m / 1000.0) / (4 * freq_ghz))

    mid_lat, mid_lon = (start_latlon[0] + end_latlon[0]) / 2, (start_latlon[1] + end_latlon[1]) / 2
    angle = math.degrees(math.atan2(end_latlon[0] - start_latlon[0], end_latlon[1] - start_latlon[1]))

    deg_h = (2 * fresnel_r1) / 111000.0
    deg_w = dist_m / (111000.0 * math.cos(math.radians(mid_lat)))

    # Expand geo_bbox to ensure it contains the ellipse
    a, b = deg_w / 2, deg_h / 2
    theta = math.radians(angle)
    dx = abs(a * math.cos(theta)) + abs(b * math.sin(theta))
    dy = abs(a * math.sin(theta)) + abs(b * math.cos(theta))

    view_w = min(geo_bbox[0], mid_lon - dx)
    view_s = min(geo_bbox[1], mid_lat - dy)
    view_e = max(geo_bbox[2], mid_lon + dx)
    view_n = max(geo_bbox[3], mid_lat + dy)

    # 2. Raster Canvas Creation
    # Instead of cropping a window, we reproject into a fixed WGS84 grid
    res = 0.0001 / downscale # ~10m resolution
    width = max(1, int((view_e - view_w) / res))
    height = max(1, int((view_n - view_s) / res))

    # Destination transform (WGS84)
    dst_transform = Affine.translation(view_w, view_n) * Affine.scale(res, -res)
    nodata = -1
    canvas = np.full((height, width), nodata, dtype=np.int16)

    # 3. Populate Canvas
    for src in src_list:
        tmp = np.full((height, width), nodata, dtype=np.int16)

        src_data = src.read(1)
        src_nodata = src.nodata

        reproject(
            source=src_data,
            destination=tmp,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs="EPSG:4326",
            src_nodata=src_nodata,
            dst_nodata=nodata,
            resampling=Resampling.nearest
        )

        mask = tmp != nodata
        canvas[mask] = tmp[mask]

    # 4. RGB Mapping & Legend
    lulc_data = canvas
    rgb_map = np.zeros((*lulc_data.shape, 3), dtype=np.uint8)
    legend_elements = []

    for cid, info in legend.items():
        mask = (lulc_data == int(cid))
        if np.any(mask):
            rgb_map[mask] = info["color"]
            legend_elements.append(patches.Patch(color=np.array(info["color"])/255.0,
                                                 label=info["name"].replace("_", " ").title()))

    # 5. Plotting

    return_base64 = kwargs.get('base64', False)
    dpi = kwargs.get('dpi', None)
    figsize = kwargs.get('figsize', None)
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
 

    ax.imshow(rgb_map, extent=[view_w, view_e, view_s, view_n], origin="upper")

    # Path & Ellipse
    ax.plot([start_latlon[1], end_latlon[1]], [start_latlon[0], end_latlon[0]],
            color="white", linestyle="--", linewidth=1.5)

    ellipse = patches.Ellipse((mid_lon, mid_lat), width=deg_w, height=deg_h, angle=angle,
                              edgecolor="yellow", facecolor="none", alpha=0.8, linewidth=2)
    ax.add_patch(ellipse)

    ax.set_title(f"LULC – 1st Fresnel Zone @ {freq_ghz} GHz")
    ax.legend(handles=legend_elements + [ellipse], loc="center left", bbox_to_anchor=(1, 0.5))
    
    if return_base64:
        buf = BytesIO()
        if figsize is None:
            fig.savefig(buf, format="png", bbox_inches="tight")
        else:
            fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05)
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)
        return encoded
    else:
        plt.tight_layout()
        plt.show()

# ---------- 7. MINIO DIRECT ACCESS (SIGNED URL) ----------------------------------------

# Return the tuple dist vs tiff info with automatic merge
def get_path_data_from_index(geojson_path, start_latlon, end_latlon, band_index=1, step=1):
    """
    Segments the path, calls the worker for each TIFF, and stitches the
    (N, 2) arrays into one continuous, strictly monotonic profile.
    """
    # 1. Get the segmented path (this function was defined in the previous step)
    path_segments = find_tiles_by_path(geojson_path, start_latlon, end_latlon)

    if not path_segments:
        return np.empty((0, 2))

    geojson_dir = Path(geojson_path).parent
    all_segments = []

    total_accumulated_dist = 0.0
    last_seg_end = None

    for i, (seg_start, seg_end, filename) in enumerate(path_segments):
        # Find tile info to get the native CRS for this specific tile
        tile_info = find_tile_by_coord(geojson_path, seg_start[0], seg_start[1])
        if not tile_info:
            continue

        tif_path = geojson_dir / filename
        crs_native = tile_info['crs_native']

        # 2. Call the worker - Returns an array of shape (N, 2)
        # column 0: distances (starting at 0), column 1: elevations
        segment_data = get_path_data_from_tiff(
            tif_path,
            crs_native,
            seg_start,
            seg_end,
            band_index,
            step=step
        )

        if segment_data.size == 0:
            continue

        # 3. Calculate the "Bridge" distance between tiles
        # Accounts for the epsilon-nudge gap to ensure strict monotonicity
        bridge_dist = 0.0
        if i > 0 and last_seg_end is not None:
            bridge_dist = geodesic(last_seg_end, seg_start).meters

        # 4. Offset the distance column (index 0)
        # New distance = local_dist + total_so_far + gap_to_this_tile
        segment_data[:, 0] += (total_accumulated_dist + bridge_dist)

        # 5. Store and update tracking variables
        all_segments.append(segment_data)
        total_accumulated_dist = segment_data[-1, 0] # End of this segment
        last_seg_end = seg_end

    # 6. Merge all segments into a single (M, 2) array
    if not all_segments:
        return np.empty((0, 2))

    return np.vstack(all_segments)

# Return the tuple dist vs profile (may be used to read from a remote Geotiff)
def get_path_data_from_tiff(tif_path, crs_str, start_latlon, end_latlon, band_index=1, step=1):
    """
    Returns an array of (distance_from_source, elevation) tuples.
    band_index: The specific band to read (e.g., 2 for Building Height).
    """
    transformer = Transformer.from_crs("EPSG:4326", crs_str, always_xy=True)
    geod = Geod(ellps="WGS84")

    start_x, start_y = transformer.transform(start_latlon[1], start_latlon[0])
    end_x, end_y = transformer.transform(end_latlon[1], end_latlon[0])

    _, _, total_dist = geod.inv(start_latlon[1], start_latlon[0],
                                end_latlon[1], end_latlon[0])

    with rasterio.open(tif_path) as src:
        s_row, s_col = src.index(start_x, start_y)
        e_row, e_col = src.index(end_x, end_y)

        # Apply decimation
        s_row_ds, s_col_ds = s_row // step, s_col // step
        e_row_ds, e_col_ds = e_row // step, e_col // step

        out_shape = (src.height // step, src.width // step)

        # Read the specific band requested
        data = src.read(band_index, out_shape=out_shape, resampling=Resampling.bilinear).astype('float32')

        # Handle NoData: Convert to 0 so we can add it to the DTM later
        nodata = src.nodatavals[band_index-1]
        if nodata is not None:
            data[data == nodata] = 0

        # Number of points based on pixel resolution
        num_points = int(np.hypot(e_row_ds - s_row_ds, e_col_ds - s_col_ds))
        if num_points < 2: num_points = 2

        rows = np.linspace(s_row_ds, e_row_ds, num_points).astype(int)
        cols = np.linspace(s_col_ds, e_col_ds, num_points).astype(int)

        rows = np.clip(rows, 0, data.shape[0] - 1)
        cols = np.clip(cols, 0, data.shape[1] - 1)

        elevations = data[rows, cols]
        distances = np.linspace(0, total_dist, num_points)

        return np.column_stack((distances, elevations))


# --------- 8. SHOW TIFF (SIGNED URL) -----------------------------------------------------

def read_geotiff_band(file_path, band=1, downscale=1):
    with rasterio.open(file_path) as src:
        if downscale > 1:
            h = int(src.height // downscale)
            w = int(src.width // downscale)
            data = src.read(band, out_shape=(h, w), resampling=Resampling.bilinear)
            transform = src.transform * src.transform.scale(src.width / w, src.height / h)
        else:
            data = src.read(band)
            transform = src.transform

        metadata = {
            "transform": transform,
            "crs": src.crs,
            "bounds": src.bounds,
            "resolution": (transform.a, -transform.e),
            "nodata": src.nodatavals[band - 1],
            "dtype": src.dtypes[band - 1],
            "band_name": src.descriptions[band - 1] or f"Band {band}",
            "shape": data.shape,
            "file_path": file_path,
            "src_width": src.width,
            "src_height": src.height,
        }

        return data, metadata
    
def show_tiff_band(
    tif_path,
    band_index=1,
    downscale_factor=1,
    return_base64=False,
    show_plot=True,
):
    from io import BytesIO
    import base64

    data, meta = read_geotiff_band(tif_path, band_index, downscale_factor)

    fig, ax = plt.subplots(figsize=(10, 8))

    bounds = meta["bounds"]
    img = ax.imshow(
        data,
        cmap="viridis",
        extent=[bounds.left, bounds.right, bounds.bottom, bounds.top],
    )

    nodata = meta["nodata"]
    plot_data = data.copy()

    if nodata is not None:
        valid_mask = plot_data != nodata
        valid_data = plot_data[valid_mask] if np.any(valid_mask) else np.array([0, 0])
    else:
        valid_data = plot_data.flatten()

    vmin, vmax = float(np.min(valid_data)), float(np.max(valid_data))
    img.set_clim(vmin, vmax)

    fig.colorbar(img, ax=ax, label="Values")

    desc = meta["band_name"]
    if downscale_factor > 1:
        desc += f" (Downscaled {downscale_factor}x)"
    ax.set_title(desc)

    crs = meta["crs"]
    if crs and crs.is_geographic:
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
    else:
        ax.set_xlabel("Easting")
        ax.set_ylabel("Northing")

    plt.tight_layout()

    if return_base64:
        buf = BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)
        return encoded

    if show_plot:
        plt.show()
        return None

    return fig, ax, data, meta

# ------ 9. ONLINE METHODS (THREE HEIGHT) -----------------

def get_quadkey(lat: float, lon: float, level : int =9)->str:
    """
    Returns the Bing Maps quadkey for a latitude, longitude, and zoom level.

    Args:
        lat: Latitude in degrees
        lon: Longitude in degrees
        level: Zoom level
    """
    sin_lat = math.sin(lat * math.pi / 180)
    x = (lon + 180) / 360
    y = 0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)

    map_size = 2**level
    tile_x = int(min(max(x * map_size, 0), map_size - 1))
    tile_y = int(min(max(y * map_size, 0), map_size - 1))

    quadkey = ""
    for i in range(level, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if (tile_x & mask) != 0: digit += 1
        if (tile_y & mask) != 0: digit += 2
        quadkey += str(digit)
    return quadkey

def get_canopy_height(lat : float, lon: float) -> str:
    """
    Retrieves 1m canopy height, handling 404 errors for missing tiles and CRS transformations.

    Args:
        lat: Latitude in degrees
        lon: Longitude in degrees
    """    
    qk = get_quadkey(lat, lon, level=9)
    s3_path = f"https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/{qk}.tif"

    # Check if the tile exists before opening with rasterio
    # This prevents the RasterioIOError from stopping the script
    try:
        response = requests.head(s3_path, timeout=5)
        if response.status_code == 404:
            # This is where your 'Inconsistency Audit' logs a 'No High-Res Data' state
            return None
    except requests.RequestException:
        return None

    with rasterio.Env(AWS_NO_SIGN_REQUEST='YES'):
        try:
            with rasterio.open(s3_path) as src:
                # 1. CRS Transformation
                if src.crs != "EPSG:4326":
                    transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
                    target_x, target_y = transformer.transform(lon, lat)
                else:
                    target_x, target_y = lon, lat

                # 2. Sample
                # We wrap in list() because sample() is a generator
                results = list(src.sample([(target_x, target_y)]))
                if not results:
                    return 0.0

                val = results[0][0]

                # 3. Data Cleanup
                if np.isnan(val) or val < 0:
                    return 0.0
                return float(val)

        except Exception as e:
            # This captures local processing errors
            return None

def get_canopy_height_list(coords_list):
    """
    Audits a list of coordinates (from LULC tree segments)
    against the 1m CHM, grouping by quadkey for efficiency.

    Args:
        coords_list: List of (lat, lon) tuples
    Returns:
        results: Dictionary mapping (lat, lon) -> high_res_height
    """
    # 1. Group coordinates by their Quadkey
    groups = defaultdict(list)
    for lat, lon in coords_list:
        qk = get_quadkey(lat, lon, level=9)
        groups[qk].append((lat, lon))

    final_audit = {}

    # 2. Process one Quadkey (tile) at a time
    with rasterio.Env(AWS_NO_SIGN_REQUEST='YES'):
        for qk, pts in groups.items():
            s3_path = f"https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/{qk}.tif"

            try:
                with rasterio.open(s3_path) as src:
                    # Prepare transformer for this specific tile
                    transformer = None
                    if src.crs != "EPSG:4326":
                        transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)

                    # Convert all points in this group to the TIFF's CRS
                    target_pts = []
                    for lat, lon in pts:
                        if transformer:
                            tx, ty = transformer.transform(lon, lat)
                            target_pts.append((tx, ty))
                        else:
                            target_pts.append((lon, lat))

                    # Sample all points in one batch
                    samples = list(src.sample(target_pts))

                    # Store results
                    for i, (lat, lon) in enumerate(pts):
                        val = samples[i][0]
                        final_audit[(lat, lon)] = float(val) if not np.isnan(val) and val >= 0 else 0.0

            except Exception:
                # If the tile 404s or fails, mark these points as 'No Data'
                for lat, lon in pts:
                    final_audit[(lat, lon)] = None

    return final_audit

def get_high_res_tree_profile(start_latlon, end_latlon, lulc_profile, lulc_codes):
    """
    Samples 1m canopy heights only for segments marked as 'tree_cover' (Code 2).

    Args:
        start_latlon, end_latlon: (lat, lon) tuples for the path
        lulc_profile: Array of [[dist, code], ...]
        lulc_codes: Dictionary of LULC classification

    Returns:
        List of (distance_m, height_m) tuples for all tree segments.
    """
    # 1. Identify the 'Tree' code (usually '2')
    tree_code = None
    for code, info in lulc_codes.items():
        if info['name'] == 'tree_cover':
            tree_code = float(code)
            break

    if tree_code is None:
        return []

    # 2. Setup path geometry
    geod = Geod(ellps="WGS84")
    total_dist = lulc_profile[-1][0]

    # We will store our high-res (d, h) results here
    tree_high_res_data = []

    # 3. Find continuous segments of trees
    # We iterate through the profile to find 'Start' and 'End' of tree spans
    is_tree = lulc_profile[:, 1] == tree_code

    # Find indices where tree status changes
    diff = np.diff(is_tree.astype(int))
    starts = np.where(diff == 1)[0] + 1
    ends = np.where(diff == -1)[0]

    # Handle edge cases (if path starts or ends with trees)
    if is_tree[0]:
        starts = np.insert(starts, 0, 0)
    if is_tree[-1]:
        ends = np.append(ends, len(lulc_profile) - 1)

    # 4. Process each tree segment with finer granularity
    for s_idx, e_idx in zip(starts, ends):
        d_start = lulc_profile[s_idx][0]
        d_end = lulc_profile[e_idx][0]

        # Calculate how many 1m points we need for this segment
        segment_length = d_end - d_start
        num_points = int(max(segment_length, 1)) # At least 1 point

        # Calculate the Lat/Lon for the start and end of this specific segment
        # fwd_az is the bearing from start to end points
        lon1, lat1 = start_latlon[1], start_latlon[0]
        lon2, lat2 = end_latlon[1], end_latlon[0]
        fwd_az, back_az, _ = geod.inv(lon1, lat1, lon2, lat2)

        # Find coordinates for the start and end of the tree segment along the path
        # npts = num_points - 2 gives us the internal points
        lons, lats = [], []

        # Create a sub-path for this segment at 1-meter intervals
        segment_distances = np.linspace(d_start, d_end, num_points)

        for d in segment_distances:
            # Move 'd' meters along the azimuth from the original start point
            lon_p, lat_p, _ = geod.fwd(lon1, lat1, fwd_az, d)
            lons.append(lon_p)
            lats.append(lat_p)

        # 5. Batch sample these coordinates from the 1m CHM
        # We reuse our existing logic that groups by quadkey
        coords_to_query = list(zip(lats, lons))
        audit_results = get_canopy_height_list(coords_to_query)

        # 6. Map results back to the distance vector
        for i, coord in enumerate(coords_to_query):
            h = audit_results.get(coord, 0.0)
            tree_high_res_data.append((segment_distances[i], h))

    return tree_high_res_data

