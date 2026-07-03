import msgpack
from copy import deepcopy
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon
from pathlib import Path
import networkx as nx
from cisei_lib.cli.tools.safe_code import tomlkit_encoder, tomlkit_decoder

# new, floating, repeater
class Serializer: 

    def __init__(self, **kwargs: dict):
        
        DEFAULT_CONTEXT = {
            'tomlkit': False,        
            'folder': None,
            'sub_folder': 'state',
            'pack_filename': 'unnamed',
            'nodes_filename': 'nodes_geo.json',
            'links_filename': 'links_geo.json',
            'append': False,
            'layer': 'new'
        }

        self.context = {**DEFAULT_CONTEXT, **kwargs}  

    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):

        if exc_type:
            print(f"Exception: {exc_type}, {exc_val}")
        return False  # Return True if you want to suppress exceptions


    # Remove GeoJSON unsuported types from dict
    def _sanitize_properties(self, records: list[dict]) -> list[dict]:
        allowed_types = (str, int, float, bool, type(None))
        allowed_geom_types = (Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon)

        sanitized = []
        for item in records:
            clean_item = {}
            for k, v in item.items():
                if k == "geometry" and isinstance(v, allowed_geom_types):
                    clean_item[k] = v
                elif isinstance(v, allowed_types):
                    clean_item[k] = v
            sanitized.append(clean_item)
        return sanitized

    # Exports planned nodes with antenna configuration and metadata.
    def to_geojson_nodes(self, new_nodes: dict):

        path = Path(self.context['folder']) / str(self.context['sub_folder']) / str(self.context['nodes_filename'])
        new_nodes = deepcopy(new_nodes)
        if self.context['append'] and path.is_file():            
            gdf = gpd.read_file(path)
            gdf["pos"] = gdf.geometry.apply(lambda p: [p.y, p.x])
            old_nodes = gdf.to_dict(orient="records")            
            old_nodes =  { n['name'] : n for n in old_nodes }             
            old_nodes.update(new_nodes) # this exposes new_nodes as references
            nodes = list(old_nodes.values())           
        else:
            nodes = new_nodes.values()             

        for n in nodes:
            if 'geometry' not in n:
                lat, lon = n.pop('pos')
                n['geometry'] = Point(lon, lat)
            if n['hop_type'] == 'rep': n['iconType'] = 'repeater'
            else: n['iconType'] = 'new'

        nodes = self._sanitize_properties(nodes)        
        gdf = gpd.GeoDataFrame(nodes, geometry='geometry', crs="EPSG:4326")

        gdf.to_file(path, driver="GeoJSON")

        return gdf

    # Exports modeled links with RF quality estimates.
    def to_geojson_links(self, edges: list, nodes: dict):
        path = Path(self.context['folder']) / str(self.context['sub_folder']) / str(self.context['links_filename'])

        new_links = {}
        for u, v, data in edges:
            lat1, lon1 = nodes[u]['pos']
            lat2, lon2 = nodes[v]['pos']
            record = {'src': u, 'dst': v}
            record.update({k: v for k, v in data.items() if k != 'geometry'})
            record['geometry'] = LineString([(lon1, lat1), (lon2, lat2)])
            new_links[(u, v)] = record

        if self.context['append'] and path.is_file():
            gdf = gpd.read_file(path)
            old_links = { (l['src'], l['dst']) : l for l in gdf.to_dict(orient="records") }
            old_links.update(new_links)
            links = old_links.values()
        else:
            links = new_links.values()

        links = self._sanitize_properties(links)   
        gdf = gpd.GeoDataFrame(links, geometry='geometry', crs="EPSG:4326")
        gdf.to_file(path, driver="GeoJSON")
        return gdf

    # Serialize nx.Graph                                                               
    def serialize_G(self, G: nx.Graph):
        nodes = list(G.nodes(data=True))
        edges = list(G.edges(data=True))
        data = {'nodes': nodes, 'edges': edges}
        self.serialize_bin(**data)

    # Deserialize nx.Graph
    def deserialize_G(self, idx=None):
        data = self.deserialize_bin(idx)
        G = nx.Graph()
        G.add_nodes_from(data['nodes'])
        G.add_edges_from(data['edges'])
        return G

    # Serialize data already converted to types compatible with msgpack
    def serialize_bin(self, **data):

        if not self.context['folder']:
            raise Exception('Serializer: folder is missing in self.context')
        
        folder = Path(self.context['folder']) / str(self.context['sub_folder'])
        folder.mkdir(parents=True, exist_ok=True)
        
        pack_files = list(folder.glob(f"{self.context['pack_filename']}*.pack"))
        if self.context['append']:
            pack_files = sorted(pack_files, key=lambda f: int(f.stem.split('_')[-1]))

            n = int(pack_files[-1].stem.split('_')[-1]) if pack_files else -1
            file = f'{self.context['pack_filename']}_{n + 1}.pack'       
        else:
            for file in pack_files:
                if file.is_file(): file.unlink()
            file = f'{self.context['pack_filename']}_0.pack' 

        
        file = folder / file        
        with open(file, "wb") as f:
            if self.context['tomlkit']:
                f.write(msgpack.packb(data, default=tomlkit_encoder))   
            else:
                f.write(msgpack.packb(data, use_bin_type=True)) 


    # Retrieve serialized data as a tuple
    def deserialize_bin(self, idx = None):

        folder = Path(self.context['folder']) / str(self.context['sub_folder']) 
        if not folder.is_dir():
            return None

        if idx is None:
            pack_files = list(folder.glob(f"{self.context['pack_filename']}*.pack"))  
            if len(pack_files) > 1:
                pack_files = sorted(pack_files, key=lambda f: int(f.stem.split('_')[-1]))
            path = pack_files[-1] if pack_files else None
        else:
            path = folder /  f'{self.context['pack_filename']}_{idx}.pack'                    
        
        try:
            with open(path, "rb") as f:       
                raw_data = f.read()         
                if self.context['tomlkit']:
                    data =  msgpack.unpackb(raw_data, object_hook=tomlkit_decoder)    
                else:
                    data = msgpack.unpackb(raw_data, raw=False, strict_map_key=False)   
            return data
                
        except FileNotFoundError:
            return None