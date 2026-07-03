import zipfile
from lxml import etree
from bs4 import BeautifulSoup
import json
import os
import geopandas as gpd
import pandas as pd
import unicodedata
from cisei_lib.cli.usermng.homeFolder import user_home
from cisei_lib.cli.importer.geo_formats import dict_to_geojson, csv_to_geodaframe, remove_trailing_zeros
from shapely.geometry import Point, shape
import cisei_lib.cli.importer.gdf_transformer as tr


# Creates a dataset from a KML file.

def replace_special_chars(texto):
    return ''.join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )

def html_table_to_dict(html):

    """
    Transforma uma string com uma tabela HTML em um dicionário Python.
    
    Parameters
    ----------
    html : str
        String com a tabela HTML.
    
    Returns
    -------
    table_data : dict
        Dicionário com os dados da tabela, onde cada chave é o valor da primeira coluna e o valor é o valor da segunda coluna.
    """
    soup = BeautifulSoup(html, 'lxml')

    # Encontre a tabela
    table = soup.find('table')
    
    # Inicializa uma lista para armazenar os dados
    table_data = {}
    
    # Iterar sobre as linhas da tabela
    for row in table.find_all('tr'):  # Ignorar o cabeçalho?
        columns = row.find_all('td')                
        row_data = [ col.get_text(strip=True) for col in columns ]
        
        
        # Cria um dicionário para a linha atual
        table_data[row_data[0]] = row_data[1] 
    
    return table_data

class importDataset:
    def __init__(self, dataset, home : user_home):
        self.home = home
        self.dataset = dataset

        self.log = 'importDataset.log'

        self.home.clear_log(self.log)
        
        if not home.check_folders():            
            raise(Exception('importDataset: user_home is not defined!'))
        
        dataset_path = self.home.get_dataset_path(self.dataset, '')
        if not os.path.isdir(dataset_path):
            os.makedirs(dataset_path)        
                            
    def __enter__(self):
        return self

    # Verifies dataset consistency after importing
    def verify_dataset(self, element_type: str = 'dads'):

        file = self.home.get_dataset_path(self.dataset, f'{element_type}_geo.json')         
        
        try:
            gdf = gpd.read_file(file)
        except Exception as e:
            self.home.write_log(self.log, 'verify_dataset: ' + e)
            raise(Exception('verify_dataset: ' + e))
       
        miss_net = 0
        miss_bhn = 0
        miss_name = 0

        for row in gdf.itertuples():
            if not hasattr(row, 'name') or row.name is None:
                miss_name += 1
            if not hasattr(row, 'network') or row.network is None or len(str(row.network)) < 3:                
                miss_net += 1
                if hasattr(row, 'bhn') and row.bhn is not None:
                    self.home.write_log(self.log, f"verify_dataset:{row.name} with bhn={row.bhn} without net")
                else:
                    self.home.write_log(self.log, f"verify_dataset:{row.name} without net")
            if not hasattr(row, 'bhn') or row.bhn is None:
                miss_bhn += 1
        
        self.home.write_log(self.log, f"verify_dataset: Missing name: {miss_name} Missing network: {miss_net} Missing backhaul: {miss_bhn}")

    # Adds cross-infortion about backhaul nodes into application level devices (dads, amigs)
    def connect_to_bhn(self, element_type: str = 'dads', bhns_dataset = None):
        
        """ 
        Adds cross-infortion about backhaul nodes into application level devices (dads, amigs)   
        """

        if bhns_dataset is None:
            bhns_dataset = self.dataset

        file = self.home.get_dataset_path(bhns_dataset, 'bhns_geo.json')

        if not os.path.isfile(file):
            msg = f"connect_to_bhn: file {file} not found!"
            self.home.write_log(self.log, msg)
            return # non-fatal error
        
        gdf = gpd.read_file(file)
        bhns = gdf.to_dict('records')            

        if element_type == 'dads':
            file = self.home.get_dataset_path(self.dataset, 'dads_geo.json')
            id_element = 'id_da'
        elif element_type == 'amigs':
            file = self.home.get_dataset_path(self.dataset, 'amigs_geo.json')
            id_element = 'id_ami'
        else:
            msg = f"connect_to_bhn: {element_type} is not valid!"
            self.home.write_log(self.log, msg)
            raise(Exception(msg))
        
        if not os.path.isfile(file):
            msg = f"connect_to_bhn: file {file} not found!"
            self.home.write_log(self.log, msg)
            raise(Exception(msg))
        
        gdf = gpd.read_file(file)
        records = gdf.to_dict('records')            

        irecords = {d['name'] : d for d in records}

        # for dads that are connected to the backhaul
        for b in bhns:
            if b[id_element] in irecords:
                irecords[b[id_element]]['bhn'] = b['name']
                irecords[b[id_element]]['network'] = b['network']
            else:
                if b[id_element] is not None and len(b[id_element]) > 3: # if the dad id is not valid (-)
                    self.home.write_log(self.log, f"Warning (connect_to_bhn): dad {b[id_element]} not found!")

        for record in records:
            record['geometry'] = shape(record['geometry'])
        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        gdf.to_file(file, driver='GeoJSON')

    # Find the nearest pop to reference (lat, lon)
    # -- called by orfan_dads
    def find_nearest_pop(self, reference, pops_dataset = None):
        """
        Find the nearest pop with respect to a reference (lat,lon) and returns its network
        
        """
        if pops_dataset is None:
            pops_dataset = self.dataset
        file = self.home.get_dataset_path(pops_dataset, 'pops_geo.json')

        if not os.path.isfile(file):
            msg = f"find_nearest_pop: file {file} not found!"
            self.home.write_log(self.log, msg)
            raise(Exception(msg))           
        gdf = gpd.read_file(file)


        reference = Point(reference[1], reference[0])
        gdf_projected = gdf.to_crs(epsg=31982) # UTM Zona 22S

        gdf_projected['dist'] = gdf_projected.geometry.distance(reference)
        nearest = gdf_projected.loc[gdf_projected['dist'].idxmin()]
        return nearest['network']

    # Finds application level devices that are not connected to the backhaul
    def find_network(self, element_type: str = 'dads', pops_dataset = None):

        file = self.home.get_dataset_path(self.dataset, f'{element_type}_geo.json')         
        
        try:
            gdf = gpd.read_file(file)
        except Exception as e:
            self.home.write_log(self.log, 'find_network: ' + e)
            raise(Exception('find_network: ' + e))           
        
        networks = []

        for row in gdf.itertuples():
            if hasattr(row, 'network') and row.network is not None and len(str(row.network)) > 3:
                networks.append(row.network)
            else:
                networks.append(self.find_nearest_pop((row.geometry.y, row.geometry.x), pops_dataset))             
                
        gdf['network'] = networks

        gdf.to_file(file, driver='GeoJSON')

    # Generate dataset info.json
    def build_dataset_info(self): 

        elements = ['dads', 'pops', 'amigs', 'bhns', 'poles']

        info = {}

        for element in elements:
            file = self.home.get_dataset_path(self.dataset, f'{element}_geo.json')
            if os.path.isfile(file):
                gdf = gpd.read_file(file)
                lon_w, lat_s, lon_e, lat_n = gdf.total_bounds # minx, miny, maxx, maxy 

                info[element] = {
                    "file": f'{element}_geo.json',
                    "lon_w": lon_w,
                    "lon_e": lon_e,
                    "lat_n": lat_n,
                    "lat_s": lat_s
                    } 

        file = self.home.get_dataset_path(self.dataset, 'info.json')
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=4, ensure_ascii=False)
    
    # Apply rules
    def apply_rules(self, rules_toml):
        rules = tr.read_rules(self.home.get_configuration_path(rules_toml))

        for element_type in rules.keys():
            file = self.home.get_dataset_path(self.dataset, f'{element_type}_geo.json')
            if os.path.isfile(file):
                gdf = gpd.read_file(file)
                gdf = tr.transform_rules(gdf, rules[element_type])
                gdf.to_file(file, driver='GeoJSON')

class importKML(importDataset):

    def __init__(self, dataset, home : user_home):

        super().__init__(dataset, home)
        self.log = 'importKML.log'
        self.home.clear_log(self.log)

    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        # Do not re-raise the exception
        self.home.write_log(self.log, 'importKML: exiting...')
        return False
          
    # Extact kml from kmz
    def kmz_to_kml(self, kmz_file, kml_file):
        """
        Extract a KML file from a KMZ file.
        """
        kmz_path = self.home.get_upload_path(kmz_file)
        kml_path = self.home.get_upload_path(kml_file)

        if not os.path.isfile(kmz_path):
            raise(Exception('importKML: kmz file does not exist!'))

        with zipfile.ZipFile(kmz_path, 'r') as kmz:
            # Extraia o arquivo KML
            for file_name in kmz.namelist():
                if file_name.endswith('.kml'):
                    with kmz.open(file_name) as kml:
                        with open(kml_path, 'wb') as f:
                            f.write(kml.read())
                    break

    # Extract project information from kml as lists of dicts (pops, dads, bhns and amigs) 
    # -- called by import_project 
    # -- this function must be revised because it is dependent of field names in the KML
    def extract_nodes(self, kml_file, all_info):

        kml_path = self.home.get_upload_path(kml_file) 
        tree = etree.parse(kml_path)
        root = tree.getroot()       
               
        
        namespace = '{http://www.opengis.net/kml/2.2}'
        
        placemarks = root.findall('.//' + namespace + 'Placemark')   
        placemarks_with_point = [pm for pm in placemarks if pm.find(namespace + 'Point') is not None]
        
        dads = []
        pops = []
        bhns = []
        amigs = []

        for placemark in placemarks_with_point:
            #print(etree.tostring(placemark, pretty_print=True, encoding='unicode'))
            element = {}
            e = placemark.find(namespace + 'name')               
            if e is not None:            
                element['name'] = e.text

            e = placemark.find(namespace + 'description') 
            if e is not None:         
                dados = html_table_to_dict(e.text)

            point = placemark.find(namespace + 'Point')    
            name_element = point.find(namespace + 'coordinates')   

            try:            
                if name_element is not None:            
                    coords = name_element.text.strip().split(',')
                    element['lon'] = float(coords[0]) 
                    element['lat'] = float(coords[1])    
                    if all_info:
                        for k, v in dados.items():   
                            element[replace_special_chars(k)] = replace_special_chars(v)
                if 'operacional' in dados: # DA
                    dads.append(element)  
                elif 'Equipamento' in dados: # P70
                    pops.append(element)
                elif 'Tipo' in dados and dados['Tipo'] in ['ECR', 'MCR', 'P70']: 
                    bhns.append(element)
                elif 'nicCount' in dados:
                    amigs.append(element)

            except Exception as e:
                self.home.write_log(self.log, 'extract_nodes: ' + str(e))


        return pops, dads, bhns, amigs
        
    # Save project imported from kml into json files
    def create_dataset(self, kml_file, all_info=True):  
    
        pops, dads, bhns, amigs = self.extract_nodes(kml_file, all_info)
        for element_type, data in (
            ('pops', pops),
            ('dads', dads),
            ('bhns', bhns),
            ('amigs', amigs),
        ):           
            with open(self.home.get_dataset_path(self.dataset, f'{element_type}_geo.json'), 'w', encoding='utf-8') as f:
                json.dump(dict_to_geojson(data, info={'layer': element_type}), f, indent=4, ensure_ascii=True)
           
class importCSV(importDataset):
    def __init__(self, dataset, home : user_home):
        super().__init__(dataset, home)

        self.log = 'importCSV.log'

        if not home.check_folders():            
            raise(Exception('importCSV: user_home is not defined!'))    

        self.home = home    

        self.home.clear_log(self.log)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False
    
    # Merge gdf
    def merge_gdf(self, gdf1, gdf2):
        gdf = pd.concat([gdf1, gdf2])
        gdf = gdf.drop_duplicates(subset=['name'], keep='first')
        return gdf


    # Append if file exists
    def import_poles(self, input_csv, **kwargs):   
        x = kwargs.get('x', 'COORD_X') # longitude os x-projection
        y = kwargs.get('y', 'COORD_Y') # latitude os y-projection
        crs = kwargs.get('crs', 31982) # SIRGAS 2000 - UTM Zona 22S
        name = kwargs.get('name', 'NUM_SEQ_GE')
        all_info = kwargs.get('all_info', False)

        if all_info:
            usecols = None
        else:
            usecols=[name,x,y]
        gdf = csv_to_geodaframe(self.home.get_upload_path(input_csv), x, y, crs, usecols=usecols, name=name)
       
        gdf[name] = gdf[name].apply(remove_trailing_zeros)

        gdf.rename(columns={name: 'name'}, inplace=True)
    
        file = self.home.get_dataset_path(self.dataset,'poles_geo.json')
        gdf.to_file(file, driver='GeoJSON')

    # Append if file exists
    def import_dads(self, input_csv, **kwargs):

        x = kwargs.get('x', 'COORD_X') # longitude os x-projection
        y = kwargs.get('y', 'COORD_Y') # latitude os y-projection
        crs = kwargs.get('crs', 31982) # SIRGAS 2000 - UTM Zona 22S
        name = kwargs.get('name', 'operacional')
        all_info = kwargs.get('all_info', False)
        net_field = kwargs.get('network', None)
        
        try:            
            new_gdf = csv_to_geodaframe(self.home.get_upload_path(input_csv), x, y, crs, name=name)
        except Exception as e:
            self.home.write_log(self.log, 'import_dads: ' + str(e))
            raise(Exception('import_dads: ' + str(e)))

        file = self.home.get_dataset_path(self.dataset,'dads_geo.json')
        if os.path.exists(file):
            gdf = gpd.read_file(file)
        else:
            gdf = gpd.GeoDataFrame(columns=['name','geometry'])
            gdf = gpd.GeoDataFrame(gdf, geometry='geometry', crs="EPSG:4326")
        
        for _, row in new_gdf.iterrows():
            geom = row.geometry
            properties = row.to_dict()

            dad = properties[name]
            
            if gdf is None or not (gdf['name'] == dad).any():
                data = {
                    'name' : [ dad ],
                    'geometry' : [ geom ],
                    'layer' : ['dads']
                }

                if net_field is not None:
                    data['network'] = [ properties[net_field] ]
                if all_info:
                    for k, v in properties.items():
                        if k not in [ 'name', 'geometry', 'network']:
                            data[k] = [ v ]

                new_record = gpd.GeoDataFrame(data, crs="EPSG:4326")                

                gdf = pd.concat([gdf, pd.DataFrame(new_record)], ignore_index=True)

        file = self.home.get_dataset_path(self.dataset,'dads_geo.json')
        gdf.to_file(file, driver='GeoJSON')
        
class importJSON(importDataset):
    def __init__(self, dataset, home : user_home):
        super().__init__(dataset, home)

        self.log = 'importJSON.log'

        if not home.check_folders():            
            raise(Exception('importJSON: user_home is not defined!'))    

        self.home = home    

        self.home.clear_log(self.log)

        self.dataset_keys = {
            'dads': {'name', 'lon', 'lat',  'network', 'bhn'},
            'bhns': {'name', 'lon', 'lat',  'network', 'bhn', 'id_da', 'id_ami', 'next_hop'},
            'pops': {'name', 'lon', 'lat',  'network'},              
            'amigs': {'name', 'lon', 'lat'}
        }   

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False
    
    # Export geojson
    def import_elements(self, input_json, element_type, all_info = True):
        if element_type not in self.dataset_keys.keys():
            raise ValueError(f'import_elements: {element_type} not is not valid!')               
 
        with open(self.home.get_dataset_path(self.dataset, f'{type}.json'), 'r') as f:
                data = json.load(f)

        if not all_info:            
            filtered_data = [{key: entry[key] for key in self.dataset_keys[element_type] if key in entry} for entry in data]
        else:
            filtered_data = data
            
        geo_data = dict_to_geojson(filtered_data, info={'layer': element_type})

        with open(self.home.get_dataset_path(self.dataset, f'{element_type}_geo.json'), 'w', encoding='utf-8') as f:
            json.dump(geo_data, f, indent=4, ensure_ascii=False)            

        # Export geojson        

    def export_geojson_all(self, properties: str = 'all'):
        type_options = ['pops', 'dads', 'amigs', 'bhns']
        for t in type_options:
            if os.path.isfile(self.home.get_dataset_path(self.dataset, f'{t}.json')):
                self.export_geojson(t, properties)

if __name__ == '__main__':

        print('It is not possible to run this module without adding the folder path in the __init__.py')


    

    

