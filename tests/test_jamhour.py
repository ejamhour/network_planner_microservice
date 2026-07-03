# Launch planner server
def test_old():
    """Launch the link planner server."""
    import cisei_lib.gui.tools.planner_server_old as ps
    import cisei_lib.globals as g
    ps.app.run(port=g.g_planner_port)

# Evaluate time for analising profile
def test_1():
    import cisei_lib.cli.elevation.rfModel as rfm
    from time import time

    t = []

    s_pos = (-26.178913,-53.072063) # ponto de origem do enlace
    d_pos = (-26.161617,-53.015026) # ponto de destino do enlace

    s_pos = (-26.0653893340446, -49.4545370100964)
    d_pos = (-26.086031876627, -49.44739813282138)

    s_ha = d_ha = 7
    
    with rfm.rfModel() as x:
        t.append(time()) 
        res = x.link_quality(s_pos, d_pos)
        t.append(time())
        dv1, odv1 = x.obstruction( x.h_t, proximity= 0, base_profile=x.hn) 
        t.append(time()) 
        dv2, odv2 = x.obstruction( x.hn, proximity= 0, base_profile=None) 
        t.append(time()) 

        for k,v in res.items(): print(k,':',v)
        print(dv1, odv1)
        print(dv2, odv2)

        
        res = x.geoInfo.highest_obstacle(s_pos, d_pos, s_ha, d_ha)
        t.append(time())
        print(res)

        print('time evaluation:', [b - a for a, b in zip(t, t[1:])], '=', t[-1] - t[0])


# Evaluate links in a monitored network 
def test_1b():
    import cisei_lib.cli.elevation.rfModel as rfm
    from pathlib import Path
    import geopandas as gpd
    import cisei_lib.cli.elevation.geoInfo as gi
    import numpy as np

    x = rfm.rfModel()
    y = gi.geoInfo()
    
    dir = Path.cwd() / 'code_examples' / 'Arquivos' / 'AGU-SE'   
    nodes = gpd.read_file( dir / 'bhnodes_geo.json') 

    pops = nodes[nodes['next_hop'].isna()| (nodes['next_hop'] == 'None')]
    pop = pops.iloc[0]  
    d_pos = pop.geometry.y, pop.geometry.x
    d_ga = pop['ant_dB'] 
    d_rssi = pop['Down Stream Avg RSSI']
    
    srcs = nodes[nodes['next_hop'] == pop['name']]  
    
    s_ha = d_ha = 7
    for _, row in srcs.iterrows():        # srcs.iloc[1:2]
        s_ga = row['ant_dB'] 
        measured = {'name': row['name'],
                    'rssi':row['Parent Node Avg Rssi'], 
                    'snr': row['Parent Node Avg Lqi'],
                    'lqi': row['Parent Node Avg Snr'],
                    'ant_type' : row.ant_type}
        
        s_pos = row.geometry.y, row.geometry.x
        x.get_profile(s_pos, d_pos, s_ha, d_ha)
        y.show_link(s_pos, d_pos, ha_s=s_ha, ha_d=d_ha)
        np.set_printoptions(precision=2, suppress=True)
        f = lambda arr : print(np.array(arr))
        if x.h_t:
            f(x.obstruction(x.h_t,-2, base_profile= x.hn, margin = 3))          
        res = x.link_quality(s_pos, d_pos, ga1= s_ga, ga2 = d_ga, td=-2)        
        print(res)
        print(measured)    
           


# Test the elevation and cover databases
def test_2():
    """Test the paths to elevation and cover databases"""
    import cisei_lib.cli.elevation.geoInfo as gi
    import cisei_lib.cli.elevation.rfModel as rfm
    
    # start = (-26.0653893340446, -49.4545370100964)
    # end = (-26.086031876627, -49.44739813282138)

    start = (-26.178913,-53.072063) # ponto de origem do enlace
    end = (-26.161617,-53.015026) # ponto de destino do enlace

    transmitter_height = 7
    receiver_height = 7
    frequency = 920000000

    geo_info = gi.geoInfo()
    geo_info.show_map(start, end)
    geo_info.show_link(start, end, ha_s=transmitter_height, ha_d=receiver_height)
    
    with rfm.rfModel() as x:
        x.get_profile(start, end, transmitter_height, receiver_height)
        total_loss = x.total_loss(tree_h=2, model='FITU-R')
        print(total_loss)
        rssi = x.compute_RSSI(total_loss, 30, 8.15, 8.15)    
        print(rssi)
        print('elevation and cover precision')
        print(x.geoInfo.res_elev)
        print(x.geoInfo.res_cover)


# Test RF modeling
def test_3():
    import cisei_lib.cli.elevation.rfModel as rfm
    import cisei_lib.cli.elevation.geoTools as gt
    import time

    start = (-26.178913,-53.072063) # ponto de origem do enlace
    end = (-26.161617,-53.015026) # ponto de destino do enlace
    start_h, end_h = 7, 7 # altura das antenas (m)
    tree_h = 2 # incerteza na altura das arvores (m)

    with rfm.rfModel(f=920000000) as rf_model:
        x_time = []
        x_time.append(time.time())        
        rf_model.get_profile(start, end, start_h, end_h)    
        x_time.append(time.time()) 
        print('number of points:', len(rf_model.dn))                        
        total_loss = rf_model.total_loss(tree_h=tree_h, model='FITU-R')
        x_time.append(time.time())                        
                
        pw, start_ga, end_ga   = 30, 8.15, 8.15 # potencia transmitida (dBm), ganhos das antenas (dB)
        rssi = rf_model.compute_RSSI(total_loss, pw, start_ga, end_ga)    
        x_time.append(time.time())                        
        print(f'RSSI (dbm): {float(rssi[1])} até {float(rssi[0])}')
        x_time.append(time.time())      
        res = rf_model.link_quality(start, end)

        for k,v in res.items(): print(k,':',v)

        print('time evaluation:', [b - a for a, b in zip(x_time, x_time[1:])], '=', x_time[-1] - x_time[0])


# Test importsDataset
def test_4():    
    from cisei_lib.cli.importer.import_dataset import importKML, importCSV
    from cisei_lib.cli.usermng.homeFolder import user_home
    import cisei_lib.cli.importer.gdf_transformer as tr

    home = user_home('cisei', 'home')

    test = 'A'
        
    if test == 'A':
        with importKML('fase2', home) as i:
            try:
                #i.kmz_to_kml('fase2.kmz', 'fase2.kml')
                i.create_dataset('fase2.kml', all_info=True)   
                i.apply_rules('transform_rules.toml')
                i.connect_to_bhn('dads')
                i.find_network('dads')
                i.verify_dataset('dads')
                i.build_dataset_info()        
                pass 
            except Exception as e:
                print(e) 

    if test == 'B':
        with importCSV('fase2', home) as i:
            try:
                pass
                i.import_poles('Sirgas2000.csv',  x = 'COORD_X_PS', y = 'COORD_Y_PS', name='NUM_SEQ_GE', all_info = False)

            except Exception as e:
                print(e) 
    
    if test == 'C':
        with importCSV('fase2_exp', home) as i:
            try:
                pass
                i.import_dads('new_da.CSV', network='REDE', all_info = True)
                i.apply_rules('transform_rules.toml')
                i.connect_to_bhn(bhns_dataset='fase2')
                i.find_network('dads', pops_dataset='fase2')  
                i.verify_dataset()  
                i.build_dataset_info()    
            except Exception as e:
                print(e)  

# Test transform dataset
def test_5():
    import cisei_lib.cli.importer.gdf_transformer as tr
    from cisei_lib.cli.usermng.homeFolder import user_home

    home = user_home('cisei', 'home')
    in_file = home.get_dataset_path('fase2', 'bhns_geo.json')        
    gdf = tr.read_geojson(in_file)
    rules = tr.read_rules(home.get_configuration_path('transform_rules.toml'))    
    gdf_out = tr.transform_rules(gdf, rules['bhns'])
    out_file = home.get_dataset_path('fase2', 'bhns_geo2.json')
    tr.save_geojson(gdf_out, out_file)

# Test project management   
def test_6():
    import cisei_lib.cli.bhnplanner.manageProject as mp
    from cisei_lib.cli.usermng.homeFolder import user_home
    import traceback

    home = user_home('cisei', 'home')
    radio_model = 'radio_model_user.toml'
    with mp.manageProject('teste_4', home, radio_model) as m:
        # project must be in upload folder
        m.run('project.toml')
        #print(m.define_region(m.nodes['dads'], plot=True))
        
# Test PoleGraph Search
def test_7():
    from cisei_lib.cli.usermng.homeFolder import user_home
    from cisei_lib.cli.bhnplanner.pole_graph import PoleGraph
    import geopandas as gpd
    import pandas as pd

    home = user_home('cisei', 'home')
    if not home.check_folders():
        home.create_folders()

    project = 'teste_4'

    poles_gdf = gpd.read_file(home.get_project_path(project, 'poles_geo.json') )    
    bhns_gdf = gpd.read_file(home.get_project_path(project, 'bhns_geo.json') ) # backhaul (where to connect)    
    new_gdf = gpd.read_file(home.get_project_path(project, 'new_geo.json') ) # new radios (what to connect)

    new_gdf["pos"] = new_gdf.geometry.apply(lambda geom: [geom.y, geom.x])
    bhns_gdf["pos"] = bhns_gdf.geometry.apply(lambda geom: [geom.y, geom.x])

    dst_node = bhns_gdf.iloc[0].to_dict()
    src_node = new_gdf.iloc[1].to_dict()

    context = {'debug': True, 'search_heuristic': 'sector_band' }

    with PoleGraph(poles_gdf, **context) as x:  
        ant_h = x.context['ant_h']
        obs = x.edge_metric(src_node['pos'], dst_node['pos'], ant_h, ant_h)   
        x.add_edge(src_node, dst_node, obs)
        res = x.get_candidates(src_node['name'], dst_node['name']) 
        print(f"GDF has {len(res)} rows")
        '''
        res = res.to_crs(epsg=4326)
        res1 = x._edge_to_gdf_points(src_node['name'], dst_node['name'])
        res = pd.concat([res, res1], ignore_index=True)
        out_file = home.get_project_path(project, 'poles_sector_geo.json')
        res.to_file(out_file, driver="GeoJSON")                
        '''
        x.show_geometry(src_node['name'], dst_node['name'], res)
        pass

def test_8():
    from cisei_lib.cli.bhnplanner.pole_graph import PoleGraph
    from cisei_lib.cli.bhnplanner.link_planner import LinkPlanner
    from cisei_lib.cli.usermng.homeFolder import user_home
    from cisei_lib.cli.tools.geo_tools import hexbin_layer, hexbin_graph_layer
    import geopandas as gpd
    import pandas as pd
    from cisei_lib.cli.usermng.configTools import configRadio


    home = user_home('cisei', 'home')
    if not home.check_folders():
        home.create_folders()


    conf = configRadio(home)

    pass
                                

    project = 'teste_4'


           
    poles_gdf = gpd.read_file(home.get_project_path(project, 'poles_geo.json') )    
    bhns_gdf = gpd.read_file(home.get_project_path(project, 'bhns_geo.json') ) # backhaul (where to connect)    
    new_gdf = gpd.read_file(home.get_project_path(project, 'new_geo.json') ) # new radios (what to connect)

    new_gdf["pos"] = new_gdf.geometry.apply(lambda geom: [geom.y, geom.x])
    bhns_gdf["pos"] = bhns_gdf.geometry.apply(lambda geom: [geom.y, geom.x])
    nodes_gdf = pd.concat([new_gdf, bhns_gdf], ignore_index=True)

    dst_node = bhns_gdf.iloc[0].to_dict()
    src_node1 = new_gdf.iloc[1].to_dict()

    assert new_gdf.crs == bhns_gdf.crs, "CRS mismatch"

    context = {'debug': True }
    
    test = 3
    if test == 0:
        in_file = home.get_project_path(project, 'poles_geo.json')
        out_file = home.get_project_path(project, 'poles_density_geo.json')
        hexbin_layer(in_file, out_file)
    
    if test == 1: # PoleGraph
        with PoleGraph(poles_gdf, **context) as x:  
            x.run(src_node1, dst_node, False)                    
            pass
    if test == 2: # LinkPlanner    
        src_node2 = new_gdf.iloc[2].to_dict()
        with LinkPlanner(project, home, **context) as x:
            x.run(nodes_gdf, src_node2['name'], dst_node['name'])
            x.export_geojson()
            x.run(nodes_gdf, src_node1['name'], dst_node['name']) 
            x.export_geojson(append=True)             
            x.serialize_plan()  
            pass
    if test == 3: # Serialize        

        with LinkPlanner(project, home, **context) as x:
            x.deserialize_plan()
            src_node3 = new_gdf.iloc[3].to_dict()
            x.run(nodes_gdf, src_node3['name'], dst_node['name'])
            x.export_geojson(append=True)   
            x.serialize_plan()                        

            out_file = home.get_project_path(project, 'graph_density_geo.json')
            hexbin_graph_layer(x.G, x.d_label, out_file)                     

            pass


    if test == 4: # Plan Antennas

        pass 


# Test link quality and antenna
def test_9():
    from cisei_lib.cli.usermng.homeFolder import user_home
    import cisei_lib.cli.elevation.rfModel as rfModel
    from cisei_lib.cli.usermng.configTools import configTools, configRadio
    
    home = user_home('cisei', 'home')
    if not home.check_folders():
        home.create_folders()

    start = (-26.0653893340446, -49.4545370100964)
    end = (-26.086031876627, -49.44739813282138)   

    # conf = configRadio(home, 'radio_model_user.toml')
    conf = configRadio(home)

    rf = rfModel.rfModel(cover_height=conf.get('ENVIRONMENT.cover_height'))      
    ants = conf.get('BACKHAUL.antennas')          
    ants = sorted(ants, key = lambda x : x['gain'])
                         
    kwargs = {
        'ha1' : conf.get('BACKHAUL.radio.default_height'),          
        'ha2' : conf.get('BACKHAUL.radio.default_height'),         
        'ga1' : ants[0]['gain'],
        'ga2' : ants[0]['gain'],
        'pw' : conf.get('BACKHAUL.radio.power'),          
        'tm' : conf.get('ENVIRONMENT.trees.tree_model'),
        'td' : conf.get('ENVIRONMENT.trees.tree_delta')
    }

    print(rf.link_quality(start, end, **kwargs))


# projetar antenas
def test_10():
    from cisei_lib.cli.usermng.homeFolder import user_home    
    from cisei_lib.cli.bhnplanner.link_planner import linkPlan
    import geopandas as gpd
    
    home = user_home('cisei', 'home')

    project = 'teste'

    bhns = gpd.read_file( home.get_project_path(project, 'bhns_geo.json') ) 
    new = gpd.read_file( home.get_project_path(project, 'new_geo.json') ) 
    s_label = 'teste_BHN_2'
    d_label = 'AGU-S-GE-025'

    start = new[new['name']==s_label].iloc[0]
    start = (start.geometry.y, start.geometry.x)
    end = bhns[bhns['name']==d_label].iloc[0]
    end = (end.geometry.y, end.geometry.x)
    
    if False:
        s_label = 'R2_1'                   
        end =  (-26.058974999976, -49.32859999923694)
        start = (-26.00780734884017, -49.27442785662058)                    
    
    with linkPlan('teste', home, config_file='radio_model_user.toml') as x:
        x.set_link(s_label, start, d_label, end)
        x.plan_link()
        # x.improve_link(0.02, 20)
        x.path_antennas()        
        x.to_geoJSON(save=True)
        x.to_geoJSON_links(save=True)
        pass


# test configTools
def test_11():
    from cisei_lib.cli.usermng.homeFolder import user_home
    from cisei_lib.cli.usermng.configTools import configTools, configRadio

    home = user_home('cisei', 'home')
    conf = configTools(home)

    conf.load_configuration('radio_model.toml', 'radio_model_user.toml')

    print(conf._get(conf.default_config, 'BACKHAUL.radio.power'))
    print(conf.get('BACKHAUL.radio.power_set'))

    conf = configRadio(home, 'radio_model_user.toml')
    print(conf.get('BACKHAUL.radio.power_set'))

    pass

# test geoRPL
def test_12():
    from cisei_lib.cli.bhnplanner.geo_rpl import geoRPL
    from cisei_lib.cli.usermng.homeFolder import user_home
    import geopandas as gpd

    home = user_home('cisei', 'home')

    project = 'teste_2'

    bhns = gpd.read_file( home.get_project_path(project, 'bhns_geo.json') ) 
    new = gpd.read_file( home.get_project_path(project, 'new_geo.json') ) 

    contexto = {'ga1' : 8.15, 'ga2' : 8.15, 'ha1' : 7, 'ha2' : 7, 'pw' : 30, 'tm' : 'FITU-R', 'td' : 2, 'level' : 1}  
    
    with geoRPL(**contexto) as x:     
        x.set_nodes_gdf(bhns, new)
        x.create_graph(3)
        # x.show_network()
        # res = x.calculate_rank('AGU-S-GE-066', 'teste_BHN_11')
        x.run_RPL()
        x.show_network(G=x.G_res)
        pass


# test bhPlan
def test_13():
    from cisei_lib.cli.bhnplanner.bhPlan import bhPlan
    from cisei_lib.cli.usermng.homeFolder import user_home
    home = user_home('cisei', 'home')

    test = 1

    if test == 1:
        with bhPlan('teste_4', home, config_file='radio_model_user.toml') as x: 
            
            # while x.plan_round() is None: pass
            # x.plan_round()
            step = 0
            x.deserialize_plan(f'temp_{step}.pack')
            x.consolidate_round()
            # x.export_plan(f'temp_{step}.pack')
            


    if test == 2:
        with bhPlan('teste_2', home, config_file='radio_model_user.toml') as x:                     
            x.run()
  
# Create theoretical model
def test_14():
    from pathlib import Path
    import geopandas as gpd
    from cisei_lib.cli.usermng.configTools import configRadio
    from cisei_lib.cli.usermng.homeFolder import user_home
    import cisei_lib.cli.elevation.rfModel as rfModel

    home = user_home('cisei', 'home')
    radio_model_file = 'radio_model_user.toml'
    conf = configRadio(home, radio_model_file)
    rf = rfModel.rfModel(cover_height=conf.get('ENVIRONMENT.cover_height'))

    proj = 'BRO-SE'

    # Load raw JSON
    
    base_path = Path.cwd()
    links_path = base_path / "code_examples" / "Arquivos" / proj / "bhlinks_geo.json"
    gdf_links = gpd.read_file(links_path)
    nodes_path = base_path / "code_examples" / "Arquivos" / proj / "bhnodes_geo.json"
    gdf_nodes = gpd.read_file(nodes_path)  

    print(gdf_nodes.head())

    for row in gdf_links.itertuples():
        line = row.geometry
        coord_1, coord_2 = list(line.coords)
        coord_1 = coord_1[::-1]
        coord_2 = coord_2[::-1]
        
        src_node = gdf_nodes[gdf_nodes["name"] == row.src].iloc[0]
        dst_node = gdf_nodes[gdf_nodes["name"] == row.dst].iloc[0]

        link_kwargs = {
                    'ha1' : 7,
                    'ha2' : 7,
                    'ga1' : src_node.ant_dB,
                    'ga2' : dst_node.ant_dB,
                    'pw' : 30,
                    'tm' : conf.get('ENVIRONMENT.trees.tree_model'),
                    'td' : conf.get('ENVIRONMENT.trees.tree_delta')
                }   
        
        qos = rf.link_quality(coord_1, coord_2, **link_kwargs)

        for key, value in qos.items():
            if key not in gdf_links.columns:
                gdf_links[key] = None
            gdf_links.at[row.Index, key] = value

    gdf_links.to_file(links_path, driver="GeoJSON")

# Teste base_metric
def test_15():
    from cisei_lib.cli.usermng.home_folder import UserHome
    from cisei_lib.cli.monitoring.preprocessor import Preprocessor as pp

    network = 'bro-s'
    home = UserHome('cisei', 'home')

    context = {'topology' : 'bhns_geo.json'}
   
    with pp(network=network, home=home, **context) as x:
        x.load_topology()
        x.geojson_to_graph()
        x.save_netgraph()
        # x.show_graph(f_layout=x._hierarchical_layout)
        # x.show_graph(root="AGU-S-GE-010")
        x.export_visgraph()
        pass


    

# Test graph_view
def test_16():
    from cisei_lib.cli.importer.gdf_transformer import fix_string_coord
    from pathlib import Path
    from cisei_lib.cli.evaluator.preprocessor import Preprocessor as pp
    from cisei_lib.cli.usermng.home_folder import UserHome
    from cisei_lib.cli.usermng.configTools import configTools

    import matplotlib.pyplot as plt
    import numpy as np
    

    if False:
        proj = 'BRO-SE'
        in_path = Path(f"code_examples/Arquivos/{proj}/bhls_geo.json")
        out_path = Path(f"code_examples/Arquivos/{proj}/bhlinks_geo.json")
        fix_string_coord(in_path, out_path)
        in_path2 = Path(f"code_examples/Arquivos/{proj}/bhns_geo.json")
        out_path2 = Path(f"code_examples/Arquivos/{proj}/bhnodes_geo.json")
        fix_string_coord(in_path2, out_path2)

    def pavg(data, p):
        per = np.percentile(data, p)
        filtered_data = [x for x in data if x <= per]
        return np.mean(filtered_data)
    
    def base(data, p, r =2):
        min_val = min([x for x in data if x > 0])
        filtered = [x for x in data if x < r * min_val]
        return np.percentile(filtered, p)  

    def replace_zeros(data, base):
        return [d if d > 0 else 7* base for d in data]          
    
    home = UserHome('cisei', 'home')

    # rules, meta = configTools.load_configs_from_dir(home.get_monitoring_path('agu-s','configuration'), 'fuzzy_rules')

    path = home.get_monitoring_path('bro-s','inbox','host_items.msgpack')

    with open(path,'rb') as f:
        data = msgpack.unpack(f, raw=False)
    
    node = 'BRO-S-GE-0015'

    min_rtt  = data[node]['Round Trip Time Min']
    max_rtt  = data[node]['Round Trip Time Max']
    avg_rtt  = data[node]['Round Trip Time Avg']
    rt_base = base(min_rtt, 0.9, r=2.1)
    avg_rtt = replace_zeros(avg_rtt, rt_base)
    rtt_avg = [(a-rt_base)/a for a in avg_rtt]


    loss  = data[node]['Packet Loss Count']
    rt = data[node]['MAC Stats Tx Retry']
    tx = data[node]['MAC Stats Tx Success']
    tx_i = [a - b for a, b in zip(tx, tx[1:])]
    rt_i = [a - b for a, b in zip(rt, rt[1:])]
    retry = [a/b for a,b in zip(rt_i, tx_i)]
    retry = retry + [sum(retry)/len(retry)]
    loss = [l/10 for l in loss]
        

    x  = range(len(loss))    

    fig, ax1 = plt.subplots()
    ax1.plot(x, rtt_avg, 'b-', label='rtt_avg')
    ax1.set_ylabel('rtt increase', color='b')
    rtt_avg = pavg(rtt_avg,90)
    ax1.axhline(rtt_avg, color='b', linestyle='dotted', label=f"{rtt_avg:.2f}")

    ax2 = ax1.twinx()
    ax2.plot(x, retry, 'r--', label='retry')
    ax2.set_ylabel('retry rate', color='r')
    retry_avg = pavg(retry,99)
    ax2.axhline(retry_avg, color='r', linestyle='dotted', label=f"{retry_avg:.2f}")

    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc='best')


    plt.grid(True)
    plt.show()
    pass


    with pp(network='agu-s', home=home) as x:
        x.load_geojson('bhnodes_geo.json')
        x.geojson_to_graph()
        # x.show_graph(f_layout=x._hierarchical_layout)
        # x.show_graph(root="AGU-S-GE-010")
        x.export_graph()
        pass


def test_17():
    # pip install duckdb
    from cisei_lib.cli.usermng.home_folder import UserHome
    from cisei_lib.cli.monitoring.import_metrics import consumeMetrics

    network = 'bro-s'
    home = UserHome('cisei', 'home')
    scada_db_path = home.get_monitoring_path(network, 'current', 'scada_import_duckdb' )
    
    context = {}
    with consumeMetrics(network, home, **context) as x:
        #x.run_import_metrics()
        #x.import_scada_metrics(scada_db_path)
        x.open_database()
        #x.build_bin_ref(86400, 1800)  # last 1 day, 30min bins
        #x.build_bin_ref(604800, 1800) # last 7 days, 30min bins
        # 30 minutes, 2025-07-01 00:00:00 UTC, 2025-07-24 00:00:00 UTC
        
        #x.build_bin_ref(bin_size=1800, start_ts=1751328000, end_ts=1753401600, replace_existing=True)
        
        # x.build_metrics_bins(bin_size=1800)                 # step 4
        x.build_events_bins(bin_size=1800)                 # step 4
        pass









#---------------------------------------------------------------------
if __name__ == '__main__':

    import argparse
    import traceback
    import os
    from cisei_lib.cli.tools.debug_utils import patch_print
    from cisei_lib.cli.tools.debug_utils import benchmark


    patch_print()

    
    parser = argparse.ArgumentParser()
    parser.add_argument("function", help="Name of the function to run")
    args = parser.parse_args()

    try:
        func = globals()[args.function]
        if callable(func):
            func()
        else:
            raise Exception(f'Function {args.function} is not callable!')
    except Exception as e:
        print(e)        
        print(traceback.format_exc())
    
    exit()
    # test_importDataset()
    #test_TOML()
    test_manageProject()
    exit()

    import cisei_lib.cli.importer.geo_formats as geof
    import os
   
    tested = ['AGU-S','AIS-S', 'ATO-R','BAN-S','BEM-S', 'BEM-S2', 'CON-S', 'CPT-S', 'CRB-S'  
              'FRG-S2', 'FZI-S', 'GGA-S', 'IMB-S', 'IMS-S', 'IRT-R', 'IRT-S', 'IVI-S', 'LAP-S',
              'MIB-S', 'PAL-S', 'PAM-R', 'PRU-S', 'QUI-S', 'RDR-S', 'REB-S', 'RIL-S', 'SJT-S', 
              'TAF-S', 'TSO-S', 'TZC-S', 'VMI-S', 'XIS-S']
    redes = get_redes(remove=tested)

    for sub in redes:
        try:
            test_5(sub)
            test_6(sub)  
            geof.json_to_geojson(os.path.join('Projects', sub, f'new_{sub}.json'), os.path.join('Projects', sub, f'new_{sub}_geo.json'))
            #update_report(sub)
        except Exception as e:
            print(e)



   

