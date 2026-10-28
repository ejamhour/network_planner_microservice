from pathlib import Path
import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routes_planning import router
from app.session import UserRuntime
from cisei_lib.planners import PlanningScenario
from cisei_lib.planners.mesh_link_planner import MeshLinkPlanner
from cisei_planning_sdk import load_scenario_bundle, PlanningClient, PlanningProject
from cisei_planning_sdk.local_io import load_poles_bundle

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/projects/exemple_4_Mesh_basic'

class Geo:
    def __init__(self):
        self.calls = 0
    async def get_features(self, tx, rx, **kwargs):
        self.calls += 1
        # Direct endpoints are obstructed; links via the candidate are clear.
        obstructed = {tuple(tx), tuple(rx)} == {(-25.43273083515008, -49.339207159685344), (-25.42777449459199, -49.2655361005508)}
        return {'dist_m': 5000., 'terrain_peak': {'vv_0': 2. if obstructed else 0.},
                'terrain_peaks_vv': [{'v_v': 2., 'd_norm': 0.5}] if obstructed else []}
    async def point_samples(self, points, **kwargs):
        return [700.] * len(points)


def scenario():
    config = load_scenario_bundle(EXAMPLE / 'scenario.toml')
    config['sites'].extend(load_poles_bundle(EXAMPLE / 'scenario.toml'))
    return PlanningScenario.from_config_dict(config)


def test_selects_repeater_without_home_or_raw_cache_mutation():
    geo = Geo()
    planner = MeshLinkPlanner(scenario(), src_site='copel', dst_site='guaira',
                              pole_sites=['barigui'], geo=geo, search_midpoint=True,
                              sector_exclusions=[], max_repeaters=1)
    graph, result = asyncio.run(planner.plan())
    assert planner.home is None and planner.serializer is None
    assert result['mesh_search']['node_path'] == ['copel', 'barigui', 'guaira']
    assert result['mesh_search']['meets_segment_threshold']
    nodes = {n['node_id']: n for n in result['planned_nodes']}
    assert nodes['copel:d0:i0']['parent'] == 'barigui:d0:i0'
    assert nodes['barigui:d0:i0']['parent'] == 'guaira:d0:i0'
    assert nodes['guaira:d0:i0']['connected']
    assert len(result['rpl_nodes']) == 3
    assert planner.input_graph.nodes['guaira'].devices[0].connected
    assert 'metric' not in asyncio.run(geo.get_features((0,0),(1,1)))


def test_repeater_limit_and_infeasible_quality_are_explicit():
    planner = MeshLinkPlanner(scenario(), src_site='copel', dst_site='guaira',
                              pole_sites=['barigui'], geo=Geo(), max_repeaters=0,
                              search_midpoint=True, sector_exclusions=[])
    _, result = asyncio.run(planner.plan())
    assert result['mesh_search']['node_path'] == ['copel', 'guaira']
    assert not result['mesh_search']['meets_segment_threshold']


def test_quality_feasible_path_precedes_cheaper_direct_link():
    class QualityGeo(Geo):
        async def get_features(self, tx, rx, **kwargs):
            record = await super().get_features(tx, rx, **kwargs)
            record['segment_cost'] = 2.5 if record['terrain_peak']['vv_0'] else 1.4
            return record
    s = scenario()
    s.metric_specs_by_tech['mesh'] = lambda record: record['features']['segment_cost']
    planner = MeshLinkPlanner(s, src_site='copel', dst_site='guaira',
                              pole_sites=['barigui'], geo=QualityGeo(), max_repeaters=1,
                              search_midpoint=True, sector_exclusions=[])
    _, result = asyncio.run(planner.plan())
    assert result['mesh_search']['path_cost'] == pytest.approx(2.8)
    assert result['mesh_search']['node_path'] == ['copel', 'barigui', 'guaira']
    assert result['mesh_search']['meets_segment_threshold']
    assert result['mesh_search']['path_selection'] == 'quality_first_then_cost'


def test_filters_disabled_candidate_and_can_keep_direct_link():
    s = scenario()
    s.site_nodes['barigui'].interfaces[0].enabled = False
    planner = MeshLinkPlanner(s, src_site='copel', dst_site='guaira', pole_sites=['barigui'], geo=Geo())
    _, result = asyncio.run(planner.plan())
    assert result['mesh_search']['node_path'] == ['copel', 'guaira']
    assert result['mesh_search']['eligible_candidates'] == []
    assert result['mesh_search']['excluded_candidates'][0]['site_id'] == 'barigui'


def test_endpoint_sdk_and_portable_cache(tmp_path):
    import shutil
    root = tmp_path / 'projects/exemple_4_Mesh_basic'
    shutil.copytree(EXAMPLE, root)
    app = FastAPI(); app.include_router(router)
    runtime = UserRuntime(); app.state.runtime = runtime
    geo = Geo()
    async def pool(**options): return geo
    runtime.get_geo_pool = pool
    with TestClient(app) as api:
        sdk = PlanningClient('http://testserver', workspace=tmp_path, auto_register=False)
        def request(method, path, **kwargs):
            response = api.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
            if payload['status'] != 'OK': raise RuntimeError(payload['data'])
            return payload
        sdk._request = request
        session = sdk.open_project(PlanningProject(root))
        before = {name: (root / name).read_bytes() for name in ('scenario.toml', 'nodes.csv', 'poles.csv')}
        assert set(runtime.planning_scenarios[session.scenario_id].site_nodes) == {'copel', 'guaira', 'italia'}
        result = session.plan_mesh_link('copel', 'guaira', geo_base_url='http://features:8080')
        assert result['mesh_search']['node_path'][0] == 'copel'
        assert (root / 'result.json').exists()
        cache = session.project.load_features()
        assert cache['entries']
        assert all('metric' not in e['features'] and 'obs_pos' not in e['features'] for e in cache['entries'])
        count = geo.calls
        session.plan_mesh_link('copel', 'guaira', geo_base_url='http://features:8080')
        assert geo.calls == count
        assert all((root / name).read_bytes() == content for name, content in before.items())
        assert set(runtime.planning_scenarios[session.scenario_id].site_nodes) == {'copel', 'guaira', 'italia'}
        assert runtime.planning_scenarios[session.scenario_id].site_nodes['guaira'].devices[0].connected


def test_metric_uses_instance_radio_configuration_and_conservative_direction():
    s = scenario()
    s.site_nodes['copel'].interfaces[0].tx_power_dbm = 42.
    s.metric_specs_by_tech['mesh'] = lambda record: record['tx']['pw'] / 10.
    planner = MeshLinkPlanner(s, src_site='copel', dst_site='guaira',
                              pole_sites=['barigui'], geo=Geo(), max_repeaters=0)
    _, result = asyncio.run(planner.plan())
    assert result['mesh_search']['path_cost'] == pytest.approx(4.2)


def test_feature_errors_are_reported_instead_of_accepting_a_partial_graph():
    class BrokenGeo:
        async def get_features(self, *args, **kwargs):
            raise RuntimeError('No raster coverage')
    planner = MeshLinkPlanner(scenario(), src_site='copel', dst_site='guaira',
                              pole_sites=['barigui'], geo=BrokenGeo())
    with pytest.raises(RuntimeError, match='No raster coverage'):
        asyncio.run(planner.plan())


def test_auto_candidates_filter_technology_and_select_device_interface():
    planner = MeshLinkPlanner(scenario(), src_site='copel:d0:i0', dst_site='guaira:d0:i0', geo=Geo())
    assert 'shopping' not in planner.resolved
    assert planner.resolved['deneka'].node_id == 'deneka:d0:i0'
    assert planner.resolved['torre'].extra['antenna_id'] == 'mesh_high_gain'
    assert planner.excluded_candidates == [{'site_id': 'shopping', 'reason': 'no_enabled_compatible_routing_interface'}]
    assert 'italia' not in planner.resolved


def test_candidate_graph_contains_all_evaluated_alternatives():
    planner = MeshLinkPlanner(scenario(), src_site='copel:d0:i0', dst_site='guaira:d0:i0',
                              geo=Geo(), search_midpoint=True, sector_exclusions=[])
    _, result = asyncio.run(planner.plan())
    candidates = result['mesh_candidates']
    assert len(candidates['candidate_edges']) == planner.G.number_of_edges()
    assert len(candidates['candidate_edges']) > len(result['planned_edges'])
    assert len(candidates['metric_edges']) == len(candidates['candidate_edges'])
    assert candidates['planned_nodes'] == []
    assert any(not e['selected'] for e in candidates['candidate_edges'])
    assert all(e['evaluated'] for e in candidates['candidate_edges'])
    assert any(e['expanded'] for e in candidates['candidate_edges'])
    assert {n['node_id'] for n in candidates['rpl_nodes']}.isdisjoint({'shopping:d0:i0', 'deneka:d0:i1'})
    assert not {n['site_id'] for n in result['rpl_nodes']} - set(result['mesh_search']['node_path'])


def test_candidates_need_route_and_candidate_flag():
    s = scenario()
    s.site_nodes['barigui'].devices[0].can_route = False
    s.site_nodes['tenis'].site.extra['candidate'] = False
    planner = MeshLinkPlanner(s, src_site='copel', dst_site='guaira', geo=Geo())
    assert 'barigui' not in planner.resolved and 'tenis' not in planner.resolved
    assert any(e['site_id'] == 'barigui' for e in planner.excluded_candidates)


def test_endpoints_require_compatible_interfaces_and_connected_destination():
    s = scenario()
    with pytest.raises(ValueError, match='technology and frequency'):
        MeshLinkPlanner(s, src_site='copel:d0:i0', dst_site='shopping:d0:i0', geo=Geo())
    s.site_nodes['guaira'].devices[0].connected = False
    with pytest.raises(ValueError, match='destination must be connected'):
        MeshLinkPlanner(s, src_site='copel', dst_site='guaira', geo=Geo())


def test_poles_are_separate_until_link_planning():
    config = load_scenario_bundle(EXAMPLE / 'scenario.toml')
    assert {site['site_id'] for site in config['sites']} == {'copel', 'guaira', 'italia'}
    original = PlanningScenario.from_config_dict(config)
    poles = load_poles_bundle(EXAMPLE / 'scenario.toml')
    assert len(poles) == 10
    planner = MeshLinkPlanner(original, src_site='copel', dst_site='guaira',
                              poles=poles, geo=Geo(), search_midpoint=True)
    _, result = asyncio.run(planner.plan())
    assert len(original.site_nodes) == 3
    assert result['mesh_search']['eligible_candidates']
    assert 'shopping' not in planner.resolved
    assert any(n['site_id'] == 'torre' for n in result['mesh_candidates']['rpl_nodes'])


@pytest.mark.parametrize('edit, message', [
    ('missing_id', 'requires pole_id'),
    ('duplicate', 'Duplicated site id'),
    ('collision', 'collide with network'),
    ('profile', 'Unknown device profile'),
    ('escape', 'inside the project'),
])
def test_invalid_pole_file_is_rejected_locally(tmp_path, edit, message):
    import shutil, csv
    root = tmp_path / 'project'; shutil.copytree(EXAMPLE, root)
    path = root / 'poles.csv'
    rows = list(csv.DictReader(path.open()))
    if edit == 'missing_id': rows[0]['pole_id'] = ''
    elif edit == 'duplicate': rows.append(dict(rows[0]))
    elif edit == 'collision': rows[0]['pole_id'] = 'copel'
    elif edit == 'profile': rows[0]['device_profile'] = 'missing'
    if edit == 'escape':
        p = root / 'scenario.toml'
        p.write_text(p.read_text().replace('source = "poles.csv"', 'source = "../poles.csv"'))
    else:
        with path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    with pytest.raises(ValueError, match=message):
        load_poles_bundle(root / 'scenario.toml')


def test_service_rejects_pole_id_collisions_without_mutating_original():
    original = PlanningScenario.from_config_dict(load_scenario_bundle(EXAMPLE / 'scenario.toml'))
    poles = load_poles_bundle(EXAMPLE / 'scenario.toml')
    poles[0]['site_id'] = 'copel'
    with pytest.raises(ValueError, match='conflicting pole ID'):
        MeshLinkPlanner(original, src_site='copel', dst_site='guaira', poles=poles, geo=Geo())
    assert len(original.site_nodes) == 3
