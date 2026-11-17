from tomlkit import parse
import geopandas as gpd
import pandas as pd
import re
import json
from shapely.geometry import Point, LineString
from pathlib import Path



def read_geojson(file, to_crs=None):
    try:
        gdf = gpd.read_file(file)
        if gdf.empty:
            raise ValueError(f"GeoJSON file '{file}' is empty.")
        if to_crs:
            gdf = gdf.to_crs(to_crs)
        return gdf
    except Exception as e:
        raise RuntimeError(f"Error reading '{file}': {e}")

def save_geojson(gdf, file):
    gdf.to_file(file, driver='GeoJSON')    

def read_rules(rules_path):
    with open(rules_path, 'r') as file:
        rules = parse(file.read())
    return rules

def fix_string_coord(in_file, out_file):

    with open(in_file) as f:
        gj = json.load(f)

        geoms = []
        props = []

        for feature in gj["features"]:
            coords = feature["geometry"]["coordinates"]
            geom_type = feature["geometry"]["type"]
            
            # Convert coordinates from strings to floats (safe for nested lists too)
            if geom_type == "Point":
                x, y = map(float, coords)
                geometry = Point(x, y)
            elif geom_type == "LineString":
                geometry = LineString([[float(x), float(y)] for x, y in coords])
            else:
                raise ValueError(f"Unsupported geometry type: {geom_type}")

            geoms.append(geometry)
            props.append(feature["properties"])

        gdf = gpd.GeoDataFrame(props, geometry=geoms)

        # Set CRS and convert to lat/lon
        gdf.set_crs(epsg=4326, inplace=True)

        # Save to file
        gdf.to_file(out_file, driver="GeoJSON")
    
def transform_rules(gdf, rules):

    safe_globals = {'__builtins__': None, 'int' : int, 'float' : float, 'str' : str, 're' : re, 'len' : len}
    
    gdf_res = gdf.copy()

    def safe_eval(value, expression, default):
        try:
            return eval(expression, safe_globals, {'value' : value})
        except Exception as e:      
            return default

    for r in rules:

        # Remove src column if specified
        if 'remove_src' in r and r['remove_src'] and r['src'] in gdf_res.columns:
            gdf_res.drop(r['src'], axis=1, inplace=True)
        
        if 'dst' not in r:
            continue # nothing else to do           

        if r['src'] not in gdf.columns:     # result is fixed from default
            if 'dst' in r and r['dst'] not in gdf_res.columns:
                gdf_res[r['dst']] = r.get('default', None)
        elif 'expression' in r:             # result comes from expression               
            try:
                default = r.get('default', None)
                gdf_res[r['dst']] = gdf[r['src']].apply(lambda value : safe_eval(value, r['expression'], default))
            except Exception as e:
                pass # ignore rule
        
            
    return gdf_res

def transform_rules_whitelist(gdf, rules):
    safe_globals = {'__builtins__': None, 'int': int, 'float': float, 'str': str, 're': re, 'len': len, 'round' : round}
    
    # Start with an empty DataFrame with the same index as the original
    gdf_res = gpd.GeoDataFrame(index=gdf.index, geometry=gdf.geometry, crs=gdf.crs)

    def safe_eval(value, expression, default):
        try:            
            return eval(expression, safe_globals, {'value': value}) if not pd.isna(value) else default
        except Exception as e:
            return default

    for r in rules:
        if 'dst' not in r:
            continue

        src_col = r.get('src')
        dst_col = r['dst']
        expression = r.get('expression', 'value')
        default_val = r.get('default', None)

        if src_col is None:
            # If no source, we always use the 'default' value
            gdf_res[dst_col] = default_val

        elif src_col not in gdf.columns:
            if dst_col in gdf.columns:
                # If src column is missing, but dst column already exists, copy it as-is.
                gdf_res[dst_col] = gdf[dst_col]
            else:
                # If both src and dst are missing, fall back to the default value.
                gdf_res[dst_col] = default_val        
        else:
            # Source column exists, so we apply the expression
            gdf_res[dst_col] = gdf[src_col].apply(lambda value: safe_eval(value, expression, default_val))

    return gdf_res

def load_rules_from_dir(config_dir, rule_type = 'normalization'):
    merged_rules = []
    meta = {}

    for path in sorted(Path(config_dir).glob("[0-9][0-9]-*.toml")):
        try:
            content = path.read_text(encoding='utf-8')
            data = parse(content)
        except Exception as e:
            print(f"Skipping {path.name}: {e}")
            continue

        temp_meta = data.get("meta")
        if not temp_meta or temp_meta.get("type") != rule_type:
            continue
        
        meta.update(temp_meta)
        rules = data.get("rules", {})
        for tech_name, rule_list in rules.items():
            for rule in rule_list:
                rule_dict = {k: v for k, v in rule.items()}
                rule_dict["tech"] = tech_name
                merged_rules.append(rule_dict)

    return merged_rules, meta


# Example usage
if __name__ == "__main__":
    gdf = read_geojson("bhns_geo.json")
    gdf_out = transform_rules(gdf, 'bhns', "transform_rules.toml")
    save_geojson(gdf_out, "bhns_geo_transformed.json")


