import os
from pathlib import Path
import json
import math
from math import hypot
import numpy as np
from bisect import bisect_left


import rasterio
from rasterio.mask import mask
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.merge import merge
from rasterio.plot import show
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from rasterio.transform import xy
from pyproj import Transformer

from pyproj import Transformer, Geod
from geopy.distance import geodesic 

from shapely.geometry import shape, Point, LineString, box
from shapely.ops import transform as shapely_transform


import matplotlib.pyplot as plt

from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.colors as mcolors

import requests
from collections import defaultdict

class PointNotCoveredError(Exception):
	"""Raised when at least one point is not covered by the dataset."""
	pass

# ------------ 1. INDEX CREATION ---------------------------------

# Create a txt index for the GeoTiff files (generic)
def create_geotiff_index(folder_path, output_file="tile_index.txt"):
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

# Create a GeoJSON index for the GeoTiff files (generic)
def create_global_tile_index(folder_path, output_file="index-geo.json"):
	"""
	Creates a GeoJSON index of tiles with detailed band information.
	"""
	geod = Geod(ellps="WGS84")
	features = []
	
	# Check if folder exists
	if not os.path.exists(folder_path):
		print(f"Error: Folder {folder_path} not found.")
		return

	# Get all .tif files
	files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.tif', '.tiff'))]
	
	for filename in files:
		file_path = os.path.join(folder_path, filename)
		
		with rasterio.open(file_path) as src:
			# Always get WGS84 Lat/Lon bounds for the GeoJSON geometry
			w_ll, s_ll, e_ll, n_ll = transform_bounds(src.crs, 'EPSG:4326', *src.bounds)
			
			# Start building properties
			props = {
				"id_filename": filename,
				"crs_native": str(src.crs),
				"px_width": src.width,
				"px_height": src.height,
				"geo_west": w_ll,
				"geo_south": s_ll,
				"geo_east": e_ll, # Corrected typo from 'geo_eat'
				"geo_north": n_ll,
				"affine_vector": [src.transform.a, src.transform.b, src.transform.c, 
								  src.transform.d, src.transform.e, src.transform.f]
			}

			# --- Band Information Extraction ---
			props["band_count"] = src.count
			
			for i in src.indexes:
				# Use a prefix like 'b1_', 'b2_', etc. for each band
				props[f"b{i}_dtype"] = str(src.dtypes[i-1])
				props[f"b{i}_nodata"] = src.nodatavals[i-1]
				props[f"b{i}_interp"] = src.colorinterp[i-1].name
				
				# Check for band descriptions
				description = src.descriptions[i-1]
				if description:
					props[f"b{i}_desc"] = description

			# --- Coordinate System Logic ---
			if src.crs.is_projected:
				props["coord_type"] = "projected"
				props["res_m_x"] = src.res[0]
				props["res_m_y"] = abs(src.res[1])
				
				props["utm_left"] = src.bounds.left
				props["utm_right"] = src.bounds.right
				props["utm_bottom"] = src.bounds.bottom
				props["utm_top"] = src.bounds.top
			else:
				props["coord_type"] = "geographic"
				mid_lat = (s_ll + n_ll) / 2
				mid_lon = (w_ll + e_ll) / 2
				
				_, _, dist_x = geod.inv(mid_lon, mid_lat, mid_lon + src.res[0], mid_lat)
				_, _, dist_y = geod.inv(mid_lon, mid_lat, mid_lon, mid_lat + abs(src.res[1]))
				
				props["res_m_x"] = round(dist_x, 3)
				props["res_m_y"] = round(dist_y, 3)

			# Area calculation
			lons = [w_ll, e_ll, e_ll, w_ll, w_ll]
			lats = [s_ll, s_ll, n_ll, n_ll, s_ll]
			poly_area, _ = geod.polygon_area_perimeter(lons, lats)
			props["area_km2"] = round(abs(poly_area) / 1_000_000, 4)

			feature = {
				"type": "Feature",
				"bbox": [w_ll, s_ll, e_ll, n_ll],
				"geometry": {
					"type": "Polygon",
					"coordinates": [[
						[w_ll, s_ll], [e_ll, s_ll], 
						[e_ll, n_ll], [w_ll, n_ll], 
						[w_ll, s_ll]
					]]
				},
				"properties": props
			}
			features.append(feature)

	geojson_output = {
		"type": "FeatureCollection",
		"features": features
	}

	with open(output_file, 'w') as f:
		json.dump(geojson_output, f, indent=2)
	
	print(f"Index complete: {len(features)} tiles processed. File saved to {output_file}")

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
	"""
	Locates the tile for a given lat/lon and returns spatial info 
	along with band counts and descriptions.
	"""
	if isinstance(geojson, dict):
		index_data = geojson
	elif isinstance(geojson, (str, Path)):
		with open(geojson, "r") as f:
			index_data = json.load(f)
	else: 
		raise TypeError( f"geojson must be dict, str, or Path; got {type(geojson).__name__}" )

	# GeoJSON uses [Lon, Lat] order
	target_point = Point(lon, lat)

	for feature in index_data['features']:
		polygon = shape(feature['geometry'])
		
		if polygon.contains(target_point):
			return feature['properties']			
			
	return None

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
    """
    Fixed Segmentation:
    1. Returns segment-specific entry/exit points (TX/RX of the segment).
    2. Strictly clips the BBox to the tile's physical boundaries.
    """
    # Use UTM 22S or local equivalent for accurate meters
    utm_epsg = get_utm_epsg(start_latlon[1], start_latlon[0])
    
    to_metric = Transformer.from_crs("EPSG:4326", utm_epsg, always_xy=True).transform
    to_geo = Transformer.from_crs(utm_epsg, "EPSG:4326", always_xy=True).transform

    # 1. Global Metric Link
    tx_m = to_metric(start_latlon[1], start_latlon[0])
    rx_m = to_metric(end_latlon[1], end_latlon[0])
    global_line_m = LineString([tx_m, rx_m])
    
    # Fresnel Width at Midpoint
    L_km = global_line_m.length / 1000.0
    max_f1 = 17.32 * math.sqrt((L_km / 4) / (freq_mhz / 1000.0))
    
    # 2. Create the 2D Corridor
    corridor_m = global_line_m.buffer(max_f1 * 1.2, cap_style=2)

    segments = []

    for tile in geojson_index['features']:
        props = tile['properties']
        # Tile Boundary in WGS84
        t_west, t_south, t_east, t_north = (props['geo_west'], props['geo_south'], 
                                            props['geo_east'], props['geo_north'])
        tile_poly_geo = box(t_west, t_south, t_east, t_north)
        
        # Check if the 2D corridor touches this tile
        if not shapely_transform(to_metric, tile_poly_geo).intersects(corridor_m):
            continue

        # 3. Calculate Segment Boresight (The Entry/Exit points)
        # We intersect the global line with the tile's boundary (in metric space)
        tile_poly_m = shapely_transform(to_metric, tile_poly_geo)
        boresight_seg_m = global_line_m.intersection(tile_poly_m)
        
        if boresight_seg_m.is_empty:
            # Case where only the Fresnel 'bulge' hits the tile, not the center line
            seg_start_geo = start_latlon # Fallback to nearest logical point
            seg_end_geo = end_latlon
        else:
            # Get entry/exit in Lat/Lon
            coords_m = list(boresight_seg_m.coords)
            s_m, e_m = coords_m[0], coords_m[-1]
            seg_start_geo = to_geo(s_m[0], s_m[1])[::-1] # (lat, lon)
            seg_end_geo = to_geo(e_m[0], e_m[1])[::-1]

        # 4. Calculate the SAFE BBox (The Intersection)
        # This is where we prevent the WindowError
        corridor_geo = shapely_transform(to_geo, corridor_m)
        safe_intersection = corridor_geo.intersection(tile_poly_geo)
        
        segments.append({
            'file': props['id_filename'],
            'bbox': safe_intersection.bounds, # (w, s, e, n) strictly inside tile
            'tx': seg_start_geo, # Local entry
            'rx': seg_end_geo    # Local exit
        })

    return segments

#--------- 3. SIMPLE RASTER SAMPLING -------------------------------------

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


#--------- 4. SURFACE DICT CREATION ------------------------------------------

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
def extract_and_profile(src, west, south, east, north, tx_latlon, rx_latlon, radius_m = None, band_index=1, key_decimals=1):
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
	
	data = src.read(band_index, window=window)
	H, W = data.shape

	# --- ADD THIS GUARD CLAUSE ---
	if H == 0 or W == 0:
		# Return an empty dict so merge_corridors_dicts doesn't crash
		return defaultdict(list) 
	# -----------------------------

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
	bin_size = abs(x_m_1d[1] - x_m_1d[0])
	result = defaultdict(list)
	
	flat_d = d_raw.ravel()
	flat_dh = dh.ravel()
	flat_v = data.ravel()

	if radius_m is None:
		Δx_m = abs(x_m_1d[1] - x_m_1d[0])
		Δy_m = abs(y_m_1d[1] - y_m_1d[0])
		radius_m = max(Δx_m, Δy_m)

	for i in range(len(flat_v)):
		if abs(flat_dh[i]) <= radius_m:
			# Group into 1D distance buckets
			d_key = round(round(flat_d[i] / bin_size) * bin_size, key_decimals)
			result[d_key].append((round(flat_dh[i], 1), flat_v[i]))

	return result

def extract_and_profile_old(src, bbox, tx_latlon, rx_latlon, band_index=1, key_decimals=1):
    """
    CRS-Agnostic Extraction:
    1. Projects geographic BBox to Raster Native CRS.
    2. Clips Native coordinates against Native src.bounds (Meters to Meters).
    3. Handles 1D projection relative to global link.
    """
    west_geo, south_geo, east_geo, north_geo = bbox

    # 1. Coordinate Transformers
    # Geo -> Native (Whatever the TIFF uses: UTM, Albers, etc.)
    to_native = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
    
    # Transform Geo BBox to Native CRS
    # We transform corners to ensure we capture the extent in the native system
    w_nat, s_nat = to_native.transform(west_geo, south_geo)
    e_nat, n_nat = to_native.transform(east_geo, north_geo)

    # 2. Native Boundary Clipping (Meters vs Meters)
    # This is the only place where clipping is mathematically valid
    t_w, t_s, t_e, t_n = src.bounds
    
    # We must handle the fact that Native CRS might have inverted axes
    # We ensure safe_w < safe_e and safe_s < safe_n
    safe_w = max(min(w_nat, e_nat), t_w)
    safe_e = min(max(w_nat, e_nat), t_e)
    safe_s = max(min(s_nat, n_nat), t_s)
    safe_n = min(max(s_nat, n_nat), t_n)

    if safe_w >= safe_e or safe_s >= safe_n:
        return defaultdict(list)

    # 3. Window Generation (Integer Snapping)
    window = rasterio.windows.from_bounds(safe_w, safe_s, safe_e, safe_n, src.transform).round()
    
    # Final pixel-grid intersection
    tile_limit = rasterio.windows.Window(0, 0, src.width, src.height)
    try:
        window = window.intersection(tile_limit)
    except rasterio.errors.WindowError:
        return defaultdict(list)

    # 4. Read Data
    data = src.read(band_index, window=window)
    H, W = data.shape
    if H == 0 or W == 0:
        return defaultdict(list)

    # 5. Metric Setup for 1D Path
    # Local Metric CRS (UTM or similar) for 1D distance calculations
    mid_lon, mid_lat = (west_geo + east_geo) / 2, (south_geo + north_geo) / 2
    metric_epsg = get_utm_epsg(mid_lon, mid_lat)
    to_metric = Transformer.from_crs(src.crs, metric_epsg, always_xy=True)

    # Generate Native coordinate vectors for the pixels
    row_start, col_start = int(window.row_off), int(window.col_off)
    rows = np.arange(row_start, row_start + H)
    cols = np.arange(col_start, col_start + W)
    
    xs_native, _ = rasterio.transform.xy(src.transform, [row_start] * W, cols, offset='center')
    _, ys_native = rasterio.transform.xy(src.transform, rows, [col_start] * H, offset='center')

    xs_native, ys_native = np.array(xs_native), np.array(ys_native)

    # Transform to Metric Space
    x_m_1d, _ = to_metric.transform(xs_native, np.full_like(xs_native, ys_native[H // 2]))
    _, y_m_1d = to_metric.transform(np.full_like(ys_native, xs_native[W // 2]), ys_native)

    # 6. Project to 1D Path (TX-RX Line)
    # Project TX/RX from Geo to the same Metric space
    tx_x, tx_y = to_metric.transform(*to_native.transform(tx_latlon[1], tx_latlon[0]))
    rx_x, rx_y = to_metric.transform(*to_native.transform(rx_latlon[1], rx_latlon[0]))

    dx, dy = rx_x - tx_x, rx_y - tx_y
    L_total = math.hypot(dx, dy)
    ux, uy = dx / L_total, dy / L_total

    rel_x = x_m_1d[np.newaxis, :] - tx_x
    rel_y = y_m_1d[:, np.newaxis] - tx_y
    
    d_raw = (rel_x * ux) + (rel_y * uy)
    dh    = (-rel_x * uy) + (rel_y * ux)

    # 7. Dictionary Assembly
    bin_size = abs(x_m_1d[1] - x_m_1d[0]) if len(x_m_1d) > 1 else 1.0
    result = defaultdict(list)
    flat_d, flat_dh, flat_v = d_raw.ravel(), dh.ravel(), data.ravel()

    for i in range(len(flat_v)):
        d_key = round(round(flat_d[i] / bin_size) * bin_size, key_decimals)
        result[d_key].append((round(flat_dh[i], 1), flat_v[i]))

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
def sample_dtm_surface(d_targets, dtm_dict, dh_targets=None):
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

def sample_dsm_surface(d_targets, dsm_dict, dh_targets=None):
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
        vs_dtm = sample_dtm_surface(d_targets, dtm_dict, dh_targets=dhs)
        
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


#--------- 6. PLOT PROFILES -----------------------------------------------------

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

	plt.figure(figsize=(14, 6))
	
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

# Plot multi-profile with real trees high
def plot_advanced_profiles(res_dtm=None, res_dsm=None, res_lulc=None, lulc_heights=None, res_chm=None, title="Path Profile Audit: 10m LULC vs 1m CHM"):
	"""
	Plots DTM, DSM, LULC-based heights, AND the 1-meter High-Res Tree heights.
	"""
	plt.figure(figsize=(15, 7))
	y_values = []

	# 1. DTM (The Foundation)
	if res_dtm is not None:
		res_dtm = np.asarray(res_dtm)
		dist_dtm, h_dtm = res_dtm[:, 0], res_dtm[:, 1]
		y_min_plot = np.min(h_dtm) - 5
		y_values.extend(h_dtm)
		plt.fill_between(dist_dtm, h_dtm, y_min_plot, color='brown', alpha=0.15, label='DTM (Ground)')
		plt.plot(dist_dtm, h_dtm, color='brown', linewidth=1.2, alpha=0.5)

	# 2. DSM (The Satellite Surface)
	if res_dsm is not None:
		res_dsm = np.asarray(res_dsm)
		y_values.extend(res_dsm[:, 1])
		plt.plot(res_dsm[:, 0], res_dsm[:, 1], color='blue', alpha=0.4, 
				 linestyle=':', linewidth=1.0, label='DSM (Satellite Surface)')

	# 3. LULC (The 10m Categorical Assumption)
	if res_lulc is not None and lulc_heights is not None:
		res_lulc = np.asarray(res_lulc)
		dist_lulc, codes = res_lulc[:, 0], res_lulc[:, 1]
		fixed_heights = np.array([lulc_heights.get(int(c), 0) for c in codes])
		
		if res_dtm is not None:
			h_dtm_interp = np.interp(dist_lulc, dist_dtm, h_dtm)
			h_lulc_combined = h_dtm_interp + fixed_heights
			y_values.extend(h_lulc_combined)
			plt.plot(dist_lulc, h_lulc_combined, color='green', linewidth=2, 
					 alpha=0.8, label='LULC Assumption (10m)')

	# 4. CHM Audit (The 1m High-Res Truth)
	if res_chm is not None:
		# res_chm is a list of (distance, height) tuples from our tree sampler
		res_chm = np.asarray(res_chm)
		if len(res_chm) > 0:
			dist_chm, h_chm = res_chm[:, 0], res_chm[:, 1]
			
			if res_dtm is not None:
				# Interpolate DTM to the 1m granularity of the CHM
				h_dtm_chm_interp = np.interp(dist_chm, dist_dtm, h_dtm)
				h_chm_combined = h_dtm_chm_interp + h_chm
				
				y_values.extend(h_chm_combined)
				# We use a distinct color (Red or Orange) to highlight discrepancies
				plt.scatter(dist_chm, h_chm_combined, color='red', s=5, 
							alpha=0.6, label='1m Tree Audit (GEDI/ALS)')
				
				# Optionally draw a line for the high-res canopy
				plt.plot(dist_chm, h_chm_combined, color='red', linewidth=0.8, alpha=0.3)

	# Styling
	if y_values:
		plt.ylim(np.min(y_values) - 5, np.max(y_values) + 15)
	
	plt.title(title)
	plt.xlabel("Distance from Source (meters)")
	plt.ylabel("Elevation (m ASL)")
	plt.legend(loc='upper right', frameon=True, fontsize='small')
	plt.grid(True, linestyle='--', alpha=0.3)
	plt.tight_layout()
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
def plot_surface_dict(data, legend_dict=None):
    plot_data = []
    for d, points in data.items():
        for dh, v in points:
            plot_data.append((d, dh, v))

    ds, dhs, vs = zip(*plot_data)

    plt.figure(figsize=(15, 3))
    
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

    plt.axhline(0, color='red', linestyle='--', alpha=0.5)
    plt.title(title)
    plt.xlabel("Distance from TX (m)")
    plt.ylabel("Perpendicular Offset (m)")
    plt.tight_layout()
    plt.show()

# Plot several lists of tuples (d,v) in the same plot
def plot_data_tuples(*dv_tuples_list, labels=None, colors=None, title="Values along Distance",
                     xlabel="Distance from TX (m)", ylabel="Value (m)", figsize=(12, 5),
                     grid_alpha=0.3):
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
    plt.figure(figsize=figsize)
    
    # Generate default labels if needed
    labels = labels or [f"Series {i+1}" for i in range(len(dv_tuples_list))]
    
    for i, dv_tuples in enumerate(dv_tuples_list):
        if not dv_tuples:
            continue
            
        dist_coords, heights = zip(*dv_tuples)
        color = colors[i] if colors and i < len(colors) else None
        
        plt.plot(dist_coords, heights, label=labels[i], color=color, alpha=0.8)
    
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.legend()
    plt.grid(True, alpha=grid_alpha)
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
    plot_data_tuples(*data_series, labels=labels, **kwargs)


#---------- 7. MINIO DIRECT ACCESS (SIGNED URL) ----------------------------------------

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


#--------- 8. SHOW TIFF (SIGNED URL) -----------------------------------------------------

# Plot tiles
def create_geotiff_plot(file_path, band=1, downscale=1):
	"""
	Create georeferenced TIFF plot objects.
	
	Returns: fig, ax, data, metadata dict with transform, crs, bounds, etc.
	"""
	
	with rasterio.open(file_path) as src:
		# Read with optional downscaling
		if downscale > 1:
			h = int(src.height // downscale)
			w = int(src.width // downscale)
			data = src.read(band, out_shape=(h, w), resampling=Resampling.bilinear)
			transform = src.transform * src.transform.scale(src.width/w, src.height/h)
		else:
			data = src.read(band)
			transform = src.transform
			h, w = src.height, src.width
		
		# Create figure and axes
		fig, ax = plt.subplots(figsize=(10, 8))
		
		# Plot with georeferencing
		show(data, transform=transform, ax=ax)
		
		# Build metadata
		metadata = {
			'transform': transform,
			'crs': src.crs,
			'bounds': src.bounds,
			'resolution': (transform.a, -transform.e),
			'nodata': src.nodatavals[band-1],
			'dtype': src.dtypes[band-1],
			'band_name': src.descriptions[band-1] or f"Band {band}",
			'shape': data.shape,
			'file_path': file_path,
			'src_width': src.width,
			'src_height': src.height,
		}
		
		return fig, ax, data, metadata

# Wrapper with default values
def show_tiff_band(tif_path, band_index=1, downscale_factor=1, save_png=False, output_name=None):
	"""
	Downscales, previews, and optionally saves a TIFF band.
	"""
	# Create plot
	fig, ax, data, meta = create_geotiff_plot(tif_path, band_index, downscale_factor)
	ax.clear()
	
	# Get bounds for extent
	bounds = meta['bounds']
	
	# Plot WITHOUT transform parameter
	img = ax.imshow(data, 
					cmap='viridis',
					extent=[bounds.left, bounds.right, bounds.bottom, bounds.top])
	
	# Get valid data range
	plot_data = data.copy()
	nodata = meta['nodata']
	
	if nodata is not None:
		valid_mask = plot_data != nodata
		if np.any(valid_mask):
			valid_data = plot_data[valid_mask]
		else:
			valid_data = np.array([0, 0])
	else:
		valid_data = plot_data.flatten()
	
	vmin, vmax = float(np.min(valid_data)), float(np.max(valid_data))
	
	# Set image normalization
	img.set_clim(vmin, vmax)
	
	# Colorbar
	cbar = fig.colorbar(img, ax=ax, label="Values")
	
	# Title
	desc = meta['band_name'] or f"Band {band_index}"
	if downscale_factor > 1:
		desc += f" (Downscaled {downscale_factor}x)"
	ax.set_title(f"{desc}")
	
	# Axis labels
	crs = meta['crs']
	if crs and crs.is_geographic:
		ax.set_xlabel("Longitude")
		ax.set_ylabel("Latitude")
	else:
		ax.set_xlabel("Easting")
		ax.set_ylabel("Northing")
	
	plt.tight_layout()
	
	# Optional PNG Saving
	if save_png:
		if not output_name:
			output_name = f"preview_b{band_index}_downsampled.png"
		
		# Normalize to 8-bit
		d_min, d_max = np.nanmin(data), np.nanmax(data)
		if d_max != d_min:
			scaled = ((data - d_min) / (d_max - d_min) * 255)
			scaled = np.nan_to_num(scaled, nan=0).astype('uint8')
			Image.fromarray(scaled).save(output_name)
			print(f"Saved: {output_name}")
	
	plt.show()

# Plots a rectangular area of LULC data with a Fresnel zone overlay.
def show_lulc_fresnel_zone(geojson_path, legend_path, start_latlon, end_latlon, freq_ghz, margin_px=50, step=1):
	"""
	Plots a rectangular area of LULC data with a Fresnel zone overlay.
	Correctly handles native CRS transformations and alignment.
	"""
	# 1. Initialize variables and identify tiles
	lats = [start_latlon[0], end_latlon[0]]
	lons = [start_latlon[1], end_latlon[1]]
	geojson_dir = Path(geojson_path).parent
	
	# Find tiles needed for the endpoints
	tile_names = find_tiles_by_list(geojson_path, [start_latlon, end_latlon])
	if not tile_names:
		print("No tiles found for these coordinates.")
		return

	# 2. Extract Native CRS and Resolution info from the first tile
	with rasterio.open(geojson_dir / tile_names[0]) as sample_src:
		native_crs = sample_src.crs
		res_x, res_y = sample_src.res

	# 3. Handle Coordinate Transformations
	to_native = Transformer.from_crs("EPSG:4326", native_crs, always_xy=True)
	to_wgs84 = Transformer.from_crs(native_crs, "EPSG:4326", always_xy=True)
	
	# Transform endpoints to native coordinates
	s_x, s_y = to_native.transform(start_latlon[1], start_latlon[0])
	e_x, e_y = to_native.transform(end_latlon[1], end_latlon[0])
	
	# 4. Define Expanded Native Bounds (Margin is applied here)
	west_n = min(s_x, e_x) - (margin_px * res_x)
	east_n = max(s_x, e_x) + (margin_px * res_x)
	south_n = min(s_y, e_y) - (margin_px * res_y)
	north_n = max(s_y, e_y) + (margin_px * res_y)
	
	# 5. Calculate geographic extent for Matplotlib (Alignment fix)
	# We transform the corners of the expanded box back to Lat/Lon
	west_lon, south_lat = to_wgs84.transform(west_n, south_n)
	east_lon, north_lat = to_wgs84.transform(east_n, north_n)
	
	# 6. Merge, Crop, and Downscale
	src_files = [rasterio.open(geojson_dir / name) for name in tile_names]
	
	# Define desired output shape for downscaling
	out_height = int(abs(north_n - south_n) / (res_y * step))
	out_width = int(abs(east_n - west_n) / (res_x * step))
	
	mosaic, out_trans = merge(
		src_files, 
		bounds=(west_n, south_n, east_n, north_n),
		resampling=Resampling.nearest,
		target_aligned_pixels=True
	)
	for src in src_files: src.close()
	
	lulc_data = mosaic[0]

	# 7. Apply the Legend (Categorical Color Mapping)
	with open(legend_path, 'r') as f:
		legend_data = json.load(f)

	rgb_map = np.zeros((lulc_data.shape[0], lulc_data.shape[1], 3), dtype=np.uint8)
	legend_elements = []
	
	for class_id, info in legend_data.items():
		cid = int(class_id)
		if cid in lulc_data:
			rgb_map[lulc_data == cid] = info['color']
			# Create a swatch for the legend display
			patch = patches.Patch(color=np.array(info['color'])/255, 
								  label=info['name'].replace('_', ' ').title())
			legend_elements.append(patch)

	# 8. Fresnel Geometry Calculations
	geod = Geod(ellps="WGS84")
	_, _, dist_m = geod.inv(start_latlon[1], start_latlon[0], end_latlon[1], end_latlon[0])
	fresnel_r1 = 17.32 * np.sqrt((dist_m/1000) / (4 * freq_ghz))

	# 9. Final Plotting
	fig, ax = plt.subplots(figsize=(12, 10))
	
	# Use the expanded geographic extent calculated in step 5
	img_extent = [west_lon, east_lon, south_lat, north_lat]
	ax.imshow(rgb_map, extent=img_extent, origin='upper')
	
	# Draw Link line
	ax.plot([start_latlon[1], end_latlon[1]], [start_latlon[0], end_latlon[0]], 
			color='white', linestyle='--', linewidth=1.5, alpha=0.9)

	# Add Fresnel Ellipse
	mid_lat, mid_lon = np.mean(lats), np.mean(lons)
	angle = np.degrees(np.arctan2(end_latlon[0] - start_latlon[0], end_latlon[1] - start_latlon[1]))
	deg_h = (2 * fresnel_r1) / 111000
	deg_w = dist_m / (111000 * np.cos(np.radians(mid_lat)))
	
	ellipse = patches.Ellipse((mid_lon, mid_lat), width=deg_w, height=deg_h, angle=angle,
							  edgecolor='yellow', facecolor='yellow', alpha=0.25, linewidth=2)
	ax.add_patch(ellipse)

	ax.set_title(f"ESRI Land Cover: 1st Fresnel Zone at {freq_ghz} GHz")
	ax.set_xlabel("Longitude")
	ax.set_ylabel("Latitude")
	ax.legend(handles=legend_elements + [ellipse], loc='center left', bbox_to_anchor=(1, 0.5))
	
	plt.tight_layout()
	plt.show()



# ------ 9. ONLINE METHODS (THREE HEIGHT) -----------------

def get_quadkey(lat, lon, level=9):
	"""Calculates the Bing Maps Quadkey for a given coordinate."""
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

def get_canopy_height(lat, lon):
	"""
	Retrieves 1m canopy height, handling 404 errors for missing tiles 
	and CRS transformations.
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

