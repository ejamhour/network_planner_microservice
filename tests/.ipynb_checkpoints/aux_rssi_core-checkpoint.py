import cisei_lib.core.rf.rf_engine as rf
from geopy.distance import distance
from cisei_lib.core.profiles.geo_info import DSFlags, P2PLink
import json
from pathlib import Path
from itertools import batched
import cisei_lib.core.rf.rssi_core as rc

def to_path(file):
    current_dir = Path.cwd()    
    return Path(current_dir.parent / 'data' / file )

def find_record(r, records):
    
    rx_name = r['rx']['name']
    tx_name = r['tx']['name']

    found = None
    for idx, r in enumerate(records):
        if r['tx']['name'] == tx_name and r['rx']['name'] == rx_name:
            found = idx
            break 

    if found is not None:
        return records[found]

def define_link(record, log = False):
    link = record['rx']
    rx, rx_ha = link['pos'], link['ant_height']
    link = record['tx']
    tx, tx_ha = link['pos'], link['ant_height']
    
    D = distance(tx,rx).meters
    if int(tx[0]) != int(rx[0]) or int(tx[1]) != int(rx[1]):
        if log: print('Links is cross-tile')
    if D < 100:
        if log:  print(f'link is too short: {D}')
        return None
    if rx_ha < 7:        
        if log: print(f"rx_ha adjusted from {rx_ha}")
        rx_ha = 7
    if tx_ha < 7:        
        if log: print(f"tx_ha adjusted from {tx_ha}")
        tx_ha = 7

    return P2PLink(tx, rx, tx_ha, rx_ha, 900)

def explain_eval(w):
    print("idx:", w["idx"])
    print("measured:", w["measured"])
    print("predicted:", w["predicted"]) 
    exp = w["explain"]
    
    print("\nFixed terms:")
    for name, v in exp["fixed"].items():
        print(name, v["value"],  v["contribution"])
    
    print("\nLearned terms:")
    for name, v in exp["learned"].items():
        print(name, v["value"],  v["contribution"])

def calc_features(record, rfo):
    link = define_link(record)    
    rfo.load_profile(link)
    features = rfo.evaluate_link()

    # print(features)
    # print(record['features'])
    return features

def recalc_diffra(record, rfo):
    link = define_link(record)    
    rfo.load_profile(link)
    dl = rfo.diffraction_loss()
    r_dl = record['features']['delta_diffra']
    
    return dl == r_dl, r_dl, dl

# Add features to link files
def chunk_processing(input_file, output_file, chunk_size =200):

    with open(to_path(input_file), 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    data_chunks = batched(data, chunk_size) 

    rfo = rf.RFEngine()

    for i, data in enumerate(data_chunks):
        res = []  
        for record in data:
            try:              
                record['features'] = calc_features(record, rfo)            
                res.append(record)
            except Exception as e:            
                print('one link was ignored:', str(e))

        file = to_path(f'chunk_{i}.json')
        file.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8",)
        print(f"Chunck {i} was completed")   

    base_path = to_path('.')
    output_path = to_path(output_file)
    merge_chunks(base_path, output_path)

def merge_chunks(base_path: Path, output_file: Path):
    merged_data = []

    # 1. Iterate through subdirectories matching the pattern
    for file_path in base_path.glob('chunk_*'):
        try:
            with file_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Ensure data is a list before extending
                if isinstance(data, list):
                    merged_data.extend(data)
                else:
                    # Handle case where file might contain a single dict
                    merged_data.append(data)
                    
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error processing {file_path}: {e}")

    # 2. Write the aggregated list to a single JSON file
    with output_file.open('w', encoding='utf-8') as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=4)

    print(f"Merged {len(merged_data)} records into {output_file}")  

def add_prediction(input_file, output_file, model, n = 50):
    features_file = to_path(input_file)    
    records = rc.load_json(features_file)

    records = [
        r for r in records
        if isinstance(r.get("features"), dict)
    ]

    rules = [
        {"feature_path": "measures.rssi", "min_value": -120, "max_value": -35, "include_max": True},
        {"feature_path": "tx.pw", "min_value": 0, "max_value": 40, "include_max": True},
        {"feature_path": "tx.ant_gain", "min_value": 0, "max_value": 40, "include_max": True},
        {"feature_path": "rx.ant_gain", "min_value": 0, "max_value": 40, "include_max": True},
        # {"feature_path": "features.tx_near_terminal_clearance_m", "min_value": -20, "max_value": 20, "include_max": True}
    ]

    clean = rc.filter_records_by_many(records, rules)
    print("clean records:", len(clean))

    final_model = rc.ExpressionModel.load(to_path(model))

    rows = []

    for i, r in enumerate(clean):   # or clean, borderline, bad
        exp = final_model.explain(r)
        if exp is None or exp["residual"] is None:
            continue

        rows.append({
            "idx": i,
            "measured": exp["measured"],
            "predicted": exp["prediction"],
            "residual": exp["residual"],
            "abs_error": abs(exp["residual"]),
            "record": r,
            "explain": exp,
        })

    worst = sorted(rows, key=lambda x: x["abs_error"], reverse=True)[:n]

    with open(to_path(output_file), "w", encoding="utf-8") as f:
        json.dump(worst, f, indent=4)  # indent=4 makes it readable

    return worst
    
def check_prediction_file(prediction_file, features_file):
    worst = rc.load_json(to_path(prediction_file))
    records = rc.load_json(to_path(features_file))
    rfo = rf.RFEngine()
        
    good = 0
    for w in worst:
        r = w['record']
        s_r = find_record(r, records )
        
        status, r_dl, dl = recalc_diffra(w['record'], rfo)    
        if abs( r_dl - dl) > 1:
            status, s_dl, s_dl = recalc_diffra(s_r, rfo)
            print(f'file: {r_dl} -> calculated : {dl} -> source : {s_dl}')        
        else:
            good += 1
            
    print('Good links', good)
            

