import duckdb
import rasterio
from rasterio.warp import transform
import numpy as np
import json
import os
import glob
from multiprocessing import Process
import math
import matplotlib.pyplot as plt
from shapely import wkt
from matplotlib.patches import Polygon as MplPolygon


'''
This module is used to process buildings footprints in 2D from google
Download from:https://sites.research.google/gr/open-buildings/#open-buildings-download
Data is downloaded in csv.gz format
'''


# --- SET YOUR DIRECTORY HERE ---
DATA_DIR = "openbuilding-curitiba" 

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


def check_db_bounds(db_file):
    # Connect to your database
    con = duckdb.connect(db_file)
    
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

def check_db_schema(db_file):

    # Connect to your existing database
    db = duckdb.connect(db_file)
    
    # This command describes the structure of the 'buildings' table
    print("--- Table Schema for 'buildings' ---")
    schema = db.execute("PRAGMA table_info('buildings')").fetchall()
    
    for column in schema:
        # column[1] is the name, column[2] is the type
        print(f"Column Name: {column[1]:<20} | Type: {column[2]}")
    
    # Also show the first 2 rows of data to verify the values
    print("\n--- First 2 rows of data ---")
    data = db.execute("SELECT * FROM buildings LIMIT 2").df()
    return(data)

# Sample height from GeoTiff and saves into DuckDB
def process_tile(db_file, tiff_path, geo_bounds):
    try:
        db = duckdb.connect(db_file)
        fname = os.path.basename(tiff_path)
        
        with rasterio.open(tiff_path) as src:
            # Check for p90_height IS NULL (since we reset it)
            buildings = db.execute(f"""
                SELECT rowid, longitude, latitude FROM buildings 
                WHERE longitude BETWEEN {geo_bounds['w']} AND {geo_bounds['e']}
                  AND latitude BETWEEN {geo_bounds['s']} AND {geo_bounds['n']}
                  AND p90_height IS NULL
            """).fetchall()

            if not buildings:
                db.close()
                return

            print(f"Processing {fname}: {len(buildings)} buildings found.")
            
            full_data = src.read(2) 
            inv_transform = ~src.transform
            
            updates = []
            for r_id, lon, lat in buildings:
                # Transform 4326 to TIFF CRS
                x_crs, y_crs = transform('EPSG:4326', src.crs, [lon], [lat])
                px, py = inv_transform * (x_crs[0], y_crs[0])
                px, py = int(px), int(py)
                
                # Surgical 3x3 window (1.5m x 1.5m)
                y_min, y_max = max(0, py-1), min(src.height, py+2)
                x_min, x_max = max(0, px-1), min(src.width, px+2)
                
                window = full_data[y_min:y_max, x_min:x_max]
                valid = window[window > 1.5] 
                
                if valid.size > 0:
                    updates.append((
                        round(float(np.mean(valid)), 2),
                        round(float(np.percentile(valid, 90)), 2),
                        round(float(np.percentile(valid, 50)), 2),
                        round(float(np.percentile(valid, 10)), 2),
                        r_id
                    ))

            if updates:
                db.execute("CREATE TEMP TABLE tmp_upd(h_avg FLOAT, h_p90 FLOAT, h_p50 FLOAT, h_p10 FLOAT, rid BIGINT)")
                db.executemany("INSERT INTO tmp_upd VALUES (?, ?, ?, ?, ?)", updates)
                db.execute("""
                    UPDATE buildings SET 
                        avg_height = tmp_upd.h_avg, p90_height = tmp_upd.h_p90,
                        p50_height = tmp_upd.h_p50, p10_height = tmp_upd.h_p10
                    FROM tmp_upd WHERE buildings.rowid = tmp_upd.rid
                """)
                print(f"Saved {len(updates)} updates for {fname}")
        db.close()
    except Exception as e:
        print(f"Error processing {tiff_path}: {e}")

# Scan GeoTiff folder with heights and save into DuckDB (Worker to avoid GDAL memory overload)
def update_height(db_file, bh_tiff_dir):
    # Join the directory path with the filenames
    index_path = os.path.join(bh_tiff_dir, 'index-geo.json')
    
    if not os.path.exists(index_path):
        print(f"Error: Could not find {index_path}. Check your DATA_DIR variable.")
    else:
        with open(index_path, 'r') as f:
            index_data = json.load(f)

        tiles = {f['properties']['id_filename']: {
            'w': f['properties']['geo_west'], 'e': f['properties']['geo_east'],
            's': f['properties']['geo_south'], 'n': f['properties']['geo_north']
        } for f in index_data['features']}

        # Find all .tif files in that directory
        tiff_search_path = os.path.join(bh_tiff_dir, "*.tif")
        tiff_files = glob.glob(tiff_search_path)
        
        print(f"Found {len(tiff_files)} TIFF files in {bh_tiff_dir}")

        for tiff_path in tiff_files:
            fname = os.path.basename(tiff_path)
            if fname in tiles:                
                p = Process(target=process_tile, args=(db_file, tiff_path, tiles[fname]))
                p.start()
                p.join()
            else:
                print(f"Warning: {fname} found but not in index-geo.json")

# Return the top buildings in the DuckDB file
def check_top_buildings(db_file, height, rows):

    db = duckdb.connect(db_file)
    
    query = f"""
        SELECT 
            rowid, 
            latitude, 
            longitude, 
            area_in_meters, 
            full_plus_code, 
            avg_height,
            p90_height,
            p50_height,
            p10_height
        FROM buildings 
        WHERE p90_height > {height}
        ORDER BY p90_height DESC
        LIMIT {rows}
    """
    
    rows = db.execute(query).fetchall()
    
    if not rows:
        print("No results found. Did you process the first tile yet?")
    else:
        # Expanded header to include the new P10 measure
        header = f"{'RowID':<8} | {'Lat':<11} | {'Lon':<11} | {'Area':<7} | {'Plus Code':<15} | {'AvgH':<6} | {'P90H':<6} | {'MedH':<6} | {'BaseH':<6}"
        print(header)
        print("-" * len(header))
        
        for r in rows:
            # Mapping: r[0]=rowid, r[1]=lat, r[2]=lon, r[3]=area, r[4]=plus, 
            # r[5]=max, r[6]=avg, r[7]=p90, r[8]=p50, r[9]=p10
            print(f"{r[0]:<8} | {r[1]:<11.6f} | {r[2]:<11.6f} | {r[3]:<7.1f} | {r[4]:<15} | "
                  f"{r[5]:<6.1f} | {r[6]:<6.1f} | {r[7]:<6.1f} | {r[8]:<6.1f}")

    db.close()


def get_max_fresnel_radius_geodesic(tx_coords, rx_coords, freq_ghz=0.9):
    """
    Calculates the maximum Fresnel zone radius (first zone) and the 
    geodesic distance between two points.
    """
    # 1. Calculate Geodesic Distance (Haversine)
    lat1, lon1 = map(math.radians, tx_coords)
    lat2, lon2 = map(math.radians, rx_coords)
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    distance_m = 6371000 * c  # Earth radius in meters
    
    # 2. Fresnel Zone Calculation
    # Formula: r = 17.32 * sqrt( (d1 * d2) / (f * D) )
    # For maximum radius at midpoint: d1 = d2 = D/2
    # Simplified: r = 17.32 * sqrt( D / (4 * f) )
    # Where D is in km and f is in GHz
    
    dist_km = distance_m / 1000.0
    max_radius_m = 17.32 * math.sqrt(dist_km / (4 * freq_ghz))
    
    return max_radius_m, distance_m


# Used in get region obstacles
def point_to_line_dist(py, px, p1, p2):
    # Standard formula for distance from point to line segment
    y1, x1 = p1
    y2, x2 = p2
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0: return math.sqrt((px-x1)**2 + (py-y1)**2)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx*dx + dy*dy)
    t = max(0, min(1, t))
    closest_x, closest_y = x1 + t * dx, y1 + t * dy
    return math.sqrt((px - closest_x)**2 + (py - closest_y)**2)

# Return obstacles insides a bouding box (lat, lon)
def get_link_obstacles(tx_coords, rx_coords, min_height):

    
    db = duckdb.connect("parana_buildings.db")
    
    # Calculate Bounding Box
    margin = 0.001
    min_lon = min(tx_coords[1], rx_coords[1]) - margin
    max_lon = max(tx_coords[1], rx_coords[1]) - margin
    min_lat = min(tx_coords[0], rx_coords[0]) + margin
    max_lat = max(tx_coords[0], rx_coords[0]) + margin
    
    # SQL Query with your vertical threshold
    query = f"""
        SELECT rowid, latitude, longitude, p90_height, full_plus_code, geometry
        FROM buildings
        WHERE longitude BETWEEN {min_lon} AND {max_lon}
          AND latitude BETWEEN {min_lat} AND {max_lat}
          AND p90_height > {min_height}
    """
    
    candidates = db.execute(query).fetchall()
    db.close()
    
    # Second Stage: Cross-track distance filter (Vector Math)
    # We check if the building is within, say, 20m of the direct path
    path_obstacles = []
    for b in candidates:
        dist = point_to_line_dist(b[1], b[2], tx_coords, rx_coords)
        if dist < 0.0002:  # Approx 20 meters in degrees
            path_obstacles.append(b)
            
    return path_obstacles


def plot_rf_with_polygons(tx, rx, results):   

    if not results:
        print("No buildings with geometry and height found in this area.")
        return

    plt.figure(figsize=(12, 10))
    ax = plt.gca()
    
    for row in results:
        wkt_geom, height =  row[5], row[3]
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

def add_fresnel_to_plot(tx, rx, freq_ghz=0.9):
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
    print(wavelength)
    
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
    plt.plot(lons_l, lats_l, color='orange', alpha=0.6, linewidth=1.5)
    plt.plot(lons_r, lats_r, color='orange', alpha=0.6, linewidth=1.5)
    
    # Fill the zone
    plt.fill(list(lons_l) + list(lons_r)[::-1], 
             list(lats_l) + list(lats_r)[::-1], 
             color='orange', alpha=0.15)

if __name__ == "__main__":
     
    # 1. Define the Paraná "Filter Box" 
    # (Rough bounds to exclude most of Paraguay/MS/SC)
    wg84_box = -55, -27, -47, -22
    # import_csv("parana_buildings.db", "open_building_csv", wg84_box, 0.7)