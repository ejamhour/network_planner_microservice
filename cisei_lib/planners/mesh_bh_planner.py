from cisei_lib.cli.usermng.homeFolder import user_home
from cisei_lib.cli.usermng.configTools import configRadio
from cisei_lib.cli.bhnplanner.geo_rpl import GeoRPL
import geopandas as gpd
from pandas import concat, isna
from multiprocessing import Manager, Lock, Process, Queue
from queue import Empty  # for timeout handling
from cisei_lib.cli.bhnplanner.link_planner_worker import LinkPlannerWorker

class MeshBHPlanner(GeoRPL):

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
        
        # updatable planning variables
        self.virtual_links = {} # type: dict[str, dict] # multihop paths among two nodes
        self.links = {}    # type: dict[str, dict] # links among adjacent nodes
        self.ranked_nodes = {}    # type: dict[str, dict] # nodes with rank (sub-set of new)
        self.repeaters = {} # type: dict[str, dict] # repeaters used in planned_links

        DEFAULT_CONTEXT = {
            'degree': self.conf.get('BACKHAUL.optimization.candidates'),
            'dads_relay': self.conf.get('BACKHAUL.optimization.allow_dads_relay'),
            'level': self.conf.get('ENVIRONMENT.trees.tree_level') ,
            'etx': eval(self.conf.get('BACKHAUL.radio.ETX'), {"__builtins__": {}}),
            'workers' : self.conf.get('BACKHAUL.optimization.linkPlan_workers')
        }

        context = {**DEFAULT_CONTEXT, **kwargs}
        super().__init__(**context)

        # shared dictionary used by LinkPlan
        manager = Manager()
        self.shared_edges = manager.dict()
        self.shared_nodes = manager.dict()
        self.queue = manager.Queue()
        self.lock = Lock()
        

        '''
        with self.lock:
            if edge_key not in self.shared_edges:
                self.shared_edges[edge_key] = edge_data
        '''

    def __enter__(self):
        return self 
    
    def __exit__(self, exc_type, exc_value, traceback):
        pass


        # Mark eligible relay nodes in `new` and `bhns`
    
        # return the rssi previously calculated in geoJSON bhnlinks
    
    # Update new and bhn gdfs with 'relay' flag indicating if they can act as routers 
    def _get_rssi_from_gdf(self, node_name):
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

   # Update new and bhn gdfs with 'relay' flag indicating if they can act as routers
    def _init_relay_property(self):

        if not self.context['dads_relay']:
            is_relay = lambda n : not (hasattr(n, 'id_da') and not isna(n.id_da))       
            self.new['relay'] = self.new.apply(is_relay, axis=1)
            self.bhns['relay'] = self.bhns.apply(is_relay, axis=1)

    # Calculate and update the rank of all nodes in the existing backhaul (inplace=True)
    def _init_fixed_ranks(self, save=True ):

        # remove existing ranks to facilite update       
        if 'rank' in self.bhns.columns:
            self.bhns.drop(columns=['rank'],  inplace=True)   

        nodes = self.bhns
  
        # set rank=0 for pop nodes
        pops = nodes.query("next_hop == '' or next_hop == None")
        if pops.empty:
            raise RuntimeError('bhPlan: no pop found in bhns_geo.json')

        ranks = {} # dictionary of ranks assinged to nodes

        # initialize the rank of pop nodes        
        for _, row in pops.iterrows():
            ranks[row['name']] = 0
            nodes.loc[nodes['name'] == row['name'], 'rank'] = 0

        # update node ranks in rounds (one hope at a time)        
        while True:

            # nodes with a parent node but without rank
            new = nodes[(nodes['next_hop'].isin(ranks.keys())) & (nodes['rank'].isna())]

            # all nodes updated
            if new.empty:
                break

            # updates nodes in the round    
            for _, row in new.iterrows():     
                rssi = self._get_rssi_from_gdf(row['name'])                    
                ranks[row['name']] = self.rssi_to_ETX(rssi) + ranks[row['next_hop']]
                nodes.loc[nodes['name'] == row['name'], 'rank'] =  ranks[row['name']]

        all_valid = 'rank' in self.bhns.columns and (self.bhns['rank'] < float('inf')).all()
        if not all_valid:            
            self.home.write_log(self.log, 'bhPlan: some nodes in the backhaul are disconected', True) 

        if save:
            self.bhns.to_file( self.home.get_project_path(self.project, 'bhns_geo.json') )

        # Extend the planning graph with repeaters to support incremental RPL convergence

    # Update GDF and create the GeoRPL graph   
    def init_planning(self):     
        all_valid = 'rank' in self.bhns.columns and (self.bhns['rank'] < float('inf')).all()
        if not all_valid: self._init_fixed_ranks(save=True )
        self._init_relay_property()

        self.set_nodes_gdf(self.bhns, self.new)
        super().create_graph_gdf(self.context['degree'])

    # Run workers until all virtual links are planned
    def run_workers(self, num_workers):
        processes = []
        for _ in range(num_workers):
            instance = LinkPlannerWorker(self.project, self.home, config_file=self.radio_model)
            p = Process(
                target=instance.consume_links,
                args=(self.queue, self.utm_nodes, self.lock, self.shared_edges)
            )
            p.start()
            processes.append(p)
        for p in processes:
            p.join()

    # Intilize virtual links graphs and perform planning
    def run(self):

        pool = list(set(self.G.edges) - set(self.shared_edges.keys()))
        for edge in pool:
            self.queue.put(edge)

        self.run_workers(self.context['workers'])

