import logging
import numpy as np
import duckdb
import pandas as pd
from pathlib import Path
import copy
from cisei_lib.core.profiles.geo_info import geoInfo, DSFlags, P2PLink
import cisei_lib.dem.gob_footprints_utils as bldg_utils
import cisei_lib.core.profiles.geo_tools as geoTools
from io import BytesIO
import base64

log = logging.getLogger(__name__)

class geoInfoVector(geoInfo):

    def __init__(self, **kwargs):
        """
        Extends geoInfo to support DuckDB-backed building footprints.
        """
        super().__init__(**kwargs)
        
        # Persistent DuckDB in-memory session
        self.vector_con = duckdb.connect(":memory:")
        self.vector_con.execute("CREATE TABLE IF NOT EXISTS loaded_tiles (filename TEXT UNIQUE)")

        self.building_df = None # current building dataframe
        self.link_dist_m = None # projected link distance
        self.link = None # enriched link object with absolute antennas heights

    # Load parquets into local repository and duckdb memory by page faulting
    def get_cached_buildings(self, points):
        """
        Ensures all Parquet tiles intersecting the given points
        are downloaded locally and paged into DuckDB memory.

        This is the vector analogue of get_cached_raster().
        """

        # 1. Dataset selection (pure metadata + tiling)
        ds_key, tiles = self.select_dataset(DSFlags.BLDG, points)

        if not tiles:
            return False

        # 2. Ensure Parquets exist locally (MinIO cache)
        local_dir = self.repo_access.local_home / ds_key
        for tile in tiles:
            self.repo_access.download(Path(ds_key) / tile)

        # 3. Page-fault into DuckDB (in-memory cache)
        bldg_utils.duckdb_page_fault(
            self.vector_con,
            tiles,
            local_dir
        )

        return True

    # Orchestrator to create a dataframe with the buildings affecting Fresnel
    def create_link_dataframe(self, link: P2PLink, mode='linear_max'):
        """
        Build and store a link-conditioned DataFrame of buildings.
        Requires DTM dictionaries to be already initialized.
        """

        if self.dtm_dict is None:
            raise RuntimeError(
                "DTM dictionaries not initialized. "
                "Call initialize_2D_dictionaries(tx, rx) before create_link_dataframe()."
            )

        # 1. Ensure Parquet tiles are paged into DuckDB (vector cache)
        ok = self.get_cached_buildings([link.tx, link.rx])
        # print('Cached building:', ok)
        if not ok:
            self.building_df = None
            return None
        
        # 2. Extract buildings intersecting the Fresnel envelope
        df = self._extract_buildings_for_link(link)
        if df is None or df.empty:
            self.building_df = None
            return None

        # 3. Enrich with LOS position and ground heights
        df = self._enrich_building_dataframe(df, link, mode)

        # 4. Persist link state
        self.building_df = df
        return df

    # Filter the dataframe using vertical and horizontal minimal invasions
    def filter_buildings_by_fresnel(self, v_v=-1, v_h=-1):

        if self.building_df.empty:
            return self.building_df.empty

        D = self.link_dist_m
        d = self.building_df['d_los_m'].to_numpy()

        # LOS height
        if self.link.tx_ha_abs is None or self.link.rx_ha_abs is None:
            raise ValueError("tx_ha_abs and rx_ha_abs must be defined before calling ")
                      
        h_tx, h_rx = self.link.tx_ha_abs, self.link.rx_ha_abs

        h_los = h_tx + (h_rx - h_tx) * (d / D)

        # Fresnel radius
        R = geoTools.fresnel_radius(d, D, self.link.freq_mhz)
       
        # ----------------
        # Vertical filter
        # ----------------
        h_f = h_los + v_v * R

        v_mask = (
            (self.building_df['h_top'].to_numpy() >= h_f) 
            # & (df['h_base'].to_numpy() <= h_f)
        )

        df_v = self.building_df[v_mask].copy()
        if df_v.empty:
            return df_v        

        # --- NEW: vertical v-values for record ---
        idx = df_v.index
        with np.errstate(divide="ignore", invalid="ignore"):
            df_v['v_v_top']  = (df_v['h_top'].to_numpy()  - h_los[idx]) / R[idx]
            df_v['v_v_base'] = (df_v['h_base'].to_numpy() - h_los[idx]) / R[idx]

        # immediately after
        r0 = (R[idx] == 0)
        df_v.loc[r0, 'v_v_top']  = -1.0
        df_v.loc[r0, 'v_v_base'] = -1.0

        # ------------------
        # Horizontal filter
        # ------------------
        d_v = df_v['d_los_m'].to_numpy()
        R_v = geoTools.fresnel_radius(d_v, D, self.link.freq_mhz)

        dh = df_v['dh_los_m'].to_numpy()
        h_mask = dh <= abs(v_h) * R_v

        return df_v[h_mask]

    def fresnel_building_invasions(self, df: pd.DataFrame):
        """
        Extract horizontal and vertical invasion from buildings               
        :param df: filtered dataframe with vertical invasions
        """
        # Constants pulled out of the loop to save overhead
        total_dist = self.link_dist_m
        freq = self.link.freq_mhz
        
        invasions = []
        
        # itertuples is faster and allows dot-notation (row.column_name)
        for idx,row in enumerate(df.itertuples(index=False)):
            # Calculate Fresnel radius for this specific point
            fr = geoTools.fresnel_radius(row.d_los_m, total_dist, freq)
            
            # Extract features for the obstacle
            bldg = bldg_utils.extract_fresnel_features(row.proj_vertices, total_dist, fr)
            
            # Efficiently merge the row metadata into the bldg dictionary
            bldg.update({
                't': row.t,
                'v_v_top': row.v_v_top,
                'v_v_base': row.v_v_base,
                'rowid': row.rowid,
                'full_plus_code': row.full_plus_code,
                'fresnel_radius' : fr,
                'vertices' : row.proj_vertices,
                'iloc': idx  # Uses the current length as the index
            })
            
            invasions.append(bldg)

        return invasions

    def buildings_radial_invasions(self,
        df,
        r_core=0.3,
        r_fresnel=0.85,
        weight_key="deepness_m"
    ):
        """
        Aggregates building invasions into radial Fresnel zones.

        buildings: list of dicts (your data)
        weight_key: "deepness_m" or "area_m2"

        Returns summed invasion per zone.
        """

        buildings = self.fresnel_building_invasions(df)

        zones = {
            "core": 0.0,
            "fresnel": 0.0,
            "boundary": 0.0
        }

        for b in buildings:
            v_h = abs(b["v_h"])
            v0  = b["v_v_base"]
            v1  = b["v_v_top"]
            w   = b[weight_key]

            # Ensure ordering
            v_min, v_max = sorted([v0, v1])

            # Radial distance as function of v_v
            # r(v_v) = sqrt(v_h^2 + v_v^2)

            # Minimal and maximal radius touched by the building
            r_min = np.sqrt(v_h**2 + min(v_min**2, v_max**2))
            r_max = np.sqrt(v_h**2 + max(v_min**2, v_max**2))

            # Core
            if r_min < r_core:
                zones["core"] += w

            # Fresnel body
            if r_min < r_fresnel and r_max >= r_core:
                zones["fresnel"] += w

            # Boundary
            if r_max >= r_fresnel:
                zones["boundary"] += w

        return zones, buildings



# --------------- PLOT METHODS ------------------------------

    # Footprint
    def plot_link_horizontal_profile(self, df: pd.DataFrame, show_fresnel=True, **kwargs):
        """
        Visualize building obstacles for a link using the enriched building_df.
        df: allows to plot filtered of full link dataframes
        """

        if df is None or df.empty:
            print("No buildings to plot.")
            return

        import matplotlib.pyplot as plt
        from shapely import wkt
        from matplotlib.patches import Polygon as MplPolygon
        import numpy as np

        tx, rx = self.link.tx, self.link.rx

        dpi = kwargs.get('dpi', None)
        figsize = kwargs.get('figsize', None)
        fig, ax = plt.subplots(figsize=figsize, dpi=dpi)        

        # --- 1. Plot building footprints ---
        for _, row in df.iterrows():
            try:
                poly = wkt.loads(row['geometry'])

                if poly.geom_type == 'MultiPolygon':
                    polys = list(poly.geoms)
                else:
                    polys = [poly]

                # Color by absolute top height if available
                if 'h_top' in row:
                    color = plt.cm.viridis(min(row['h_top'] / 50.0, 1.0))
                else:
                    color = 'gray'

                for p in polys:
                    x, y = p.exterior.xy
                    patch = MplPolygon(
                        np.column_stack((x, y)),
                        closed=True,
                        facecolor=color,
                        edgecolor='black',
                        alpha=0.6,
                        lw=0.5
                    )
                    ax.add_patch(patch)

            except Exception:
                continue

        # --- 2. Plot TX–RX link ---
        ax.plot(
            [tx[1], rx[1]],
            [tx[0], rx[0]],
            'r--',
            linewidth=2,
            label='LOS'
        )

        ax.plot(tx[1], tx[0], 'r^', markersize=10, label='TX')
        ax.plot(rx[1], rx[0], 'rv', markersize=10, label='RX')

        # --- 3. Optional Fresnel overlay ---
        if show_fresnel:
            bldg_utils.add_fresnel_to_plot(
                tx, rx,
                freq_ghz=self.link.freq_mhz / 1000.0,
                ax=ax
            )

        # --- 4. Formatting ---
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title("Link Building Obstacles (DataFrame-based)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.axis('equal')

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

    # Heights (require antenna absolute heights)
    def plot_link_vertical_profile(self, df: pd.DataFrame, show_fresnel=True, **kwargs):
        """
        Vertical profile view of buildings along the link.
        Uses building_df and DTM-derived base heights.
        df: allows to plot filtered of full link dataframes
        """

        if df is None or df.empty or self.link_dist_m is None or self.link is None:
            print("No buildings to plot.")
            return
        
        import matplotlib.pyplot as plt

        # --- 1. Link geometry ---
        D = self.link_dist_m

        # Antenna absolute heights
        if self.link.tx_ha_abs is None or self.link.rx_ha_abs is None:
            raise ValueError("tx_ha_abs and rx_ha_abs must be defined before calling ")
                      
        h_tx, h_rx = self.link.tx_ha_abs, self.link.rx_ha_abs

        # --- 2. LOS height function ---
        def h_los(d):
            return h_tx + (h_rx - h_tx) * (d / D)

        # --- 3. Plot setup ---
        dpi = kwargs.get('dpi', None)
        figsize = kwargs.get('figsize', None)
        fig, ax = plt.subplots(figsize=figsize, dpi=dpi)

        # --- 4. Plot buildings ---
        for _, row in df.iterrows():
            d = row['d_los_m']
            h0 = row['h_base']
            h1 = row['h_top']

            ax.plot([d, d], [h0, h1], color='black', linewidth=3)

        # --- 5. Plot LOS ---
        d_line = np.linspace(0, D, 500)
        h_line = h_los(d_line)
        ax.plot(d_line, h_line, 'r--', linewidth=2, label='LOS')
        ax.plot(0, h_tx, 'ro', markersize=8)
        ax.plot(D, h_rx, 'ro', markersize=8)

        # --- 6. Fresnel envelope ---
        if show_fresnel:
            d_line = np.linspace(0, D, 500)
            fr = geoTools.fresnel_radius(d_line, D, self.link.freq_mhz)

            h_line = h_los(d_line)

            ax.fill_between(
                d_line,
                h_line - fr,
                h_line + fr,
                color='red',
                alpha=0.15,
                label='1st Fresnel zone'
            )

        
        # --- 7. DTM Plotting -----

        # extract building bases
        d_b = df['d_los_m'].to_numpy()
        h_b = df['h_base'].to_numpy()
        idx = np.argsort(d_b)
        d_b = d_b[idx]
        h_b = h_b[idx]

        # choose a ground reference below all buildings
        ground_ref = h_b.min() - 5.0

        # plot building bases as terrain markers
        ax.plot(d_b, h_b, '.', color='saddlebrown', label='DTM (sampled)')

        # fill below building bases
        ax.fill_between(
            d_b,
            h_b,
            ground_ref,
            color='sandybrown',
            alpha=0.4
        )
        # --- 8. Formatting ---
        ax.set_xlabel("Distance along link (m)")
        ax.set_ylabel("Absolute height (m ASL)")
        ax.set_title("Vertical Link Profile with Buildings")
        ax.grid(True, alpha=0.3)
        ax.legend()

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

    # Building 2D profile
    def building_browser(self, invasions_list, vh_range=(None, None), vtop_range=(None, None), **kwargs):
        
        def is_in_range(val, bounds):
            low, high = bounds
            is_above = (low is None or val >= low)
            is_below = (high is None or val <= high)
            return is_above and is_below

        for item in invasions_list:
            vh = item.get('v_h')
            vtop = item.get('v_v_top')

            if is_in_range(vh, vh_range) and is_in_range(vtop, vtop_range):                
                # Use .get() with a default to prevent crashes
                fr = item.get('fresnel_radius')
                plus_code = item.get('full_plus_code', 'Unknown')
                vertices = item.get('vertices')
                
                # Formatting the print for clarity
                vh_display = f"{vh:.2f}" if vh is not None else "N/A"
                print(f"Showing: {plus_code} | v_h: {vh_display} | v_v_top: {vtop}")
                
                # Ensure we have what we need to plot
                if vertices is not None and fr is not None:
                    img = bldg_utils.show_building_clutter(vertices, fr, item, **kwargs)
                    yield {"item" : item, "img": img }
                else:
                    print(f"⚠️ Skipping {plus_code}: Missing geometry or Fresnel data.")
                    yield {"item" : item, "img": None }
                


    def get_next_invasion(self, browser):
        try:
            return next(browser)
        except StopIteration:
            print("No more invasions match the criteria!")
    
    # --------------- PRIVATE METHODS ----------------------------

    def _extract_buildings_for_link(self, link: P2PLink):
        """
        Query DuckDB for buildings intersecting the Fresnel envelope.
        Returns a raw DataFrame (no projection, no grounding).
        """

        max_r, _, _, _ = bldg_utils.get_max_fresnel_radius(
            link.tx, link.rx, freq_ghz=link.freq_mhz / 1000.0
        )

        results, cols = bldg_utils.get_precise_fresnel_buildings(
            self.vector_con, link.tx, link.rx, max_r
        )

        if not results:
            return None

        return pd.DataFrame(results, columns=cols.keys())
    
    def _enrich_building_dataframe(self, df: pd.DataFrame, link: P2PLink, mode):
        """
        Adds LOS-projected distance and absolute ground/top heights.
        Requires:
        - self.dtm_dict initialized
        - df contains 'geometry' and height columns (e.g. p90_h)
        - mode: DTM interpolation mode
        """

        # --- 1. LOS projection (longitudinal coordinate) ---
        d_los = []
        dh_los = []
        proj_vertices = []

        transformer, rot, total_dist = bldg_utils.compute_link_frame(*link.tx, *link.rx)
        self.link_dist_m = total_dist
        
        for _, row in df.iterrows():
            local_vertices = bldg_utils.project_wkt_to_line( row['geometry'], transformer, rot)
            
            proj_vertices.append(local_vertices)

            xs = [v[0] for v in local_vertices]
            ys = [v[1] for v in local_vertices]

            # longitudinal position
            d = np.clip(np.mean(xs), 0.0, total_dist)

            # LOS crossing test (polygon spans y=0)
            y_min = np.min(ys)
            y_max = np.max(ys)

            if y_min <= 0.0 <= y_max:
                dh = 0.0
            else:
                dh = min(abs(y_min), abs(y_max))

            d_los.append(d)
            dh_los.append(dh)

        df['d_los_m'] = np.asarray(d_los)
        df['dh_los_m'] = np.asarray(dh_los)
        df['proj_vertices'] = proj_vertices

        # --- 2. Ground height sampling (DTM, dh = 0) ---
        d_targets = df['d_los_m'].to_numpy()
        dh_targets = np.zeros_like(d_targets)

        h_base = self.sample_dict(
            d_targets,
            self.dtm_dict,
            dh_targets,
            mode = mode
        )

        df['h_base'] = h_base

        # --- 3. Absolute top height (if available) ---
        if 'p90_h' in df.columns:
            df['h_top'] = df['h_base'] + df['p90_h']

        # --- 4. Update link with absolute antenna heights ---
        
        self.link = copy.copy(link)

        if link.on_rooftop:
            self.link.tx_ha_abs, self.link.rx_ha_abs = self._compute_absolute_heights(df, link)
        else:
            dtm_vals = self.sample_dict([0, total_dist], self.dtm_dict, [0, 0], mode = mode)
            self.link.tx_ha_abs = dtm_vals[0] + link.tx_ha
            self.link.rx_ha_abs = dtm_vals[1] + link.rx_ha 

        return df

    def _compute_link_geometry(self, link: P2PLink):
        """
        Computes and stores link-level geometry once.
        """
        _, total_dist = bldg_utils.project_wkt_to_line(
            link.tx[0], link.tx[1],
            link.rx[0], link.rx[1],
            "POINT (0 0)"  # dummy geometry, ignored
        )

        self.link_dist_m = total_dist

    def _compute_absolute_heights(self, df: pd.DataFrame, link: P2PLink):
        if df is None or df.empty :
            raise ValueError('Link dataframe must be initialized before calling')
        
        df_sorted = df.sort_values('d_los_m')
        # first and last buildings
        if link.on_rooftop:
            h_tx_abs = df_sorted.iloc[0]['h_top'] + link.tx_ha
            h_rx_abs  = df_sorted.iloc[-1]['h_top'] + link.rx_ha
        else:
            h_tx_abs = df_sorted.iloc[0]['h_base'] + link.tx_ha
            h_rx_abs  = df_sorted.iloc[-1]['h_base'] + link.rx_ha

        return h_tx_abs, h_rx_abs
