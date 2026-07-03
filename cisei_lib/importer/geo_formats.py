from pykml.factory import KML_ElementMaker as KML
from lxml import etree
import json
import geopandas as gpd
from shapely.geometry import Point
import pandas as pd


def pd_to_geojson(data : dict, output_geojson, keys = None):
   
    res = {}
    res['type'] = "FeatureCollection"
    res['features'] = []

    if keys is None:
        keys = data.keys()

    for k in keys:
        for _,r in data[k].iterrows():
            e = {}
            e['type'] = "Feature"
            e['geometry'] = {'type': 'Point', 'coordinates' : [r['long'], r['lat']] }
            e['properties'] = {'type': k, 'uid': r['uid'], 'pole' : r['pole_uid'], 'city' : r['city_name'] }
            res['features'].append(e)

    with open(output_geojson, 'w') as f:
        json.dump(res, f, indent=4)

def dict_to_geojson(elements, info):

    res = {}
    res['type'] = "FeatureCollection"
    res['features'] = []

    for element in elements:
        e = {}
        e['type'] = "Feature"

        if 'lon' in element:
            e['geometry'] = {'type': 'Point', 'coordinates' : [element['lon'], element['lat']] }
            element.pop('lon', None)
            element.pop('lat', None)
        else:
            e['geometry'] = {'type': 'LineString', 'coordinates' : element['coordinates'] }
            element.pop('coordinates', None)

        e['properties'] = element | info

        res['features'].append(e)

    return res

def to_geoJSON_nodes(nodes: dict, s_label: str, path = None):

        data = []
        
        while True:
            entry = {
                'type': 'Feature',
                'geometry': {'type' : 'Point'},
                'properties': None
            }

            start = nodes[s_label]  # Get the start node from your nodes dictionary
            d_label = start['next_hop']  # Get the destination label from the start node

            entry['geometry']['coordinates'] = [ start['pos'][1], start['pos'][0] ]  # lon, lat (correct order)            

            entry['properties'] = {
                'name' : s_label,
                'layer': 'new',
                'iconType': 'new_radio'
            }

            info = {k:v for k,v in start.items() if not isinstance(v, (list, tuple, set, dict))}
            entry['properties'].update(info)
            for k in ('obs_h', 'obs_dm', 'tested'):
                entry['properties'].pop(k, None)

            if 'id_da' in start:
                entry['properties']['id_da'] = start['id_da']
            if 'id_ami' in start:
                entry['properties']['id_ami'] = start['id_ami']

            data.append(entry) # if entry creation is outside the loop copy is required!!!
          
            if d_label is None:
                break  # If no destination, exit the loop (if appropriate)  

            s_label = d_label

        # Create the GeoJSON structure
        geojson = {
            'type': 'FeatureCollection',
            'features': data
        }

        if path:

            if file_name is None:
                file_name = f'{s_label}_{d_label}_geo.json'            
            
            with open(path, 'w') as f:
                json.dump(geojson, f, indent=4)
        
        return data

def json_to_geojson(input_json, output_geojson, info={}):

    with open(input_json, 'r', encoding='utf-8') as f:
        elements = json.load(f)
   
    res = dict_to_geojson(elements, info) 

    with open(output_geojson, 'w') as f:
        json.dump(res, f, indent=4)

def kml_to_geojson(input_kml, output_geojson):
    gdf = gpd.read_file(input_kml, driver='KML')
    geojson = gdf.to_json()
    # Save GeoJSON to file
    with open(output_geojson, 'w') as f:
        f.write(geojson)

def geojson_to_kml(geojson_file, kml_file):
    # Load GeoJSON file into GeoDataFrame
    gdf = gpd.read_file(geojson_file)

    # Create a KML Document
    kml_doc = KML.Document()

    for _, row in gdf.iterrows():
        geom = row.geometry
        properties = row.to_dict()
        properties.pop('geometry', None)  # Remove geometry from properties

        if geom.geom_type == 'Point':
            kml_geom = KML.Point(KML.coordinates(f'{geom.x},{geom.y}'))
        elif geom.geom_type == 'LineString':
            coords = ' '.join([f'{x},{y}' for x, y in zip(geom.xy[0], geom.xy[1])])
            kml_geom = KML.LineString(KML.coordinates(coords))
        elif geom.geom_type == 'Polygon':
            coords = ' '.join([f'{x},{y}' for x, y in zip(*geom.exterior.xy)])
            kml_geom = KML.Polygon(
                KML.outerBoundaryIs(KML.LinearRing(KML.coordinates(coords)))
            )
        else:
            print(f"Unsupported geometry type: {geom.geom_type}")
            continue  # Skip unsupported geometry types


        # Create a KML Placemark with properties
        placemark = KML.Placemark(
            KML.name(properties.get('uid', '')),
            KML.description(properties.get('city', '')),
            kml_geom
        )
        kml_doc.append(placemark)

    # Convert KML.Document to an ElementTree
    kml_etree = etree.ElementTree(kml_doc)

    # Write the KML document to a file
    with open(kml_file, 'wb') as f:
        kml_etree.write(f, pretty_print=True, xml_declaration=True, encoding='UTF-8')

def csv_to_geodaframe(input_csv, x, y, crs=4326, **kwargs):
    '''
    input_csv: csv file
    x: column name of x-projection (longitude)
    y: column name of y-projection (latitude)
    crs: epsg code 4326 for lat,lon or 31982 for UTM Zona 22S
    '''
    # df_raw = gpd.read_file(input_csv)

    usecols = kwargs.get('usecols', None)
    sep = kwargs.get('sep', ';')
    decimal = kwargs.get('decimal', ',')
    name = kwargs.get('name', None)
    encoding = kwargs.get('encoding', 'latin1') # ISO-8859-1
    if name is not None:
        dtype = {name:str}
    else:
        dtype = None

    df = pd.read_csv(input_csv, usecols=usecols, sep=sep, decimal=decimal, dtype=dtype, index_col=False, encoding=encoding)
    df[x] = pd.to_numeric(df[x], errors='coerce')
    df[y] = pd.to_numeric(df[y], errors='coerce')
    df = df[df[x].notna() | df[y].notna()]

    df['geometry'] = df.apply(lambda row: Point(row[x], row[y]), axis=1)

    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs=crs)

    # gdf.set_crs(epsg=crs, inplace=True)
    # WGS 84 (World Geodetic System 1984)
    if crs != 4326:
        gdf = gdf.to_crs(epsg=4326)    

    return gdf

# Remove trailing zeros in a string
def remove_trailing_zeros(s):
    if pd.isna(s):
        return s
    # Split on the comma, if it exists
    parts = s.split(',')
    if len(parts) > 1:
        # Remove trailing zeros in the decimal part
        parts[1] = parts[1].rstrip('0')
        # Rejoin parts
        return ','.join(parts).rstrip(',')
    return s




#---------------------------------------------------------------------
if __name__ == '__main__':

    # Example usage
    # geojson_to_kml('teste_geo.json', 'output.kml')
    # geojson_to_kml('output.kml', 'output_geo.json')

    #geojson_to_kml('teste_geo.json', 'output.kml')
    #json_to_geojson('automacao.json', 'automacao_geo.json')
    # csv_to_geojson('new_da.CSV', 'new_da_geo.json')


    csv_to_geojson_poles('Sirgas2000.csv', 'poles_geo.json')
    #gdf = gpd.read_file('new_da_geo.json')

    # Visualizar
    #gdf.plot()
    #plt.show()



