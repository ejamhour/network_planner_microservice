from cisei_lib.usermng.homeFolder import user_home
from cisei_lib.planners.pole_graph_async import PoleGraph
import geopandas as gpd
import pandas as pd
from cisei_lib.planners.serializer import Serializer
from cisei_lib.planners.planner_classes import LinkNode, PlannerNode
from copy import deepcopy


# Segment-based LOS Repair with Infrastructure Constraints.
#
# This class plans virtual links by progressively inserting repeaters at available pole positions
# to restore line of sight between fixed endpoints. The process begins with a direct link, evaluates
# obstruction, and refines the path by segment, one repeater at a time.
#
# Antennas are assigned after the path is defined. The algorithm prioritizes omni-directional antennas,
# introducing directional antennas and B2B nodes only when link quality cannot be achieved otherwise.
#
# All decisions are constrained by pole availability, RF model predictions, and configuration parameters.
# While deterministic, the method does not explore global alternatives or backtrack, making it simple
# but limited. ETX estimates are theoretical and will later be revised by real-world measurements.

MIN_EDGE_METRIC = 1.0
REPEATER_BREAK_EVEN_METRIC = 2 * MIN_EDGE_METRIC


class LinkPlanner(PoleGraph): #[tour:linkplanner]

    # Initializes the planning context: loads poles, config, and RF model for a specific project.
 
    def __init__(
        self,
        project: str,
        user_id: str,
        **kwargs,
    ):
        self.user_id = user_id
        self.home = user_home(user_id)
        self.project = project

        self.path = None
        self.nodes = {}

        self.s_label = None
        self.d_label = None

        DEFAULT_CONTEXT = {
            "radio_model": "GE-NX",
            "max_repeaters": 5,
            "max_candidates": 5,
        }

        context = {
            **DEFAULT_CONTEXT,
            **kwargs,
        }

        self.node_catalog = gpd.GeoDataFrame(
            columns=["node_id", "name", "pos", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

        super().__init__(**context)

        self.serializer = Serializer(
            folder=self.home.get_project_path(project, "."),
            sub_folder="link-planner",
        )

    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False


    # --- Data input methods ---

    def add_node(self, node: PlannerNode) -> None:
        self.add_nodes([node])

    def add_nodes(
        self,
        nodes: list[PlannerNode],
        replace: bool = False,
    ) -> None:
        records = [node.to_record() for node in nodes]

        new_gdf = gpd.GeoDataFrame(
            records,
            geometry="geometry",
            crs="EPSG:4326",
        )

        duplicated = set(new_gdf["node_id"]) & set(self.node_catalog["node_id"])

        if duplicated and not replace:
            raise ValueError(f"Nodes already exist: {sorted(duplicated)}")

        if replace and duplicated:
            self.node_catalog = self.node_catalog[
                ~self.node_catalog["node_id"].isin(duplicated)
            ]

        self.node_catalog = gpd.GeoDataFrame(
            pd.concat(
                [self.node_catalog, new_gdf],
                ignore_index=True,
            ),
            geometry="geometry",
            crs="EPSG:4326",
        )

    async def set_link(self, src: LinkNode, dst: LinkNode):
        kwargs = {
            "s_info": src.extra or None,
            "d_info": dst.extra or None,
        }

        if src.ant_h is not None:
            kwargs["s_ha"] = src.ant_h

        if dst.ant_h is not None:
            kwargs["d_ha"] = dst.ant_h

        await self._set_link(
            src.name,
            src.pos,
            dst.name,
            dst.pos,
            **kwargs,
        )
  
    async def set_link_by_id(self, src_id: str, dst_id: str) -> None:
        src = self._get_input_node(src_id)
        dst = self._get_input_node(dst_id)

        kwargs = {
            "s_info": src.get("extra"),
            "d_info": dst.get("extra"),
        }

        if pd.notna(src.get("ant_h")):
            kwargs["s_ha"] = float(src["ant_h"])

        if pd.notna(dst.get("ant_h")):
            kwargs["d_ha"] = float(dst["ant_h"])

        await self._set_link(
            src["name"],
            src["pos"],
            dst["name"],
            dst["pos"],
            **kwargs,
        )

    # Initializes a link using point geometries from a GeoDataFrame (obsolete).
    async def set_link_gdf(self, gdf, src_name, dst_name):
        if gdf.crs is None:
            raise ValueError("The GeoDataFrame must have a CRS")

        if gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")

        src_row = gdf.loc[gdf["name"] == src_name].iloc[0]
        dst_row = gdf.loc[gdf["name"] == dst_name].iloc[0]

        src = LinkNode(
            name=src_name,
            lat=src_row.geometry.y,
            lon=src_row.geometry.x,
            ant_h=src_row.get("ant_h"),
        )

        dst = LinkNode(
            name=dst_name,
            lat=dst_row.geometry.y,
            lon=dst_row.geometry.x,
            ant_h=dst_row.get("ant_h"),
        )

        await self.set_link(src, dst)

    # --- Internal methods ---

    # Returns the worst edge in the currest best path not tested yet.
    def get_worst_edge(self, edges) -> tuple[list | None, bool ]: #[tour:edge improvement]
        '''
        Returns (edge, needs_improvement):
        - None, False → all tested
        - edge, True  → metric exceeds the repeater break-even cost
        - edge, False → metric is at or below the repeater break-even cost
        '''
        worst = float('-inf')
        edge = None

        for e_info in edges:
            e = (e_info[0], e_info[1])
            i = e_info[2]
            if not i['tested'] and i.get('metric', float('inf')) > worst: 
                edge = e        
                worst = i['metric']

        if self.context['debug']:
            print(f'worst {edge} = {worst}')

        if worst <= REPEATER_BREAK_EVEN_METRIC:
            return edge, False
        
        return edge, True
   
    # Attempts to repair the most obstructed segment by inserting a repeater. 
    async def improve_link(self, edge_path) -> bool:
        '''
        Iterative improvement. Returns False if no edge needs improvement.
        '''
        edge, needs_improvement = self.get_worst_edge(edge_path)
        
        if edge is None or not needs_improvement:
            return False

        await self.graph_expansion(*edge)
        return True

    # Controls the full refinement process: calls improve_link() iteratively until convergence or repeater limit.
    async def plan_link(self):
        '''
        this method updates self.path and self.nodes
        '''
        if self.poles.empty:
            raise RuntimeError("No candidate poles were added")

        for k in range(self.context['max_repeaters'] + 1): # [tour:main loop]
                
            edge_paths = self.find_shortest_paths(self.s_label, self.d_label, k , self.context['max_candidates'])  

            if len(edge_paths) == 0:
                raise Exception(f'The graph has not feasible candidate path')
            
            flags = []
            for edge_path in edge_paths:
                flags.append(await self.improve_link(edge_path))

            if not any(flags): # graph was not expanded
               break               

            # Check if the solution converged to a feasible path
            all_metrics_ok = any(
                all(attr['metric'] <= REPEATER_BREAK_EVEN_METRIC for _, _, attr in edge_path)
                for edge_path in edge_paths
            )
            if all_metrics_ok: break # no need of improvement


        # run find_shortest path to update self.path
        res = self.find_shortest_path(self.s_label, self.d_label)
        self.path = res[3]

        for node in res[2]:
            if node[0] not in self.nodes:
                self._create_node(**node[1]) # using name and pos from kwargs.
        
        for h in list(zip(res[0], res[0][1:])):
            self.nodes[h[0]]['next_hop'] = h[1]

        return {
            "s_label": self.s_label,
            "d_label": self.d_label,
            "node_path": list(res[0]),
            "nodes": deepcopy(self.nodes),
            "edges": deepcopy(self.path),
        }            

    # Returns the node path from source to destination, optionally reversed.
    def get_nodes(self, reversed=True):
        path = [ self.s_label ]
        while path[-1] != self.d_label:
            path.append(self.nodes[path[-1]]['next_hop'])          
        if reversed: path.reverse()
        return {p: self.nodes[p] for p in path }

    # --- Output Methods ---

    # Save current plan and Graph
    def serialize_plan(self,**kwargs): # TODO: serialize async_pole_graph cahce
        self.serializer.context['append'] = kwargs.get('append', False)
        self.serializer.context['pack_filename'] = 'best_path'
        keys = ['s_label', 'd_label', 'nodes', 'path']  
        data = {k: self.__dict__[k] for k in keys}        
        self.serializer.serialize_bin(**data)         
        self.serializer.context['pack_filename'] = 'graph'        
        self.serializer.serialize_G(self.G) 

    # Retrieve plan and Graph
    def deserialize_plan(self, idx=None):
        # TODO: Check if cached _context matches self.context before deserializing; warn or discard if mismatch
        self.serializer.context['pack_filename'] = 'best_path'        
        data = self.serializer.deserialize_bin(idx)         
        self.__dict__.update(data)
        self.serializer.context['pack_filename'] = 'graph'        
        self.G = self.serializer.deserialize_G(idx) 

    # Export GeoJSON
    def export_geojson(self,**kwargs):
        self.serializer.context['append'] = kwargs.get('append', False)
        # Append reads the existing GeoJSON before adding new nodes
        
        self.serializer.to_geojson_nodes(self.nodes)
        self.serializer.to_geojson_links(self.path, self.nodes)

    def export_geojson_plan(self, **kwargs):
        self.serializer.context["append"] = kwargs.get("append", False)
        return self.serializer.to_geojson_plan(self.path, self.nodes)

    # Folium object that creates and interactive map
    def interactive_map(self):
        nodes_gdf = self.serializer.to_geojson_nodes(self.nodes)
        links_gdf = self.serializer.to_geojson_links(self.path, self.nodes)

        m = links_gdf.explore(name="Links")
        nodes_gdf.explore(m=m, name="Nodes")

        return m

    # Encapsulates the main execution of the class for planning a link
    async def run(self, nodes_gdf, s_label, d_label, show=False):
        await self.set_link_gdf(nodes_gdf, s_label, d_label)
        result = await self.plan_link()
        if show:
             self.show_graph(s_label, d_label)
        return result

    # --- Private methods ---

    # Creates nodes representing the final solution
    def _create_node(self, name, pos, **kwargs):

        ant_h = kwargs.get("ant_h")
        if ant_h is None:
            ant_h = self.context["ant_h"]

        self.nodes[name] = {
            'name' : name,
            'pos' : pos,
            'next_hop' : kwargs.get('next_hop',  None), # OBS. best path next_hop
            'link_mode' : kwargs.get('link_mode', self.context['radio_model']),
            'ant_h' : ant_h,
            'hop_type' : kwargs.get('hop_type', 'rep')
        }

        info = kwargs.get('info', None)
        if info: self.nodes[name].update(info)

    def _get_input_node(self, node_id: str) -> dict:
        rows = self.node_catalog[
            self.node_catalog["node_id"] == node_id
        ]

        if rows.empty:
            raise KeyError(f"Unknown node: {node_id}")

        return rows.iloc[0].to_dict()
    
    # Initializes a new point-to-point link plan with fixed endpoints (lat, lon).
    async def _set_link(self, s_label, s_pos, d_label, d_pos, **kwargs):
        '''
        Direction is upstream toward the POP. Computes initial LOS and obstruction from source.
        WARNING: input positions must be (lat, lon); GeoJSON uses (lon, lat).
        '''

        self.path = None
        self.nodes = {}
        self.G.clear()
        self.virtual_edges = []
        
        self.s_label = s_label
        self.d_label = d_label
        self.virtual_edges.append((s_label, d_label))

        # Source node
        s_args = {
            'next_hop': d_label,
            'hop_type': 'src',
            'ant_h': kwargs.get('s_ha', self.context['ant_h']),
            'info': kwargs.get('s_info')
        }
        self._create_node(s_label, s_pos, **{k: v for k, v in s_args.items() if v is not None})

        # Destination node
        d_args = {
            'next_hop': None,
            'hop_type': 'dst',
            'ant_h': kwargs.get('d_ha', self.context['ant_h']),
            'info': kwargs.get('d_info')
        }
        self._create_node(d_label, d_pos, **{k: v for k, v in d_args.items() if v is not None})        

        edge_info = await self.edge_metric(s_pos, d_pos, s_args['ant_h'], d_args['ant_h'])        
        self.add_edge(self.nodes[s_label], self.nodes[d_label], edge_info)

    # Displays each segment's LOS and antenna configuration (visual debug).        
    def _show_link(self, s_label):         
         while True:
            start = self.nodes[s_label]
            d_label = start['next_hop']
            if d_label is None: break
            end = self.nodes[d_label]
            print(s_label, d_label)                        
            self.geo.show_link(start['pos'], end['pos'], ha_s=start['ant_h'], ha_d = end['ant_h'] )
            s_label = d_label


    def _load_context(self, context, flat, inplace=True):
        for k in flat:
            if k in context:
                raise KeyError(f"Key conflict: '{k}' already exists in context")
        if inplace:
            context.update(flat)
            return context
        else:
            return {**context, **flat}

            
#---------------------------------------------------------------------
if __name__ == '__main__':

    
    '''
    find the minimum number of repeaters to create LOS link
    '''

    start = (-26.0653893340446, -49.4545370100964)
    end = (-26.086031876627, -49.44739813282138)
    htx = hrx = 7

    with LinkPlanner('AGU-S') as x:
        x.set_link('A', start, 7, 'B', end, 7)
        x.plan_link(n=2)
        # x.show_link(x.s_label)
        x.to_geoJSON_nodes()
        
   



    

   

