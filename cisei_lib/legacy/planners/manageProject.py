import os
import pandas as pd
import geopandas as gpd
from cisei_lib.cli.usermng.homeFolder import user_home
import cisei_lib.cli.elevation.rfModel as rfModel
import matplotlib.pyplot as plt
from shapely import Polygon, MultiPolygon
from shapely.geometry import LineString
from shapely.strtree import STRtree
from geopandas.tools import sjoin
import tomlkit
import traceback
from pathlib import Path
from cisei_lib.cli.usermng.configTools import configRadio


class manageProject:
    '''
    Extracts info from datasets and create a project folder
    Datasets must be in geojson format    
    '''
        
    def __init__(self, name, home : user_home, radio_model_file = None):
        
        self.name = name
        self.home = home
        self.project_dir = None
        self.log = 'manageProject.log'  
        self.nodes = {}
        self.toml = None
        
        if not home.check_folders():            
            raise(Exception('manageProject: user_home is not defined!'))
        
        dir = home.get_project_path(name, '')
        if not os.path.isdir(dir):
            os.mkdir(dir)

        self.conf = configRadio(home,radio_model_file)
               
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        # Do not re-raise the exception
        return False

    # Saves all self.nodes to geojson files
    def save(self, node_type = None):

        if node_type is None:
            for k,v in self.nodes.items():
                if v is not None:
                    f = self.home.get_project_path(self.name, f'{k}_geo.json')
                    v.to_file(f, driver='GeoJSON')
        elif self.nodes[node_type] is not None: 
            f = self.home.get_project_path(self.name, f'{node_type}_geo.json')
            self.nodes[node_type].to_file(f, driver='GeoJSON')

        if self.toml is not None:
            file = self.home.get_project_path(self.name, 'project.toml')
            with open(file, "w", encoding="utf-8") as f:
                f.write(self.toml.as_string()) 

    # Loads all self.nodes from geojson files
    def load(self, node_type = None):

        folder_path = Path(self.home.get_project_path(self.name, ''))
        geo_files = list(folder_path.glob("*_geo.json"))
        
        if node_type is None:
            for f in geo_files:
                node_type = f.stem.replace("_geo", "")
                self.nodes[node_type] = gpd.read_file(f)
        else: 
            f = self.home.get_project_path(self.name, f'{node_type}_geo.json')
            if os.path.isfile(f):
                self.nodes[node_type] = gpd.read_file(f)

    # Defines a polygon that encompasses all nodes
    def define_region(self, gdf, rect=False, plot=False ):

        utm_gdf = gdf.to_crs(epsg=31982) # UTM Zona 22S
        region = utm_gdf.unary_union.convex_hull
        utm_region = region.buffer(5000) #TODO: add to configuration file
        region = gpd.GeoSeries([utm_region], crs=31982).to_crs(epsg=4326).iloc[0]

        
        if rect:
            region = region.minimum_rotated_rectangle
        if isinstance(region, Polygon):
            coordenadas = list(region.exterior.coords)  # Para um Polígono (contorno externo)
        elif isinstance(region, MultiPolygon):
            coordenadas = [list(poligono.exterior.coords) for poligono in region]  # Para um MultiPolygon
        else:
            coordenadas = []

        if plot and coordenadas != []:
            ax = gdf.plot(color='blue', marker='o', label='nodes')   
            gpd.GeoSeries(region).plot(ax=ax, color='lightgreen', edgecolor='green', alpha=0.5, label='region')
            plt.show()
                
        return region    
           
    # Extracts nodes from a dataset 
    def add_node(self, dataset, node_type, properties=None, regions=None ):

        f = self.home.get_dataset_path(dataset, f'{node_type}_geo.json')

        if not os.path.isfile(f):
            self.home.write_log(self.log, f'add_node: file {f} not found!')
            return

        gdf = gpd.read_file(f)

        if properties is not None:
            filter = True
            for k,v in properties.items():
                if v is None or v == 'null':
                    filter &= (gdf[k].isnull())
                else:
                    filter &= (gdf[k] == v)
            gdf = gdf[filter]

        region = None       
        if regions is not None:
            for r in regions.values():
                if region is None:
                    region = Polygon(r)
                else:
                    region = region.union(Polygon(r))        
            
        if region is not None:
            gdf = gdf[gdf.within(region)]
  

        if node_type in self.nodes and self.nodes[node_type] is not None:    
            gdf_combined = gpd.GeoDataFrame(pd.concat([self.nodes[node_type], gdf], ignore_index=True))
            self.nodes[node_type] = gdf_combined.drop_duplicates(subset='name', keep='last')
        else:
            self.nodes[node_type] = gdf 

        self.create_config({'action': 'add', 'dataset': dataset, 'nodes': node_type, 'properties': properties, 'regions': regions})

    # Removes nodes from self.nodes geodataframes
    def drop_node(self, node_type, properties=None, regions=None ):

        if self.nodes[node_type] is None:   
            return

        gdf = self.nodes[node_type]
        
        if properties is not None:            
            for k,v in properties.items():
                if type(v) == tomlkit.items.Array:
                    v = list(v)
                if v is None or v == 'null':
                    gdf = gdf.dropna(subset=[k])
                elif type(v) == list:
                    gdf = gdf[~gdf[k].isin(v)]       
                else:
                    gdf = gdf[gdf[k] != v]       

        if regions is not None:
            for k,v in regions.items():
                gdf = gdf[~gdf.geometry.within(Polygon(v))]      
                                                                    
        self.nodes[node_type] = gdf
        self.create_config({'action': 'drop', 'nodes': node_type, 'properties': properties, 'regions': regions})
 
    # Creates a TOML file with the project configuration
    def create_config(self, command : dict):

        i_ds = lambda aot, nome: next(
            (i for i, tabela in enumerate(aot) if "name" in tabela and tabela["name"] == nome), -1
        )

        same_properties = lambda ds, command : (command.get('properties', None) == ds.get('properties', None))
        same_regions = lambda ds, command : (command.get('regions', None) == ds.get('regions', None))  

        if self.toml is None:
            self.toml = tomlkit.document()
            self.toml['title'] = 'BHN Planner Project'
            self.toml['name'] = self.name
            self.toml['new_bhnlayer'] = True
            self.toml['regions'] = tomlkit.table()
            self.toml.add(tomlkit.nl())
            self.toml['dataset'] = tomlkit.aot()

        if command['action'] == 'add':  
            id = i_ds(self.toml['dataset'], command['dataset'])
            if id > -1 and same_properties(self.toml['dataset'][id], command) and same_regions(self.toml['dataset'][id], command):
                ds = self.toml['dataset'][id]
                ds['nodes'] += [ command['nodes'] ]
            else:
                ds = tomlkit.table()
                ds['name'] = command['dataset']
                ds['nodes'] = [ command['nodes'] ]
                if 'properties' in command and command['properties'] is not None:
                    ds['properties'] = command['properties']
                self.toml['dataset'].append(ds)                            
            
            if 'regions' in command and command['regions'] is not None:                
                ds['regions'] = list(command['regions'].keys())
                for r in ds['regions']: 
                    if r not in self.nodes.keys():
                        self.toml['regions'][r] = command['regions'][r]

        if command['action'] == 'drop':  

            if 'remove' not in self.toml:
                self.toml.add(tomlkit.nl())
                self.toml['remove'] = tomlkit.aot()               

            ds = tomlkit.table()
            ds['nodes'] = [ command['nodes'] ]
            if 'properties' in command and command['properties'] is not None:
                ds['properties'] = command['properties']

            self.toml['remove'].append(ds)                            
            
            if 'regions' in command and command['regions'] is not None:                
                ds['regions'] = list(command['regions'].keys())
                for r in ds['regions']: 
                    if r not in self.nodes.keys():
                        self.toml['regions'][r] = command['regions'][r]     
   
    # Compile TOML - add nodes
                        self.toml['regions'][r] = command['regions'][r]            
                
    # Compiles TOML - add nodes
    def compile_TOML_add(self, project):
        for d in project['dataset']: 
            if 'name' not in d or 'nodes' not in d:
                self.home.write_log(self.log, f'compile_TOML: name or nodes not found in {d}')
                continue

            p_add = d.get('properties', None)                  

            if 'regions' in d:
                r_add = {}
                for r in d['regions']:
                    if 'regions' in project and r in project['regions']:
                        r_add[r] = project['regions'][r]
                    elif r in self.nodes.keys():
                        r_add[r] = list(self.define_region(self.nodes[r]).exterior.coords)                             
                    else:
                        self.home.write_log(self.log, f'compile_TOML: region {r} not found in dataset.regions')
            else:
                r_add = None

            for n in d['nodes']:
                self.add_node(d['name'], n, properties= p_add, regions=r_add)  

    # Compiles TOML - remove nodes
    def compile_TOML_drop(self, project):

        for d in project['remove']:             
            if 'nodes' not in d:
                self.home.write_log(self.log, f'compile_TOML: nodes not found in {d}')
                continue

            # a tomkit table may contain comments                
            p_drop = d.get('properties', None)              

            if 'regions' in d:
                r_drop = {}
                for r in d['regions']:
                    if 'regions' in project and r in project['regions']:
                        r_drop[r] = project['regions'][r]
                    elif r in self.nodes.keys():
                        r_drop[r] = list(self.define_region(self.nodes[r]).exterior.coords)                             
                    else:
                        self.home.write_log(self.log, f'compile_TOML: region {r} not found in dataset.regions')
            else:
                r_drop = None

            for n in d['nodes']: 
                self.drop_node(n, properties= p_drop, regions=r_drop) 

    # Compiles TOML
    def compile_TOML(self, toml_file):
 
        with open(toml_file, "r", encoding="utf-8") as file:
            project = tomlkit.parse(file.read())
              
        try:
            if 'dataset' not in project:
                self.home.write_log(self.log, f'compile_TOML: dataset not found in {toml_file}', True)
            else:
                self.compile_TOML_add(project)                        
                if 'remove' in project:
                    self.compile_TOML_drop(project)
                  
        except Exception as e:             
            self.home.write_log(self.log, f'compile_TOML: exception {e}', True)
            # print(traceback.format_exc())

    # Find the nearest gdf_A element for each gdf_B element
    def find_nearest(self, gdf_ref, gdf):
        def find_nearest_utm(gdf_A, gdf_B):
            tree = STRtree(gdf_A.geometry)
            names = []
            dists = []
            for geom in gdf_B.geometry:
                nearest_index = tree.nearest(geom)  # get the index of the nearest geometry
                nearest_geom = gdf_A.iloc[nearest_index].geometry
                dist = int(nearest_geom.distance(geom))
                names.append(gdf_A.iloc[nearest_index]['name'])
                dists.append(dist)                    
            
            gdf_B['ref_name'] = names
            gdf_B['ref_dist'] = dists  

            return gdf_B         
        
        utm_ref = gdf_ref.to_crs(epsg=31982) # UTM Zona 22S
        utm_gdf = gdf.to_crs(epsg=31982) # UTM Zona 22S

        gdf_B = find_nearest_utm(utm_ref, utm_gdf)
        gdf_B = gdf_B.to_crs(epsg=4326)
                                    
        return gdf_B       

    # Update pole id
    def update_pole_id(self, gdf):
 
        gdf = self.find_nearest(self.nodes['poles'], gdf)    
        # rename columns ref_name to pole_id and ref_dist to pole_dist
        gdf.rename(columns={'ref_name': 'pole_id', 'ref_dist': 'pole_dist'}, inplace=True)    
        return gdf        


    # Place backhaul nodes in dads not connected
    def update_bhns(self, gdf, max_dist=30):
        if 'bhn' not in gdf.columns:
            gdf['bhn'] = None
            
        gdf_ref = self.nodes['bhns']
        gdf = self.find_nearest(gdf_ref, gdf) 

        gdf.loc[gdf['bhn'].isnull() & (gdf['ref_dist'] < max_dist), 'bhn'] = gdf['ref_name']
        gdf.drop(columns=['ref_dist', 'ref_name'], inplace=True)

        return gdf


    # Create a bhns layer placing radios at the POP
    def create_bhns_layer(self):

        if 'bhns' in self.nodes:
             return
                
        if 'pops' not in self.nodes:
            raise Exception(f'create_bhns_layer: project is unfeasible without bhns and pop')  
        
        # Assure that a new bhns layer will be created ignoring the dataset 
        if 'dads' in self.nodes:
            self.nodes['dads']['bhn'] = None
    
        cols = ['geometry', 'name', 'next_hop', 'id_da', 'id_ami', 'pole_id']
        if 'bhns' not in self.nodes:
            self.nodes['bhns'] = self.nodes['pops'][cols]
        


    # Creates a new backhaul layer representing nodes that need to be planned
    def create_new_layer(self):       

        devices = [ d for d in ['dads', 'amigs'] if d in self.nodes ]
 
        # Create an empty geopandas data frame
        cols = ['geometry', 'name', 'next_hop', 'id_da', 'id_ami', 'pole_id', 'pole_dist']
        gdf_new = gpd.GeoDataFrame(columns=cols, geometry='geometry', crs="EPSG:4326")

        for d in devices:
            self.nodes[d] = self.update_bhns(self.nodes[d])                      
            gdf = self.nodes[d][self.nodes[d]['bhn'].isnull()]          
            gdf = self.update_pole_id(gdf) # Should I use the coordinates of the pole?
            gdf = gdf[['geometry', 'name', 'pole_id', 'pole_dist']]

            gdf['layer'] = 'new'
            gdf['iconType'] = 'new_radio'
            gdf['ant_dB'] = None
            gdf['ant_type'] = None
            gdf['ant_azimuth'] = None
            gdf['network'] = self.name
            gdf['next_hop'] = None

            pole_ids = gdf_new['pole_id'].unique()           
            gdf_included = gdf[~gdf['pole_id'].isin(pole_ids)].copy()
            gdf_excluded = gdf[gdf['pole_id'].isin(pole_ids)].copy()

            if d == 'dads':                
                gdf_included['id_da'] = gdf_included['name'].copy()
            if d == 'amigs':
                gdf_included['id_ami'] = gdf_included['name'].copy()   


            gdf_new = pd.concat([gdf_new, gdf_included])

            for _, row in gdf_excluded.iterrows(): 
                if d == 'dads':
                    gdf_new.loc[gdf_new['pole_id'] == row['pole_id'], 'id_da'] = row['name']
                if d == 'amigs':
                    gdf_new.loc[gdf_new['pole_id'] == row['pole_id'], 'id_ami'] = row['name']
                    print(row['name'])
            

        names = [ f'{self.name}_BHN_{i + 1}' for i in range(gdf_new.shape[0]) ]
        gdf_new['name'] = names
       
        self.nodes['new'] = gdf_new

    # Create a JSON file with links between bhn radios 
    def create_bhn_links(self):

        if 'bhns' not in self.nodes:
            return
        
        rf = rfModel.rfModel(cover_height=self.conf.get('ENVIRONMENT.cover_height')) 
        
        data = []       

        for row in self.nodes['bhns'].itertuples(): # itertuples is faster than iterrows

            entry = {
                'type': 'Feature',
                'geometry': {'type' : 'LineString'},
                'properties': None
            }            
            res = self.nodes['bhns'].query("name == @row.next_hop")
            
            if not res.empty:
                row_end = res.iloc[0]
                if row.geometry == row_end.geometry:
                    continue # probably back-to-back
                coord_1 = (row.geometry.x, row.geometry.y)  # Start coordinates (lon, lat)
                coord_2 = (row_end.geometry.x, row_end.geometry.y)       # End coordinates (lon, lat)
                entry['geometry']['coordinates'] = [coord_1, coord_2]                             
               
                entry['properties'] = {
                    'name': f"{row.name}:{row_end['name']}",
                    'src': row.name,
                    'dst': row_end['name'], # row end is a series and attributes are accessed with []
                }

                link_kwargs = {
                    'ha1' : row.ant_h,
                    'ha2' : row_end['ant_h'],
                    'ga1' : row.ant_dB,
                    'ga2' : row_end['ant_dB'],
                    'pw' : row.radio_pw,
                    'tm' : self.conf.get('ENVIRONMENT.trees.tree_model'),
                    'td' : self.conf.get('ENVIRONMENT.trees.tree_delta')
                }   

                qos = rf.link_quality(coord_1[::-1], coord_2[::-1], **link_kwargs)
                entry['properties'].update(qos) 
                
                data.append(entry)


        geometries = [LineString(e['geometry']['coordinates']) for e in data]
        properties = [e['properties'] for e in data]

        # Create GeoDataFrame
        self.nodes['bhlinks'] = gpd.GeoDataFrame(properties, geometry=geometries, crs="EPSG:4326")
        
    # Execute the most common sequence to create a new project
    def run(self, toml_file=None):

        try:
            if toml_file is not None:
                f = self.home.get_upload_path(toml_file)
                self.compile_TOML(f)         
            else:
                self.load()
            self.create_bhns_layer()
            self.create_new_layer() 
            self.create_bhn_links()         
            self.save()             
            #print(m.define_region(m.nodes['dads'], plot=True))
        except Exception as e:
            self.home.write_log(self.log, f'manageProject: {e}', True) 
            # print(traceback.format_exc())

if __name__ == '__main__':
    
    pass

    

