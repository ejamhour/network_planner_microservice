from copy import deepcopy
import csv
import json
from pathlib import Path
import shutil

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.routes_planning import router
from app.session import UserRuntime
from cisei_planning_sdk import InstalledNetworkProject, LinkReplanningProject, PlanningClient
from cisei_planning_sdk.reconfiguration import compact_reconfiguration


@pytest.fixture
def study(tmp_path):
    source_path = tmp_path / 'projects/source'
    shutil.copytree(Path(__file__).resolve().parents[1] / 'examples/projects/e09_diagnostico_bro', source_path)
    source = InstalledNetworkProject(source_path)
    source.import_topology()
    config = source.export_scenario()
    selected = next(e for e in source._installed_links() if e['dst'] == 'BRO-S-GE-026:d0:i0')
    preview = source.preview_reconfiguration([{'link_id': selected['link_id'], 'mode': 'link'}])
    (source.path / 'reconfiguration.json').write_text(json.dumps(compact_reconfiguration(preview)), encoding='utf-8')
    project = LinkReplanningProject(tmp_path / 'projects/replanning', source=source)
    (project.path / 'replanning.toml').write_text('''[poles]
source="poles.csv"
device_profile="ge_radio_1"
corridor_m=1500
[search]
max_repeaters=1
max_candidates=2
segment_threshold=2
search_midpoint=true
''', encoding='utf-8')
    sites = {i['interface_id']: s for s in config['sites'] for i in s['interfaces']}
    a, b = sites[selected['src']], sites[selected['dst']]
    pole = {key: (a[key] + b[key])/2 for key in ('lat', 'lon', 'x', 'y')}
    from math import hypot
    from pyproj import Transformer
    dx, dy = b['x'] - a['x'], b['y'] - a['y']
    length = hypot(dx, dy)
    pole['x'] -= 400 * dy / length
    pole['y'] += 400 * dx / length
    pole['lon'], pole['lat'] = Transformer.from_crs('EPSG:31982', 'EPSG:4326', always_xy=True).transform(pole['x'], pole['y'])
    with (project.path / 'poles.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['pole_id', 'lat', 'lon', 'x', 'y'])
        writer.writeheader(); writer.writerow({'pole_id': 'P001', **pole})
    return source, project, selected, a, b


def client_for(tmp_path, geo):
    app = FastAPI(); app.include_router(router)
    runtime = UserRuntime(); app.state.runtime = runtime
    async def pool(**options): return geo
    runtime.get_geo_pool = pool
    return app


class Geo:
    def __init__(self, a, b, impossible=False):
        self.obstructed = {tuple(a[k] for k in ('lat', 'lon')), tuple(b[k] for k in ('lat', 'lon'))}
        self.calls = 0
        self.impossible = impossible

    async def get_features(self, tx, rx, **kwargs):
        from pyproj import Geod
        self.calls += 1
        blocked = self.impossible or {tuple(tx), tuple(rx)} == self.obstructed
        distance = Geod(ellps='WGS84').inv(tx[1], tx[0], rx[1], rx[0])[2]
        return {'fspl': 155. if blocked else 90., 'dist_m': distance, 'delta_diffra': 0.,
                'terrain_peak': {'vv_0': 2. if blocked else 0.},
                'terrain_peaks_vv': [{'v_v': 2., 'd_norm': .5}] if blocked else [],
                'buildings': dict(core=0., fresnel=0., boundary=0.),
                'vegetation': dict(core=0., fresnel=0., boundary=0.)}

    async def point_samples(self, points, **kwargs): return [700.] * len(points)


def sdk(api, root):
    client = PlanningClient('http://testserver', workspace=root, auto_register=False)
    def request(method, path, **kwargs):
        response = api.request(method, path, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if payload['status'] != 'OK': raise RuntimeError(payload['data'])
        return payload
    client._request = request
    return client


def test_preserves_installed_links_ranks_and_source_and_adds_repeater(study, tmp_path):
    source, project, selected, a, b = study
    before = {p: p.read_bytes() for p in source.path.iterdir() if p.is_file()}
    geo = Geo(a, b)
    with TestClient(client_for(tmp_path, geo)) as api:
        client = sdk(api, tmp_path)
        summary = project.prepare(client, geo_base_url='http://features:8080')
        assert summary['removed_links'] == 1 and summary['preserved_links'] == 23
        assert summary['installed_radios'] == 25
        prepared = project.source.__class__(project.path).export_scenario()
        assert len(prepared['sites']) == 25
        assert len(prepared['net']['candidate_edges']) == 23
        count = geo.calls
        assert project.prepare(client, geo_base_url='http://features:8080') == summary
        assert geo.calls == count
        report = project.replan_links(client, geo_base_url='http://features:8080')
        assert report['accepted_links'] == 1 and report['served_interfaces'] == 26
        original = json.loads((project.path / 'installed_network.json').read_text())['result']
        final = project.load_result()
        pairs = {frozenset((e['src'], e['dst'])): e['metric'] for e in final['metric_edges']}
        cut = frozenset((selected['src'], selected['dst']))
        for edge in original['metric_edges']:
            pair = frozenset((edge['src'], edge['dst']))
            if pair != cut:
                assert pairs[pair] == edge['metric']
        roots = [n for n in final['rpl_nodes'] if n['connected']]
        assert len(roots) == 1 and roots[0]['node_id'] == 'BRO-S-P7-001:d0:i0'
        assert len(final['rpl_nodes']) == 26
        assert len(project.load_features()['entries']) > 22
        project.show_prepared_network()
        project.show_link_candidates(selected['link_id'])
        project.show_link_solution(selected['link_id'])
        project.show_network()
    assert before == {p: p.read_bytes() for p in before}


def test_unacceptable_path_not_inserted_and_disconnected_preserved_links_stay_visible(study, tmp_path):
    source, project, selected, a, b = study
    geo = Geo(a, b, impossible=True)
    with TestClient(client_for(tmp_path, geo)) as api:
        client = sdk(api, tmp_path)
        project.prepare(client, geo_base_url='http://features:8080')
        report = project.replan_links(client, geo_base_url='http://features:8080')
    assert report['accepted_links'] == 0
    assert report['unresolved_links'] == [selected['link_id']]
    result = project.load_result()
    assert len(result['rpl_nodes']) == 25
    assert len(result['planned_edges']) == 23
    assert report['served_interfaces'] < 25
    assert (project.path / 'link_studies').exists()


def test_subtree_requests_rejected_before_service_calls(study):
    source, project, selected, a, b = study
    selection = source.preview_reconfiguration([{'link_id': selected['link_id'], 'mode': 'subtree'}])
    (source.path / 'reconfiguration.json').write_text(json.dumps(compact_reconfiguration(selection)), encoding='utf-8')
    with pytest.raises(ValueError, match='link mode only'):
        project.prepare(None, geo_base_url='unused')
    assert not project.scenario_path.exists()


def test_invalid_pole_location_and_changed_recipe_do_not_reset_results(study, tmp_path):
    source, project, selected, a, b = study
    geo = Geo(a, b)
    with TestClient(client_for(tmp_path, geo)) as api:
        client = sdk(api, tmp_path)
        project.prepare(client, geo_base_url='http://features:8080')
        before = project.result_path.read_bytes()
        recipe = project.path / 'replanning.toml'
        recipe.write_text(recipe.read_text().replace('1500', '1800'))
        with pytest.raises(ValueError, match='inputs changed'):
            project.prepare(client, geo_base_url='http://features:8080')
        assert project.result_path.read_bytes() == before
