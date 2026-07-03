import rasterio
from rasterio.merge import merge
from rasterio.io import MemoryFile
from rasterio.warp import transform_bounds
from rasterio.enums import Resampling
import os
from geopy.distance import distance, geodesic
from collections import OrderedDict
import logging
from pathlib import Path
import json
import time
import math
import numpy as np
import tomlkit
from tomlkit import document, table, aot
from collections import defaultdict
import cisei_lib.core.profiles.geo_tools as geoTools
from cisei_lib.io.minio_access import MinioAccess
import cisei_lib.dem.dem_utils as du 
from cisei_lib.core.profiles.geo_tools import fresnel_radii, get_fresnel_offsets
from enum import IntFlag
from dataclasses import dataclass



log = logging.getLogger(__name__)

from enum import IntFlag

class DSFlags(IntFlag):
    NONE = 0
    DTM = 1 << 0      # 1
    DSM = 1 << 1      # 2  
    COVER = 1 << 2    # 4
    CLUTTER = 1 << 3  # 8
    BLDG = 1 << 4     # 16 (new flag)
    ALL = DTM | DSM | COVER | CLUTTER | BLDG   # 31 (updated to include BLDG)

@dataclass
class FresnelProfile:
    v: int
    distances: list
    dtm_h: list  
    dsm_h: list
    clutter_h: list
    lulc_ids: list
    segs: list

type Coordinate = tuple[float, float]

@dataclass(slots=True)
class P2PLink:
    tx: Coordinate
    rx: Coordinate
    tx_ha: float
    rx_ha: float
    freq_mhz: float
    tx_ha_abs: float | None = None
    rx_ha_abs: float | None = None
    on_rooftop: bool = False


# ---------------------------------------------------------------------------
# Small LRU cache for open rasterio datasets
# ---------------------------------------------------------------------------
class TileCache:
    """Keeps a small LRU cache of open rasterio datasets."""

    def __init__(self, max_open=12):
        self.cache = OrderedDict()
        self.max_open = max_open

    def get(self, path):

        """Return a cached dataset or open a new one."""
        if path in self.cache:
            log.debug(f"TileCache HIT {path}")
            self.cache.move_to_end(path)
            return self.cache[path]

        # Evict least recently used
        if len(self.cache) >= self.max_open:
            old_path, old_ds = self.cache.popitem(last=False)
            log.debug(f"TileCache EVICT {old_path}")
            old_ds.close()

        log.debug(f"TileCache MISS {path} (opening new dataset)")
        ds = rasterio.open(path)
        self.cache[path] = ds
        return ds

    def close_all(self):
        """Close all cached datasets."""
        for ds in self.cache.values():
            try:
                ds.close()
            except Exception:
                pass
        self.cache.clear()
    
# ---------------------------------------------------------------------------
# Main class for elevation and land-cover data access
# ---------------------------------------------------------------------------
class geoInfo:

    def __init__(self, **kwargs):
        self.repo_access = MinioAccess()
        cache_size = kwargs.get('cache_size', 4)
        self.cache_dem   = TileCache(max_open=cache_size)

        # --- Determine dataset roots ---
        self.dem_root_path = kwargs.get("dem_key") or os.getenv("MINIO_DEM_ROOT_KEY")
        if not self.dem_root_path:
            raise ValueError("Missing MINIO_DEM_ROOT_KEY environment variables")

        # --- Prepare datasets.tom local paths --- (!!! NEVER USED)
        local_dem_index = self.repo_access.local_home / self.dem_root_path / "datasets.toml"

        # --- Ensure datasets.toml files exist locally ---
        index_key = Path(self.dem_root_path) / 'datasets.toml'
        try:
            self.repo_access.download(index_key, 'refresh')
        except Exception as e:
            raise RuntimeError(f"Failed to download global index {index_key}: {e}")
    
        self.dem_global_index = dict(tomlkit.parse(self.repo_access._map_local(index_key).read_text(encoding="utf-8")))

        self.mirror_folders_with_index()
               
         # ---- Inicializa index files ----
        self.index_geojson = {}     # loaded index geo-json by type: DTM, DSM, COVER and BLDG
        self.dataset_keys = {}     # relative key to the repository by type: DTM, DSM, COVER and BLDG
        self.cover_legend = None # legends of the current selected COVER
        self.band_index = 1 # band with height or cover data in multi-band tiffs

        # This dict are built per-profile (TX - RX)
        self.dtm_dict = None
        self.dsm_dict = None        
        self.lulc_dict = None
        self.clutter_dict = None



    def mirror_folders_with_index(self):
        ma = self.repo_access
        cfg = self.dem_global_index

        for data_type, block in cfg.items():
            datasets = block.get("datasets")
            if not datasets:
                continue

            for ds in datasets:          # priority = TOML order
                if not ds.get("enabled"):
                    continue

                base = ds["minio_key"].rstrip("/")
                ma.download(f"{base}/index-geo.json")

                if data_type == 'COVER':
                    ma.download(f"{base}/cover-legend.json")    

    # -------------------------------------------------------------------
    # Coordinate checks
    # -------------------------------------------------------------------
    def iscoord(self, src, lat, lon):
        if src is not None:
            b = src.bounds
            return (b.left <= lon <= b.right) and (b.bottom <= lat <= b.top)
        return False

    # -------------------------------------------------------------------
    # Tile handling
    # -------------------------------------------------------------------

    # Define active dataset for the given type: DTM, DSM, COVER and BLDG
    def select_dataset(self, ds_flag: DSFlags, points=None):
        """
        Select the first dataset of a given type that covers all requested points.

        Returns:
            (dataset_minio_key, tiles) where:
            - tiles is None if points is None
            - tiles is a list of tile filenames if points are supplied
            - (None, None) if no dataset matches
        """
        def update_index():
            with open(index_geojson, 'r') as f:
                self.index_geojson[ds_flag.name] = json.load(f)
                self.dataset_keys[ds_flag.name] = minio_key 
            if ds_flag & DSFlags.COVER:
                with open(cover_legend, 'r') as f:
                    self.cover_legend = json.load(f)            

        datasets = self.dem_global_index.get(ds_flag.name, {}).get("datasets", [])
        if not datasets:
            log.error('Dataset type does not exist')
            return None, None

        for ds in datasets:
            if not ds.get("enabled", False):
                continue

            minio_key = ds["minio_key"]
            # Resolve local path to index-geo.json
            index_geojson = (
                self.repo_access._map_local(minio_key) / "index-geo.json"
            )

            if ds_flag & DSFlags.COVER:
                cover_legend  = (
                    self.repo_access._map_local(minio_key) / "cover-legend.json"
                )
            
            self.band_index = ds.get("data_band", 1)
                        
            # Case: no coordinates requested → first enabled dataset wins
            if points is None:        
                update_index()       
                return minio_key, None
            try:
                tiles = du.find_tiles_by_list(index_geojson, points)
                update_index()
                return minio_key, tiles
            except Exception:
                # Dataset does not fully cover points → try next dataset
                continue

        # No dataset of this type covers all points
        log.error('Dataset initialization failed')
        return None, None

    # Find a tiff for current dataset type
    def find_tiff(self, ds_flag: DSFlags, lat, lon):
        if ds_flag.name not in self.dataset_keys:
            log.error('Dataset type was not set yet')
            return None
        tile = du.find_tile_by_coord(self.index_geojson[ds_flag.name], lat, lon)['id_filename']
        return Path(self.dataset_keys[ds_flag.name]) / tile

    # Find and load tiff for the current dataset type - returns raster object
    def get_cached_raster(self, ds_flag: DSFlags, lat, lon):
        """Load or reuse elevation and cover rasters for given coordinates.
        Automatically updates per-dataset tiff_usage.json files.
        """
        if ds_flag.name not in self.dataset_keys:
            self.select_dataset(ds_flag)
        tile_path = self.find_tiff(ds_flag, lat, lon)
        # download raster locally if necessary
        abs_tile = self.repo_access.download(tile_path)
        self._update_usage_index(abs_tile)
        # open raster object or return cached
        return self.cache_dem.get(abs_tile)       
   
    def get_merged_raster(self, link: P2PLink, ds_flag: DSFlags):
        from rasterio.merge import merge
        from rasterio.io import MemoryFile
        from rasterio.warp import transform_bounds
        from rasterio.enums import Resampling

        self.select_dataset(ds_flag, [link.tx, link.rx])

        segments = du.find_tiles_by_path_with_bboxes(
            self.index_geojson[ds_flag.name],
            link.tx,
            link.rx,
            link.freq_mhz,
        )

        if not segments:
            raise RuntimeError("No raster coverage found for Fresnel corridor")

        # Union bbox from Fresnel-aware segments
        west  = min(s["bbox"][0] for s in segments)
        south = min(s["bbox"][1] for s in segments)
        east  = max(s["bbox"][2] for s in segments)
        north = max(s["bbox"][3] for s in segments)

        radius = max(s["radius"] for s in segments)

        # Unique tiles required by the Fresnel corridor
        files = sorted({s["file"] for s in segments})

        # Case 1: only one tile needed — no merge
        if len(files) == 1:
            fname = files[0]
            tile_path = Path(self.dataset_keys[ds_flag.name]) / fname
            abs_tile = self.repo_access.download(tile_path)
            self._update_usage_index(abs_tile)

            src = self.cache_dem.get(abs_tile)

            return {
                "src": src,
                "memfile": None,
                "bbox": (west, south, east, north),
                "radius": radius,
                "segments": segments,
                "tiles": files,
                "band_index": self.band_index,
                "merged": False,
            }

        # Case 2: multiple tiles — merge
        srcs = []

        for fname in files:
            tile_path = Path(self.dataset_keys[ds_flag.name]) / fname
            abs_tile = self.repo_access.download(tile_path)
            self._update_usage_index(abs_tile)

            srcs.append(self.cache_dem.get(abs_tile))

        crs_set = {str(src.crs) for src in srcs}
        if len(crs_set) != 1:
            raise ValueError(f"Cannot merge rasters with different CRS: {crs_set}")

        src_crs = srcs[0].crs

        left, bottom, right, top = transform_bounds(
            "EPSG:4326",
            src_crs,
            west,
            south,
            east,
            north,
            densify_pts=21,
        )

        resampling = Resampling.nearest if (ds_flag & DSFlags.COVER) else Resampling.bilinear

        src_nodata = srcs[0].nodatavals[self.band_index - 1]

        if src_nodata is None and ds_flag in (DSFlags.DTM, DSFlags.DSM):
            merge_nodata = -9999.0
        else:
            merge_nodata = src_nodata


        mosaic, out_transform = merge(
            srcs,
            bounds=(left, bottom, right, top),
            indexes=self.band_index,
            resampling=resampling,
            nodata=merge_nodata,
        )

        if mosaic.ndim == 2:
            mosaic = mosaic[np.newaxis, :, :]

        profile = srcs[0].profile.copy()
        profile.update(
            driver="GTiff",
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            count=1,
            transform=out_transform,
            crs=src_crs,
            dtype=mosaic.dtype,
            nodata=merge_nodata,
        )

        memfile = MemoryFile()
        src = memfile.open(**profile)
        src.write(mosaic)

        return {
            "src": src,
            "memfile": memfile,
            "bbox": (west, south, east, north),
            "radius": radius,
            "segments": segments,
            "tiles": files,
            "band_index": 1,
            "merged": True,
        }


    # -------------------------------------------------------------------
    # Profiles
    # -------------------------------------------------------------------

    def get_1D_profile(self, ds_flag: DSFlags, start_latlon, end_latlon, step=1):
        """
        Segments the path, calls the worker for each TIFF, and stitches the 
        (N, 2) arrays into one continuous, strictly monotonic profile.
        """

        if ds_flag.name not in self.dataset_keys:
            self.select_dataset(ds_flag, [start_latlon, end_latlon])
        
        # 1. Get the segmented path (this function was defined in the previous step)
        path_segments = du.find_tiles_by_path(self.index_geojson[ds_flag.name], start_latlon, end_latlon)
        
        if not path_segments:
            return np.empty((0, 2))

        all_segments = []
        
        total_accumulated_dist = 0.0
        last_seg_end = None

        for i, (seg_start, seg_end, filename) in enumerate(path_segments):
                            
            src = self.get_cached_raster(ds_flag, *seg_start)

            # 2. Call the worker - Returns an array of shape (N, 2)
            # column 0: distances (starting at 0), column 1: elevations
            segment_data = du.get_path_data_from_src(
                src,                 
                seg_start, 
                seg_end, 
                self.band_index,
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
 
    # Create a single dictionary for one type of profile
    def create_2D_dictionary_old(self, link: P2PLink, ds_flag: DSFlags):
    
        self.select_dataset(ds_flag, [link.tx, link.rx])    
        segments = du.find_tiles_by_path_with_bboxes(self.index_geojson[ds_flag.name], link.tx, link.rx, link.freq_mhz)        

        res = []
        segs = []
        for s in segments:             
            tx = s['tx']
            rx = s['rx']         
            src = self.get_cached_raster(ds_flag, *tx)
            box, radius = s['bbox'], s['radius']      
            res.append(du.extract_and_profile(src, *box, tx, rx, radius_m=radius, band_index=self.band_index))    
            segs.append((tx,rx))

        print(segs)    
        merged = du.merge_corridors_dicts( res, segs )

        return merged

    def create_2D_dictionary(self, link: P2PLink, ds_flag: DSFlags):
        raster = self.get_merged_raster(link, ds_flag)

        west, south, east, north = raster["bbox"]

        return du.extract_and_profile(
            raster["src"],
            west,
            south,
            east,
            north,
            link.tx,
            link.rx,
            radius_m=raster["radius"],
            band_index=raster["band_index"],
        )

    # Create several dicts and save in the class variables
    def initialize_2D_dictionaries(self, link: P2PLink, ds_types = DSFlags.ALL):

        self.dtm_dict = None
        self.dsm_dict = None
        self.clutter_dict = None
        self.lulc_dict = None

        if DSFlags.DTM & ds_types:
            self.select_dataset(DSFlags.DTM, [link.tx, link.rx])
            self.dtm_dict = self.create_2D_dictionary(link, DSFlags.DTM)            

        if DSFlags.DSM & ds_types:
            self.select_dataset(DSFlags.DSM, [link.tx, link.rx])
            self.dsm_dict = self.create_2D_dictionary(link, DSFlags.DSM)
        
        if DSFlags.CLUTTER & ds_types:
            self.clutter_dict = du.create_clutter_height_dict(self.dsm_dict, self.dtm_dict)

        if DSFlags.COVER & ds_types:
            self.select_dataset(DSFlags.COVER, [link.tx, link.rx])
            self.lulc_dict = self.create_2D_dictionary(link, DSFlags.COVER)

    # Interpolation method
    def sample_dict(self, distances, dem_dict, d_hs, mode='linear_max', sampler=None):
        modes = {
            'bilinear' : du.sample_dict_bilienar,
            'axial_nearest' : du.sample_dict_axial_nearest,
            'max_max' : du.sample_dict_max_max,
            'linear_max' : du.sample_dict_linear_max 
        }
        if sampler is None:
            if mode not in modes:
                raise ValueError(f'sample_dict: invalide mode {mode}')
            sampler = modes[mode]
        
        return sampler(distances, dem_dict, d_hs)

    # Extract a profile across an horizontal fresnel offset (dictionaries must be initialized)
    # distances are being computed used LULC resolution --- requires
    def extract_fresnel_profile(self, link: P2PLink, fresnel_v_h, mode = 'linear_max', sampler = None):
        
        if any(d is None for d in (
            self.dtm_dict,
            self.dsm_dict,
            self.lulc_dict,
            self.clutter_dict,
        )):
            raise ValueError("Dictionaries must be initialized for the link")

        # 1. Calculate the real physical length of the link
        D = distance(link.tx, link.rx).meters
    
        # 2. Filter keys to keep only points inside the actual path
        # We assume 0.0 is the start and D is the end
        valid_distances = [d for d in self.lulc_dict.keys() if 0 <= d <= D]
        if len(valid_distances) == 0:
            print(f' {link.tx, link.rx, D}' )
            raise ValueError(f"{link.tx, link.rx, D}")
        
        valid_distances.sort() # Ensure they are in order for the DTM interpolator
        
        # 3. Create the d_i vector for the Orchestrator
        distances = np.array(valid_distances)
        d_hs = get_fresnel_offsets(distances, fresnel_v_h, link.freq_mhz)
        dtm_h = self.sample_dict(distances, self.dtm_dict, d_hs, mode, sampler)
        # du.plot_data_tuples(zip(distances, hs_dtms))
                
        dsm_h = self.sample_dict(distances, self.dsm_dict, d_hs, mode, sampler)
        clutter_h = np.maximum(0, dsm_h - dtm_h)
        dsm_h = dsm_h

        lulc_ids  = du.sample_lulc_ids(distances, d_hs, self.lulc_dict)
        segs = du.get_path_segments(distances, d_hs, self.lulc_dict)

        profile = FresnelProfile(fresnel_v_h, distances, dtm_h, dsm_h, clutter_h, lulc_ids, segs)

        return profile
    
    # Cluttered Segments
    def calculate_ribbon_deepness(self, abs_elevations, distances, h_tx_abs, h_rx_abs, freq_mhz, v_bands):
        """
        Computes total linear meters of obstruction for multiple vertical bands.
        
        abs_elevations : DTM + Clutter heights along the ribbon
        distances      : The d_i vector (sampling coordinates)
        h_tx_abs       : Absolute altitude of TX (m)
        h_rx_abs       : Absolute altitude of RX (m)
        v_bands        : List of vertical intrusion levels [1.0, 0.6, 0, -0.6, -1.0]
        """
        # 1. Geometry Prep
        fr = fresnel_radii(distances, freq_mhz)
        
        # Linear LOS: The straight line between absolute antenna altitudes
        # h = h_tx + (dist / total_dist) * (h_rx - h_tx)
        total_dist = distances[-1] - distances[0]
        los = h_tx_abs + (distances - distances[0]) * (h_rx_abs - h_tx_abs) / total_dist
        
        # 2. Interval Calculation
        # Length of each "cell" represented by the sample point
        intervals = np.diff(distances, append=distances[-1])
        
        deepness_results = {}

        # 3. Band-by-Band Accumulation
        for v_target in v_bands:
            # Clearance floor = LOS height + (v_target * Fresnel Radius)
            h_clearance = los + (v_target * fr)
            
            # Hit Test: Is the absolute obstruction taller than the floor?
            hits = abs_elevations >= h_clearance
            
            # Deepness = Sum of intervals where hits are True
            total_m = np.sum(hits * intervals)
            deepness_results[v_target] = total_m
            
            # Efficiency: If this band has 0 deepness, deeper ones will too
            if total_m == 0 and v_target < 0: 
                # (Only stop if we are moving into the 'negative' deep submergence)
                pass 

        return deepness_results

    # Extract Fresnel Clutter
   
    def fresnel_matrix_invasion(self, link: P2PLink, clutter_id, v_bands=None, mode='linear_max'):
        if v_bands is None:
            v_bands = [-1, -0.6, 0, 0.6, 1]

        # Two separate results structures
        terrain_matrix = {}
        clutter_matrix = {}        

        # Iterate through Horizontal Slices (v_h)
        for v_h in v_bands:
            # Extract everything for this ribbon in one shot
            profile = self.extract_fresnel_profile(link, v_h, mode = mode)
            
            # 1. Prepare Elevations
            # Ground only
            abs_elev_terrain = profile.dtm_h
            
            # Ground + Masked Clutter
            mask = (profile.lulc_ids == clutter_id)
            filtered_clutter = np.where(mask, profile.clutter_h, 0.0)
            abs_elev_total = profile.dtm_h + filtered_clutter
            
            # 2. Establish Absolute Boresight        
            h_tx_abs = profile.dtm_h[0] + link.tx_ha    # doing inside the loop is strange
            h_rx_abs = profile.dtm_h[-1] + link.rx_ha   # however dtm_h does not change at the endpoints
            
            # 3. Calculate Deepness for this Ribbon
            # Result for Terrain only
            terrain_matrix[v_h] = self.calculate_ribbon_deepness(
                abs_elev_terrain, profile.distances, h_tx_abs, h_rx_abs, link.freq_mhz, v_bands
            )
            
            # Result for Terrain + Clutter
            total_deepness = self.calculate_ribbon_deepness(
                abs_elev_total, profile.distances, h_tx_abs, h_rx_abs, link.freq_mhz, v_bands
            )
            
            # 4. Extract "Net Clutter" 
            # We subtract terrain deepness to see how many EXTRA meters the trees add
            clutter_matrix[v_h] = {
                v_v: total_deepness[v_v] - terrain_matrix[v_h][v_v] 
                for v_v in v_bands
            }

        return terrain_matrix, clutter_matrix

    def fresnel_matrix_to_radial(self, matrix, v_bands):
        """
        Groups the NxN deepness matrix into 3 radial zones.
        Works for any number of bands (5, 7, 11, 21...).
        """
        zones = {'core': [], 'fresnel': [], 'boundary': []}
        
        # matrix is assumed to be a nested dict keyed by the floats in v_bands
        for v_h in v_bands:
            for v_v in v_bands:
                r = np.sqrt(v_h**2 + v_v**2)
                val = matrix[v_h][v_v]
                
                # The boundaries remain the same, anchoring the physics
                if r < 0.3:
                    zones['core'].append(val)
                elif r <= 0.85:
                    zones['fresnel'].append(val)
                else:
                    zones['boundary'].append(val)
        
        # If a bucket is empty (rare with higher N), return 0.0 or handle accordingly
        return {
            k: np.mean(v) if v else 0.0 
            for k, v in zones.items()
        }

    def fresnel_radial_invasions(self, link : P2PLink, v_bands = None):
        if v_bands is None:
            v_bands = [-1, -0.6, 0, 0.6, 1]
        
        clutter_id = self._get_clutter_id('tree')
    
        mat_t, mat_c = self.fresnel_matrix_invasion(link, clutter_id, v_bands)
        rad_t = self.fresnel_matrix_to_radial(mat_t, v_bands)
        rad_c = self.fresnel_matrix_to_radial(mat_c, v_bands)

        return rad_t, rad_c

    def extract_link_features(self, link: P2PLink, k=3, terminal_distance =30):
        """
        Extract local-causality features for link evaluation.
        Complements existing global features without modifying them.
        """

        if self.dtm_dict is None:
            raise RuntimeError("dtm_dict not initialized")

        # --------------------------------------------------------
        # Geometry
        # --------------------------------------------------------
        L = distance(link.tx, link.rx).meters

        distances = np.array(
            sorted(d for d in self.dtm_dict.keys() if 0.0 <= d <= L),
            dtype=float
        )

        if distances.size < 2:
            return {}

        # Absolute terminal heights (authoritative)
        h_tx, h_rx = (
            (link.tx_ha_abs, link.rx_ha_abs)
            if link.tx_ha_abs is not None and link.rx_ha_abs is not None
            else self._absolute_terminal_heights(link)
        )

        # LOS
        los = h_tx + (distances / L) * (h_rx - h_tx)

        # Fresnel radius (for v_v)
        fresnel_r = fresnel_radii(distances, link.freq_mhz)

        features = {}

        # ========================================================
        # Terrain local peaks (v_v)
        # ========================================================
        terrain_h = self.sample_dict(
            distances, self.dtm_dict, None, mode="linear_max"
        )

        terrain_dh = terrain_h - los

        features["terrain_peaks_vv"] = self._terrain_degaut_peaks(
            distances,
            terrain_h,
            los,
            fresnel_r,
            L,
            k=3,
            d0=terminal_distance,
        )


        # ========================================================
        # Worst geometric severity (terminal-sensitive)
        # ========================================================
        features["max_obstruction_angle_rad"] = self._max_terminal_angle(
            distances, terrain_dh
        )

        # ========================================================
        # Near-terminal LOS clearance (terrain only)
        # ========================================================
        tx_c, rx_c = self._near_terminal_clearance(
            distances,
            terrain_h,
            los,
            L,
            terminal_distance,
        )

        # ========================================================
        # TX → RX elevation angle (antenna alignment)
        # ========================================================
        features["tx_rx_elevation_angle_rad"] = (
            self._tx_rx_elevation_angle(link)
        )

        features["tx_near_terminal_clearance_m"] = tx_c
        features["rx_near_terminal_clearance_m"] = rx_c

        return features


    # -------------------------------------------------------------------
    # Visualization 
    # -------------------------------------------------------------------
    
    def show_fresnel_profiles(self, link: P2PLink, v_h = 0, v_v = -0.6, **kwargs):
        profile = self.extract_fresnel_profile(link, v_h, mode='linear_max')
        res_dtm = np.column_stack((profile.distances, profile.dtm_h))    
        res_dsm = np.column_stack((profile.distances, profile.dsm_h))
        h_trees = kwargs.get('h_tree', 10)
        h_build = kwargs.get('h_build', 5)
        lulc_heights = { self._get_clutter_id('tree') : h_trees, self._get_clutter_id('buil') : h_build }   
        profile_lulc = [lulc_heights.get(v, 0) for v in profile.lulc_ids]    
        res_lulc = np.column_stack((profile.distances, profile_lulc + profile.dtm_h))
        profile_fresnel = self._get_vertical_fresnel(profile, link, v_v)
        res_fresnel = np.column_stack((profile.distances, profile_fresnel))
        res = du.plot_data_tuples(res_dtm, res_dsm, res_lulc, res_fresnel, labels=['dtm', 'dsm', 'lulc', 'fresnel'],  **kwargs) 
        return res

    def show_lulc_fresnel_zone( self, link : P2PLink, **kwargs, ):
        freq_ghz = link.freq_mhz/1000

        # 1) Select COVER dataset (loads legend implicitly)
        self.select_dataset(DSFlags.COVER, [link.tx, link.rx])

        # 2) Segment using Fresnel-aware logic
        segments = du.find_tiles_by_path_with_bboxes(
            self.index_geojson[DSFlags.COVER.name],
            link.tx, link.rx, link.freq_mhz )        
       
        if not segments:
            return

        # 3) Resolve unique rasters via cache
        srcs = []
        seen = set()

        for seg in segments:
            fname = seg["file"]
            if fname in seen:
                continue
            seen.add(fname)

            lat, lon = seg["tx"]
            srcs.append(self.get_cached_raster(DSFlags.COVER, lat, lon))

        # 4) Union bbox (geo)
        west  = min(s["bbox"][0] for s in segments)
        south = min(s["bbox"][1] for s in segments)
        east  = max(s["bbox"][2] for s in segments)
        north = max(s["bbox"][3] for s in segments)

        # 5) Delegate visualization
        return du.show_lulc_fresnel_from_srcs(
            srcs,
            (west, south, east, north),
            self.cover_legend,
            link.tx,
            link.rx,
            freq_ghz,
            **kwargs,
        )

    def create_b64_map(self, link: P2PLink, ds_flag: DSFlags = DSFlags.DTM, **kwargs):
        from cisei_lib.core.plot.geo_plot import show_tiff

        merged = self.get_merged_raster(link, ds_flag)

        src = merged["src"]
        west, south, east, north = merged["bbox"]

        window = {
            "lon_w": west,
            "lat_n": north,
            "lon_e": east,
            "lat_s": south,
        }

        marks = [link.tx, link.rx]

        legend = None
        colors = None

        if ds_flag == DSFlags.COVER:
            legend = {int(k): v["name"] for k, v in self.cover_legend.items()}
            colors = {v["name"]: v["color"] for k, v in self.cover_legend.items()}

        return show_tiff(
            src,
            window,
            marks=marks,
            legend=legend,
            colors=colors
        )

    def create_b64_link(self, link: P2PLink, v_h = 0, mode='linear_max', **kwargs):
        from cisei_lib.core.plot.geo_plot import plot_profile

        self.initialize_2D_dictionaries(link)
        profile = self.extract_fresnel_profile(link, v_h, mode = mode )

        legend = {int(k):v['name'] for k,v in self.cover_legend.items() }        
        colors = {v['name']:v['color'] for k,v in self.cover_legend.items() }                
    
        los = geoTools.line_of_sight(profile.distances, profile.dtm_h, link.tx_ha, link.rx_ha)

        lulc_heights = { self._get_clutter_id('tree') : 11 }   
        h_t = [lulc_heights.get(v, 0) for v in profile.lulc_ids ] + profile.dtm_h   
        lulc_heights = { self._get_clutter_id('buil') : 6 }   
        h_b = [lulc_heights.get(v, 0) for v in profile.lulc_ids ] + profile.dtm_h        

        return plot_profile(
            profile.distances, profile.dtm_h, h_t, h_b,
            profile.lulc_ids,
            legend,
            colors,
            los=los,
            **kwargs)
       
    def show_usage_summary(self, dataset_type: str):
        """Print last used tiles with timestamps."""
        index_path = self._usage_index_path(dataset_type)
        if not index_path.exists():
            print(f"No usage log for {dataset_type}")
            return
        data = json.loads(index_path.read_text())
        print(f"Usage for {dataset_type} ({len(data)} tiles):")
        for f, ts in sorted(data.items(), key=lambda x: x[1], reverse=True):
            t = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
            print(f"  {t}  {f}")

    

    # -------------------------------------------------------------------
    # Private methods
    # -------------------------------------------------------------------

    def _get_tiff_resolution(self, src):
        res_x, res_y = src.res
        bounds = src.bounds
        lat = (bounds.top + bounds.bottom) / 2
        lon = (bounds.left + bounds.right) / 2
        dx = geodesic((lat, lon), (lat, lon + res_x)).meters
        dy = geodesic((lat, lon), (lat + res_y, lon)).meters
        return dx, dy

    def __del__(self):
        """Ensure cached datasets are closed."""
        try:
            self.cache_cover.close_all()
            self.cache_elev.close_all()
        except Exception:
            pass
    
    def _update_usage_index(self, abs_tile):
        """Update last-access timestamp for a GeoTIFF file."""

        path, rel_name = os.path.split(abs_tile)

        index_path =  Path(path) / 'tile_usage.json'
        now = time.time()

        try:
            data = json.loads(index_path.read_text()) if index_path.exists() else {}
        except Exception:
            data = {}

        data[rel_name] = now
        index_path.write_text(json.dumps(data, indent=2))

    def _list_index_files(self, root_prefix: str):
        """
        Return all remote keys whose filename is exactly 'index-geo.json',
        searched recursively under root_prefix.
        """
        objects = self.repo_access.remote_list(root_prefix)

        return [
            obj for obj in objects
            if Path(obj).name == "index-geo.json"
        ]

    def _scan_remote_datasets(self, root_prefix):
        index_files = self._list_index_files(root_prefix)

        datasets = defaultdict(list)

        for rel_index_key in index_files:
            dataset_root = str(Path(rel_index_key).parent)
            name = Path(dataset_root).name

            local_index = self.repo_access.download(rel_index_key)
            with open(local_index, "r") as f:
                idx = json.load(f)

            features = idx.get("features", [])
            props = features[0]["properties"] if features else {}

            # --- detect dataset kind ---
            is_raster = "band_count" in props

            # --- extract metadata (representative only) ---
            crs = props.get("crs_native") if is_raster else None
            bands = props.get("band_count") if is_raster else None

            resolution = None
            if is_raster:
                if "affine_vector" in props:
                    a = props["affine_vector"]
                    resolution = [abs(a[0]), abs(a[4])]
                elif "res_m_x" in props and "res_m_y" in props:
                    resolution = [props["res_m_x"], props["res_m_y"]]

            # --- classify by path (kept as-is) ---
            key_lower = dataset_root.lower()
            if "/dtm/" in key_lower:
                ds_type = "DTM"
            elif "/dsm/" in key_lower:
                ds_type = "DSM"
            elif "/lulc/" in key_lower or "/cover/" in key_lower:
                ds_type = "COVER"
            elif "/bldg/" in key_lower:
                ds_type = "BLDG"
            else:
                ds_type = "UNCLASSIFIED"

            datasets[ds_type].append({
                "name": name,
                "minio_key": dataset_root,
                "crs": crs,
                "resolution": resolution,
                "bands": bands,
                "selected_band": None if bands and bands > 1 else None,
                "enabled": True,
            })

        return dict(datasets)

    def _build_global_dem_index(self, filename="datasets.toml"):
        doc = document()
        root_prefix = self.dem_root_path
        datasets = self._scan_remote_datasets(self, root_prefix)

        for ds_type, entries in datasets.items():
            t = table()
            arr = aot()
            for entry in entries:
                clean = {k: v for k, v in entry.items() if v is not None}
                arr.append(clean)
            t.add("datasets", arr)
            doc.add(ds_type, t)

        # relative key defines BOTH local and remote paths
        relative_key = f"{root_prefix.rstrip('/')}/{filename}"

        local_path = self.repo_access._map_local(relative_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        with open(local_path, "w", encoding="utf-8") as f:
            f.write(doc.as_string())

        self.repo_access.upload(relative_key, remove_local=True)

    def _get_clutter_id(self, label : None | str =  None):
        '''  
        return clutter id or dict
        :param label: clutter string: tree_cover, built_up
        '''       
        if self.cover_legend is None:
            raise RuntimeError('LULC dataset was not initialized')
        
        name_to_key = {v['name']: int(k) for k, v in self.cover_legend.items()}

        if label is None: 
            return name_to_key
        
        label = label.lower()
        for key, value in name_to_key.items():
            if label in key.lower():
                return value                     

    def _get_vertical_fresnel(self, profile: FresnelProfile, link: P2PLink, v):
        h_tx_abs = profile.dtm_h[0] + link.tx_ha    # doing inside the loop is strange
        h_rx_abs = profile.dtm_h[-1] + link.rx_ha   # however dtm_h does not change at the endpoints
        total_dist = profile.distances[-1] - profile.distances[0]
        los = h_tx_abs + (profile.distances - profile.distances[0]) * (h_rx_abs - h_tx_abs) / total_dist
        offsets = get_fresnel_offsets(profile.distances, v, link.freq_mhz)
        return los + offsets

    def _absolute_terminal_heights(self, link):
        """
        Compute absolute TX/RX heights using self.dtm_dict,
        sampled via sample_dict at d = 0 and d = D.
        """

        if self.dtm_dict is None:
            raise RuntimeError("dtm_dict not initialized")

        if link.tx_ha is None or link.rx_ha is None:
            raise RuntimeError("Relative antenna heights not set in link")

        # Link length in meters (authoritative)
        D = distance(link.tx, link.rx).meters

        # Sample terrain at d = 0 and d = D, v_h = 0 implicitly
        samples = self.sample_dict(
            [0.0, D],
            self.dtm_dict,
            None,
            mode="linear_max"
        )

        # ASSUMPTION (consistent with geo_info):
        # sample_dict returns list aligned with distances
        h_tx_ground = samples[0]
        h_rx_ground = samples[1]

        h_tx_abs = h_tx_ground + link.tx_ha
        h_rx_abs = h_rx_ground + link.rx_ha

        return float(h_tx_abs), float(h_rx_abs)


    # ============================================================
    # Feature private methods
    # ============================================================

    def _max_terminal_angle(self, distances, dh):
        """
        Max obstruction angle as seen from the nearest terminal.
        """

        angles = []
        L = distances[-1]

        for d, h in zip(distances, dh):
            if h <= 0:
                continue

            d_term = min(d, L - d)
            if d_term > 0:
                angles.append(math.atan(h / d_term))

        return float(max(angles)) if angles else 0.0

    def _near_terminal_clearance(self, distances, terrain_h, los, L, d0):
        """
        Compute near-terminal LOS clearance at a fixed distance d0
        from TX and RX.
        """

        if L <= 2 * d0:
            return None, None

        # TX side
        idx_tx = np.searchsorted(distances, d0, side="left")
        if idx_tx >= len(distances):
            tx_clearance = None
        else:
            tx_clearance = float(los[idx_tx] - terrain_h[idx_tx])

        # RX side
        idx_rx = np.searchsorted(distances, L - d0, side="right") - 1
        if idx_rx < 0:
            rx_clearance = None
        else:
            rx_clearance = float(los[idx_rx] - terrain_h[idx_rx])

        return tx_clearance, rx_clearance

    def _tx_rx_elevation_angle(self, link: P2PLink):
        """
        Elevation angle from TX boresight to RX position.
        Positive = RX above TX.
        """

        # Absolute heights (authoritative)
        if link.tx_ha_abs is not None and link.rx_ha_abs is not None:
            h_tx = link.tx_ha_abs
            h_rx = link.rx_ha_abs
        else:
            h_tx, h_rx = self._absolute_terminal_heights(link)

        # Horizontal distance
        L = distance(link.tx, link.rx).meters
        if L <= 0:
            return None

        return float(math.atan((h_rx - h_tx) / L))

    def _terrain_degaut_peaks(self, distances, terrain_h, los, fresnel_r, L,
                            k=3, d0=30.0):
        """
        Degaut-style terrain peaks.
        Reference surface: first Fresnel boundary (v = -1).
        """

        distances = np.asarray(distances)
        terrain_h = np.asarray(terrain_h)
        los = np.asarray(los)
        fresnel_r = np.asarray(fresnel_r)

        # Degaut obstruction signal: height above F1
        dh = terrain_h - (los - fresnel_r)

        # Valid domain: above F1, exclude near terminals
        valid = (dh > 0) & (distances >= d0) & (distances <= L - d0)

        if not np.any(valid):
            return []

        # Normalized Degaut v parameter
        v = np.zeros_like(dh)
        v[valid] = dh[valid] / fresnel_r[valid]

        # Find connected lobes
        peaks = []
        in_lobe = False
        start = 0

        for i in range(len(v)):
            if valid[i] and not in_lobe:
                in_lobe = True
                start = i
            elif not valid[i] and in_lobe:
                peaks.append((start, i))
                in_lobe = False

        if in_lobe:
            peaks.append((start, len(v)))

        # Representative peak per lobe
        reps = []
        for s, e in peaks:
            idx = s + np.argmax(v[s:e])
            reps.append({
                "v_v": float(v[idx]),
                "d_norm": float(distances[idx] / L),
            })

        reps.sort(key=lambda x: x["v_v"], reverse=True)
        return reps[:k]




if __name__ == '__main__': 
   
    gio = geoInfo()
    tx = (-26.178913,-53.072063)
    rx = (-26.161617,-53.015026)
    link = P2PLink(tx, rx, 7, 7, 900)
    gio.initialize_2D_dictionaries(link) 




