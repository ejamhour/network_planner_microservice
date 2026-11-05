import asyncio
import copy

import pytest
import tomlkit

from cisei_planning_sdk.local_io import load_scenario_bundle, save_scenario
from cisei_lib.planners import PlanningScenario, GraphPlanner


def project(tmp_path):
    config = {
        'scenario': {'working_crs': 'EPSG:31982'},
        'antennas': {'omni': {'kind': 'omni', 'gain_dbi': 3, 'height_m': 7},
                     'sector': {'kind': 'sector', 'gain_dbi': 12, 'height_m': 9,
                                'azimuth_deg': 30, 'beamwidth_deg': 60}},
        'interfaces': {'radio': {'tech': 'mesh', 'freq_mhz': 900, 'tx_power_dbm': 30, 'antenna_id': 'omni'}},
        'devices': {'dual': {'interfaces': ['radio','radio'], 'can_route': True}},
        'instances': {'source': 'nodes.csv', 'format': 'csv'},
        'net': {'candidate_edges': [{'src': 'a:d0:i0', 'dst': 'b:d0:i0'}]},
    }
    save_scenario(config, tmp_path/'scenario.toml')
    (tmp_path/'nodes.csv').write_text('site_id,device_profile,lat,lon,mount_height_m,connected\na,dual,-26,-49,7,true\nb,dual,-26.001,-49,8,false\n')
    return tmp_path/'scenario.toml'


def placements(path, values):
    (path.parent/'antenna_placements.toml').write_text(tomlkit.dumps({'placements': values}))


def planner_from(config):
    scenario = PlanningScenario.from_config_dict(config)
    for node in scenario.site_nodes.values():
        node.resolve_position(scenario.working_crs)
    return GraphPlanner(scenario)


def test_optional_overrides_are_isolated_and_portable(tmp_path):
    path = project(tmp_path)
    before = load_scenario_bundle(path)
    placements(path, {'a:d0:i0': {'antenna_id': 'sector', 'azimuth_deg': 125, 'mount_height_m': 14},
                      'a:d0:i1': {'downtilt_deg': 2}})
    result = load_scenario_bundle(path)
    antenna = result['sites'][0]['interfaces'][0]['antenna_id']
    assert result['antennas'][antenna]['gain_dbi'] == 12
    assert result['antennas'][antenna]['beamwidth_deg'] == 60
    assert result['antennas'][antenna]['azimuth_deg'] == 125
    assert result['antennas']['sector'] == before['antennas']['sector']
    assert result['antennas']['omni'] == before['antennas']['omni']
    assert result['sites'][1] == before['sites'][1]
    assert result['sites'][0]['devices'] == before['sites'][0]['devices']
    assert 'antenna_placements' not in result
    planner = planner_from(result)
    assert planner.rpl_nodes[0].extra['mount_height_m'] == 14
    assert planner.rpl_nodes[1].extra['mount_height_m'] == 7
    exported = tmp_path/'export'; exported.mkdir()
    save_scenario(planner.scenario.to_config_dict(), exported/'scenario.toml')
    restored = planner_from(load_scenario_bundle(exported/'scenario.toml'))
    assert restored.rpl_nodes[0].extra['mount_height_m'] == 14
    assert restored.antenna_catalog[antenna].azimuth_deg == 125


def test_explicit_reference_antenna_only_and_empty_file_fallback(tmp_path):
    path = project(tmp_path)
    original = load_scenario_bundle(path)
    placements(path, {})
    assert load_scenario_bundle(path) == original
    placements(path, {'a:d0:i0': {'antenna_id': 'sector'}})
    (tmp_path/'antenna_placements.toml').rename(tmp_path/'installations.toml')
    doc=tomlkit.parse(path.read_text());doc['antenna_placements']={'source':'installations.toml'};path.write_text(tomlkit.dumps(doc))
    result = load_scenario_bundle(path)
    assert result['sites'][0]['interfaces'][0]['antenna_id']=='sector'
    assert len(result['antennas'])==2


def test_export_beside_extension_is_idempotent_and_can_update_orientation(tmp_path):
    path=project(tmp_path);placements(path,{'a:d0:i0':{'azimuth_deg':125}})
    result=load_scenario_bundle(path)
    save_scenario(result,tmp_path/'canonical.toml')
    assert load_scenario_bundle(tmp_path/'canonical.toml')==result
    placements(path,{'a:d0:i0':{'azimuth_deg':130}})
    updated=load_scenario_bundle(tmp_path/'canonical.toml')
    iid=updated['sites'][0]['interfaces'][0]['antenna_id']
    assert updated['antennas'][iid]['azimuth_deg']==130


@pytest.mark.parametrize('fields', [
    {'antenna_id':'missing'}, {'azimuth_deg':float('nan')}, {'azimuth_deg':True},
    {'mount_height_m':-1}, {'beamwidth_deg':361}, {'unexpected':1},
    {'tilt_deg':2,'downtilt_deg':3}, {'shadow_loss_db':-1},
])
def test_invalid_installations_fail(tmp_path,fields):
    path=project(tmp_path);placements(path,{'a:d0:i0':fields})
    with pytest.raises(ValueError,match='Antenna placement'):
        load_scenario_bundle(path)


def test_unknown_interface_and_missing_or_external_file_fail(tmp_path):
    path=project(tmp_path);placements(path,{'missing':{'azimuth_deg':10}})
    with pytest.raises(ValueError,match='unknown interface'):
        load_scenario_bundle(path)
    doc=tomlkit.parse(path.read_text());doc['antenna_placements']={'source':'missing.toml'};path.write_text(tomlkit.dumps(doc))
    with pytest.raises(FileNotFoundError):load_scenario_bundle(path)
    doc['antenna_placements']={'source':'../outside.toml'};path.write_text(tomlkit.dumps(doc))
    with pytest.raises(ValueError,match='inside the project'):load_scenario_bundle(path)


def test_rf_evaluation_uses_instance_gain_and_height(tmp_path):
    path=project(tmp_path)
    placements(path,{'a:d0:i0':{'antenna_id':'sector','azimuth_deg':180,'mount_height_m':14}})
    planner=planner_from(load_scenario_bundle(path))
    calls=[]
    class Geo:
        async def get_features(self,*args,**kwargs):
            calls.append(kwargs)
            return {'dist_m':100,'fspl':80,'delta_diffra':0,
                    'vegetation':{'core':0,'fresnel':0,'boundary':0},
                    'buildings':{'core':0,'fresnel':0,'boundary':0}}
    contexts=[]
    planner.metric_functions_by_tech['mesh']=lambda c: contexts.append(c) or 1
    asyncio.run(planner.compute_edge_metrics(Geo()))
    assert calls[0]['tx_ha']==14 and calls[0]['rx_ha']==8
    assert contexts[0]['tx']['ant_gain']==12
    assert contexts[0]['rx']['ant_gain']==3


def test_directional_library_uses_instance_orientation(tmp_path):
    path=project(tmp_path)
    doc=tomlkit.parse(path.read_text()).unwrap()
    doc['antennas']['sector']['model_id']='directional'
    doc['antenna_library']={'antennas':[{'id':'directional','type':'directional',
        'patterns':[{'frequency_mhz':900,'max_gain_dbi':12,
                     'attenuation':{'0':{'0':0},'180':{'0':20}}}]}]}
    save_scenario(doc,path)
    placements(path,{'a:d0:i0':{'antenna_id':'sector','azimuth_deg':180,'downtilt_deg':0}})
    planner=planner_from(load_scenario_bundle(path))
    a,b=planner.rpl_nodes[0],planner.rpl_nodes[2]
    antenna=planner.antenna_catalog[a.extra['antenna_id']]
    facing=planner._link_antenna_gain(a,b,antenna)
    antenna.azimuth_deg=0
    away=planner._link_antenna_gain(a,b,antenna)
    assert facing > away + 15


def test_derived_project_preserves_and_tracks_placements(tmp_path):
    from cisei_planning_sdk import ExtendedPlanningProject
    source=tmp_path/'source';source.mkdir();path=project(source)
    placements(path,{'a:d0:i0':{'azimuth_deg':125}})
    derived=ExtendedPlanningProject(tmp_path/'derived',source=source)
    derived.extension_path.write_text('[[replacement.rules]]\nprofile = "dual"\nreplace_with = "dual"\n')
    derived.prepare()
    assert (derived.path/'antenna_placements.toml').exists()
    loaded=load_scenario_bundle(derived.scenario_path)
    iid=loaded['sites'][0]['interfaces'][0]['antenna_id']
    assert loaded['antennas'][iid]['azimuth_deg']==125
    placements(path,{'a:d0:i0':{'azimuth_deg':130}})
    with pytest.raises(FileExistsError,match='changed'):
        derived.prepare()
    derived.prepare(overwrite=True)
    (source/'antenna_placements.toml').unlink()
    with pytest.raises(FileExistsError,match='changed'):
        derived.prepare()
    derived.prepare(overwrite=True)
    assert not (derived.path/'antenna_placements.toml').exists()
    assert load_scenario_bundle(derived.scenario_path)['sites'][0]['interfaces'][0]['antenna_id']=='omni'
