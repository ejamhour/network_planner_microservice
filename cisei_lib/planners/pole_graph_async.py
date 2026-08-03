import asyncio

from cisei_lib.io.async_geo_service_pool import (
    AsyncGeoServicePool,
    GeoServiceError,
)
import networkx as nx
import geopandas as gpd
from pyproj import Geod
from shapely.affinity import rotate, translate
from shapely.geometry import box
from shapely.geometry import Point, LineString
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from math import atan2, degrees
from cisei_lib.planners.planner_classes import Pole
import cisei_lib.planners.metric_compiler as mc
from importlib.resources import files
from collections.abc import Iterable

# Base class for finding paths between poles.
#
# This class allows edges to be added dynamically and evaluates the shortest path.
# Except for the endpoints, most edges are identified by a pole_id.
# Default metric is to optimize paths with LOS, but it can be overhidden by the child class
#
# The algorithm places repeaters at available poles, so projection and distance calculations must be accurate.
# GeoJSON always stores coordinates in longitude–latitude order (CRS84), even though GeoPandas reports EPSG:4326.
# EPSG:4326 is automatically assigned when importing GeoJSON, but it's treated as CRS84 internally.
# EPSG:4326 (WGS 84) formally defines axis order as latitude–longitude (y, x).
# OGC:CRS84 (used by GeoJSON and KML) defines axis order as longitude–latitude (x, y) and has no EPSG code.
# EPSG:31982 is SIRGAS 2000 / UTM zone 22S, used in southern Brazil with coordinates in meters.
# GeoPandas ignores axis order differences for compatibility, treating both EPSG:4326 and CRS84 as (x, y).
# crs.is_geographic is True for degrees and crs.is_projected is True for UTM.

# CHATGPT about the heuristic in this code: Here lies the one who tried to improve the 
# worst untested segment of the best current path — and in doing so, walked every suboptimal route this world allowed.

# Fair enough. Just be sure to include the part where you designed the most poetic failure mode in algorithmic mesh planning history 
# — and gave it a tombstone. That’s not bullying. That’s inscription.

_GEOD = Geod(ellps="WGS84") # creates one reusable calculator using the WGS84 Earth model.

class PoleGraph:
    def __init__(self, **kwargs):

        self.G = nx.Graph()
        self.node_counter = 0

        self.geographic_crs = "EPSG:4326"

        # Creates an empty geodataframe

        self.poles = gpd.GeoDataFrame(
            columns=["pole_id", "name", "pos", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

        if "name" not in self.poles.columns:
            self.poles["name"] = self.poles["pole_id"]


        # Context parameters (avoid the use of TOML configuration in this class)
        DEFAULT_CONTEXT = {
            'clearance': 0,
            'ant_h': 7,
            'search_heuristic': 'sector_band',
            'search_midpoint' : False,  # uses middle point edges as fallback to non-obstruction
            'radius_min': 1, # lower bound for short links (km)
            'radius_max' : 5, # upper bound for long links (km)
            'radius_percent' : 0.5, # percentage of the link length            
            'radius_increment' : 0.5, # how much the radius increase when failed             
            'radius_poles': 10, # controls adaptative radius search goal
            'cone_angle': 0, # dual-cone angle (0 - disable_constraint)
            'cone_poles': 10, # controls the number of poles selected by cone
            'sector_exclusion' : 30, # semi-sector
            'sector_count' : 6, # number of sectors
            'sector_bands' : 3, # number of rings around the obstacle
            'sector_poles': 3, # number of poles per sector (sector_count * sector_poles)
            'debug' : False, # enable print of debug messages 
            'freq_mhz' : 900, # default radio carrier frequency in mhz
            'geo_pool_size': 4,
            'geo_timeout': 120,
            'geo_user_prefix': None,
            "projected_crs": "EPSG:31982", # UTM 22S is for Parana only          
        }

        self.context = {**DEFAULT_CONTEXT, **kwargs}

        self.geo = AsyncGeoServicePool(
            base_url=self.context.get("geo_url"),            
            pool_size=self.context["geo_pool_size"],
            timeout=self.context["geo_timeout"],
            user_prefix=self.context.get("geo_user_prefix"),
        )

        self.projected_crs = self.context["projected_crs"]
        self.poles_utm = self.poles.to_crs(self.projected_crs)


        # Register all search strategies as methods
        self._pole_search_strategies = {
            'dual_cone': self._get_poles_by_dual_cone,
            'sector_band' : self._get_poles_by_sector_band
        }

        # Features to metric function
        metric_name = kwargs.get("metric_spec", "default")
        try:
            metric_spec = PoleGraph.load_metric_spec(metric_name)
            self.metric_function = mc.compile_metric_spec(metric_spec) 
        except Exception as error:
            raise RuntimeError(f'Invalid metric specification: ', str(error))
            
    async def start(self):
        await self.geo.start()
        return self

    async def close(self):
        await self.geo.close()

    # --- Static methods ---

    @staticmethod
    def _normalize_pos(pos) -> tuple[float, float]:
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            raise TypeError("Position must be [lat, lon] or (lat, lon)")

        return float(pos[0]), float(pos[1])

    @staticmethod
    def load_metric_spec(name = 'default') -> str:
        return (
            files("cisei_lib")
            .joinpath(
                "resources",
                "metrics",
                f"{name}_metric.toml",
            )
            .read_text(encoding="utf-8")
        )

    # --- Non Overridable methods ---

    # Implements a LOS penalty metric - perfect links have cost = 1
    async def edge_metric(self, src_pos, dst_pos, src_ha, dst_ha) -> dict | None:

        src_pos = self._normalize_pos(src_pos)
        dst_pos = self._normalize_pos(dst_pos)

        if src_pos == dst_pos:
            self._debug('an edge cannot have equal src and dst positions')
            return None

        try:
            features = await self.geo.get_features(
                src_pos,
                dst_pos,
                tx_ha=src_ha,
                rx_ha=dst_ha,
                freq_mhz=self.context['freq_mhz'],
            )
        except GeoServiceError as error:
            self._debug(
                f'failed to compute the features for the edge '
                f'{src_pos} {dst_pos}: {error}'
            )
            return None

        # The pool returns the cached dictionary itself. Derived values are
        # therefore computed only once and remain available to later links.
        if 'obs_pos' not in features:
            features['obs_pos'] = self._top_peak_coordinates(
                src_pos,
                dst_pos,
                features,
            )

        if 'metric' not in features:
            features['metric'] = self.metric_function(features)

        return features

    # changes the current features-to-metric function 
    def set_metric_function(self, spec_string):
        try:
            self.metric_function = mc.compile_metric_spec(spec_string)
            # Cached dictionaries may already contain a metric calculated by
            # the previous function. A metric change therefore starts a new cache.
            self.geo.clear_cache()
        except Exception as error:
            raise RuntimeError(f'Invalid metric specification: ', str(error))

    def add_pole(self, pole: Pole) -> None:
        self.add_poles([pole])

    def add_poles(self, poles: Iterable[Pole]) -> None:
        """
        Adds one or more candidate poles.

        Input coordinates:
        - Pole.lat and Pole.lon use EPSG:4326.
        - self.poles remains in EPSG:4326.
        - self.poles_utm remains in EPSG:31982.
        """
        records = [pole.to_record() for pole in poles]

        if not records:
            return

        new_poles = gpd.GeoDataFrame(
            records,
            geometry="geometry",
            crs="EPSG:4326",
        )

        # Reject duplicate IDs inside the submitted batch.
        duplicated = new_poles.loc[
            new_poles["pole_id"].duplicated(keep=False),
            "pole_id",
        ].unique()

        if len(duplicated):
            raise ValueError(
                f"Duplicated pole IDs in batch: {sorted(duplicated)}"
            )

        # Reject IDs already present in the planner.
        if not self.poles.empty:
            existing_ids = set(self.poles["pole_id"])
            conflicts = existing_ids.intersection(new_poles["pole_id"])

            if conflicts:
                raise ValueError(
                    f"Poles already exist: {sorted(conflicts)}"
                )

        # Project the complete batch only once.
        new_poles_utm = new_poles.to_crs(self.poles_utm.crs)

        self.poles = gpd.GeoDataFrame(
            pd.concat(
                [self.poles, new_poles],
                ignore_index=True,
            ),
            geometry="geometry",
            crs="EPSG:4326",
        )

        self.poles_utm = gpd.GeoDataFrame(
            pd.concat(
                [self.poles_utm, new_poles_utm],
                ignore_index=True,
            ),
            geometry="geometry",
            crs=self.poles_utm.crs,
        )

    def add_poles_gdf(self, poles_gdf: gpd.GeoDataFrame) -> None:
        if poles_gdf.crs is None:
            raise ValueError("poles_gdf must have a CRS")

        poles_gdf = poles_gdf.to_crs("EPSG:4326")

        poles = [
            Pole(
                pole_id=row["pole_id"],
                name=row.get("name"),
                lat=row.geometry.y,
                lon=row.geometry.x,
                ant_h=row.get("ant_h"),
                elevation=row.get("elevation"),
            )
            for _, row in poles_gdf.iterrows()
        ]

        self.add_poles(poles)

    def clear_poles(self) -> None:
        self.poles = self.poles.iloc[0:0].copy()
        self.poles_utm = self.poles_utm.iloc[0:0].copy()

    # Add an edge to the graph, all arguments are dicts
    def add_edge(self, src_node, dst_node, edge):
        """
        Add a link between two pole-based nodes.
        - src_node and dst_node: dicts with 'pole_id' and optionally 'name'
        - edge: dict with at least 'metric' (used as 'weight'), plus any other edge attributes
        """
        if 'metric' not in edge:
            raise ValueError("Edge dictionary must include a 'metric' key.")

        src_label, src_new = self._get_or_create_node(src_node)
        dst_label, dst_new = self._get_or_create_node(dst_node)

        if src_new or dst_new or not self.G.has_edge(src_label, dst_label):
            edge_data = dict(edge)
            edge_data['weight'] = edge['metric']
            edge_data['tested'] = False
            self.G.add_edge(src_label, dst_label, **edge_data)
    
    # Retrieve a subset of poles that are using to search repeaters locations.
    async def get_candidates(self, s_label, d_label):
        strategy = self.context.get('search_heuristic')
        func = self._pole_search_strategies.get(strategy)

        if not func:
            raise ValueError(f"Unknown search heuristic: {strategy}")

        return await func(s_label, d_label)
    
    # Update the graph with options to improve the link between src and dest
    async def graph_expansion(self, s_label, d_label):
        '''
        Finds the best pole with respect to the LOS.
        Candidate edge metrics are evaluated concurrently by AsyncGeoServicePool.
        '''
        s_pos = self._normalize_pos(self.G.nodes[s_label]["pos"])
        d_pos = self._normalize_pos(self.G.nodes[d_label]["pos"])
        s_ha = self.G.nodes[s_label].get('ant_h', self.context['ant_h'])
        d_ha = self.G.nodes[d_label].get('ant_h', self.context['ant_h'])

        top_poles = await self.get_candidates(s_label, d_label)
        self._debug(f'improving: {s_label} {d_label}')

        pending = []

        for _, row in top_poles.iterrows():
            r_pos = self._normalize_pos(row.pos)

            if s_pos == r_pos or d_pos == r_pos:
                continue

            r_ha = row.get("ant_h", self.context["ant_h"])
            if pd.isna(r_ha):
                r_ha = self.context["ant_h"]

            rep_node = {
                'name': row['name'],
                'pos': row['pos'],
                'ant_h': r_ha,
                'hop_type': 'rep',
            }

            if not self.G.has_edge(s_label, row['name']):
                pending.append((
                    self.G.nodes[s_label],
                    rep_node,
                    self.edge_metric(s_pos, r_pos, s_ha, r_ha),
                ))

            if not self.G.has_edge(row['name'], d_label):
                pending.append((
                    rep_node,
                    self.G.nodes[d_label],
                    self.edge_metric(r_pos, d_pos, r_ha, d_ha),
                ))

        if pending:
            edge_results = await asyncio.gather(
                *(request for _, _, request in pending)
            )

            # NetworkX is updated sequentially in the event-loop thread.
            for (src_node, dst_node, _), edge_data in zip(
                pending,
                edge_results,
            ):
                if edge_data is not None:
                    self.add_edge(src_node, dst_node, edge_data)

        self.G.edges[s_label, d_label]['tested'] = True

    # Returns the best path in the graph and related path details
    def find_shortest_path(self, s_label, d_label):
        """
        Returns:
            path: list of node labels
            cost: total path cost (sum of 'metric')
            path_details: list of (label, node_attrs)
            edge_details: list of (src, dst, edge_attrs)
        """
        path = nx.dijkstra_path(self.G, s_label, d_label, weight='weight')
        cost = nx.dijkstra_path_length(self.G, s_label, d_label, weight='weight')

        path_details = [(n, self.G.nodes[n]) for n in path]
        edge_details = [
            (path[i], path[i+1], self.G.edges[path[i], path[i+1]])
            for i in range(len(path) - 1)
        ]

        return path, cost, path_details, edge_details

    # Find up to n-shortest path with cutoff hops or less
    def find_shortest_paths(self, s_label, d_label, cutoff, beam_width, weight='metric', max_metric=None):
        candidates = []
        for path in nx.all_simple_paths(self.G, s_label, d_label, cutoff=cutoff + 1):
            cost = sum(self.G[u][v][weight] for u, v in zip(path, path[1:]))
            if max_metric is not None and cost > max_metric:
                continue
            edge_path = [(u, v, self.G[u][v]) for u, v in zip(path, path[1:])]
            candidates.append((cost, edge_path))

        candidates.sort()  # Sort by cost (ascending)
        return [edge_path for _, edge_path in candidates[:beam_width]]

    # Show networkx Graph
    def show_graph(
        self,
        source=None,
        target=None,
        weight="metric",
        show_metric=False,
    ):
        pos = {
            node: (lon, lat)
            for node, (lat, lon) in nx.get_node_attributes(
                self.G,
                "pos",
            ).items()
        }

        edge_colors = ["gray"] * self.G.number_of_edges()
        edge_widths = [1] * self.G.number_of_edges()

        if source is not None and target is not None:
            path = nx.dijkstra_path(
                self.G,
                source,
                target,
                weight=weight,
            )
            path_edges = set(zip(path, path[1:]))

            for index, edge in enumerate(self.G.edges()):
                if edge in path_edges or (edge[1], edge[0]) in path_edges:
                    edge_colors[index] = "red"
                    edge_widths[index] = 2.5

        if show_metric:
            labels = {
                node: str(node)
                for node in self.G.nodes
            }
        elif source is not None and target is not None:
            lengths = nx.single_source_dijkstra_path_length(
                self.G,
                source,
                weight=weight,
            )

            labels = {
                node: f"{lengths.get(node, float('inf')):.1f}"
                for node in self.G.nodes
            }
        else:
            labels = {
                node: str(node)
                for node in self.G.nodes
            }

        nx.draw(
            self.G,
            pos=pos,
            with_labels=True,
            labels=labels,
            node_color="lightblue",
            edge_color=edge_colors,
            width=edge_widths,
            font_size=8,
        )

        if show_metric:
            edge_labels = {
                (src, dst): f"{data['metric']:.2f}"
                for src, dst, data in self.G.edges(data=True)
                if "metric" in data
            }

            nx.draw_networkx_edge_labels(
                self.G,
                pos=pos,
                edge_labels=edge_labels,
                font_size=8,
            )

        plt.show()

    # Shows the link, obstacle position and pole search region
    def show_geometry(self, s_label, d_label, poles_utm):

        # plot link endpoints in UTM for visual precision
        src_utm = self._to_utm(self.G.nodes[s_label]['pos'])
        dst_utm = self._to_utm(self.G.nodes[d_label]['pos'])

        # edge must be present at the graph before ploting
        edge_data = self.G.get_edge_data(s_label, d_label)

        if edge_data is None:
            raise ValueError(f"Edge {s_label}-{d_label} does not exist")

        obs_pos = edge_data.get("obs_pos")
        target_utm = self._to_utm(obs_pos) if obs_pos is not None else None

        # Create a LineString between src_pos and dst_pos
        line = LineString([src_utm, dst_utm])
        line_gs = gpd.GeoSeries([line], crs=self.poles_utm.crs)

        # Plot poles
        ax = poles_utm.plot(color='gray', markersize=20, marker='x')

        # Plot the line
        line_gs.plot(ax=ax, color='blue', linewidth=2)

        # ➕ Plot center point of the search area
        if target_utm is not None:
            center_gs = gpd.GeoSeries(
                [target_utm],
                crs=self.poles_utm.crs,
            )
            center_gs.plot(
                ax=ax,
                color="green",
                markersize=50,
                marker="o",
            )

        plt.title("Poles, Search Area, and Link Line")
        plt.axis('equal')
        plt.show()

    # Demonstrates a single round of planning by adding one repeater
    async def run(self, src_node, dst_node):
        ant_h = self.context['ant_h']
        obs = await self.edge_metric(
            src_node['pos'],
            dst_node['pos'],
            ant_h,
            ant_h,
        )
        self.add_edge(src_node, dst_node, obs)
        s_label = src_node['name']
        d_label = dst_node['name']
        await self.graph_expansion(s_label, d_label)
        res = self.find_shortest_path(s_label, d_label)
        self._debug(f'{res[0]} = {res[1]}')


# Private methods

    def _debug(self, msg):
        if self.context['debug']: print(msg)

    def _to_utm(self, latlon):            
        return gpd.GeoSeries([Point(latlon[1], latlon[0])], crs="EPSG:4326").to_crs(self.poles_utm.crs).iloc[0]

    def _get_or_create_node(self, node_dict):

        node_dict = node_dict.copy()
        node_dict["pos"] = self._normalize_pos(node_dict["pos"])

        label = node_dict.get('name') or node_dict.get('pole_id') # pole_id is for repeaters

        if label is None:
            label = f"Node_{self.node_counter}"
            self._debug(f'WARNING: node {label} received local ID')
            self.node_counter += 1

        created = False

        if not self.G.has_node(label):
            self.G.add_node(label, **node_dict)
            created = True

        return label, created
     
    # Search repeater in a corridor between p1 and p2
    def _rotated_rectangle(p1, p2, width):
        # search_area = rotated_rectangle(src_utm.coords[0], dst_utm.coords[0], width=500)
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = (dx**2 + dy**2)**0.5
        angle = np.degrees(np.arctan2(dy, dx))
        rect = box(0, -width/2, length, width/2)
        rotated = rotate(rect, angle, origin=(0, 0), use_radians=False)
        return translate(rotated, xoff=p1[0], yoff=p1[1])

    # Adaptative radius search
    def _adaptive_radius_filter(self, obs_pos, len_km):
        '''
        Search for poles near the obstacle position with adaptive radius expansion based on density.
        '''
        # Initial radius from context
        radius_km = max(self.context["radius_min"], self.context["radius_percent"] * len_km)
        radius_km = min(radius_km, self.context["radius_max"])
        target_utm = self._to_utm(obs_pos)
        
        while True:        
            buffer_m = radius_km * 1000
            search_area = target_utm.buffer(buffer_m)

            # Filter poles
            poles = self.poles_utm[self.poles_utm.geometry.within(search_area)].copy()
            
            if len(poles) >= self.context['radius_poles']:    
                return poles

            if radius_km >= self.context['radius_max']: 
                return poles # this link cannot be improved 
            else:
                radius_km += self.context['radius_increment']

    # Apply an angle contraint for the repeater positions searched along the link
    def _angular_filter(self, s_label, d_label, poles_in_radius):
    
        max_angle = self.context['max_angle']
        src_utm = self._to_utm(self.G.nodes[s_label]['pos'])
        dst_utm = self._to_utm(self.G.nodes[d_label]['pos'])   

        poles_in_radius["angle"] = poles_in_radius.geometry.apply(
            lambda pt: self._angles_from_path(src_utm.coords[0], dst_utm.coords[0], (pt.x, pt.y))
        )
        filtered = poles_in_radius[
            poles_in_radius["angle"].apply(lambda a: max(a) <= max_angle)
        ]

        if len(filtered) >= self.context['angle_poles']:
            poles_in_radius = filtered
        else: # complete the remaining poles sorted by angle
            extra = poles_in_radius[~poles_in_radius.index.isin(filtered.index)]
            extra_sorted = extra.sort_values(by="angle", key=lambda s: s.apply(max))
            poles_in_radius = pd.concat([filtered, extra_sorted]).head(self.context['angle_poles'])
        
        return poles_in_radius

    # Return poles around the highest obstacles and angle restriction to the straight path
    async def _get_poles_by_dual_cone(self, s_label, d_label):
        '''
        For a given link already in the graph, search for poles within a radius of ~n km,
        and return up to test_poles with the highest elevation.
        '''
        obs_pos = self.G.get_edge_data(s_label, d_label, {}).get('obs_pos')
        len_dm = self.G.get_edge_data(s_label, d_label, {}).get('dist_m')

        if obs_pos is None:
            if self.context.get("search_midpoint"):
                obs_pos = self._edge_midpoint(s_label, d_label)
            else:
                self._debug(
                    f"Edge {s_label}-{d_label} has no terrain obstruction; "
                    "expansion skipped"
                )
                return self.poles_utm.iloc[0:0].copy()                

        # Filter poles within the buffer        
        poles_in_radius = self._adaptive_radius_filter(obs_pos, len_dm/1000)
        
        if poles_in_radius.empty:
            self._debug(f'Warning: {s_label} {d_label} accused empty poles')
            return poles_in_radius # Nothing to do

        # Add angle constraint if present
        if self.context['cone_angle'] > 0: 
            poles_in_radius = self._filter_poles_by_direction(s_label, d_label, poles_in_radius)

        # Add elevation (assuming self.geo.get_elevation(lat, lon))
        points = poles_in_radius["pos"].tolist()
        elevations = await self.geo.point_samples(
            points,
            ds_string="DTM",
            nodata_value=0,
        )

        poles_in_radius["elevation"] = elevations

        # Calculate antenna elevation - the effective criteria for pole/tower selection
        default_ant_h = self.context["ant_h"]

        if "ant_h" in poles_in_radius.columns:
            poles_in_radius["candidate_ant_h"] = (
                poles_in_radius["ant_h"].fillna(default_ant_h)
            )
        else:
            poles_in_radius["candidate_ant_h"] = default_ant_h

        poles_in_radius["antenna_elevation"] = (
            poles_in_radius["elevation"]
            + poles_in_radius["candidate_ant_h"]
        )

        return poles_in_radius.nlargest(self.context['cone_poles'], "elevation")
    
    # Return poles spread into different directions around the obstacle
    async def _get_poles_by_sector_band(self, s_label, d_label):
        """
        Selects poles around the highest obstacle, excluding two opposite sectors centered on the s→d path
        and dividing the remaining directions into uniform angular sectors.
        """

        # Step 1: Get obstacle and length
        edge_data = self.G.get_edge_data(s_label, d_label, {})
        obs_pos = edge_data.get('obs_pos')
        len_dm = edge_data.get('dist_m')

        if obs_pos is None:
            if self.context.get("search_midpoint"):
                obs_pos = self._edge_midpoint(s_label, d_label)
            else:
                self._debug(
                    f"Edge {s_label}-{d_label} has no terrain obstruction; "
                    "expansion skipped"
                )
                return self.poles_utm.iloc[0:0].copy() 

        # Step 2: Find candidate poles in radius
        poles_in_radius = self._adaptive_radius_filter(obs_pos, len_dm / 1000)
        if poles_in_radius.empty:
            self._debug(f'Warning: {s_label} {d_label} returned empty poles')
            return poles_in_radius

        if self.context['cone_angle'] > 0:
            poles_in_radius = await self._get_poles_by_dual_cone(
                s_label,
                d_label,
            )

        # Step 3: Compute base angle (s→d direction)
        s_utm = self._to_utm(self.G.nodes[s_label]['pos'])
        d_utm = self._to_utm(self.G.nodes[d_label]['pos'])
        obs_utm = self._to_utm(obs_pos)
        dx = d_utm.x - s_utm.x
        dy = d_utm.y - s_utm.y
        base_angle = (degrees(atan2(dy, dx)) + 360) % 360

        # Step 4: Compute relative angle from obstacle to pole
        cx, cy = obs_utm.x, obs_utm.y

        def relative_angle(p):
            dx = p.geometry.x - cx
            dy = p.geometry.y - cy
            angle = (degrees(atan2(dy, dx)) + 360) % 360
            return (angle - base_angle + 360) % 360

        poles_in_radius["angle"] = poles_in_radius.apply(relative_angle, axis=1)

        # Step 5: Exclude two opposite sectors and define usable ones
        exclude_width = self.context['sector_exclusion']  # degrees
        n_sectors = self.context['sector_count']
        poles_per_sector = self.context['sector_poles']

        half_width = exclude_width / 2
        excluded_ranges = [(360 - half_width, half_width),  # centered at 0°
                        (180 - half_width, 180 + half_width)]  # centered at 180°

        # Mask poles in excluded sectors
        def is_excluded(angle):
            for start, end in excluded_ranges:
                if start < end:
                    if start <= angle < end:
                        return True
                else:
                    if angle >= start or angle < end:
                        return True
            return False

        poles_in_radius = poles_in_radius[~poles_in_radius["angle"].apply(is_excluded)]

        # Step 6: Divide usable span and assign sectors
        usable_span = 360 - 2 * exclude_width
        sector_width = usable_span / n_sectors
        start_angle = exclude_width + sector_width / 2  # first usable sector starts after excluded zone

        # Step 7: update filtering columns
        def sector_id(angle):
            shifted = (angle - start_angle + 360) % 360
            return int(shifted // sector_width)

        points = poles_in_radius["pos"].tolist()
        elevations = await self.geo.point_samples(
            points,
            ds_string="DTM",
            nodata_value=0,
        )

        # Update pole base and antenna height
        poles_in_radius["elevation"] = elevations

        default_ant_h = self.context["ant_h"]

        if "ant_h" in poles_in_radius.columns:
            poles_in_radius["candidate_ant_h"] = (
                poles_in_radius["ant_h"].fillna(default_ant_h)
            )
        else:
            poles_in_radius["candidate_ant_h"] = default_ant_h

        poles_in_radius["antenna_elevation"] = (
            poles_in_radius["elevation"]
            + poles_in_radius["candidate_ant_h"]
        )

        # Implement sector filtering
        poles_in_radius["sector"] = poles_in_radius["angle"].apply(sector_id)
        poles_in_radius["distance"] = poles_in_radius.geometry.distance(obs_utm)
        n_bands = self.context.get("score_band", 3)  # default to 3 if not defined
        max_dist = poles_in_radius["distance"].max()
        band_width = max_dist / n_bands
        poles_in_radius["band"] = (poles_in_radius["distance"] // band_width).clip(upper=n_bands - 1).astype(int)

        # Step 8: Collect top poles per sector
        selected = []

        for sector in range(n_sectors):
            subset = poles_in_radius[poles_in_radius["sector"] == sector]
            subset = subset.sort_values(
                "antenna_elevation",
                ascending=False,
            ).copy()

            # Build band queues
            band_queues = {b: [] for b in range(n_bands)}
            for _, row in subset.iterrows():
                band_queues[row["band"]].append(row)

            picked = []
            while len(picked) < poles_per_sector:
                advanced = False
                for b in range(n_bands):
                    if band_queues[b]:
                        picked.append(band_queues[b].pop(0))
                        advanced = True
                        if len(picked) >= poles_per_sector:
                            break
                if not advanced:
                    break  # all queues exhausted

            if picked:
                selected.append(gpd.GeoDataFrame(picked, crs=poles_in_radius.crs))


        return pd.concat(selected) if selected else poles_in_radius.iloc[0:0]

    # Given a (lon, lat) tuple, returns the (lat, lon) coordinates of the nearest pole.
    def _nearest_pole(self, lon_lat):
        '''
        Given a (lon, lat) tuple, returns the (lat, lon) coordinates of the nearest pole.
        '''
        target_point = Point(lon_lat)           
        target_point = gpd.GeoSeries([target_point], crs="EPSG:4326").to_crs(self.poles_utm.crs).iloc[0]

        self.poles_utm['distance'] = self.poles_utm.geometry.distance(target_point)

        nearest_entry = self.poles.loc[self.poles_utm['distance'].idxmin()]
        nearest_coords = nearest_entry.geometry.coords[0]
        return nearest_coords[1], nearest_coords[0]
  
    # Determines the direction from src_utm to pos_utm with respect to dst_utm
    def _angles_from_path(self, a, b, p):
        # Angles higher than 90 may be in the wrong direction (answer between 0 and 180)
        v = np.array(b) - np.array(a)
        u1 = np.array(p) - np.array(a)
        u2 = np.array(p) - np.array(b)

        def angle_between(u, v):
            norm_u = np.linalg.norm(u)
            norm_v = np.linalg.norm(v)
            if norm_u == 0 or norm_v == 0:
                return 0.0  # or float('nan') depending on how you want to handle it
            cos = np.dot(u, v) / (norm_u * norm_v)
            return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))

        return angle_between(u1, v), angle_between(u2, -v)

    # returns with tree points and obstacle   
    def _edge_to_gdf_points(self, s_label, d_label):
        records = []

        # Add source and destination nodes
        for label in [s_label, d_label]:
            lat, lon = self.G.nodes[label]['pos']
            records.append({
                'type': 'endpoint',
                'node': label,
                'geometry': Point(lon, lat),
                'iconType' : 'new'
            })

        # Add obstacle points (if any)
        if 'obs_pos' in self.G[s_label][d_label]:
                lat, lon = self.G[s_label][d_label]['obs_pos']
                records.append({
                    'type': 'obstacle',
                    'node': 'obs',
                    'geometry': Point(lon, lat),
                    'iconType' : 'obstacle'
                })

        return gpd.GeoDataFrame(records, crs="EPSG:4326")

    # obstruct is missing
    def _edge_midpoint(self, s_label, d_label):
        s_pos = self.G.nodes[s_label]["pos"]
        d_pos = self.G.nodes[d_label]["pos"]

        obs_pos = [
            (s_pos[0] + d_pos[0]) / 2,
            (s_pos[1] + d_pos[1]) / 2,
        ] 

        return obs_pos      

    # this will required update when new features are added (TODO)
    def _top_peak_coordinates(self, src_pos, dst_pos, features):
        peaks = features["terrain_peaks_vv"]        

        if not peaks:
            obs_position  = None
        else:
            peak = max(peaks, key=lambda item: item["v_v"])

            src_lat, src_lon = src_pos
            dst_lat, dst_lon = dst_pos

            azimuth, _, _ = _GEOD.inv(
                src_lon,
                src_lat,
                dst_lon,
                dst_lat,
            )

            obs_distance = peak["d_norm"] * features["dist_m"]

            obs_lon, obs_lat, _ = _GEOD.fwd(
                src_lon,
                src_lat,
                azimuth,
                obs_distance,
            )

            obs_position = (obs_lat, obs_lon)
        
        return obs_position


#---------------------------------------------------------------------
if __name__ == '__main__':

    pass
