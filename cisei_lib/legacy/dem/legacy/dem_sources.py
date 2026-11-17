import os, json, shutil
import urllib.request
import requests
import pystac
import planetary_computer
import rioxarray
import math
import numpy as np
from shapely.geometry import box
from pyproj import Transformer
import rasterio
from rasterio.windows import Window
from rasterio.merge import merge
from rasterio.transform import Affine
from rasterio.warp import transform_bounds



PR_WGS84_BBOX = (-54.6188888889, -26.7166666667, -48.0936111111, -22.5161111111)
# (west, south, east, north) 


# ---------DATASET CREATION --------------------------------------

# Determine collision in tiles names return _N 
def get_next_suffix(index_data, base_name):
	"""
	Scans index_data to determine the next integer suffix N.
	"""
	if not index_data:
		return None
	
	# Ensure we are looking at the features list
	features = index_data.get('features', [])
	existing_suffixes = []
	
	for feature in features:
		fname = feature['properties'].get('id_filename', "")
		if fname.startswith(base_name):
			# Remove extension and split by underscores
			name_part = fname.replace(".tif", "")
			parts = name_part.split("_")
			
			# Format expected: lat_lon (len 2) or lat_lon_N (len 3)
			if len(parts) == 3:
				try:
					existing_suffixes.append(int(parts[-1]))
				except ValueError:
					continue
			elif len(parts) == 2:
				existing_suffixes.append(0) # The base file exists

	if not existing_suffixes:
		return None
	
	return max(existing_suffixes) + 1

# Check if a new tile already exist in index-geo.json
def is_already_covered(index_data, base_name, new_bounds, margin=0.0001):
	"""
	Checks if new_bounds (west, south, east, north) is contained within 
	an existing indexed tile with the same base_name.
	"""
	if not index_data:
		return False
	
	features = index_data.get('features', [])
	new_poly = box(*new_bounds)
	
	for feature in features:
		props = feature['properties']
		if props.get('id_filename', "").startswith(base_name):
			existing_poly = box(
				props['geo_west'], props['geo_south'], 
				props['geo_east'], props['geo_north']
			)
			# Use a small buffer to handle float precision issues
			if existing_poly.buffer(margin).contains(new_poly):
				return True
	return False

# Slip large files into degree tiles
def split_geotiff_to_degree_tiles(input_path, output_dir="tiles", index_path_or_dict=None, min_size=50):
	"""
	Shatters a GeoTIFF into 1-degree tiles using index-geo.json as the state registry.
	"""
	if not os.path.exists(output_dir):
		os.makedirs(output_dir)

	# Handle index loading (Path string or Dictionary)
	index_data = None
	if isinstance(index_path_or_dict, str) and os.path.exists(index_path_or_dict):
		with open(index_path_or_dict, 'r') as f:
			index_data = json.load(f)
	elif isinstance(index_path_or_dict, dict):
		index_data = index_path_or_dict

	with rasterio.open(input_path) as src:
		transformer_to_deg = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
		transformer_to_native = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
		
		b = src.bounds
		lon_min, lat_min = transformer_to_deg.transform(b.left, b.bottom)
		lon_max, lat_max = transformer_to_deg.transform(b.right, b.top)

		lons = np.arange(math.floor(lon_min), math.ceil(lon_max), 1)
		lats = np.arange(math.floor(lat_min), math.ceil(lat_max), 1)

		for t_lon in lons:
			for t_lat in lats:
				lat_label, lon_label = f"s{abs(int(t_lat)):02d}", f"w{abs(int(t_lon)):03d}"
				base_name = f"{lat_label}_{lon_label}"
				
				# Check coverage in index before processing
				new_degree_bounds = (t_lon, t_lat, t_lon + 1, t_lat + 1)
				if index_data and is_already_covered(index_data, base_name, new_degree_bounds):
					continue

				# Native projection corners for Window
				w, s = transformer_to_native.transform(t_lon, t_lat)
				e, n = transformer_to_native.transform(t_lon + 1, t_lat + 1)
				
				# Pixels indices
				r1, c1 = src.index(w, n)
				r2, c2 = src.index(e, s)
				
				r_start, r_end = max(0, min(r1, r2)), min(src.height, max(r1, r2))
				c_start, c_end = max(0, min(c1, c2)), min(src.width, max(c1, c2))
				
				win_w, win_h = c_end - c_start, r_end - r_start
				if win_w < min_size or win_h < min_size:
					continue

				window = Window(c_start, r_start, win_w, win_h)
				out_image = src.read(window=window)
				
				# Data check
				if not np.any(out_image != (src.nodata if src.nodata is not None else 0)):
					continue

				# Filename logic via Index registry
				suffix = get_next_suffix(index_data, base_name)
				fname = f"{base_name}_{suffix}.tif" if suffix else f"{base_name}.tif"
				out_path = os.path.join(output_dir, fname)

				# Metadata and Write
				out_meta = src.meta.copy()
				out_meta.update({
					"height": win_h,
					"width": win_w,
					"transform": src.window_transform(window),
					"compress": "lzw"
				})

				with rasterio.open(out_path, "w", **out_meta) as dest:
					dest.write(out_image)

def _utm_zone_from_epsg(epsg: int):
    # returns (zone_number, hemisphere) or (None, None)
    if epsg is None:
        return None, None
    if 32601 <= epsg <= 32660:
        return epsg - 32600, "N"
    if 32701 <= epsg <= 32760:
        return epsg - 32700, "S"
    return None, None

def _utm_lat_band(lat_center: float) -> str:
    # C..X (I,O skipped), X repeated for 80–84
    bands = "CDEFGHJKLMNPQRSTUVWXX"
    if -80 <= lat_center <= 84:
        return bands[int((lat_center + 80) / 8)]
    return "Z"

def get_utm_designator(src):
    epsg = src.crs.to_epsg() if src.crs else None
    zone, hemi = _utm_zone_from_epsg(epsg)

    # latitude band still comes from latitude (a naming convention)
    lon_center, lat_center = Transformer.from_crs(
        src.crs, "EPSG:4326", always_xy=True
    ).transform(
        (src.bounds.left + src.bounds.right) / 2,
        (src.bounds.bottom + src.bounds.top) / 2
    )
    band = _utm_lat_band(lat_center)

    if zone is None:
        # Not a standard UTM EPSG. Use EPSG as tag to avoid lying.
        return f"EPSG{epsg if epsg is not None else 'UNKNOWN'}_{band}"
    return f"{zone}{band}"

def _assert_north_up(transform: Affine, tol=1e-12):
    # north-up means no rotation/shear (b == d == 0)
    if abs(transform.b) > tol or abs(transform.d) > tol:
        raise ValueError(
            "This tiler assumes a north-up raster (no rotation/shear). "
            "Your dataset has a rotated/skewed affine transform."
        )

def _copy_rasterio_metadata(src, dest):
    # dataset tags
    dest.update_tags(**src.tags())
    # per-band tags, descriptions, and color interpretation
    for i in range(1, src.count + 1):
        dest.update_tags(i, **src.tags(i))
        desc = src.descriptions[i - 1]
        if desc:
            dest.set_band_description(i, desc)
    try:
        dest.colorinterp = src.colorinterp
    except Exception:
        pass
    # scales/offsets (if present)
    try:
        dest.scales = src.scales
    except Exception:
        pass
    try:
        dest.offsets = src.offsets
    except Exception:
        pass
    # colormap (paletted rasters)
    try:
        cm = src.colormap(1)
        if cm:
            dest.write_colormap(1, cm)
    except Exception:
        pass

def split_geotiff_to_utm_tiles(input_path, output_dir="tiles", tile_size_m=100_000):
    os.makedirs(output_dir, exist_ok=True)

    with rasterio.open(input_path) as src:
        if not src.crs or not src.crs.is_projected:
            raise ValueError(
                "This function assumes a projected CRS with meter units. "
                "For geographic CRS (lon/lat), reproject first or define tile size in degrees."
            )

        _assert_north_up(src.transform)

        # Pixel size in CRS units (assumed meters for UTM-like grids)
        res_x = abs(src.transform.a)
        res_y = abs(src.transform.e)

        # Enforce that tile_size_m is an integer multiple of pixel size, so boundaries can align.
        # If not, you can still tile, but you cannot guarantee non-overlap + exact 100km edges simultaneously.
        tile_px_w = int(round(tile_size_m / res_x))
        tile_px_h = int(round(tile_size_m / res_y))
        if abs(tile_px_w * res_x - tile_size_m) > 1e-6 or abs(tile_px_h * res_y - tile_size_m) > 1e-6:
            raise ValueError(
                f"tile_size_m={tile_size_m} is not compatible with pixel sizes "
                f"({res_x}, {res_y}). Choose a tile size that is a multiple of the pixel resolution."
            )

        utm_tag = get_utm_designator(src)
        stem = os.path.splitext(os.path.basename(input_path))[0]

        # Snap the tiling grid to the CRS origin, not to the raster edge.
        x_min = math.floor(src.bounds.left / tile_size_m) * tile_size_m
        x_max = math.ceil(src.bounds.right / tile_size_m) * tile_size_m
        y_min = math.floor(src.bounds.bottom / tile_size_m) * tile_size_m
        y_max = math.ceil(src.bounds.top / tile_size_m) * tile_size_m

        # Anchor the pixel grid once, then step by fixed pixel counts.
        inv = ~src.transform

        # Anchor at top-left corner of the snapped grid (x_min, y_max)
        f_col0, f_row0 = inv * (x_min, y_max)
        col0 = int(round(f_col0))
        row0 = int(round(f_row0))

        nx = int(round((x_max - x_min) / tile_size_m))
        ny = int(round((y_max - y_min) / tile_size_m))

        nodata = src.nodata

        for ix in range(nx):
            for iy in range(ny):
                # Pixel-window for the *full* 100km tile in the snapped grid
                col_start = col0 + ix * tile_px_w
                row_start = row0 + iy * tile_px_h
                full_tile = Window(col_start, row_start, tile_px_w, tile_px_h)

                # Intersect with the source raster pixel extent to avoid reading outside
                src_extent = Window(0, 0, src.width, src.height)
                try:
                    window = full_tile.intersection(src_extent)
                except Exception:
                    continue

                if window.width <= 0 or window.height <= 0:
                    continue

                # Read data
                out = src.read(window=window, boundless=False)

                # Skip if all nodata (careful if nodata is None)
                if nodata is not None and not np.any(out != nodata):
                    continue

                # Name is defined by snapped grid origin (t_x, t_y) so it cannot overlap for the same grid.
                t_x = x_min + ix * tile_size_m
                t_y = y_max - (iy + 1) * tile_size_m  # bottom edge of the tile
                e_km = int(round(t_x / 1000))
                n_km = int(round(t_y / 1000))

                # Include input stem + tile size so two different sources/sizes never collide.
                fname = f"{stem}_{utm_tag}_{int(tile_size_m/1000)}km_E{e_km}_N{n_km}.tif"
                out_path = os.path.join(output_dir, fname)

                out_meta = src.meta.copy()
                out_meta.update({
                    "height": int(window.height),
                    "width": int(window.width),
                    "transform": src.window_transform(window),
                    "compress": "lzw",
                    "driver": "GTiff"
                })

                with rasterio.open(out_path, "w", **out_meta) as dest:
                    dest.write(out)
                    _copy_rasterio_metadata(src, dest)

    print(f"Grid-aligned tiling complete: {utm_tag}.")


# Merge tiffs
def merge_geotiffs(merge_list, output_path):

	# List of files to merge
	files_to_merge = merge_list

	# Open the datasets
	src_files_to_mosaic = []
	for fp in files_to_merge:
		src = rasterio.open(fp)
		src_files_to_mosaic.append(src)

	# Merge the datasets
	# 'mosaic' is the numpy array, 'out_trans' is the new Affine transform
	mosaic, out_trans = merge(src_files_to_mosaic)

	# Copy the metadata from one of the source files
	out_meta = src_files_to_mosaic[0].meta.copy()

	# Update the metadata with new dimensions and transform
	out_meta.update({
		"driver": "GTiff",
		"height": mosaic.shape[1],
		"width": mosaic.shape[2],
		"transform": out_trans,
		"crs": src_files_to_mosaic[0].crs
	})

	# Write the merged file to disk

	with rasterio.open(output_path, "w", **out_meta) as dest:
		dest.write(mosaic)

	# Close the sources
	for src in src_files_to_mosaic:
		src.close()

	print(f"Merge complete! Created: {output_path}")

# Move tiles outside bounds 
def move_tiles_outside_bounds(geojson_path, lat_n, lon_w, lat_s, lon_e, source_folder, target_folder):
	'''
	Move tiles 
	'''

	if not os.path.exists(target_folder):
		os.makedirs(target_folder)

	# 3. Process the Index
	with open(geojson_path, 'r') as f:
		data = json.load(f)

	moved_count = 0

	for feature in data['features']:
		props = feature['properties']
		filename = props['id_filename']
		
		# Extract tile bounds from properties
		t_north = props['geo_north']
		t_south = props['geo_south']
		t_west = props['geo_west']
		t_east = props['geo_east']

		# Logic: A tile is OUTSIDE if it is entirely North, South, East, or West of PR
		is_outside = (
			t_south > lat_n or  # Entirely North of PR
			t_north < lat_s or  # Entirely South of PR
			t_east < lon_w or    # Entirely West of PR
			t_west > lon_e       # Entirely East of PR
		)

		if is_outside:
			source_path = os.path.join(source_folder, filename)
			target_path = os.path.join(target_folder, filename)
			
			if os.path.exists(source_path):
				#print(f"Moving {filename} (Location: {t_north}N, {t_west}W)")
				shutil.move(source_path, target_path)
				moved_count += 1
			else:
				print(f"File {filename} not found in source folder.")

	print(f"\nTask complete. Moved {moved_count} tiles to {target_folder}.")

def move_tiles_outside_roi_native(
    geojson_path,
    source_folder,
    target_folder,
    roi_wgs84_bbox,  # (west, south, east, north) in EPSG:4326
    densify_pts=21
):
    """
    Moves tiles whose NATIVE bounds are entirely outside an ROI.
    ROI is provided in WGS84 and transformed to each tile's native CRS before comparison.
    """
    os.makedirs(target_folder, exist_ok=True)

    w, s, e, n = roi_wgs84_bbox

    with open(geojson_path, "r") as f:
        data = json.load(f)

    moved_count = 0
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        filename = props.get("id_filename")

        # Tile native bounds
        t_left  = props["native_left"]
        t_right = props["native_right"]
        t_low   = props["native_bottom"]
        t_high  = props["native_top"]

        crs_native = props.get("crs_native")
        if not crs_native:
            raise ValueError(f"Missing crs_native for {filename}")

        # Transform ROI bbox from WGS84 into this tile CRS (returns xmin, ymin, xmax, ymax)
        roi_xmin, roi_ymin, roi_xmax, roi_ymax = transform_bounds(
            "EPSG:4326", crs_native, w, s, e, n, densify_pts=densify_pts
        )

        # Entirely outside test (same logic as yours, but in the tile CRS)
        is_outside = (
            t_low   > roi_ymax or
            t_high  < roi_ymin or
            t_left  > roi_xmax or
            t_right < roi_xmin
        )

        if is_outside:
            source_path = os.path.join(source_folder, filename)
            target_path = os.path.join(target_folder, filename)
            if os.path.exists(source_path):
                shutil.move(source_path, target_path)
                moved_count += 1

    print(f"Cleanup complete. Moved {moved_count} tiles to {target_folder}.")


'''
WARNING: This modules is not used in production
Instead it has the code to automate the download of tiles

This SCRIPT dowloads all tiles in info.json of a DTS model
This is copy of the tiles from Bristol University (FABDEM) with south brazil
FABDEM original site has much larger tiles in zip format and it is very slow
https://data.bris.ac.uk/data/dataset/25wfy0f9ukoge2gs7a5mqpq2j7
-----------------------------------------------------------
FABDEM (Forest And Buildings removed DEM).
Project Name    FABDEM
Type    DTM (Digital Terrain Model)
Base Data   Copernicus GLO-30
Resolution  30 meters
Key Advantage   Removes artifacts (forests/buildings) that distort elevation.
University of Bristol
https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn
Mirror with south Brazil.
https://github.com/cordmaur/fabdem-brazil-south/
-- 
Replaced by ANADEM (Agência Nacional de Águas DEM)
Project Name    ANADEM
Institution UFRGS / IPH & ANA (Brazil)
Type    DTM (Vegetation removed)
Base Data   Copernicus GLO-30
Resolution  30 meters
Validation  Reduces the vertical bias (error) in forested areas from ~9.6m down to ~1.5m.
https://metadados.snirh.gov.br/geonetwork/srv/api/records/93664c15-1ff8-4e87-bbed-2bb69d321309
'''

#-----------------------------------------------------

'''
Dataset: Copernicus GLO-30 Public.
Provider: European Space Agency (ESA) / Copernicus Programme, hosted on AWS by Sinergise.
Resolution: 30 meters (approx. 1 arc-second).
Type: DSM (Digital Surface Model)
'''
def download_parana_tiles(output_folder="parana_copernicus"):
	# Ensure folder exists
	if not os.path.exists(output_folder):
		os.makedirs(output_folder)

	# Geographic boundaries for the State of Paraná
	lat_range = range(-27, -21) # South to North
	lon_range = range(-55, -47) # West to East

	base_url = "https://copernicus-dem-30m.s3.amazonaws.com"

	print(f"Starting download for Paraná tiles...")

	for lat in lat_range:
		for lon in lon_range:
			# Format coordinates for URL (e.g., -26 -> S26, -50 -> W050)
			ns = "S" if lat < 0 else "N"
			ew = "W" if lon < 0 else "E"
			
			lat_str = f"{ns}{abs(lat):02d}"
			lon_str = f"{ew}{abs(lon):03d}"
			
			# Construct the specific AWS tile name and URL
			tile_name = f"Copernicus_DSM_COG_10_{lat_str}_00_{lon_str}_00_DEM"
			url = f"{base_url}/{tile_name}/{tile_name}.tif"
			file_path = os.path.join(output_folder, f"{tile_name}.tif")

			try:
				if os.path.exists(file_path):
					print(f"Skipping {lat_str} {lon_str} (Already exists)")
				else:
					print(f"Downloading {lat_str} {lon_str}...")
					urllib.request.urlretrieve(url, file_path)
			except Exception:
				# Some tiles on the coast might be empty/ocean, AWS won't have them
				print(f"Tile {lat_str} {lon_str} not found (might be ocean).")

	print("\nDownload process finished.")

'''
Aster is outdated
'''
def download_aster_tile(lat, lon, username, password):
	# Format: ASTGTMV003_S26W050
	ns = "S" if lat < 0 else "N"
	ew = "W" if lon < 0 else "E"
	tile_name = f"ASTGTMV003_{ns}{abs(int(lat)):02d}{ew}{abs(int(lon)):03d}_dem.tif"
	
	# NASA Earthdata URL
	url = f"https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/ASTGTM.003/{tile_name}"
	
	print(f"Attempting to download {tile_name}...")
	
	with requests.Session() as session:
		session.auth = (username, password)
		r1 = session.get(url, stream=True)
		if r1.status_code == 200:
			with open(tile_name, 'wb') as f:
				f.write(r1.content)
			print("Download successful.")
		else:
			print(f"Failed. Status code: {r1.status_code}")

# Usage:
# download_aster_tile(-25, -50, "your_username", "your_password")


# https://planetarycomputer.microsoft.com/dataset/io-lulc
# !pip install pystac-client planetary-computer requests

def download_esri_microsoft():

	item_url = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/io-lulc-annual-v02/items/21J-2023"

	# Load the individual item metadata and sign the assets
	item = pystac.Item.from_file(item_url)

	signed_item = planetary_computer.sign(item)

	# Open one of the data assets 
	asset_href = signed_item.assets["data"].href
	ds = rioxarray.open_rasterio(asset_href)
	ds
	print(ds.rio.crs) # CRS
	print(ds) # metadata
	'''    
	bbox = [-50.0, -26.0, -49.0, -25.0] 
	# Slice the data while it's still in the cloud
	clipped_ds = ds.rio.clip_box(*bbox)
	# Now saving is safe because it's only a few megabytes
	clipped_ds.rio.to_raster("copel_region_lulc.tif", compress="LZW")
	'''
	print(signed_item.assets["data"].href) # this is the URL
	
	'''
	Go here and use the explorer
	https://planetarycomputer.microsoft.com/catalog?filter=esri
	Adjust tile name and download from URL generated by print

	'''