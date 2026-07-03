import cisei_lib.cli.elevation.rfModel as rfModel
from cisei_lib.cli.usermng.homeFolder import user_home
from cisei_lib.cli.usermng.configTools import configRadio
from cisei_lib.cli.bhnplanner.pole_graph import PoleGraph
import geopandas as gpd
from cisei_lib.cli.bhnplanner.serializer import Serializer


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


class LinkPlanner(PoleGraph): #[tour:linkplanner]

    # Initializes the planning context: loads poles, config, and RF model for a specific project.
    def __init__(self, project, home: user_home, **kwargs):   

        self.home = home
        self.project = project

        # Set radio_model manually after initialization to use other models

        self.conf = configRadio(home, user_file=kwargs.get('config_file', None))
        self.rf = rfModel.rfModel(cover_height=self.conf.get('ENVIRONMENT.cover_height'))  
        
        self.path = None # type: list[tuple[str, str, dict]] #[tour:self.plan]
        self.nodes = {}  # {label = {pos, obs, tested} }
        
        self.s_label = self.d_label = None # current edge

        self._cached_edges = {} 
       
        poles_file = self.home.get_project_path(project, 'poles_geo.json')            
        poles_gdf = gpd.read_file(poles_file)

        DEFAULT_CONTEXT = {
            'radio_model': self.conf.get('BACKHAUL.radio.model'),
            'ant_h': self.conf.get('BACKHAUL.radio.default_height'),
        }

        self._load_context(DEFAULT_CONTEXT, self.conf.flatten(self.conf.get('BACKHAUL.optimization.pole_search')))
        self._load_context(DEFAULT_CONTEXT, self.conf.flatten(self.conf.get('BACKHAUL.optimization.link_planner')))

        context = {**DEFAULT_CONTEXT, **kwargs}      

        PoleGraph.__init__(self, poles_gdf, **context)    # initializes polesGraph (i.e., self.G and indexing structures)

        #[tour:serializer]
        self.serializer = Serializer(**{       
            'folder': home.get_project_path(project, '.'),
            'sub_folder': 'link-planner'                   
        })

    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):

        if exc_type:
            print(f"Exception: {exc_type}, {exc_val}")
        return False  # Return True if you want to suppress exceptions

    # --- Overrides ---

    def get_cached_quality(self, s_label, d_label):
        edge_label = f'{s_label}:{d_label}'
        if edge_label in self._cached_edges:
            print(f'retrieved {s_label} {d_label} from cache ')
        return self._cached_edges.get(edge_label) 

    def set_cached_quality(self, s_label, d_label, quality : dict):
        edge_label = f'{s_label}:{d_label}'
        self._cached_edges[edge_label] = quality

    # --- Specialization ---

    # Initializes a link using point geometries from a GeoDataFrame (GeoJSON-style).
    def set_link_gdf(self, gdf, s_label, d_label):

        if gdf.crs != "EPSG:4236":
            gdf = gdf.to_crs("EPSG:4326")
        
        s_pos = gdf[gdf["name"] == s_label].geometry.iloc[0] 
        d_pos = gdf[gdf["name"] == d_label].geometry.iloc[0]

        # PoleGraph use nodes as lists and not tuples because of serialization
        self._set_link(s_label, [s_pos.y, s_pos.x], d_label, [d_pos.y, d_pos.x])

    # Overhide the method in the parent class to support alternative metrics 
    def edge_metric(self, src_pos, dst_pos, src_ha, dst_ha): #[tour:override edge_metric]
        
        if self.context['metric'] == 'improve_clearance':
            return super().edge_metric(src_pos, dst_pos, src_ha, dst_ha)

        raise Exception('evaluate_repeater: unknown metric')
   
    # Returns the worst edge in the currest best path not tested yet.
    def get_worst_edge(self, edges) -> tuple[list | None, bool ]: #[tour:edge improvement]
        '''
        Returns (edge, needs_improvement):
        - None, False → all tested
        - edge, True  → metric > threshold
        - edge, False → metric ≤ threshold
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

        if worst <= self.context['metric_threshold']:  # this segment does not need improvement
            return edge, False
        
        return edge, True
   
    # Attempts to repair the most obstructed segment by inserting a repeater. 
    def improve_link(self, edge_path) -> bool:
        '''
        Iterative improvement. Returns False if no edge needs improvement.
        '''
        edge, needs_improvement = self.get_worst_edge(edge_path)
        
        if edge is None or not needs_improvement:
            return False

        self.graph_expansion(*edge)  # [tour:expand graph]
        return True

    # Controls the full refinement process: calls improve_link() iteratively until convergence or repeater limit.
    def plan_link(self):
        '''
        this method updates self.path and self.nodes
        '''
        
        for k in range(self.context['max_repeaters']): # [tour:main loop]
                
            edge_paths = self.find_shortest_paths(self.s_label, self.d_label, k , self.context['max_candidates'])  

            if len(edge_paths) == 0:
                raise Exception(f'The graph has not feasible candidate path')
            
            flags = [ self.improve_link(edge_path) for edge_path in edge_paths ]

            if not any(flags): # graph was not expanded
               break               

            # Check if the solution converged to a feasible path
            all_metrics_ok = any(
                all(attr['metric'] < self.context['metric_threshold'] for _, _, attr in edge_path)
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

    # Returns the node path from source to destination, optionally reversed.
    def get_nodes(self, reversed=True):
        path = [ self.s_label ]
        while path[-1] != self.d_label:
            path.append(self.nodes[path[-1]]['next_hop'])          
        if reversed: path.reverse()
        return {p: self.nodes[p] for p in path }

    # Save current plan and Graph
    def serialize_plan(self,**kwargs):
        self.serializer.context['append'] = kwargs.get('append', False)
        self.serializer.context['pack_filename'] = 'best_path'
        keys = ['s_label', 'd_label', 'nodes', 'path', '_cached_edges']  
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

    # Encapsulates the main execution of the class for planning a link
    def run(self, nodes_gdf, s_label, d_label, show = False): #[tour:start point]
        self.set_link_gdf( nodes_gdf, s_label, d_label)
        self.plan_link()
        if show:
            self.show_graph(s_label, d_label) 

# Private methods

    # Creates nodes representing the final solution
    def _create_node(self, name, pos, **kwargs):

        self.nodes[name] = {
            'name' : name,
            'pos' : pos,
            'next_hop' : kwargs.get('next_hop',  None), # OBS. best path next_hop
            'link_mode' : kwargs.get('link_mode', self.context['radio_model']),
            'ant_h' : self.context['ant_h'],
            'hop_type' : kwargs.get('hop_type', 'rep')
        }

        info = kwargs.get('info', None)
        if info: self.nodes[name].update(info)

    # Initializes a new point-to-point link plan with fixed endpoints (lat, lon).
    def _set_link(self, s_label, s_pos, d_label, d_pos, **kwargs):
        '''
        Direction is upstream toward the POP. Computes initial LOS and obstruction from source.
        WARNING: input positions must be (lat, lon); GeoJSON uses (lon, lat).
        '''

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

        edge_info = self.edge_metric(s_pos, d_pos, s_args['ant_h'], d_args['ant_h'])        
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




class AntennaPlanner():

    def __init__(self, project, home: user_home, **kwargs):  
        Serializer.__init__()

        self.home = home
        self.project = project    

        self.conf = configRadio(home, user_file=kwargs.get('config_file', None))
        self.rf = rfModel.rfModel(cover_height=self.conf.get('ENVIRONMENT.cover_height'))   

        DEFAULT_CONTEXT = {
            'radio_power' : self.conf.get('BACKHAUL.radio.power'),
            'tree_model' : self.conf.get('ENVIRONMENT.trees.tree_model'),
            'tree_delta' : self.conf.get('ENVIRONMENT.trees.tree_delta'),
            'level' : self.conf.get('ENVIRONMENT.trees.tree_level')
        }

        self.context = {**DEFAULT_CONTEXT, **kwargs}      
     
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):

        if exc_type:
            print(f"Exception: {exc_type}, {exc_val}")
        return False  # Return True if you want to suppress exceptions

    # Estimates link quality using the RF model. Can exclude antenna gains to support antenna planning.
    def set_path_nodes(self, nodes):
        self.nodes = nodes

    # Estimates link quality using the RF model. Can exclude antenna gains to support antenna planning.
    def link_quality(self, src_label, dst_label, ant=True):

        src_pos = self.nodes[src_label]['pos']
        dst_pos = self.nodes[dst_label]['pos']   
        
        ga1 = 0 if not ant else self.nodes[src_label].get('ant_dB', 0)
        ga2 = 0 if not ant else self.nodes[dst_label].get('ant_dB', 0)
   
        args = {
            'ha1' : self.nodes[src_label]['ant_h'],
            'ha2' : self.nodes[dst_label]['ant_h'],
            'ga1': ga1,
            'ga2': ga2,
            'pw' : self.conf.get('BACKHAUL.radio.power'),
            'tm' : self.conf.get('ENVIRONMENT.trees.tree_model'),
            'td' : self.conf.get('ENVIRONMENT.trees.tree_delta'),
            'level' : self.conf.get('ENVIRONMENT.trees.tree_level')
        }
        
        lqos = self.rf.link_quality(src_pos, dst_pos, **args)

        return lqos
    
    # Determines whether a new antenna can be assigned directly or requires B2B node insertion.
    def b2b_test(self, label, ant):
        # None (don't change), False (replace antenna), True (B2B)

        # node already has an omny antenna with higher gain --> do nothing
        cond0 = lambda label : 'ant_type' in self.nodes[label] and self.nodes[label]['ant_type'] == 'omni' and ant['type'] == 'omni' and self.nodes[label]['ant_dB'] >= ant['gain']
        # node has no antenna --> add new antenna to node 
        cond1 = lambda label : 'ant_type' not in self.nodes[label]
        # node has an omni antenna with a gain lower than the new omni antenna --> replace the old antenna 
        cond2 = lambda label : 'ant_type' in self.nodes[label] and self.nodes[label]['ant_type'] == 'omni' and ant['type'] == 'omni' and self.nodes[label]['ant_dB'] < ant['gain']
        # node has any type of antenna and the new antenna is directional --> add antenna to a new B2B radio and calculate azimute
        cond3 = lambda label: 'ant_type' in self.nodes[label] and ant['type'] == 'directional'
        # node has a directional antenna --> add antenna to a new B2B radio
        cond4 = lambda label: 'ant_type' in self.nodes[label] and self.nodes[label]['ant_type'] == 'directional'

        if cond0(label):
            return None
        if cond1(label) or cond2(label):
            return False    # no B2B required - assign antenna to the same node
        elif cond3(label) or cond4(label):
            return True

    # Selects and assigns antennas to meet RSSI target. Prefers omni antennas; uses directional and B2B only when required.
    def link_antennas(self, s_label, antennas = None):

        next = self.nodes[s_label]['next_hop']

        if next is None: 
            return False               

        if antennas is None:
            antennas = self.conf.get('BACKHAUL.antennas')       
        
        # calculate the link quality
        lqos = self.link_quality(s_label, next, False)
   
        # calculate required sum of the gain of both antennas
        ga_total = self.conf.get('BACKHAUL.radio.target_rssi') - lqos['rssi']
        ants = sorted(antennas, key = lambda x : x['gain']) 
        ga1 = ga2 = ants[0]

        for a in ants:
            ga1 = a # highest gain
            if ga1['gain'] + ga2['gain'] > ga_total:
                break
            ga2 = a
            if ga1['gain'] + ga2['gain'] > ga_total:
                break            

        # highest gain is for source node if antennas are omni, otherwise destination node 
        ant = {s_label : ga1,  next : ga2 } if ga1['type'] == 'ommi' else {s_label : ga2,  next : ga1 }
        self.nodes[s_label]['rssi'] = ga1['gain'] + ga2['gain'] + lqos['rssi'] 
        # OBS. received rssi assingned to the source node (simetric link)
        
        # - B2B nodes keep the same label followed by numbers 1, 2, 3, etc.
        par_map = list(zip(['ant_dB', 'ant_type', 'ant_name'], ['gain','type','name']))  
        
        # Destination node antenna selection
        b2bQ = self.b2b_test(next, ant[next])
        
        # already has a better antenna
        if b2bQ is not None:

            # new antenna in the same radio (does not change next_hop!)       
            if b2bQ is False:                    
                for a,b in par_map: self.nodes[next][a] = ant[next][b]               
                b2b_label_d = None
            # new antenna requires B2B - new node is downstream (b2b connection to the original node)
            if b2bQ is True: # this condition arises when connecting to an existent backhaul  (TODO: test this condition!)          
                # update the original node                
                if 'id_b2b' not in self.nodes[next]: self.nodes[next]['id_b2b'] = []
                b2b_label_d = f"{next}_{len(self.nodes[next]['id_b2b'])+1}" 
                self.nodes[next]['id_b2b'].append(b2b_label_d)     
                # create b2b node
                self.create_node(b2b_label_d, self.nodes[next]['pos'], next_hop=next, link_mode='b2b')                     
                for a,b in par_map: self.nodes[b2b_label_d][a] = ant[next][b] # receiver from downstream
                

        # Source node antenna selection
        b2bQ = self.b2b_test(s_label, ant[s_label])

        # already has a better antenna
        if b2bQ is not None:

            # new antenna in the same radio (check if next_hop was changed!)                      
            if b2bQ is False:        
                for a,b in par_map: self.nodes[s_label][a] = ant[s_label][b]   
                if b2b_label_d: self.nodes[s_label]['next_hop'] = b2b_label_d             
            # new antenna requires B2B - new node is upstream (RF connection to the next node)    
            if b2bQ is True:
                # update the original node
                if 'id_b2b' not in self.nodes[s_label]: self.nodes[s_label]['id_b2b'] = []                
                b2b_label_s = f"{s_label}_{len(self.nodes[s_label]['id_b2b'])+1}" 
                self.nodes[s_label]['id_b2b'].append(b2b_label_s) 
                self.nodes[s_label]['next_hop'] = b2b_label_s
                self.nodes[s_label]['link_mode'] = 'b2b'
                rssi = self.nodes[s_label].pop('rssi',None)
                # create b2b node
                next_hop = b2b_label_d if b2b_label_d is not None else next 
                self.create_node(b2b_label_s, self.nodes[s_label]['pos'], next_hop = next_hop)           
                for a,b in par_map: self.nodes[b2b_label_s][a] = ant[s_label][b] # transmitter to upstream 
                if rssi is not None: self.nodes[b2b_label_s]['rssi'] = rssi

    # Sets antennas for all segments in the current path.
    def path_antennas(self, antennas = None):
        # clear antennas and B2B nodes 

        node = self.s_label
        while node is not None:
            self.link_antennas(node, antennas)
            link_mode = self.nodes[node]['link_mode']
            node = self.nodes[node]['next_hop']
            if link_mode == 'b2b': 
                node = self.nodes[node]['next_hop']


            
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
        
   



    

   

