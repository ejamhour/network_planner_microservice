import s2sphere
from s2sphere import CellId, LatLng, LatLngRect
from pathlib import PurePosixPath
import s2sphere
import os
import requests
import shutil
from pathlib import Path
import rasterio
from pyproj import Geod
from rasterio.warp import transform_bounds
from rasterio.warp import transform
from rasterio.warp import transform as transform_coords
import json
from shapely.geometry import shape, Point
import numpy as np
import matplotlib.pyplot as plt

'''
Google Openbuilding 2.5D (heights) are GeoTiff files with building heights
It requires a list of URL to download the tiles
The list is obtained using a notebook found in this website:
https://sites.research.google/gr/open-buildings/temporal/
A possibly outdated link is:
https://colab.research.google.com/github/google-research/google-research/blob/master/
building_detection/open_buildings_temporal_download_region_geotiffs.ipynb
'''

# From lat/lon coordinates to Google S2 tokens
def get_tokens_for_bbox(min_lat, max_lat, min_lon, max_lon, level):
	## Find S2 Level 7 cells for given coordinates (~35 km)
	# Define your area of interest (e.g., from one of your info.json entries)
	rect = LatLngRect.from_point_pair(
		LatLng.from_degrees(min_lat, min_lon),
		LatLng.from_degrees(max_lat, max_lon)
	)
	# The Open Buildings 2.5D dataset uses Level 4 S2 cells
	# We find all Level 4 cells that intersect your bounding box
	coverer = s2sphere.RegionCoverer()
	coverer.min_level = level
	coverer.max_level = level
	cells = coverer.get_covering(rect)
	return [c.to_token() for c in cells]

# From S2 tokens to coordiantes (from the URL download list)
def check_open_buildings_urls(file_path):
	'''
	gs://open-buildings-temporal-data/v1/geotiffs/
	94f4c_2023_06_30/tile_ldKeWEfH9YQ.tif
	'''
	# Use a dictionary to store unique tokens as keys
	unique_results = {}
	
	with open(file_path, 'r') as f:
		for line in f:
			line = line.strip()
			if not line or "geotiffs" not in line:
				continue
			
			path = PurePosixPath(line)
			filename = path.name
			# Extract the folder name, e.g., '94f4c_2023_06_30'
			innermost_dir = path.parent.name
			token = innermost_dir.split('_')[0]
			
			# If we've already processed this token, skip it
			if filename in unique_results:
				continue
			
			try:
				cell_id = s2sphere.CellId.from_token(token)
				if cell_id.is_valid():
					cell = s2sphere.Cell(cell_id)
					rect = cell.get_rect_bound()
					
					unique_results[filename] = {
						"filename": filename,
						"token": token,
						"level": cell_id.level(),
						"min_lat": rect.lat_lo().degrees,
						"max_lat": rect.lat_hi().degrees,
						"min_lon": rect.lng_lo().degrees,
						"max_lon": rect.lng_hi().degrees
					}
			except Exception as e:
				print(f"Error processing {token}: {e}")
				continue
				
	# Return only the values of the dictionary as a list
	return list(unique_results.values())

# Filter the URLs from the URL download list
def filter_urls_by_bbox(input_file, output_file, target_bbox):
	"""
	input_file: path to the large list of gs:// URLs
	output_file: path to save the filtered URLs
	target_bbox: dictionary with min_lat, max_lat, min_lon, max_lon
	"""
	# Create an S2 LatLngRect for the target area
	target_rect = s2sphere.LatLngRect.from_point_pair(
		s2sphere.LatLng.from_degrees(target_bbox['min_lat'], target_bbox['min_lon']),
		s2sphere.LatLng.from_degrees(target_bbox['max_lat'], target_bbox['max_lon'])
	)

	filtered_count = 0
	with open(input_file, 'r') as f_in, open(output_file, 'w') as f_out:
		for line in f_in:
			line = line.strip()
			if not line or "geotiffs" not in line:
				continue
			
			# Extract token from the directory structure
			path = PurePosixPath(line)
			innermost_dir = path.parent.name
			token = innermost_dir.split('_')[0]
			
			try:
				cell_id = s2sphere.CellId.from_token(token)
				if cell_id.is_valid():
					# Get the bounding box of the S2 cell
					cell_rect = s2sphere.Cell(cell_id).get_rect_bound()
					
					# Check if the S2 cell intersects our target area
					if target_rect.intersects(cell_rect):
						f_out.write(line + '\n')
						filtered_count += 1
			except Exception:
				continue

	print(f"Filtering complete. Saved {filtered_count} URLs to {output_file}.")

# Download the TIFFs from the list
def download_open_building_tiffs(url_file, output_folder="downloaded_tiles", other_folders=None):
	# Create the folder if it doesn't exist
	if not os.path.exists(output_folder):
		os.makedirs(output_folder)
		print(f"Created folder: {output_folder}")

	# The base URL for public HTTP access
	base_http_url = "https://storage.googleapis.com/open-buildings-temporal-data/v1/geotiffs/"
	skipped = downloaded = 0
	with open(url_file, 'r') as f:
		for line in f:
			line = line.strip()
			if not line or "geotiffs/" not in line:
				continue
			
			# a) Convert gs:// format to the relative path needed for the URL
			# We want the part after 'geotiffs/'
			# Example: '9493c_2023_06_30/tile_IHSbFIkP9ZI.tif'
			relative_path = line.split("geotiffs/")[-1]
			
			# The filename for local storage
			filename = os.path.basename(relative_path)
			local_path = os.path.join(output_folder, filename)

			# b) Check if the tile already exists locally			
			if any(os.path.exists(os.path.join(folder, filename)) for folder in (other_folders or [])):
				skipped += 1
				continue			
			else:
				downloaded +=1

			# Construct the full download URL
			full_url = base_http_url + relative_path
			
			try:
				print(f"Downloading {filename}...")
				r = requests.get(full_url, stream=True) # stream=True is better for large TIFs
				
				if r.status_code == 200:
					with open(local_path, 'wb') as f_out:
						for chunk in r.iter_content(chunk_size=8192):
							f_out.write(chunk)
				else:
					print(f"Failed: {filename} (Error {r.status_code})")
			except Exception as e:
				print(f"Error downloading {filename}: {e}")

		print(f'skipped: {skipped} downloaded: {downloaded}')

# Move extra tiffs found in a folder using an URL list
def cleanup_tiles(tiles_folder, filtered_urls_file, trash_folder):
	# 1. Create the trash folder if it doesn't exist
	if not os.path.exists(trash_folder):
		os.makedirs(trash_folder)

	# 2. Extract the expected filenames from your filtered URL list
	# We assume the filename is the last part of the URL (e.g., tile_xxx.tif)
	with open(filtered_urls_file, 'r') as f:
		valid_filenames = {line.strip().split('/')[-1] for line in f if line.strip()}

	print(f"Loaded {len(valid_filenames)} valid file names from the URL list.")

	# 3. Scan the folder and move files
	moved_count = 0
	keep_count = 0
	
	for filename in os.listdir(tiles_folder):
		# Only process .tif files
		if not filename.endswith('.tif'):
			continue
			
		if filename in valid_filenames:
			keep_count += 1
		else:
			# Move the file to the trash folder
			src = os.path.join(tiles_folder, filename)
			dst = os.path.join(trash_folder, filename)
			shutil.move(src, dst)
			moved_count += 1

	print(f"Cleanup finished.")
	print(f"Files kept: {keep_count}")
	print(f"Files moved to '{trash_folder}': {moved_count}")

# Report statistics of an openbuilding Tiff
def read_info_open_building_tiff(tile_path):

	with rasterio.open(tile_path) as src:

		# Get the resolution (pixel size) from metadata
		res_x, res_y = src.res

		# Calculate total area in square kilometers
		total_area_km2 = (src.width * res_x * src.height * res_y) / 1_000_000

		print(f"Tile Area: {total_area_km2:.2f} km²")
		print(f"Resolution: {res_x:.2f} m x {res_y:.2f} m ")

		# We use a step/stride of 5 to keep CPU usage low while testing
		step = 10

		# fractional is addictive = sum to count the number of buildings
		'''
		fractional = src.read(1, out_shape=(int(src.height/step), int(src.width/step)))
		heights = src.read(2, out_shape=(int(src.height/step), int(src.width/step)))
		presence = src.read(3, out_shape=(int(src.height/step), int(src.width/step)))
		'''
		data = src.read(out_shape=(3, int(src.height/step), int(src.width/step)))
		fractional, heights, presence = data

		nodata = src.nodata

		# Create a mask: 
		# Must not be NoData AND Presence should be above a threshold (e.g., 0.5)
		mask = (heights != nodata) & (presence > 0.5)    
		building_heights_05 = heights[mask]
		mask = (heights != nodata) & (presence > 0.9)    
		building_heights_09 = heights[mask]


		if building_heights.size > 0:
			print(f"Building Analysis for {src.name} with step = {step}:")
			print(f"  Detected Buildings: {heights.size} sampled pixels")
			print(f"  Average Building Height (unfiltered): {np.mean(heights):.2f}m")
			print(f"  Average Building Height (0.5 Presence): {np.mean(building_heights_05):.2f}m")
			print(f"  Average Building Height (0.9 Presence): {np.mean(building_heights_09):.2f}m")
			print(f"  Tallest Building in Sample: {np.max(heights):.2f}m")        
			print(f"  Total Fractional: {np.sum(fractional):.2f}")
			print(f"  Estimated building count: {np.sum(fractional * step * step):.2f}")
			print(f"  Average Presence: {np.mean(presence):.2f}")

			bounds = src.bounds
			file_crs = src.crs

			# If the file is NOT already in Lat/Lon, convert the bounds to EPSG:4326
			if file_crs and file_crs.to_string() != 'EPSG:4326':
				west, south, east, north = transform_bounds(file_crs, 'EPSG:4326', *bounds)
			else:
				west, south, east, north = bounds

			var = f"https://www.google.com/maps/dir/{north},{west}/{south},{east}/"            
			print(var)


		else:
			print("No buildings detected with high confidence in this sample.")

# From lat, lon to row, col
def get_valid_indices(src, lat, lon):
    """
    Geodetically honest coordinate-to-pixel mapping.
    Ensures lat/lon is transformed to the TIFF's specific CRS.
    """
    # 1. Transform Lat/Lon (EPSG:4326) to the TIFF's CRS
    # We pass [lon], [lat] because transform_coords expects sequences
    xs, ys = transform_coords('EPSG:4326', src.crs, [lon], [lat])
    target_x, target_y = xs[0], ys[0]

    # 2. Convert CRS coordinates to pixel indices (row, col)
    row, col = src.index(target_x, target_y)
    
    # 3. Check for Out-of-Bounds
    is_clamped = (
        row < 0 or row >= src.height or 
        col < 0 or col >= src.width
    )
    
    # 4. Clamp to valid image space
    valid_row = max(0, min(int(row), src.height - 1))
    valid_col = max(0, min(int(col), src.width - 1))
    
    return valid_row, valid_col, is_clamped

# Plot a line profile of Band 2 of openbuilding 
def plot_height_profile(tif_path, start, end, step=10):
	# start_coords = (lat, lon)
	with rasterio.open(tif_path) as src:

		# 1. IMPORTANT: src.index needs (LON, LAT)
		# We swap the indices from your (lat, lon) tuple
		# Now use this in your loop
		s_row, s_col, _ = get_valid_indices(src, start[0], start[1])
		e_row, e_col, _ = get_valid_indices(src, end[0], end[1])
		
		# 2. Scale for your decimated read
		s_row, s_col = s_row // step, s_col // step
		e_row, e_col = e_row // step, e_col // step
		
		# 3. Read decimated height band (Band 2)
		out_shape = (src.height // step, src.width // step)
		heights = src.read(2, out_shape=out_shape)
		
		# 4. Generate points along the pixel-line
		num_points = 1000
		rows = np.linspace(s_row, e_row, num_points).astype(int)
		cols = np.linspace(s_col, e_col, num_points).astype(int)
		
		# 5. Extract values and handle potential out-of-bounds
		rows = np.clip(rows, 0, heights.shape[0] - 1)
		cols = np.clip(cols, 0, heights.shape[1] - 1)
		profile = heights[rows, cols]

		#-------------------------------

		#1. Find the index of the highest obstacle
		peak_idx = np.argmax(profile)
		peak_height = profile[peak_idx]
		
		# 2. Get the corresponding Row/Col in the sampled image
		# (Multiply by 'step' to get back to the original full-res pixel)
		orig_peak_row = rows[peak_idx] * step
		orig_peak_col = cols[peak_idx] * step
		
		# 3. Get the coordinates in the file's CRS (usually meters)
		# src.xy returns the center of the pixel
		peak_x, peak_y = src.xy(orig_peak_row, orig_peak_col)
		
		# 4. Transform back to Lat/Lon

		peak_lons, peak_lats = transform(src.crs, 'EPSG:4326', [peak_x], [peak_y])
		
		print(f"Highest Obstacle: {peak_height:.2f}m")
		print(f"Coordinates: {peak_lats[0]}, {peak_lons[0]}")


		#-------------------------------
		
		# 6. Plotting
		plt.figure(figsize=(12, 5))
		plt.fill_between(range(num_points), profile, color='teal', alpha=0.4)
		plt.plot(profile, color='teal', linewidth=1)
		
		plt.title(f"Skyline Profile: {tif_path.split('/')[-1]}")
		plt.ylabel("Height (meters)")
		plt.xlabel("Sampled Distance")
		plt.grid(axis='y', linestyle='--', alpha=0.7)
		plt.show()