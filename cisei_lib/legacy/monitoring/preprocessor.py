from cisei_lib.cli.usermng.home_folder import UserHome
import cisei_lib.cli.importer.gdf_transformer as gdf_tr
import geopandas as gpd
import networkx as nx
from networkx.readwrite import json_graph
import matplotlib.pyplot as plt
from collections import defaultdict
from pyvis.network import Network 
import json

class Preprocessor():

    gdf_nodes: gpd.GeoDataFrame | None
    nx_dg: nx.DiGraph | None

    def __init__(self, network: str, home: UserHome, **kwargs): 

        self.home = home
        self.network = network

        DEFAULT_CONTEXT = {
            'topology' : 'bhns_geo.json',
            'graph' : 'graph.html',
            'visgraph_data' : 'vis_graph.json',
            'netgraph_data' : 'net_graph.json'
        }
        
        self.context = {**DEFAULT_CONTEXT, **kwargs}  
        self.inbox = self.home.get_monitoring_path(self.network, 'inbox', self.context['topology'] )        
        self.config = self.home.get_monitoring_path(self.network, 'configuration')  
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):

        if exc_type:
            print(f"Exception: {exc_type}, {exc_val}")
        return False  # Return True if you want to suppress exceptions

    # Load and normalize GeoJSON parameters representing topology and configuration
    def load_topology(self):
       
        gdf = gdf_tr.read_geojson(self.inbox)        
        rules, meta = gdf_tr.load_rules_from_dir(self.config, rule_type = 'topology')

        if meta['schema_control'] == 'whitelist':  
            nodes = gdf_tr.transform_rules_whitelist(gdf, rules)                  
        else:
            nodes = gdf_tr.transform_rules(gdf, rules)

        self.gdf_nodes = nodes
        
        return nodes

    # Build nx.Digraph
    def geojson_to_graph(self):

        if not hasattr(self, 'gdf_nodes'):
            print('GDF was not initialized')
            return

        self.nx_dg = nx.DiGraph()

        for _, row in self.gdf_nodes.iterrows():                        
            
            # Get all attributes from the row as a dictionary
            data = row.to_dict()
            
            # Add the 'pos' attribute
            data['pos'] = (row.geometry.x, row.geometry.y)
            if 'geometry' in data: del data['geometry']
            
            
            self.nx_dg.add_node(data['name'], **data)
            
            # next_hop can be missing or be set as 'None' in a GeoJSON
            if 'next_hop' in data and data['next_hop'] and data['next_hop'] != 'None':
                self.nx_dg.add_edge(data['next_hop'], data['name'])

        return self.nx_dg

    # Export networkx as JSON
    def save_netgraph(self):
        filename = self.home.get_monitoring_path(self.network, 'current', self.context['netgraph_data'])
        data = json_graph.node_link_data(self.nx_dg)
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

    # Export networkx as JSON
    def load_netgraph(self):
        filename = self.home.get_monitoring_path(self.network, 'current', self.context['netgraph_data'])        
        with open(filename, 'r') as f:
            json_str = f.read()
        data_loaded = json.loads(json_str)
        self.nx_dg = json_graph.node_link_graph(data_loaded)
        return self.nx_dg

    # Export graph to HTML 
    def export_visgraph(self, to_json=True):

        color_map = { "access-point": "red", "store-and-forward": "blue", "remote": "green" }
        shape_map = { "access-point": "triangle", "store-and-forward": "square", "remote": "dot" }
        net = Network(height="800px", width="100%", directed=True)  

        #net.from_nx(G) # automatic without custom positioning

        net.barnes_hut()  # optional: smooth layout
        root = self._find_gdf_root()

        if root is None: raise Exception('This network has a missing root')
        
        pos, _ = self._hierarchical_layout(self.nx_dg, root)

        for node, (x, y) in pos.items():
            net.add_node(node,
                label=str(node)[-6:],
                title="\n".join(f"{k}: {v}" for k, v in self.nx_dg.nodes[node].items() if v is not None),
                color=color_map.get(self.nx_dg.nodes[node].get("radio_role"), "#aaaaaa"),
                x=x * 100,
                y=-y * 100,
                # physics=False,
                # font={'color': 'black', 'size': 20}
            )
        for u, v in self.nx_dg.edges:
            net.add_edge(u, v)
        

        for node in self.nx_dg.nodes:
            net.get_node(node)['shape'] = shape_map.get(self.nx_dg.nodes[node].get("radio_role"), 'dot')

        options = defaultdict(dict)
        options['nodes']['font'] = {"color": "black", "size": 16,"multi": "md"}
        options['nodes']['scaling'] = {"label": {"enabled": True, "min": 10, "max": 30}}
        options['physics']['enabled'] =  False  

        net.set_options(json.dumps(options))
        
        # net.show_buttons(filter_=['physics'])  # optional UI for layout tuning

        nodes = net.nodes

        views = {
            "dumb": {
                "labels": {node["id"]: self.nx_dg.nodes[node['id']].get("name") for node in nodes},
                "colors": {node["id"]: "#00cc00" for node in nodes}  # example
            },
            "rssi": {
                "labels": {node["id"]: self.nx_dg.nodes[node['id']].get("next_hop_rssi") for node in nodes},
                "colors": {node["id"]: "#00cc00" for node in nodes}  # example
            }
        }

        if to_json:
            output = {
                'nodes' : nodes,
                'edges' : net.edges,
                'views' : views,
                'options': options
            }

            filename = self.home.get_monitoring_path(self.network, 'current', self.context['visgraph_data'])
            with open(filename, "w") as f:
                json.dump(output, f, indent=2)

        else:
            filename = self.home.get_monitoring_path(self.network, 'current', self.context['graph'])
            net.write_html(filename) # this create some annoying lib in the home folder

    # Show graph in matlab
    def show_graph(self, f_layout=None, root=None):

        if not hasattr(self, 'nx_dg'):
            print('Digraph was not initialized')
            return

        color_map = { "access-point": "red", "store-and-forward": "blue", "remote": "green" }
        
        # These variables will hold the final state for drawing
        G_to_draw = self.nx_dg
        pos = None

        if f_layout is None and root is None:
            # Case 1: Draw the full graph with predefined positions
            nodes_with_data = self.nx_dg.nodes(data=True)
            pos = {n: d['pos'] for n, d in nodes_with_data}

        elif f_layout is None and root is not None:
            # Case 2: Draw a BFS tree with predefined positions
            T = nx.bfs_tree(self.nx_dg, root)
            for n in T.nodes:
                T.nodes[n].update(self.nx_dg.nodes[n])
            G_to_draw = T
            nodes_with_data = G_to_draw.nodes(data=True)
            pos = {n: d['pos'] for n, d in nodes_with_data}
        
        else: # f_layout is not None
            # Case 3: Draw the full graph using a custom layout function
            if not root:
                root = self._find_gdf_root()
            G_to_draw = self.nx_dg
            pos, _ = f_layout(self.nx_dg, root)
            nodes_with_data = G_to_draw.nodes(data=True)
        
        # --- The drawing setup is now done just once for all cases ---
        labels = {n: d['name'][-3:] for n, d in nodes_with_data} 
        colors = [color_map.get(d.get("radio_role"), "gray") for n, d in nodes_with_data] 
        
        nx.draw(
            G_to_draw,
            pos,
            labels=labels,
            node_color=colors,
            with_labels=True,
            node_size=400,
            font_size=5,
            font_color="white"
        )
        
        plt.show()
       
# Private methods    

    def _hierarchical_layout(self, G, root, x=0, y=0, x_spacing=1.0, y_spacing=1.5, pos=None, visited=None):
        if pos is None:
            pos = {}
        if visited is None:
            visited = set()

        children = [n for n in G.neighbors(root) if n not in visited]
        visited.add(root)

        if not children:
            pos[root] = (x, y)
            return pos, x

        child_x = x
        for child in children:
            pos, child_x = self._hierarchical_layout(G, child, child_x, y - y_spacing, x_spacing, y_spacing, pos, visited)
            child_x += x_spacing

        min_x = min(pos[child][0] for child in children)
        max_x = max(pos[child][0] for child in children)
        pos[root] = ((min_x + max_x) / 2, y)
        return pos, child_x
 
    def _find_gdf_root(self):
        roots = self.gdf_nodes[self.gdf_nodes['next_hop'].isnull() | (self.gdf_nodes['next_hop'] == 'None')]
        
        if len(roots) == 1:
            return roots.iloc[0]['name']
        
        if len(roots) > 1:
            print(f'Multi-root is not supported in the current version')
        
        return None
            
            



#---------------------------------------------------------------------
if __name__ == '__main__':

    '''
    dir = Path.cwd() / 'code_examples' / 'Arquivos' / 'AGU-SE'  
    path = dir / 'bhnodes_geo.json'
    G = import_file(path)
    aps = [k for k, v in G.nodes(data=True) if v.get('mode') == 'access-point']
    # aps2 = [k for k, v in G.nodes(data=True) if v.get('hops') == 1]

    print(aps)
    root = aps[0]    
    # show_graph(G, hierarchical_layout, root)

    filename = str(dir / 'graph.html')

    export_interactive_graph(G, filename=filename)

        {
    "nodes": [...],
    "edges": [...],
    "views": {
        "rssi": {
        "labels": { "A": "-78 dBm", "B": "-85 dBm" },
        "colors": { "A": "#00cc00", "B": "#ff0000" }
        },
        "rank": {
        "labels": { "A": "Rank 1", "B": "Rank 2" },
        "colors": { "A": "#0055ff", "B": "#0033aa" }
        }
    },
    "options": {...}
    }
    '''

    pass