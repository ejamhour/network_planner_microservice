# Standard library imports (alphabetical)
import glob
import json
import math
import multiprocessing as mp
from functools import partial
from multiprocessing import Manager, Pool, Process
import os
import re
import logging

# Third-party library imports (alphabetical within groups)
import duckdb
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
import numpy as np

# Geospatial libraries (grouped by functionality)
import pyproj
from pyproj import Transformer # compiled CRS projections
from geopy.distance import geodesic

# Raster/geospatial processing
import rasterio
from rasterio.warp import transform # convert x,y between CRS
from rasterio import features
from rasterio.windows import from_bounds # extract subwindow

# Geometry/spatial operations
from shapely import wkt
from shapely.geometry import Polygon, box, LineString, shape, Point, MultiPolygon
from shapely.ops import transform # reproject coordinate

from io import BytesIO
import base64


'''
This module is used to process buildings footprints in 2D from google
Download from:https://sites.research.google/gr/open-buildings/#open-buildings-download
Data is downloaded in csv.gz format
'''

logger = logging.getLogger(__name__)

# ---- 1. VERIFY THE DUCKDB IN MEMORY ------------------------------------

# Return maximum coordinates in the database
def check_db_bounds(db_input):

	# Identify input type and manage the connection
	is_path = isinstance(db_input, str)
	
	if is_path:
		# If it's a string, we create a temporary connection
		con = duckdb.connect(db_input)
	else:
		# If it's already a connection, we use it directly
		con = db_input
	
	# Get the bounding box of the data
	coverage = con.execute("""
		SELECT 
			MIN(latitude) as min_lat, 
			MAX(latitude) as max_lat, 
			MIN(longitude) as min_lon, 
			MAX(longitude) as max_lon,
			COUNT(*) as total_buildings
		FROM buildings
	""").df()

	return(coverage)    

# Return some rows of the database
def check_db_schema(db_input):

	# Identify input type and manage the connection
	is_path = isinstance(db_input, str)
	
	if is_path:
		# If it's a string, we create a temporary connection
		con = duckdb.connect(db_input)
	else:
		# If it's already a connection, we use it directly
		con = db_input
	
	# This command describes the structure of the 'buildings' table
	print("--- Table Schema for 'buildings' ---")
	schema = con.execute("PRAGMA table_info('buildings')").fetchall()
	
	for column in schema:
		# column[1] is the name, column[2] is the type
		print(f"Column Name: {column[1]:<20} | Type: {column[2]}")
	
	# Also show the first 2 rows of data to verify the values
	print("\n--- First 2 rows of data ---")
	data = con.execute("SELECT * FROM buildings LIMIT 2").df()
	return(data)

# Return the top buildings in the DuckDB file
def check_top_buildings(db_input, height, rows):

	flag_close = False

	# Identify input type and manage the connection
	is_path = isinstance(db_input, str)
	
	if is_path:
		# If it's a string, we create a temporary connection
		db = duckdb.connect(db_input)
		flag_close = True
	else:
		# If it's already a connection, we use it directly
		db = db_input
	
	query = f"""
		SELECT * 
		FROM buildings 
		WHERE p90_h > {height}
		ORDER BY p90_h DESC
		LIMIT {rows}
	"""
	
	rows = db.execute(query).df()
	if flag_close:
		db.close()
	
	if rows.empty:
		print("No results found. Did you process the first tile yet?")
	else:
		return rows	


# ---- 2. LOAD PARQUETS TO DUCKDB IN MEMORY ---------------------------------

# Identify required parquets (generate tiles for page_fault)
def identify_required_tiles(lat_lon_list, geojson_index):
	"""
	Accepts coordinates in (Lat, Lon) order.
	Checks if geojson_index is a file path string or a dictionary object.
	"""
	# 1. Handle the input type for geojson_index
	if isinstance(geojson_index, str):
		if not os.path.exists(geojson_index):
			raise FileNotFoundError(f"The index file '{geojson_index}' was not found.")
		with open(geojson_index, 'r') as f:
			index_data = json.load(f)
	else:
		index_data = geojson_index

	# 2. Fix the notation: (Lat, Lon) -> (Lon, Lat) for the geometry engine
	engine_coords = [(lon, lat) for lat, lon in lat_lon_list]

	# 3. Create the search geometry
	if len(engine_coords) == 1:
		input_geom = Point(engine_coords[0])
	else:
		input_geom = LineString(engine_coords)

	# 4. Perform the intersection
	required_tiles = set()
	for feature in index_data['features']:
		tile_geom = shape(feature['geometry'])
		if input_geom.intersects(tile_geom):
			required_tiles.add(feature['properties']['id_filename'])
			
	return list(required_tiles)

# Check if parquet tiles are already in memory and load otherwise
def duckdb_page_fault(con, required_tiles, local_repo_path):
	"""
	Ensures required tiles are loaded into the in-memory 'buildings' table.
	Initializes the 'buildings' table and tracking metadata if they don't exist.
	"""
	# 1. Initialize the metadata tracker
	con.execute("CREATE TABLE IF NOT EXISTS loaded_tiles (filename TEXT UNIQUE)")
	
	# 2. Check if the 'buildings' table exists in this connection
	table_exists = con.execute("""
		SELECT count(*) FROM information_schema.tables 
		WHERE table_name = 'buildings'
	""").fetchone()[0] > 0

	# 3. Get currently paged-in files
	already_loaded = {row[0] for row in con.execute("SELECT filename FROM loaded_tiles").fetchall()}

	for tile_file in required_tiles:
		if tile_file not in already_loaded:
			file_path = os.path.join(local_repo_path, tile_file)
			
			if not os.path.exists(file_path):
				print(f"Critical Error: {tile_file} not found in local repository.")
				continue

			logger.debug(f"Page Fault: Loading {tile_file} into memory...")
			
			# 4. Initialize OR Append logic
			if not table_exists:
				# First time: Create the table and define schema from the first parquet
				con.execute(f"""
					CREATE TABLE buildings AS 
					SELECT * FROM read_parquet('{file_path}', union_by_name=True)
				""")
				table_exists = True # Flag that future loads should be INSERTS
			else:
				# Subsequent times: Append to the existing schema
				con.execute(f"""
					INSERT INTO buildings 
					SELECT * FROM read_parquet('{file_path}', union_by_name=True)
				""")
			
			# 5. Update tracker
			con.execute("INSERT INTO loaded_tiles VALUES (?)", [tile_file])
		else:
			logger.debug(f"Cache Hit: {tile_file} is already paged in.")

	# 6. Ensure indices exist for the now-populated table
	if table_exists:
		con.execute("CREATE INDEX IF NOT EXISTS idx_coords ON buildings (latitude, longitude)")


# ---- 3. LEGACY FUNCTIONS DO SELECT BUILDING CLUTTERING -------------------

# Return maximum fresnel radius and link distance
def get_max_fresnel_radius(tx_coords, rx_coords, freq_ghz=0.9):
	# 1. Calculate geodesic distance (Accurate)
	distance_m = geodesic(tx_coords, rx_coords).meters
	dist_km = distance_m / 1000.0
	
	# 2. Fresnel radius in meters (Accurate)
	max_radius_m = 17.32 * math.sqrt(dist_km / (4 * freq_ghz))
	
	# 3. BETTER CONVERSION: Direction-aware degree margins
	# Latitude is constant: ~111km per degree
	lat_margin_deg = max_radius_m / 111132.0 
	
	# Longitude depends on the cosine of the latitude
	avg_lat = math.radians((tx_coords[0] + rx_coords[0]) / 2.0)
	lon_margin_deg = max_radius_m / (111320.0 * math.cos(avg_lat))
	
	# Return both so the BBOX can be perfectly padded
	return max_radius_m, distance_m, lat_margin_deg, lon_margin_deg

# Computes the distance from a point to a line
def point_to_line_dist(py, px, p1, p2):
	# p1 and p2 are (lat, lon) tuples of the TX and RX
	y1, x1 = p1
	y2, x2 = p2
	
	dx, dy = x2 - x1, y2 - y1
	
	# Handle the case where TX and RX are the same point
	if dx == 0 and dy == 0:
		return geodesic((py, px), (y1, x1)).meters
	
	# Calculate the projection fraction t in coordinate space
	t = ((px - x1) * dx + (py - y1) * dy) / (dx*dx + dy*dy)
	t = max(0, min(1, t))
	
	# Find the closest point on the line segment in degrees
	closest_y = y1 + t * dy
	closest_x = x1 + t * dx
	
	# Calculate the geodesic distance between the building and the closest point
	return geodesic((py, px), (closest_y, closest_x)).meters

# Return obstacles insides a bouding box (lat, lon)
def get_link_obstacles(con, tx_coords, rx_coords, min_height, margin = None):	
	
	# Calculate Bounding Box
	box_margin = 0.0005
	min_lon = min(tx_coords[1], rx_coords[1]) - box_margin
	max_lon = max(tx_coords[1], rx_coords[1]) + box_margin
	min_lat = min(tx_coords[0], rx_coords[0]) - box_margin
	max_lat = max(tx_coords[0], rx_coords[0]) + box_margin
	
	# SQL Query with your vertical threshold
	query = f"""
		SELECT rowid, *
		FROM buildings
		WHERE longitude BETWEEN {min_lon} AND {max_lon}
		  AND latitude BETWEEN {min_lat} AND {max_lat}
		  AND p90_h > {min_height}
	"""

  
	cursor = con.execute(query)
	cols = {d[0]: i for i, d in enumerate(con.description)}
	candidates = cursor.fetchall()    
	
	# Second Stage: Cross-track distance filter (Vector Math)    
	if margin is not None:
		path_obstacles = []
		for b in candidates:
			dist = point_to_line_dist(b[1], b[2], tx_coords, rx_coords)
			if dist < margin:  
				path_obstacles.append(b)                   
	else:
		path_obstacles = candidates


	return path_obstacles, cols

# Return obstacles insides a bouding box (lat, lon)
def plot_rf_with_polygons(tx, rx, results, cols):   

	if not results:
		print("No buildings with geometry and height found in this area.")
		return

	plt.figure(figsize=(12, 10))
	ax = plt.gca()
	
	for row in results:
		wkt_geom, height =  row[cols['geometry']], row[cols['p90_h']]
		try:
			# Convert WKT string to a Shapely object
			poly = wkt.loads(wkt_geom)
			
			# If it's a MultiPolygon, iterate through parts
			if poly.geom_type == 'MultiPolygon':
				polygons = list(poly.geoms)
			else:
				polygons = [poly]
				
			# Color by height
			color = plt.cm.viridis(min(height / 30.0, 1.0))
			
			for p in polygons:
				# Get coordinates for the exterior ring
				x, y = p.exterior.xy
				# Create a Matplotlib Polygon patch
				patch = MplPolygon(np.column_stack((x, y)), 
								   closed=True, color=color, 
								   alpha=0.7, ec='black', lw=0.5)
				ax.add_patch(patch)
		except Exception as e:
			continue # Skip invalid geometries

	# Draw the RF Link
	plt.plot([tx[1], rx[1]], [tx[0], rx[0]], color='red', linestyle='--', 
			 linewidth=2, label='RF Path (LOS)', zorder=10)
	
	# Plot TX/RX
	plt.plot(tx[1], tx[0], 'r^', markersize=12, label='TX', zorder=11)
	plt.plot(rx[1], rx[0], 'rv', markersize=12, label='RX', zorder=11)

	plt.xlabel('Longitude')
	plt.ylabel('Latitude')
	plt.title('RF Link: Real Building Footprints')
	plt.legend()
	plt.grid(True, alpha=0.3)
	plt.axis('equal')

	add_fresnel_to_plot(tx,rx, freq_ghz = 0.9)
	
	plt.savefig('rf_polygon_plot.png', dpi=300)
	print(f"Plot saved with {len(results)} real polygons.")

# Auxiliary function of plot_rf_with_polygons and other plot functions (NOT LEGACY)
def add_fresnel_to_plot(tx, rx, freq_ghz=0.9, ax=None):
	import matplotlib.pyplot as plt

	lat1, lon1 = tx
	lat2, lon2 = rx
	
	# 1. Coordinate Delta
	d_lon = lon2 - lon1
	d_lat = lat2 - lat1
	
	# 2. Distance in Meters (for Paraná/Curitiba latitude)
	# Using 111320 as a standard conversion factor
	total_dist_deg = math.sqrt(d_lon**2 + d_lat**2)
	total_dist_m = total_dist_deg * 111320.0 
	
	# 3. Physics: Wavelength at 900MHz is ~0.333m
	c = 299792458
	wavelength = c / (freq_ghz * 1e9)
	
	angle = math.atan2(d_lat, d_lon)
	
	steps = 100 
	fresnel_points_left = []
	fresnel_points_right = []
	
	for i in range(steps + 1):
		fraction = i / steps
		curr_lon = lon1 + fraction * d_lon
		curr_lat = lat1 + fraction * d_lat
		
		d1 = fraction * total_dist_m
		d2 = total_dist_m - d1
		
		# CORRECTED RADIUS MATH (Pure meters)
		if total_dist_m == 0:
			radius_m = 0
		else:
			# R = sqrt( (lambda * d1 * d2) / D )
			radius_m = math.sqrt((wavelength * d1 * d2) / total_dist_m)
		
		# Convert radius to degrees for the plot
		radius_deg = radius_m / 111320.0
		
		# Perpendicular Offsets
		left_lon = curr_lon + radius_deg * math.cos(angle + math.pi/2)
		left_lat = curr_lat + radius_deg * math.sin(angle + math.pi/2)
		right_lon = curr_lon + radius_deg * math.cos(angle - math.pi/2)
		right_lat = curr_lat + radius_deg * math.sin(angle - math.pi/2)
		
		fresnel_points_left.append((left_lon, left_lat))
		fresnel_points_right.append((right_lon, right_lat))

	# Plot the boundaries
	lons_l, lats_l = zip(*fresnel_points_left)
	lons_r, lats_r = zip(*fresnel_points_right)

	if ax is None:
		ax = plt.gca()
	ax.plot(lons_l, lats_l, color='orange', alpha=0.6, linewidth=1.5)
	ax.plot(lons_r, lats_r, color='orange', alpha=0.6, linewidth=1.5)
	
	# Fill the zone
	ax.fill(list(lons_l) + list(lons_r)[::-1], 
			 list(lats_l) + list(lats_r)[::-1], 
			 color='orange', alpha=0.15)


# ---- 4. PRECISE FUNCTIONS TO SELECT BUILDING CLUTTERING-----------------

# UDF function for Fresnel SQL filtering
def is_building_intersecting(b_lat, b_lon, tx_lat, tx_lon, rx_lat, rx_lon, max_radius_m, area_m2=None):
	"""
	Generic spatial filter that accounts for building mass using its area.
	If area_m2 is provided, it expands the 'danger zone' to include the 
	building's physical footprint.
	"""
	# 1. Coordinate Scaling (Generic for any Latitude)
	avg_lat = (tx_lat + rx_lat) / 2.0
	lat_to_m = 111132.0 
	lon_to_m = 111320.0 * np.cos(np.radians(avg_lat))
	
	# 2. Local Meter Grid Translation (Relative to TX)
	A = np.array([0, 0])
	B = np.array([(rx_lat - tx_lat) * lat_to_m, (rx_lon - tx_lon) * lon_to_m])
	P = np.array([(b_lat - tx_lat) * lat_to_m, (b_lon - tx_lon) * lon_to_m])
	
	# 3. Vector Math for Projection
	ab = B - A
	ap = P - A
	ab_mag_sq = np.dot(ab, ab)
	
	if ab_mag_sq == 0: 
		return False
	
	# Position along the link (t)
	t = np.dot(ap, ab) / ab_mag_sq
	
	# 4. Building Radius Calculation (The Safety Margin)
	# We treat the building as a circle with radius R_b to guard the edges.
	building_radius = 0.0
	if area_m2 is not None and area_m2 > 0:
		# Conservative: using sqrt(area) to handle diagonal length of squares
		building_radius = np.sqrt(area_m2) 

	# 5. Expanded Boundary Check
	# Check if the building is longitudinally near the link, including its footprint
	if t < (0.0 - building_radius/np.linalg.norm(ab)) or t > (1.0 + building_radius/np.linalg.norm(ab)):
		return False
		
	# 6. Cross-track Distance (v_h) with Mass Buffer
	closest_point = A + t * ab
	dist_m = np.linalg.norm(P - closest_point)
	
	# The building is an obstacle if the distance to its center is less than 
	# the Fresnel radius PLUS the building's own radius.
	return bool(dist_m <= (max_radius_m + building_radius))

# UDF based selection of buildinds intersecting Fresnel (approximated by the max_radius)
def get_precise_fresnel_buildings_old(con, tx, rx, max_radius_m):
	
	# 1. Update the Function Registration
	# We change the name to 'is_building_intersecting'
	# We add an 8th argument for 'area_in_meters'

	res = con.execute("SELECT function_name FROM duckdb_functions() WHERE function_name = 'is_building_intersecting'").fetchall()

	if not res:
		print("Registering UDF...")
		con.create_function(
			name='is_building_intersecting',
			function=is_building_intersecting,
			parameters=['DOUBLE', 'DOUBLE', 'DOUBLE', 'DOUBLE', 'DOUBLE', 'DOUBLE', 'DOUBLE', 'DOUBLE'],
			return_type='BOOLEAN'
		)
	else:
		logger.debug("UDF is already hot in memory.")

	# 2. Generic BBOX Filtering (No Angle Simplifications)
	# This creates a "safe net" that accounts for Curitiba's longitude squeeze
	avg_lat = (tx[0] + rx[0]) / 2.0
	lat_margin = max_radius_m / 111132.0
	lon_margin = max_radius_m / (111320.0 * math.cos(math.radians(avg_lat)))

	lat_min, lat_max = min(tx[0], rx[0]) - lat_margin, max(tx[0], rx[0]) + lat_margin
	lon_min, lon_max = min(tx[1], rx[1]) - lon_margin, max(tx[1], rx[1]) + lon_margin

	# 3. Optimized Query
	# We now pass 'area_in_meters' to the UDF to enable mass-aware filtering
	query = f"""
		SELECT *            
		FROM buildings
		WHERE latitude BETWEEN {lat_min} AND {lat_max}
		  AND longitude BETWEEN {lon_min} AND {lon_max}
		  AND is_building_intersecting(
			  latitude, longitude, 
			  {tx[0]}, {tx[1]}, 
			  {rx[0]}, {rx[1]}, 
			  {max_radius_m}, 
			  area_in_meters
		  )
	"""
	
	cursor = con.execute(query)
	cols = {d[0]: i for i, d in enumerate(con.description)}
	results = cursor.fetchall() # to tuples

	print("Registering UDF...Done")
	return results, cols

def get_precise_fresnel_buildings(con, tx, rx, max_radius_m):
    tx_lat, tx_lon = tx
    rx_lat, rx_lon = rx

    avg_lat = (tx_lat + rx_lat) / 2.0
    lat_to_m = 111132.0
    lon_to_m = 111320.0 * math.cos(math.radians(avg_lat))

    # Link in local metric coordinates, TX = origin
    bx = (rx_lat - tx_lat) * lat_to_m
    by = (rx_lon - tx_lon) * lon_to_m

    ab_mag_sq = bx * bx + by * by
    if ab_mag_sq == 0:
        return [], {}

    ab_len = math.sqrt(ab_mag_sq)

    lat_margin = max_radius_m / lat_to_m
    lon_margin = max_radius_m / lon_to_m

    lat_min = min(tx_lat, rx_lat) - lat_margin
    lat_max = max(tx_lat, rx_lat) + lat_margin
    lon_min = min(tx_lon, rx_lon) - lon_margin
    lon_max = max(tx_lon, rx_lon) + lon_margin

    query = f"""
    WITH candidates AS (
        SELECT *,
               (latitude  - {tx_lat}) * {lat_to_m} AS px,
               (longitude - {tx_lon}) * {lon_to_m} AS py,
               COALESCE(
                   CASE
                       WHEN area_in_meters > 0 THEN sqrt(area_in_meters)
                       ELSE 0
                   END,
                   0
               ) AS building_radius
        FROM buildings
        WHERE latitude BETWEEN {lat_min} AND {lat_max}
          AND longitude BETWEEN {lon_min} AND {lon_max}
    ),
    proj AS (
        SELECT *,
               ((px * {bx}) + (py * {by})) / {ab_mag_sq} AS t
        FROM candidates
    )
    SELECT *
    FROM proj
    WHERE t >= -building_radius / {ab_len}
      AND t <= 1.0 + building_radius / {ab_len}
      AND (
            (px - t * {bx}) * (px - t * {bx}) +
            (py - t * {by}) * (py - t * {by})
          ) <= ( {max_radius_m} + building_radius ) * ( {max_radius_m} + building_radius )
    """

    cursor = con.execute(query)
    cols = {d[0]: i for i, d in enumerate(cursor.description)}
    results = cursor.fetchall()
    return results, cols

# ---- 5. CALCULATE FRESNEL INVASIONS ---------------------------
# Reprojects a single building polygon using TX as horizontal AXIS
def project_wkt_to_line_standalone(tx_lat, tx_lon, rx_lat, rx_lon, polygon_wkt):
	"""
	Transforms GPS coordinates to a local 2D meter-grid centered at TX
	using the WGS84 ellipsoid. RX is rotated to the positive X-axis.
	"""
	
	# 1. Define a custom Azimuthal Equidistant projection centered on the TX
	# This is the 'Planet Earth' standard for local distance/bearing accuracy.
	aeqd_proj = pyproj.Proj(proj='aeqd', ellps='WGS84', datum='WGS84', 
							lat_0=tx_lat, lon_0=tx_lon, units='m')
	
	# 2. Project the Receiver to get its (x, y) relative to TX
	rx_x, rx_y = aeqd_proj(rx_lon, rx_lat)
	
	# Calculate the geodetic path angle (theta)
	theta = np.arctan2(rx_y, rx_x)
	
	# 3. Parse the Building and project its vertices
	poly = wkt.loads(polygon_wkt)
	
	# Function to project each point in the polygon
	project = pyproj.Transformer.from_crs("EPSG:4326", aeqd_proj.crs, always_xy=True).transform
	projected_poly = transform(project, poly)
	
	local_vertices = []
	# 4. Rotate all vertices so the LOS (TX -> RX) is the X-axis
	for x_p, y_p in projected_poly.exterior.coords:
		# Standard rotation matrix to align RX to the X-axis
		x_rot = x_p * np.cos(theta) + y_p * np.sin(theta)
		y_rot = -x_p * np.sin(theta) + y_p * np.cos(theta)
		local_vertices.append((x_rot, y_rot))
		
	return local_vertices, np.linalg.norm([rx_x, rx_y])

# Auxiliare function for project_wkt_to_line

def compute_link_frame(tx_lat, tx_lon, rx_lat, rx_lon):

    proj_str = (
        f"+proj=aeqd +lat_0={tx_lat} +lon_0={tx_lon} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )

    transformer = pyproj.Transformer.from_crs(
        "EPSG:4326", proj_str, always_xy=True
    )

    rx_x, rx_y = transformer.transform(rx_lon, rx_lat)
    total_dist = np.hypot(rx_x, rx_y)

    theta = np.arctan2(rx_y, rx_x)
    rot = np.array([
        [ np.cos(-theta), -np.sin(-theta)],
        [ np.sin(-theta),  np.cos(-theta)]
    ])

    return transformer, rot, total_dist

def project_wkt_to_line(wkt_geom, transformer, rot):

    def project_coords(coords):
        out = []
        for lon, lat in coords:
            x, y = transformer.transform(lon, lat)
            xr, yr = rot @ np.array([x, y])
            out.append((xr, yr))
        return out

    geom = wkt.loads(wkt_geom)
    local_vertices = []

    if isinstance(geom, Point):
        local_vertices = project_coords([geom.coords[0]])

    elif isinstance(geom, LineString):
        local_vertices = project_coords(geom.coords)

    elif isinstance(geom, Polygon):
        local_vertices = project_coords(geom.exterior.coords)

    elif isinstance(geom, MultiPolygon):
        for poly in geom.geoms:
            local_vertices.extend(project_coords(poly.exterior.coords))

    else:
        raise TypeError(f"Unsupported geometry type: {geom.geom_type}")

    return local_vertices

# Evaluates Fresnel invasions of the Building
def extract_fresnel_features(local_vertices, total_dist, f_radius):
	"""
	Extracts v_h, Deepness, and Area from local (x, y) coordinates.
	
	local_vertices: List of (x, y) from project_wkt_to_line
	total_dist: Geodetic distance between TX and RX
	f_radius: Fresnel radius at the building's midpoint distance (constant approximation)
	"""

	if f_radius == 0:
		return {
		"v_h": -1.0,
		"deepness_m": 0.0,
		"area_m2": 0.0,
	}
	
	# Create a Shapely polygon from the local vertices for area/clipping
	bldg_poly = Polygon(local_vertices)
	
	# --- 1. Pessimistic v_h calculation ---
	# Convert y-offsets to v-factors (v = y / Rf)
	# y > 0 is Left, y < 0 is Right (or vice versa, it's symmetric)
	y_coords = [v[1] for v in local_vertices]
	v_values = [y / f_radius for y in y_coords]
	
	v_min, v_max = min(v_values), max(v_values)
	
	# Case Logic based on your "Slow Thinking" dump:
	if v_min > 0: # Entirely on one side
		v_h = -v_min
	elif v_max < 0: # Entirely on the other side
		v_h = v_max  # stays negative
	else:
		# Straddling the LOS (v=0)
		# We take the minimum of the two side invasions (pessimistic)
		# v_max is the invasion on the 'positive' side
		# abs(v_min) is the invasion on the 'negative' side
		v_h = min(v_max, abs(v_min))

	# --- 2. Deepness calculation ---
	# We find the span of the building along the X-axis where it's inside the Fresnel 'strip'
	# Define the Fresnel Strip as a box spanning the whole link vertically +/- f_radius
	fresnel_strip = box(0, -f_radius, total_dist, f_radius)
	
	# Intersection of building and the strip
	intersected_poly = bldg_poly.intersection(fresnel_strip)
	
	if intersected_poly.is_empty:
		deepness = 0.0
		area_inside = 0.0
	else:
		# Deepness is the X-extent of the intersection
		bounds = intersected_poly.bounds  # (minx, miny, maxx, maxy)
		deepness = bounds[2] - bounds[0]
		
		# --- 3. Area calculation ---
		area_inside = intersected_poly.area

	return {
		"v_h": round(v_h, 4),
		"deepness_m": round(deepness, 2),
		"area_m2": round(area_inside, 2)
	}

# Shows a single building projection
def show_building_clutter(local_vertices, f_radius, features=None, **kwargs):
	"""
	Visualizes the building's interaction with the Fresnel zone.
	
	features: Optional dict from extract_fresnel_features to display on the plot.
	"""
	def fmt(val, precision=".2f"):
		return f"{val:{precision}}" if isinstance(val, (int, float)) else "N/A"

	x_coords, y_coords = zip(*local_vertices)
	
	# Calculate limits for a balanced view
	x_min, x_max = min(x_coords), max(x_coords)
	y_limit = max(f_radius * 1.5, max(abs(y) for y in y_coords) + 2)
	
	return_base64 = kwargs.get('base64', False)
	dpi = kwargs.get('dpi', None)
	figsize = kwargs.get('figsize', None)
	fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
	
	# 1. The Fresnel Zone (The 'River')
	# We shade the area between -Rf and +Rf to visualize the 'clearance' zone
	ax.fill_between([x_min - 5, x_max + 5], -f_radius, f_radius, 
					color='green', alpha=0.1, label='1st Fresnel Zone')
	
	# 2. LOS and Fresnel Boundaries
	ax.axhline(0, color='red', linewidth=1.5, label='LOS (v=0)')
	ax.axhline(f_radius, color='green', linestyle='--', linewidth=1, label=f'Fresnel Rad ±{fmt(f_radius)}m')
	ax.axhline(-f_radius, color='green', linestyle='--', linewidth=1)

	# 3. The Building Mass
	ax.fill(x_coords, y_coords, color='gray', alpha=0.6, edgecolor='black', label='Building Clutter')

	# 4. Display the extracted features (if provided)
	if features:
		info_text = (
			f"v_h: {fmt(features.get('v_h'))}\n"
			f"v_v_top: {fmt(features.get('v_v_top'))}\n"
			f"v_v_base: {fmt(features.get('v_v_base'))}\n"
			f"Deepness: {fmt(features.get('deepness_m'))}m\n"
			f"Area: {fmt(features.get('area_m2'))}m²"
		)
		# Place text box in the upper left
		ax.text(0.02, 0.95, info_text, transform=ax.transAxes, verticalalignment='top',
				bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

	ax.set_title(f"Clutter Feature Extraction: {features.get('full_plus_code', 'missing')}")
	ax.set_xlabel("Local Path Distance (m)")
	ax.set_ylabel("Lateral Offset (m)")
	ax.set_xlim(x_min - 5, x_max + 5)
	ax.set_ylim(-y_limit, y_limit)
	ax.grid(True, linestyle=':', alpha=0.6)
	ax.axis('equal')
	ax.legend(loc='upper right')

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


# ----- 6. CREATE A DUCKDB FOOTPRING AND MERGE WITH HEIGHTS-------------

# Convert CSV to Duckdb (AFTER DOWNLOAD)
def import_csv(out_db, csv_folder, wg84_box, confidence):

	lon_min, lat_min, lon_max, lat_max = wg84_box    
	db = duckdb.connect()
	
	print(f"Processing CSVs in {csv_folder} folder...")
	
	# Query the CSVs directly without a full 'Import'
	# This uses the '*' wildcard to read all files at once
	db.execute(f"""
		CREATE TABLE buildings AS 
		SELECT * FROM read_csv_auto('{csv_folder}/*.csv')
		WHERE latitude BETWEEN {lat_min} AND {lat_max}
		  AND longitude BETWEEN {lon_min} AND {lon_max}
		  AND confidence >= {confidence}
	""")
	
	# Quick check of what we have
	count = db.execute("SELECT count(*) FROM buildings").fetchone()[0]
	print(f"Success! Imported {count} buildings into the Paraná database.")

# Import information of Google Heights into Google FootPrints
# Update a duckDB directly
#---------------------------------------------------
# --- 1. The Worker (Remains Optimized) ---
def process_tile_worker(tiff_path, buildings_list):
	"""
	buildings_list: list of (rowid, longitude, latitude, wkt_geometry)
	"""
	try:
		if not buildings_list:
			return []

		updates = []
		with rasterio.open(tiff_path) as src:
			# Setup projection transformer
			transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
			full_data = src.read(2) # Reading band 2 (height)
			
			for r_id, lon, lat, geom_wkt in buildings_list:
				try:
					# 1. Parse WKT and transform to CRS
					poly_4326 = wkt.loads(geom_wkt)
					
					# Transform polygon coordinates to CRS of the TIFF
					# We do this by mapping the transform function over the exterior coordinates
					lons, lats = poly_4326.exterior.xy
					xs, ys = transformer.transform(lons, lats)
					poly_crs = wkt.loads(f"POLYGON(({','.join([f'{x} {y}' for x,y in zip(xs, ys)])}))")
					
					# 2. Get the Bounding Box in Pixel Space
					# This allows us to work on a tiny 'chip' of the image
					window = from_bounds(*poly_crs.bounds, transform=src.transform)
					
					# Round and pad window to ensure we catch all pixels
					row_start, row_stop = int(window.row_off), int(window.row_off + window.height) + 1
					col_start, col_stop = int(window.col_off), int(window.col_off + window.width) + 1
					
					# 3. Extract the Chip and Generate Mask
					# Crop data to the building's bounding box
					chip = full_data[row_start:row_stop, col_start:col_stop]
					
					if chip.size == 0:
						continue

					# Create a mask specifically for this chip
					# This is the 'index space' trick: transform must be shifted to the window start
					win_transform = src.window_transform(window)
					mask = features.geometry_mask(
						[poly_crs], 
						out_shape=chip.shape, 
						transform=win_transform, 
						invert=True # True = inside the polygon
					)

					# 4. Filter and Calculate Statistics
					# Extract pixels that are inside the polygon AND > 1.5m
					valid_pixels = chip[mask & (chip > 1.5)]
					
					if valid_pixels.size > 0:
						updates.append((
							round(float(np.mean(valid_pixels)), 2),
							round(float(np.percentile(valid_pixels, 90)), 2),
							round(float(np.percentile(valid_pixels, 50)), 2),
							round(float(np.percentile(valid_pixels, 10)), 2),
							r_id
						))
				except Exception as e:
					# Skip problematic geometries but keep the worker alive
					continue

		return updates
	except Exception as e:
		print(f"Critical error in worker for {tiff_path}: {e}")
		return []

# --- 2. The High-Scale Orchestrator ---
def update_height(db_file, bh_tiff_dir, max_workers=4, limit=None, batch_size=50):
	index_path = os.path.join(bh_tiff_dir, 'index-geo.json')
	progress_file = os.path.join(bh_tiff_dir, 'processed_tiles.json')
	
	with open(index_path, 'r') as f:
		index_data = json.load(f)

	# A. Persistent Indexing (Critical for 16M rows)
	db = duckdb.connect(db_file)
	print("Ensuring spatial index exists on 16M buildings...")
	db.execute("CREATE INDEX IF NOT EXISTS idx_coords ON buildings (longitude, latitude)")
	
	processed_data = {}
	if os.path.exists(progress_file):
		with open(progress_file, 'r') as f:
			try: processed_data = json.load(f)
			except: pass

	# B. Identify TIFFs to process
	all_tiff_paths = glob.glob(os.path.join(bh_tiff_dir, "*.tif"))
	tile_meta = {feat['properties']['id_filename']: feat['properties'] for feat in index_data['features']}
	
	to_do = []
	for tp in all_tiff_paths:
		fname = os.path.basename(tp)
		if fname in tile_meta and fname not in processed_data:
			to_do.append((tp, fname, tile_meta[fname]))
	
	if limit:
		to_do = to_do[:limit]
	
	db.close()

	# C. Batch Processing Loop
	print(f"Processing {len(to_do)} tiles in batches of {batch_size}...")
	
	for i in range(0, len(to_do), batch_size):
		current_batch = to_do[i : i + batch_size]
		batch_tasks = []
		batch_info = []

		# Fetch data for this batch only
		db = duckdb.connect(db_file)
		for tiff_path, fname, b in current_batch:
			buildings = db.execute(f"""
				SELECT rowid, longitude, latitude, geometry FROM buildings 
				WHERE longitude BETWEEN {b['geo_west']} AND {b['geo_east']}
				  AND latitude BETWEEN {b['geo_south']} AND {b['geo_north']}
				  AND p90_h IS NULL
			""").fetchall()
			
			if buildings:
				batch_tasks.append((tiff_path, buildings))
				batch_info.append((fname, b))
			else:
				processed_data[fname] = b # Skip empty tiles
		db.close()

		if not batch_tasks:
			continue

		# Run Parallel Pool for this batch
		print(f"Batch {i//batch_size + 1}: Processing {len(batch_tasks)} tiles...")
		with Pool(processes=max_workers, maxtasksperchild=1) as pool:
			results = pool.starmap(process_tile_worker, batch_tasks)

		# Collect and Commit this batch
		batch_updates = [upd for res in results for upd in res]
		if batch_updates:
			print(f"Batch {i//batch_size + 1}: Committing {len(batch_updates)} updates...")
			db = duckdb.connect(db_file)
			db.execute("CREATE TEMP TABLE tmp_upd(h_avg FLOAT, h_p90 FLOAT, h_p50 FLOAT, h_p10 FLOAT, rid BIGINT)")
			db.executemany("INSERT INTO tmp_upd VALUES (?,?,?,?,?)", batch_updates)
			db.execute("""
				UPDATE buildings SET avg_height=h_avg, p90_h=h_p90, p50_h=h_p50, p10_h=h_p10 
				FROM tmp_upd WHERE buildings.rowid = tmp_upd.rid
			""")
			db.close()

		# Update Progress Index for batch
		for fname, b in batch_info:
			processed_data[fname] = b
		
		with open(progress_file, 'w') as f:
			json.dump(processed_data, f, indent=4)

	print("Processing complete.")
#---------------------------------------------------

# -----7. FUNCTIONS FOR CREATING PARQUETS -------------

# Shatter duckdb into parquests
def shatter_duckdb_with_heights(con, output_dir):
	"""
	Shatters the in-memory 'buildings' table into SXX_WXX parquets.
	Includes all columns (2D + Heights) and uses an index for performance.
	"""
	os.makedirs(output_dir, exist_ok=True)

	# 1. Create the optimization index
	# This prevents a full table scan for every single file export.
	print("Creating coordinate index for fast sharding...")
	con.execute("CREATE INDEX IF NOT EXISTS idx_coords ON buildings (latitude, longitude)")

	# 2. Identify tiles based on integer coordinates
	print("Identifying tiles...")
	tiles = con.execute("""
		SELECT DISTINCT 
			CAST(ABS(FLOOR(latitude)) AS INTEGER) as s_id, 
			CAST(ABS(FLOOR(longitude)) AS INTEGER) as w_id
		FROM buildings
	""").fetchall()

	# 3. Export each tile
	for s_id, w_id in tiles:
		filename = f"OpenBuildings_S{s_id}_W{w_id}.parquet"
		path = os.path.join(output_dir, filename)
		
		print(f"Exporting {filename}...")
		
		# Using SELECT * ensures all columns (including heights) are included
		# without needing to explicitly name them.
		con.execute(f"""
			COPY (
				SELECT *
				FROM buildings 
				WHERE CAST(ABS(FLOOR(latitude)) AS INTEGER) = {s_id}
				  AND CAST(ABS(FLOOR(longitude)) AS INTEGER) = {w_id}
			) TO '{path}' (FORMAT PARQUET)
		""")

	print(f"\nShattering complete. Files saved to: {output_dir}")

# Create a geojson index for parquet location
def create_parquet_index(folder_path, output_file="parquet-index-geo.json"):
	"""
	Creates a GeoJSON index for the building parquelets.
	"""
	features = []
	
	if not os.path.exists(folder_path):
		print(f"Error: Folder {folder_path} not found.")
		return

	# Pattern to match: OpenBuildings_S24_W52.parquet
	pattern = re.compile(r"OpenBuildings_S(\d+)_W(\d+)\.parquet")
	
	# Get all .parquet files
	files = [f for f in os.listdir(folder_path) if f.endswith('.parquet')]
	
	con = duckdb.connect()
	print(f"Indexing {len(files)} parquelets...")

	for filename in files:
		match = pattern.search(filename)
		if not match:
			continue
			
		s_id = int(match.group(1))
		w_id = int(match.group(2))
		
		# Calculate bounds (WGS84)
		# S24 means -24 to -23
		# W52 means -52 to -51
		w_ll, s_ll = -w_id, -s_id
		e_ll, n_ll = -w_id + 1, -s_id + 1
		
		# Get building count for this tile (for your Leaflet info box)
		file_path = os.path.join(folder_path, filename)
		count = con.execute(f"SELECT count(*) FROM read_parquet('{file_path}')").fetchone()[0]

		props = {
			"id_filename": filename,
			"geo_west": float(w_ll),
			"geo_south": float(s_ll),
			"geo_east": float(e_ll),
			"geo_north": float(n_ll),
			"building_count": count,
			"coord_type": "geographic"
		}

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
	
	con.close()
	print(f"Building index complete. File saved to {output_file}")



if __name__ == "__main__":
	 
	# 1. Define the Paraná "Filter Box" 
	# (Rough bounds to exclude most of Paraguay/MS/SC)
	wg84_box = -55, -27, -47, -22
	# import_csv("parana_buildings.db", "open_building_csv", wg84_box, 0.7)