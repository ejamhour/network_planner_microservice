import asyncio
from copy import deepcopy
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.routes_planning import router
from app.session import UserRuntime
from cisei_lib.planners import MeshPlanner, PlanningScenario
from cisei_lib.planners.graph_planner import GraphPlanner
from cisei_planning_sdk import PlanningClient, PlanningProject
from cisei_planning_sdk.local_io import load_scenario_bundle, save_scenario


def inputs(records=None, poles=None):
    config = {
        'scenario': {'working_crs': 'EPSG:31982'},
        'antennas': {'omni': {'kind': 'omni', 'gain_dbi': 3., 'height_m': 7.}},
        'interfaces': {
            'radio': {'tech': 'mesh', 'freq_mhz': 900., 'tx_power_dbm': 30., 'antenna_id': 'omni', 'can_relay': True},
            'leaf_radio': {'tech': 'mesh', 'freq_mhz': 900., 'tx_power_dbm': 30., 'antenna_id': 'omni', 'can_relay': False}},
        'devices': {'relay': {'can_route': True, 'interfaces': ['radio']},
                    'leaf': {'can_route': False, 'interfaces': ['leaf_radio']}},
        'metrics': {'mesh': {'spec': 'version = 1\nresult = "metric"\n[expressions]\nmetric = "features.dist_m"'}},
    }
    scenario = PlanningScenario.from_config_dict(config)
    records = records or [('root', 0, True, 'relay', 0), ('a', 200, False, 'leaf', None), ('b', 220, False, 'leaf', None)]
    scenario.add_node_instances([dict(site_id=s, x=500000+x, y=7200000, connected=c,
                                      device_profile=p, rank=r, mount_height_m=7)
                                  for s, x, c, p, r in records])
    pole_scenario = PlanningScenario.from_config_dict(config)
    pole_scenario.add_node_instances([dict(site_id=s, x=500000+x, y=7200000,
                                           device_profile='relay', mount_height_m=7)
                                       for s, x in (poles if poles is not None else [('p', 100), ('far', 3000)])])
    return scenario, pole_scenario.to_config_dict().get('sites', [])


class Geo:
    def __init__(self, scenario, poles, allowed):
        positions = {tuple(n.site.geo_pos): sid for sid, n in scenario.site_nodes.items()}
        positions.update({(p['lat'], p['lon']): p['site_id'] for p in poles})
        self.positions = positions
        self.allowed = {frozenset(pair): cost for pair, cost in allowed}
        self.calls = []

    async def get_features(self, tx, rx, **kwargs):
        pair = (self.positions[tuple(tx)], self.positions[tuple(rx)])
        self.calls.append(pair)
        cost = self.allowed.get(frozenset(pair), 4.)
        return {'dist_m': cost, 'terrain_peak': {'vv_0': 2. if cost > 2 else 0.},
                'terrain_peaks_vv': [{'v_v': 2., 'd_norm': .5}] if cost > 2 else []}

    async def point_samples(self, points, **kwargs):
        return [700.] * len(points)


def run(scenario, poles, allowed, **options):
    geo = Geo(scenario, poles, allowed)
    planner = MeshPlanner(scenario, geo=geo, poles=poles, parent_candidates=1,
                          corridor_m=10, max_repeaters=1, search_midpoint=True,
                          **options)
    return planner, asyncio.run(planner.plan()), geo


def test_progressive_repeater_reuse_and_only_original_roots():
    scenario, poles = inputs()
    before = deepcopy(scenario.to_config_dict())
    _, (graph, result), geo = run(scenario, poles, [(('root', 'p'), 1), (('p', 'a'), 1), (('p', 'b'), 1)])
    report = result['mesh_network']
    assert [s['source'] for s in report['steps']] == ['a:d0:i0', 'b:d0:i0']
    assert report['steps'][1]['selected_destination'] == 'p:d0:i0'
    assert report['used_repeaters'] == ['p']
    assert report['unserved_interfaces'] == []
    nodes = {n['node_id']: n for n in result['planned_nodes']}
    assert {i for i, n in nodes.items() if n['connected']} == {'root:d0:i0'}
    assert nodes['b:d0:i0']['rank'] == 2
    assert 'far' not in graph.nodes
    assert all('far' not in pair for pair in geo.calls)
    assert len(report['iterations']) == 3
    assert scenario.to_config_dict() == before
    assert {s['site_id'] for s in report['solution_scenario']['sites']} == {'root', 'a', 'b', 'p'}


def test_final_pass_removes_repeater_unused_by_any_required_path():
    scenario, poles = inputs([('root', 0, True, 'relay', 0), ('other', 1000, True, 'relay', 0),
                             ('a', 200, False, 'leaf', None)])
    planner, (_, result), geo = run(scenario, poles, [(('root', 'p'), 1), (('p', 'a'), 1), (('other', 'a'), 1)])
    report = result['mesh_network']
    assert report['selected_repeaters'] == ['p']
    assert report['removed_repeaters'] == ['p']
    assert report['used_repeaters'] == []
    assert {s['site_id'] for s in report['iterations'][1]['scenario']['sites']} == {'root', 'other', 'a', 'p'}
    assert {n['site_id'] for n in result['rpl_nodes']} == {'root', 'other', 'a'}
    assert next(n for n in result['planned_nodes'] if n['site_id'] == 'a')['parent'] == 'other:d0:i0'
    calls = len(geo.calls)
    planner.build_candidate_edges()
    assert set(planner.graph.nodes) == {'root', 'other', 'a'}
    assert len(geo.calls) == calls


def test_failed_terminal_is_retried_after_new_relay_becomes_available():
    scenario, poles = inputs([('root', 0, True, 'relay', 0), ('a', 100, False, 'leaf', None),
                             ('b', 200, False, 'leaf', None)], [('p', 150)])
    _, (_, result), _ = run(scenario, poles, [(('root', 'p'), 1), (('p', 'b'), 1), (('p', 'a'), 1)])
    steps = result['mesh_network']['steps']
    assert [s['source'] for s in steps] == ['a:d0:i0', 'b:d0:i0', 'a:d0:i0']
    assert steps[0]['selected_destination'] is None
    assert steps[-1]['selected_destination'] == 'p:d0:i0'
    assert result['mesh_network']['unserved_interfaces'] == []


def test_parent_choice_includes_existing_rank_not_only_local_link_cost():
    scenario, poles = inputs([('root', 0, True, 'relay', 10), ('other', 1000, True, 'relay', 0),
                             ('a', 200, False, 'leaf', None)], [])
    geo = Geo(scenario, poles, [(('root', 'a'), 1), (('other', 'a'), 2)])
    _, result = asyncio.run(MeshPlanner(scenario, geo=geo, parent_candidates=2, max_repeaters=0).plan())
    assert result['mesh_network']['steps'][0]['selected_destination'] == 'other:d0:i0'
    nodes = {n['site_id']: n for n in result['planned_nodes']}
    assert nodes['root']['rank'] == 10
    assert nodes['a']['rank'] == 2


def test_no_acceptable_path_keeps_required_nodes_unserved_without_repeater():
    scenario, poles = inputs()
    _, (_, result), _ = run(scenario, poles, [])
    assert result['mesh_network']['unserved_interfaces'] == ['a:d0:i0', 'b:d0:i0']
    assert result['mesh_network']['used_repeaters'] == []
    assert len(result['rpl_nodes']) == 3
    assert len(result['planned_nodes']) == 1


def test_disabled_poles_are_not_offered_and_external_poles_are_rejected():
    scenario, poles = inputs()
    poles[0]['interfaces'][0]['enabled'] = False
    _, (_, result), _ = run(scenario, poles, [(('root', 'p'), 1), (('p', 'a'), 1)])
    assert result['mesh_network']['used_repeaters'] == []
    poles[0]['devices'][0]['connected'] = True
    with pytest.raises(ValueError, match='external accesses'):
        MeshPlanner(scenario, geo=None, poles=poles)


def test_invalid_roots_and_feature_errors_do_not_become_partial_successes():
    scenario, poles = inputs()
    scenario.site_nodes['root'].devices[0].connected = False
    scenario.site_nodes['root'].devices[0].rank = float('inf')
    with pytest.raises(ValueError, match='external access'):
        MeshPlanner(scenario, geo=None, poles=poles)
    scenario, poles = inputs()
    class BrokenGeo:
        async def get_features(self, *args, **kwargs):
            raise RuntimeError('No raster coverage')
    with pytest.raises(RuntimeError, match='No raster coverage'):
        asyncio.run(MeshPlanner(scenario, geo=BrokenGeo(), poles=poles).plan())


def test_final_candidates_keep_alternatives_without_creating_accesses():
    scenario, poles = inputs([('root', 0, True, 'relay', 0), ('other', 1000, True, 'relay', 0),
                             ('a', 200, False, 'leaf', None)], [])
    geo = Geo(scenario, poles, [(('root', 'a'), 1), (('other', 'a'), 2)])
    _, result = asyncio.run(MeshPlanner(scenario, geo=geo, max_repeaters=0, final_degree=2).plan())
    assert len(result['candidate_edges']) == 3
    assert any(e['metric'] > 2 for e in result['metric_edges'])
    assert len(result['planned_edges']) == 1
    assert result['mesh_network']['fixed_interfaces'] == ['other:d0:i0', 'root:d0:i0']
    assert sum(n['connected'] for n in result['rpl_nodes']) == 2
    json.dumps(result, allow_nan=False)


def test_common_metrics_preserve_default_direction_and_optional_eligibility():
    scenario, poles = inputs()
    graph = GraphPlanner(scenario)
    graph.set_candidate_edges([('root:d0:i0', 'a:d0:i0')])
    class DirectionalGeo(Geo):
        async def get_features(self, tx, rx, **kwargs):
            row = await super().get_features(tx, rx, **kwargs)
            row['dist_m'] = 1 if self.positions[tuple(tx)] == 'root' else 3
            return row
    geo = DirectionalGeo(scenario, poles, [])
    asyncio.run(graph.compute_edge_metrics(geo))
    assert list(graph.edge_metrics.values()) == [1]
    assert len(geo.calls) == 1
    asyncio.run(graph.compute_edge_metrics(geo, bidirectional=True))
    assert list(graph.edge_metrics.values()) == [3]
    assert len(geo.calls) == 3
    nodes = set(graph.nodes)
    graph.run_rpl(max_edge_metric=2)
    assert len(graph.to_result_dict()['planned_nodes']) == 1
    assert len(graph.candidate_edges) == len(graph.edge_metrics) == 1
    graph.run_rpl()
    assert len(graph.to_result_dict()['planned_nodes']) == 2
    assert set(graph.nodes) == nodes


@pytest.mark.parametrize('solve', [False, True])
def test_mesh_workflow_respects_compute_and_solve_boundary(solve):
    scenario, poles = inputs()
    geo = Geo(scenario, poles, [(('root', 'p'), 1), (('p', 'a'), 1), (('p', 'b'), 1)])
    app = FastAPI(); app.include_router(router)
    runtime = UserRuntime(); app.state.runtime = runtime
    async def pool(**kwargs):
        return geo
    runtime.get_geo_pool = pool
    from cisei_planning_sdk.local_io import _json_value
    with TestClient(app) as api:
        response = api.post('/planning/workflow/run', json=_json_value({
            'scenario': scenario.to_json_dict(), 'planner': 'mesh', 'solve': solve,
            'geo_base_url': 'http://features:8080',
            'mesh': {'poles': poles, 'parent_candidates': 1, 'corridor_m': 10, 'max_repeaters': 1}})).json()
        assert response['status'] == 'OK', response
        result = response['data']
        assert bool(result['planned_nodes']) == solve
        assert ('mesh_network' in result) == solve
        assert result['candidate_edges']


@pytest.mark.parametrize('staged', [False, True], ids=['shortcut', 'planner-mesh-stages'])
def test_sdk_endpoint_persists_iterations_and_reuses_portable_features(tmp_path, staged):
    scenario, poles = inputs()
    root = tmp_path / 'projects/mesh'
    root.mkdir(parents=True)
    save_scenario(scenario.to_json_dict(), root / 'scenario.toml')
    (root / 'nodes.csv').write_text('original input must remain untouched\n', encoding='utf-8')
    original = {p.name: p.read_bytes() for p in root.iterdir()}
    app = FastAPI(); app.include_router(router)
    runtime = UserRuntime(); app.state.runtime = runtime
    geo = Geo(scenario, poles, [(('root', 'p'), 1), (('p', 'a'), 1), (('p', 'b'), 1)])
    async def pool(**kwargs):
        return geo
    runtime.get_geo_pool = pool
    with TestClient(app) as api:
        sdk = PlanningClient('http://testserver', workspace=tmp_path, auto_register=False)
        def request(method, path, **kwargs):
            response = api.request(method, path, **kwargs)
            response.raise_for_status()
            body = response.json()
            if body['status'] != 'OK':
                raise RuntimeError(body['data'])
            return body
        sdk._request = request
        session = sdk.open_project(PlanningProject(root), planner='mesh' if staged else 'graph')
        def execute():
            if not staged:
                return session.plan_mesh_network(poles=poles, geo_base_url='http://features:8080',
                                                  parent_candidates=1, corridor_m=10, max_repeaters=1)
            if session.scenario_id not in runtime.mesh_planners:
                with pytest.raises(RuntimeError, match='Call add_repeaters'):
                    session.build_candidate_edges()
            addition = session.add_repeaters(poles=poles, geo_base_url='http://features:8080',
                                              parent_candidates=1, corridor_m=10, max_repeaters=1)
            assert addition['mesh_network']['phase'] == 'repeaters_added'
            assert addition['mesh_network']['solution_scenario'] is None
            assert (root / 'mesh_expanded/scenario.toml').exists()
            expanded_nodes = set(runtime.planning_planners[session.scenario_id].nodes)
            assert expanded_nodes == {'root', 'a', 'b', 'p'}
            before_build = len(geo.calls)
            session.build_candidate_edges()
            assert len(geo.calls) == before_build
            with pytest.raises(RuntimeError, match='Metrics are incomplete'):
                session.solve()
            with pytest.raises(RuntimeError, match='Solve the network'):
                session.remove_unused_repeaters()
            session.compute_metrics(geo_base_url='http://features:8080')
            assert set(runtime.planning_planners[session.scenario_id].nodes) == expanded_nodes
            assert runtime.planning_planners[session.scenario_id].rpl is None
            with pytest.raises(RuntimeError, match='No saved solution'):
                session.get_solution()
            cost_calls = len(geo.calls)
            session.solve()
            session.remove_unused_repeaters()
            assert len(geo.calls) == cost_calls
            session.save_result(rank_threshold=10, primary_tech='mesh')
            assert 'evaluation' in session.result
            return session.result
        result = execute()
        assert result['mesh_network']['used_repeaters'] == ['p']
        assert (root / 'mesh_iterations/0001/nodes.csv').exists()
        assert (root / 'mesh_solution/scenario.toml').exists()
        assert (root / 'mesh_solution/nodes.csv').exists()
        assert set(s['site_id'] for s in load_scenario_bundle(root / 'mesh_solution/scenario.toml')['sites']) == {'root', 'a', 'b', 'p'}
        assert original == {name: (root / name).read_bytes() for name in original}
        cache = session.project.load_features()
        assert cache['entries'] and all('metric' not in e['features'] for e in cache['entries'])
        calls = len(geo.calls)
        execute()
        assert len(geo.calls) == calls
        assert set(runtime.planning_scenarios[session.scenario_id].site_nodes) == {'root', 'a', 'b'}
        saved = json.loads((root / 'result.json').read_text(encoding='utf-8'))
        assert saved['mesh_network']['used_repeaters'] == ['p']
        if staged:
            session.build_candidate_edges(planner='graph')
            assert session.scenario_id not in runtime.mesh_planners
            assert 'mesh_network' not in session.export_result()
