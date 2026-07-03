import geopandas as gpd
import networkx as nx
from pandas import concat, isna
import cisei_lib.cli.elevation.rfModel as rfModel
import re

# Author: Edgard Jamhour
# Base class for RPL-based planning; agnostic to radio model
# Designed for single-round use within a multi-round algorithm that adds repeaters iteratively
# Builds a single-path topology connecting new nodes to fixed nodes (border routers)
# Radio-specific parameters should be defined in self.context

class GeoRPL:
    
    # Initialize base RPL planning context, radio model, and internal graph state
    def __init__(self, **context):
        self.G = nx.Graph() # graph used to run RPL
        self.G_res = nx.Graph() # graph with the result of RPL
        self.utm_nodes = gpd.GeoDataFrame()  # all nodes in UTM projection 
        self.edges = {} # type: dict[str, float] # dictionary with calculated edges quality

        self.context = context  # context is a dictionary with radio parameters  

        if not hasattr(self, "rf"):                    
            self.rf = rfModel.rfModel(**context) 

    # Enable geoRPL to be used as a context manager        
    def __enter__(self):
        return self 
    
    # No cleanup logic needed for context manager exit
    def __exit__(self, exc_type, exc_value, traceback):
        pass                          

    # Define fixed and new nodes for RPL planning
    # Fixed nodes have precalculated ranks; new nodes will be connected by propagation
    def set_nodes_gdf(self, fixed : gpd.GeoDataFrame, new : gpd.GeoDataFrame):

        # https://epsg.io/transform
        if fixed.empty:
            raise ValueError('geoRPL: no fixed nodes found')
        if new.empty:
            raise ValueError('geoRPL: no new nodes found')            
         
        new['fixed'] = False
        fixed['fixed'] = True     
        new['rank'] = float('inf')     
        fixed['rank'] = fixed['rank'].apply(lambda x: x if isinstance(x, (int, float)) else 0)    
        # if hasattr(n, 'rank') and not isna(n.rank)
        
        self.utm_nodes = gpd.GeoDataFrame(
            concat([fixed, new], ignore_index=True),
            geometry="geometry",       # or the actual name of your geometry column
            crs=fixed.crs              # or new.crs, assuming they match
        )       

        self.utm_nodes["lat"] = self.utm_nodes.geometry.y
        self.utm_nodes["lon"] = self.utm_nodes.geometry.x
        crs = self.context.get('crs', 'EPSG:31982')
        self.utm_nodes = self.utm_nodes.to_crs(crs)  # UTM projection is required for accurate distance calculations           

    # define the edges quality to avoid recaculation
    # -- parent class may include a rank key to overhidde ETX calculation based on rssi
    def set_edges_qos(self, edges, update=True):    
        if not update:
            self.edges = {}

        self.edges.update(edges)   

    # Create a graph from nodes dicts and edges list o tuples
    def create_graph(self, nodes, edges):
        G = nx.Graph()
        G.add_nodes_from(nodes.keys())               
        nx.set_node_attributes(G, nodes)
        G.add_edges_from(edges)
        return G

    # Create a graph connecting floating nodes to fixed nodes
    def create_graph_gdf(self, degree):
    
        nodes = {}
        edges = []

        # new nodes

        if 'relay' not in self.utm_nodes.columns:
            self.utm_nodes['relay'] = True

        new = self.utm_nodes[self.utm_nodes['fixed'] == False]

        # only nodes that are relays are considered neighbors
        gdf_relay = self.utm_nodes[self.utm_nodes['relay'].apply(lambda x: x is not False)]  

        # for each new node find the nearest neighbors in all_gdf        
        for row in new.itertuples(index=False):
            neighbors = self.find_neighbors(gdf_relay, row.geometry, degree, projected=True)           
        # -add neighbors to the graph and create an edge for each onde of then
            for n in neighbors.itertuples():
                pos = (n.geometry.x, n.geometry.y) # UTM coordinates
                nodes[n.name] = {'pos': pos, 'fixed': n.fixed, 'relay': n.relay, 'rank': n.rank}                                   
                if n.name != row.name: 
                    edges.append((n.name, row.name))     
            # the node itself was not included if it is not a relay
            if row.name not in nodes:                
                nodes[row.name] = {'pos': (row.geometry.x, row.geometry.y), 'fixed': row.fixed, 'relay': row.relay, 'rank': row.rank}    

        self.G = self.create_graph(nodes, edges)
    
    # Calculate the ETX based on the RSSI
    def rssi_to_ETX(self, rssi):

    
        if 'etx' not in self.context:
            sensitivity = self.context.get('sensitivity', -200)
            target_rssi = self.context.get('target_rssi', -70)
            step = (target_rssi - sensitivity)/5
            etx =  { -70-i*step : 1+i*1 for i in range(5) }
        else:
            etx  = self.context['etx']

        res = None
        for k, v in etx.items():
            if rssi >= k : 
                res = v           
                break

        if res is None:
            res = float('inf')
            # raise ValueError('A link with unacceptable RSSI was detected')
        
        return res 
    
    # Calculate the quality of a link, the ETX and the rank
    # -- this function must be overhidded in the child class
    def calculate_rank(self, parent, child):  

        if (child, parent) is self.edges:
            res = self.edges[(child, parent)]
        else:
            row = self.utm_nodes[self.utm_nodes['name'] == child].iloc[0]
            src_pos = row['lat'], row['lon']
            row = self.utm_nodes[self.utm_nodes['name'] == parent].iloc[0]
            dst_pos = row['lat'], row['lon']
            res = self.rf.link_quality(src_pos, dst_pos, **self.context)
            self.edges[(child, parent)] = res

        if 'rank' in res:
            rank = res['rank'] +  + self.G.nodes[parent]['rank']
        else:
            rank = self.rssi_to_ETX(res['rssi']) + self.G.nodes[parent]['rank']
        
        return rank   

    # Execute one round of RPL propagation from fixed nodes over the virtual link graph
    # Builds a single-path topology (DODAG) and saves the result in G_res
    def run_RPL(self):

        nodes= list(self.G.nodes)
        nodes_to_process = { n for n in nodes if ( self.G.nodes[n]['fixed'] and self.G.nodes[n]['rank'] < float('inf')) }      
        # TODO: can I use only rank instead?
            
        while nodes_to_process:
            current_node = nodes_to_process.pop()
            
            # Send DIO to all neighbors
            for neighbor in self.G.neighbors(current_node):

                if self.G.nodes[neighbor]['fixed']: # backhaul node previously planned
                    continue

                neighbor_rank = self.calculate_rank(current_node, neighbor)

                old_rank = self.G.nodes[neighbor].get('rank', None)
                if isna(old_rank) : old_rank = float('inf') 
                # atualiza o rank dos nós que recebera a mensagem DIO
                if old_rank > neighbor_rank: 
                    self.G.nodes[neighbor]['rank'] = neighbor_rank
                    self.G.nodes[neighbor]['parent'] = current_node   
                    if self.G.nodes[neighbor]['relay']:
                        nodes_to_process.add(neighbor)     

        self.G_res = nx.Graph()
        self.G_res.add_nodes_from(self.G.nodes(data=True))               
        edges = [ (n, self.G.nodes[n]['parent'] ) for n in self.G.nodes if 'parent' in self.G.nodes[n] ]
        self.G_res.add_edges_from(edges)
        self.G_res.remove_nodes_from(list(nx.isolates(self.G_res)))

    # Show the network in mathplotlib (use G_res for results)
    def show_network(self, layout='pos', G=None):
        import matplotlib.pyplot as plt

        if G is None: G = self.G 

        def label_function(s):
            match = re.search(r'(\d+)\D*$', s)
            return match.group(1) if match else s[0]+s[-3:]
        
        def color_function(node):
            if node['fixed']:
                return 'gray' if node['relay'] else 'black'
            else:                
                if node['relay']: 
                    return 'lightgreen' if node.get('rank', float('inf')) < float('inf') else 'green'                    
                else: 
                    return 'lightblue' if node.get('rank', float('inf')) < float('inf') else 'blue'                    
               

        if layout == 'pos':
            pos = nx.get_node_attributes(G, 'pos')
        elif layout == 'spiral':
            pos = nx.spiral_layout(G)
        elif layout == 'circular':
            pos = nx.circular_layout(G)
        else:
            pos = nx.spring_layout(G)
        
        node_colors = [color_function(node[1]) for node in G.nodes(data=True) ]        
        nx.draw(G, pos=pos, node_color=node_colors, with_labels=False)   
        labels = {node: label_function(node) for node in G.nodes}
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=8, font_color="white")     
        # nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels)        
        plt.show()

        # Locate n-neighbors for a point (geometry) in gdf (projected)
    
    # Compute the n-nearest neighbors using an UTM projection
    def find_neighbors(self, geo_df: gpd.GeoDataFrame, target_point, n, projected = False) -> gpd.GeoDataFrame:      
        # Ensure CRS consistency
        if geo_df.crs and geo_df.crs.is_geographic:  
            raise ValueError('find_neighbors: GDF must be projected')          
        if not projected:
            target_point = gpd.GeoSeries([target_point], crs="EPSG:4326").to_crs(geo_df.crs).iloc[0]

        geo_df = geo_df.copy()
        geo_df['distance'] = geo_df.geometry.distance(target_point)

        # Get the n nearest points
        nearest_n_points = geo_df.nsmallest(n, 'distance')
        return nearest_n_points



    



  