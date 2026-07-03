from cisei_lib.cli.usermng.homeFolder import user_home
from cisei_lib.cli.bhnplanner.link_planner import linkPlan
import geopandas as gpd
from pandas import concat, isna
from concurrent.futures import ProcessPoolExecutor
from cisei_lib.cli.usermng.configTools import configRadio
from cisei_lib.cli.bhnplanner.geo_rpl import geoRPL
import msgpack
from cisei_lib.cli.tools.safe_code import tomlkit_encoder, tomlkit_decoder
from cisei_lib.cli.tools.debug_utils import patch_print, debug_vars, debug_page
from pathlib import Path
from copy import deepcopy

'''
TODO:
- Projection must be defined in the TOML configuration file (Paraná uses UTM zone 22S).
- RPL algorithm must be adapted to support Wi-SUN mesh networks.
- Link ranking should include a penalty for the use of repeaters.

PRECONDITIONS:
- A project must be created using manageProject.
- Nodes requiring connection must be defined in new_geo.json.
- The backhaul must contain at least one node with a radio (the POP node).
- Backhaul node ranks must be updated beforehand if necessary.

ABOUT POOL LINK PLANNING:
- Links are planned using multiprocessing (pool-based).
- The links to be planned correspond to the graph’s edges.
- An external structure maintains the set of edges already processed.
- Directional antennas are only applied during the final planning phase to fine-tune link quality.
- linkplan results must be indexed by edge, including nodes, links, and ranks.
- Implement save/load mechanisms to allow planning to be paused and resumed later.

ALGORITHM:

INITIALIZATION:
- Merge bhn_geo.json and new_geo.json, converting coordinates to UTM.
- Update the resulting GeoDataFrame with node attributes: fixed or relay.
- Fixed nodes belong to the backhaul and must have predefined ranks.
- Nodes that cannot serve as relays must be treated as leaf nodes in the graph.

ITERATIVE PLANNING LOOP:
- Construct a graph with candidate edges connecting non-fixed nodes to fixed nodes.
- Run the RPL algorithm to establish initial paths for new nodes.
- Add repeaters to edges linking nodes with finite rank values.
- Recalculate neighbors, excluding edges with infinite rank or those traversing a repeater to a fixed node.
- Re-run the RPL algorithm and repeat the process until ranks stabilize.
- Remove repeaters not included in the final paths to non-fixed nodes.
- Recalculate link quality using directional antennas for final adjustments.

KNOWN ISSUE:
- Repeaters need to be renamed because linkPlan gives names independently: R0,R1,etc.
- All links from the candidate graph are planned in parallel saved in self.planned_links
- RPL define used_links from the graph by prunning
- Everything is saved as independent structures by serialization
- Repeaters are renamed by add_repeaters only after deserialization
- Repeaters in the same position receives the same label
- The label of the link and the internal hops must be renamed
- Any inconsistence will break the code
- add_repeaters and eval_round are presently called after deserialization

SOLUTION:
# -- TODO: create a consolidate_round function that will replace add_repeaters and call eval_round
# -- TODO: serialize only consolidated data (as a parcial implementable result)
'''

class bhPlan(geoRPL):

    def __init__(self, project, home: user_home, **kwargs):
        self.home = home
        self.log = 'manageProject.log'
        self.project = project
        self.radio_model = kwargs.get('config_file', None)
        self.conf = configRadio(home, user_file=self.radio_model)     
   
        # read GeoJSON
        self.bhnlinks = gpd.read_file( self.home.get_project_path(project, 'bhlinks_geo.json') ) # type: gpd.GeoDataFrame
        self.bhns = gpd.read_file( self.home.get_project_path(self.project, 'bhns_geo.json') ) # type: gpd.GeoDataFrame
        self.new = gpd.read_file( self.home.get_project_path(self.project, 'new_geo.json') )  # type: gpd.GeoDataFrame

        # context is used by the geoRPL class
        context = {}
        context['degree'] = kwargs.get('degree', self.conf.get('BACKHAUL.optimization.candidates'))
        context['dads_relay'] = kwargs.get('dads_relay', self.conf.get('BACKHAUL.optimization.allow_dads_relay'))
        context['level'] = self.conf.get('ENVIRONMENT.trees.tree_level')          
        context['etx'] = eval(self.conf.get('BACKHAUL.radio.ETX'), {"__builtins__": {}})

        # updatable planning variables
        self.virtual_links = {} # type: dict[str, dict] # multihop paths among two nodes
        self.links = {}    # type: dict[str, dict] # links among adjacent nodes
        self.ranked_nodes = {}    # type: dict[str, dict] # nodes with rank (sub-set of new)
        self.repeaters = {} # type: dict[str, dict] # repeaters used in planned_links

        super().__init__(**context)

        # udpate rank and relay information if necessary
        all_valid = 'rank' in self.bhns.columns and (self.bhns['rank'] < float('inf')).all()
        if not all_valid:
            self.update_fixed_ranks(save=True )
        self.update_relay_property()

    def __enter__(self):
        return self 
    
    def __exit__(self, exc_type, exc_value, traceback):
        pass

    # Mark eligible relay nodes in `new` and `bhns`
    def update_relay_property(self):

        if not self.context['dads_relay']:
            is_relay = lambda n : not (hasattr(n, 'id_da') and not isna(n.id_da))       
            self.new['relay'] = self.new.apply(is_relay, axis=1)
            self.bhns['relay'] = self.bhns.apply(is_relay, axis=1)

    # Calculate the full path rank of a child node from the root
    def calculate_rank(self, parent, child):

        if (parent, child) not in self.edges:
            raise Exception('bhPlan: must set_edges_qos() first')
        edge_rank = self.edges[(parent, child)]['rank']
        return edge_rank + self.G.nodes[parent]['rank']               
                    
    # return the rssi previously calculated in geoJSON bhnlinks
    def get_rssi_gdf(self, node_name):
        res = self.bhnlinks.query(f"src == '{node_name}'")
        if not res.empty:
            if 'rssi' in res.columns:
                return res['rssi'].values[0]
            else:
                rssi_max = res["rssi_max"].values[0]
                rssi_min = res["rssi_min"].values[0]
                rssi =  rssi_min + (rssi_max - rssi_min) * self.context['level']
                return rssi
        else: 
            # TODO: if there is no link, check if it is a b2b link
            res = self.bhns.query(f"name == '{node_name}'").iloc[0] 
            next_hop = res['next_hop']           
            if next_hop is None:
                return 0  # POP has no rssi?                          
            src_pos = res.geometry
            res = self.bhns.query(f"name == '{next_hop}'").iloc[0]

            if src_pos == res.geometry:
                return 0                                          
            
        raise RuntimeError(f'get_rssi_gdf: {node_name} has no link and is not b2b')    
   
    # return a row based on the name attribute
    def get_row_gdf(self, gdf, name):
        gdf.loc[gdf['name'] == name].iloc[0]

    # Calculate and update the rank of all nodes in the existing backhaul (inplace=True)
    def update_fixed_ranks(self, save=True ):

        # Todo: remove os ranks se existirem        
        if 'rank' in self.bhns.columns:
            self.bhns.drop(columns=['rank'],  inplace=True)   

        nodes = self.bhns
  
        # set rank=0 for pop nodes
        pops = nodes.query("next_hop == '' or next_hop == None")
        if pops.empty:
            raise RuntimeError('bhPlan: no pop found in bhns_geo.json')
                
        ranks = {}
        for _, row in pops.iterrows():
            ranks[row['name']] = 0
            nodes.loc[nodes['name'] == row['name'], 'rank'] = 0

        while True:

            # query rows without rank and next_hop in the with_rank list
            new = nodes[(nodes['next_hop'].isin(ranks.keys())) & (nodes['rank'].isna())]

            # if empty, all nodes that are not pop already have a rank
            if new.empty:
                break

            for _, row in new.iterrows():     
                rssi = self.get_rssi_gdf(row['name'])                    
                ranks[row['name']] = self.rssi_to_ETX(rssi) + ranks[row['next_hop']]
                nodes.loc[nodes['name'] == row['name'], 'rank'] =  ranks[row['name']]

        all_valid = 'rank' in self.bhns.columns and (self.bhns['rank'] < float('inf')).all()
        if not all_valid:            
            self.home.write_log(self.log, 'bhPlan: some nodes in the backhaul are disconected', True) 

        if save:
            self.bhns.to_file( self.home.get_project_path(self.project, 'bhns_geo.json') )
   
    # Worker function for multiprocessing: plans a virtual link using linkPlan,
    # placing repeaters as needed based on RF constraints and terrain.
    # The link is treated as a direct edge in the RPL graph; if selected, its repeaters
    # are promoted to the backhaul in the next planning round.
    def linkPlan_worker(self, parent, child):  
        patch_print()
        instance =  linkPlan(self.project, self.home, config_file=self.radio_model)
        instance.set_link_gdf(self.utm_nodes, child, parent)
        instance.plan_link()     
        antennas = [ a for a in self.conf.get('BACKHAUL.antennas') if a['type'] == 'omni' ] 
        instance.path_antennas(antennas) 
        nodes = instance.get_nodes()
        # update the ranks of the path assuming parent node rank is zero
        rank = 0
        for k, v in nodes.items():
            rank = self.rssi_to_ETX(v['rssi']) + rank if 'rssi' in v else rank
            nodes[k]['rank'] = rank

        return parent + ':' + child, nodes              
       
    # Plan all links in the adjacency graph using multiprocessing
    # Links are precomputed over a bounded-degree adjacency graph before RPL runs
    # This decouples link evaluation from the RPL loop, enabling parallel computation
    def pool_linkPlan(self):
        max_workers = self.conf.get('BACKHAUL.optimization.linkPlan_workers')  
        # ATTENTION: edges are from fixed to new (parent, child)     
        pool = list(set(self.G.edges) - set(self.edges.keys()))
        print(f'planning {len(pool)} links with {max_workers} workers ...')
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(self.linkPlan_worker, *zip(*pool)))                
        return { r[0]:r[1] for r in results }

    # Transfer link quality from linkPlan results to the geoRPL internal structure
    def set_edges_qos(self):

        edges = {}
        for e in self.G.edges:
            edge = e[0] + ':' + e[1]            
            if edge not in self.planned_links:
                raise Exception(f'Link {edge} is not planned!')
            child = self.planned_links[edge][e[1]]
            edges[e] = {'rank' : child['rank']}                                     

        super().set_edges_qos(edges)
    # Locate a repeater label by coordinates
    def get_repeater_label(self, node_pos, add=True):

        clone = None
        for k,v in self.repeaters.items():
            if v["pos"] == node_pos: 
                clone = k 
                break            
        
        if add and clone is None:
            new_label = f'{self.project}_R{len(self.repeaters.keys())}' 
        else:
            new_label = clone        

        return new_label, clone

    # Identify RPL-assigned links and nodes, and mark unconnected (floating) nodes 
    def eval_round(self):
    
        if self.G_res is None or self.planned_links == {}:
            self.home.write_log(self.log, 'eval_round: no planning results to add!', True)
            return
                
        RPL_nodes = { n[0] : n[1] for n in self.G_res.nodes(data=True) }
        connected_nodes = set(k for k,v in RPL_nodes.items() if not v['fixed'] and v['rank'] < float('inf'))        
        RPL_edges = [ RPL_nodes[n]['parent'] + ':' + n for n in connected_nodes ]

        # select the links used to connect these nodes      
        used_links = { k: v for k,v in self.planned_links.items() if k in RPL_edges }        
        rpl_nodes = dict(self.G_res.nodes(data=True))

        return used_links, rpl_nodes, set(self.new["name"]) - set(RPL_nodes.keys())

    # Add selected repeaters to the next RPL planning round
    def add_repeaters(self):
         
        used_links, rpl_nodes, floating_nodes = self.eval_round()         

        # Add the repeaters in the used links to the self.repeaters dict        
        repeaters = 0 # new repeaters
        for link, hops in used_links.items(): 
            parent, child = link.split(':')         

            # ajust the rank of the repeaters (links are planned with 0 in the parent rank)
            if hops[parent]['rank'] != rpl_nodes[parent]['rank']:
                dif = rpl_nodes[parent]['rank'] - hops[parent]['rank'] # are you affraid?
                for k in hops.keys(): hops[k]['rank'] += dif
            if hops[child]['rank'] != rpl_nodes[child]['rank']:
                print(f"child:{child} link: {hops[child]['rank']} rpl: {rpl_nodes[child]['rank']}" )
                # raise Exception('add_repeaters: error in child rank')
                

            # append only new repeaters to nodes
            for h_k, h_v in hops.items(): 
                if h_k in (parent, child) : continue # use only repeaters
                new_label, clone = self.get_repeater_label(h_v['pos']) 
                
                if clone is None: # a repeater does not exist in this position
                    repeaters += 1        
                    self.repeaters[new_label] = deepcopy(hops[h_k]) # add repeater to the nodes list (copy.deepcopy)
                    self.repeaters[new_label]['relay'] = True         
                    self.repeaters[new_label]['iconType'] = 'repeater'   
                    next_hop = h_v['next_hop']                                                         
                    next_hop, _ = self.get_repeater_label(hops[next_hop]['pos'], False) 
                    if next_hop is not None:
                        self.repeaters[new_label]['next_hop'] = next_hop                                                

            # Save link segments as individual links
            if len(hops) == 2: continue    

            replace_label = lambda label, node: (  
                    self.get_repeater_label(node['pos'], True)[0]     
                    if node['hop_type'] == 'rep'
                    else label
                )
                     
            for h_k, h_v in hops.items(): # TODO VERIFY THIS (HERE)
                if h_k == parent: continue
                child_node = deepcopy(h_v)
                child_label = replace_label(h_k, child_node ) 
                next_node = deepcopy(hops[h_v['next_hop']])                                
                next_label = replace_label(child_node['next_hop'], next_node )                
                child_node['next_hop'] = next_label
                child_node['rank'] -= next_node['rank']  # edge rank           
                next_node['rank'] = 0 # likewise all planned links
                self.planned_links[next_label + ':' + h_k] = {next_label: next_node, child_label : child_node} 

        return repeaters, floating_nodes

    # Consolidate round
    def consolidate_round(self):

        if self.G_res is None or self.virtual_links == {}:
            self.home.write_log(self.log, 'eval_round: no planning results to add!', True)
            return

        RPL_nodes = { n[0] : n[1] for n in self.G_res.nodes(data=True) }
        

        connected_nodes = [
            k for k, v in RPL_nodes.items()
            if not v['fixed'] and v['rank'] < float('inf')
        ]

        RPL_edges = [ RPL_nodes[n]['parent'] + ':' + n for n in connected_nodes ]

        # ranked_nodes are new_nodes that are already connected
        self.ranked_nodes = {
            r["name"]: {
                k: v for k, v in r.items()
                if k != "geometry" and k != "name"
            } | {
                "pos": [r["geometry"].y, r["geometry"].x],
                "rank": RPL_nodes[r['name']]['rank'],
                "next_hop": RPL_nodes[r['name']]['parent']                 
            }
            for r in self.new.to_dict('records') 
            if r['name'] in connected_nodes # list of dicts
        }

        # Filter virtual links
        used_virtual_links = { k: v for k,v in self.virtual_links.items() if k in RPL_edges }

        # Extract and rename repeaters
        


        # Generate links from virtual links
        link_ids = [
            f"{v_h['next_hop']}:{k_h}"
            for k_l, v_l in used_virtual_links.items()
            for k_h, v_h in v_l.items()
            if v_h['next_hop']
        ]

        pass

    # Unify names of repeaters at the same coordinates, since linkPlan_worker plans them independently
    def rename_link_repeaters(self, links_dict, inplace=True):

        if not inplace:
            links_dict = deepcopy(links_dict)

        def replace_label(pos, label):            
            for k,v in self.repeaters.items():
                if v["pos"] == pos: return k 
            return label

        for link, hops in links_dict.items():
            label_dict = { hop_k : replace_label(hop_v['pos'], hop_k) for hop_k, hop_v in hops.items() }
            links_dict[link] = {label_dict.get(hop_k, hop_k): hop_v for hop_k, hop_v in hops.items() } 
            for h in links_dict[link].keys():
                links_dict[link][h]['next_hop'] = label_dict.get(links_dict[link][h]['next_hop'], links_dict[link][h]['next_hop'])
        
        return links_dict

    # Extend the planning graph with repeaters to support incremental RPL convergence
    def create_graph_gdf(self, degree=None):
        if degree is None:
            degree = self.context['degree']

        if len(self.repeaters) > 0:
            bhns = concat([self.bhns, self.to_geoJSON_nodes(self.repeaters)], ignore_index=True)    
            self.set_nodes_gdf(bhns, self.new)
        else:
            self.set_nodes_gdf(self.bhns, self.new)
        
        super().create_graph_gdf(degree)
    
    # Execute a single planning round with serialization for fault recovery and user-supervised refinement
    def plan_round(self):
    
        saved = self.deserialize_plan()
          
        if saved:
            nodes = dict(self.G_res.nodes(data=True))
            print([k for k,v in nodes.items() if v['rank'] == 0])

            repeaters, floating_nodes  = self.add_repeaters()   

            print(len(self.repeaters))         
            print( [k for k,v in self.repeaters.items() if v['rank'] == 0] )
            if not floating_nodes:
                print('planning terminated: all nodes connected')                
                return True
            elif not repeaters:
                print('planning terminated: with floating nodes:', floating_nodes)
                return False  
            print(f'repeaters: {repeaters}, floating nodes: {floating_nodes}')     
            print(f'total repeaters: {len(self.repeaters)}')

        # self.show_network(G=self.G_res)          

        self.create_graph_gdf(1)
        res = self.pool_linkPlan()
        self.planned_links.update(res)
        self.set_edges_qos()
        self.run_RPL()
        # self.show_network(G=self.G_res)
        self.serialize_plan()

        return None

    # return a path formed by node names
    def _track_repeater_path(self, node):
        path = {node}
        for i in range(10):
            next = self.repeaters[node]['next_hop']
            if next not in self.repeaters.keys():
                break
            else:
                path.add(next)
                node = next
        return path
    
    # Returns all links used by the node along its path.
    def split_link(self, link):

        links = {}
        
        if link not in self.planned_links:
            return None
        
        parent, child = link.split(':')
        
        hops = self.planned_links[link]

        hop_label = child        
        while hop_label != parent:        
            next_label = hops[hop_label]['next_hop']
            link =  next_label + ':' + hop_label
            if link not in self.planned_links: 
                hop_node = deepcopy(hops[hop_label])
                next_node = deepcopy(hops[next_label])            
                links[link] = {next_label: next_node, hop_label: hop_node}  
            hop_label = next_label
        
        return links

    # Save the current planning to a file
    def serialize_plan(self, file = None, remove = False ):
        G = self.G if self.G_res is None else self.G_res
        if G is None: raise('serialize_plan: Nothing to serialize')

        data = [self.planned_links, dict(G.nodes(data=True)), list(G.edges), self.repeaters]

        in_prog_folder = Path(self.home.get_project_path(self.project, 'in-progress'))        
        in_prog_folder.mkdir(parents=True, exist_ok=True)
                
        if file is None:
            pack_files = list(in_prog_folder.glob("*.pack"))
            pack_files = sorted(pack_files, key=lambda f: int(f.stem.split('_')[1]))

            n = int(pack_files[-1].stem.split('_')[1]) if pack_files else -1
            file = f'temp_{n + 1}.pack'       

        if remove:
            for file in pack_files:
                if file.is_file(): file.unlink()

        file = in_prog_folder / file        
        with open(file, "wb") as f:
            f.write(msgpack.packb(data, default=tomlkit_encoder))       

    # Retrieve the last planning from a file
    def deserialize_plan(self, file = None):

        in_prog_folder = Path(self.home.get_project_path(self.project, 'in-progress'))  


        if file is None:
            pack_files = list(in_prog_folder.glob("*.pack"))  
            pack_files = sorted(pack_files, key=lambda f: int(f.stem.split('_')[1]))
            path = pack_files[-1] if pack_files else None

        else:
            path = in_prog_folder / file
                   
        if path is None:
            return False

        try:
            with open(path, "rb") as f:       
                data = f.read()         
                self.virtual_links, g_nodes, g_edges, self.repeaters =  msgpack.unpackb(data, object_hook=tomlkit_decoder)         
                self.G_res = self.create_graph(g_nodes, g_edges)
            return True
                
        except FileNotFoundError:
            return False

    # save bhnodes as GeoJSON
    def to_geoJSON_nodes(self, nodes_dict, save=False, file_name=None, utm=False):

        data = []

        for name,value in nodes_dict.items():

            entry = {
                'type': 'Feature',
                'geometry': {'type' : 'Point'},
                'properties': None
            }

            if utm:
                entry['geometry']['coordinates'] = [ value['pos'][0], value['pos'][1] ]  # x, y for UTM  
            else:
                entry['geometry']['coordinates'] = [ value['pos'][1], value['pos'][0] ]  # lon, lat (correct order)  
                zone_number = int((value['pos'][1] + 180) / 6) + 1
                if zone_number != 22:
                    print('Zone number: ', zone_number)

            entry['properties'] = {
                'name' : name,
                'layer': 'planned',
                'iconType': value.get('iconType','planned'),
            }

            info = {k:v for k,v in value.items() if not isinstance(v, (list, tuple, set, dict))} # not supported in geoJSON
            entry['properties'].update(info)
            for k in ('obs_h', 'obs_dm', 'tested'):
                entry['properties'].pop(k, None)
            
            data.append(entry)

        geojson = {
            'type': 'FeatureCollection',
            'features': data
        }
        
        gdf =  gpd.GeoDataFrame.from_features(geojson["features"])
        if utm:
            gdf = gdf.set_crs(epsg=31982)
            gdf = gdf.to_crs(epsg=4326)
        else:
            gdf = gdf.set_crs(epsg=4326)

        
        if save:

            if file_name is None:
                file_name = f'plannedNodes_geo.json'
            
            path = self.home.get_project_path(self.project, file_name)

            gdf.to_file(path, driver="GeoJSON")
            
        
        return gdf

    # save bhlinks as GeoJSON
    def to_geoJSON_links(self, links_dict, save=False, file_name=None):

      
        data = [] 
      
        for link, hops in links_dict.items():

            # ihops = iter(hops.items())
            parent, child = link.split(':')            
            s_label = child
                       
            while True:
                start = hops[s_label] 
                d_label = start['next_hop'] 
                                
                if d_label is None: 
                    if s_label != parent: self.home.write_log(self.log, f'to_geoJSON_links: path {link} is broken', True)
                    break

                if d_label not in hops.keys():
                    self.home.write_log(self.log, f'to_geoJSON_links: path {link} is broken', True)
                    break
                
                entry = {
                    'type': 'Feature',
                    'geometry': {'type' : 'LineString'},
                    'properties': None
                }                                               
                
                if start['link_mode'] == 'b2b':
                    s_label = d_label
                    continue
                
                end = hops[d_label]
                
                coord_1 = (start['pos'][1], start['pos'][0])
                coord_2 = (end['pos'][1], end['pos'][0])
                entry['geometry']['coordinates'] = [coord_1, coord_2] 

                entry['properties'] = {
                    'name': f"{s_label}:{d_label}",
                    'src': s_label,
                    'dst': d_label, # row end is a series and attributes are accessed with []
                    'rank' : start.get('rank','inf') - end.get('rank','inf')                    
                }


                # qos = self.link_quality(s_label, d_label)
                # entry['properties'].update(qos) 
                
                data.append(entry)

                s_label = d_label

        geojson = {
            'type': 'FeatureCollection',
            'features': data
        }

        gdf =  gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")

        if save:

            if file_name is None:
                file_name = f'plannedLinks_geo.json'
            
            path = self.home.get_project_path(self.project, file_name)

            gdf.to_file(path, driver="GeoJSON")
        
        return data

    # Export the plan to GeoJSON
    def export_plan(self, file = None):
        if file is not None:
            step = file.split('_')[1].split('.')[0]
            sufix = f'{step}_geo.json'
        else:
            sufix = '_geo.json'

        self.deserialize_plan(file)
        repeaters, floating_nodes  = self.add_repeaters()        
        g_nodes = dict(self.G_res.nodes(data=True))
        self.to_geoJSON_nodes(g_nodes, utm=True, save=True, file_name=f'g_nodes{sufix}')        
        self.to_geoJSON_nodes(self.repeaters, save=True, file_name=f'repeaters{sufix}') 
        g_edges = [ e[0] + ':' + e[1] for e in self.G_res.edges() ]        
        used_links  = { k:v for k,v in self.planned_links.items() if k in g_edges }
        self.to_geoJSON_links(used_links, save=True, file_name=f'u_links{sufix}')

    def run(self):

        # print(self.plan_round())

        self.deserialize_plan()
        
        # g_nodes has the parent and rank of nodes
        g_nodes = dict(self.G_res.nodes(data=True))
        self.to_geoJSON_nodes(g_nodes, save=True, file_name='g_nodes_geo.json')

        # g_edges is just a list of link names
        g_edges = [ e[0] + ':' + e[1] for e in self.G_res.edges() ]        
        used_links  = { k:v for k,v in self.planned_links.items() if k in g_edges }         
        # self.repeaters link names are unique but the name assigned to hops must be adjusted
        # self.rename_link_repeaters(used_links, inplace=True)                        
        # used_links is a dictionary with hops and cannot be converted directly to geo_json

        self.to_geoJSON_links(used_links, save=True, file_name='u_links_geo.json') # see that must explode
        
        exit(0)

        links  = list(self.planned_links.keys())

        self.new['pos'] = self.new.geometry.apply(lambda geom: [geom.y, geom.x])
        new_nodes = self.new.drop(columns='geometry').to_dict(orient='records')


        # SUA LÓGICA É INCERTA!!!!


        self.split_link(links[0])
        

        # self.to_geoJSON(nodes, save=True, utm=True)
        self.to_geoJSON_links(used_links, save=True)

        used_repeaters = set()
        for link, hops in used_links.items():
            for k_hop in hops.keys():
                if k_hop in self.repeaters.keys(): # remove parent and child
                    used_repeaters.add(k_hop)

        
        for rep in used_repeaters:
            used_repeaters = used_repeaters | track_repeater(rep)
                        
        rep_nodes = {k:v for k,v in self.repeaters.items() if k in used_repeaters}
        for rep_node in rep_nodes.values(): rep_node['iconType'] = 'repeater'

        keep_keys = {'pos', 'next_hop', 'link_mode', 'ant_h', 'ant_dB', 'ant_type','hop_type', 'rank' }
        for key in rep_nodes:
            rep_nodes[key] = {k: v for k, v in rep_nodes[key].items() if k in keep_keys}


        
        
        # update new nodes with rank and next hop information
        nodes = rep_nodes.copy()        
        for n in new_nodes:
            if n['name'] in g_nodes:
                n['rank'] = g_nodes[n['name']]['rank']
                n['next_hop'] = g_nodes[n['name']]['parent']  
                n['iconType'] = 'new'             
            else:
                n['iconType'] = 'floating'
            
            nodes[n['name']] = n
        # check the rank of the nodes

        
        self.to_geoJSON_nodes(nodes, save=True)

        pass





        
        
        

        








    



  