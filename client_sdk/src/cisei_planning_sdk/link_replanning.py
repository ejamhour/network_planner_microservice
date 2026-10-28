"""Independent link-repair studies over an installed forest, using service planners."""
from copy import deepcopy
import csv
import hashlib
import json
from math import hypot, isfinite
from pathlib import Path

import tomlkit

from .project import PlanningProject
from .installed_project import InstalledNetworkProject
from .local_io import load_scenario_bundle, save_scenario, save_result, _instantiate_sites, _json_value


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _key(identifier):
    return hashlib.sha256(identifier.encode()).hexdigest()[:12]


class LinkReplanningProject(PlanningProject):
    """Keep installed radios/links and replace only manually selected RF links.

    The service computes costs, searches pole paths, and selects final routes.
    The SDK prepares local inputs, caches and independent study artifacts.
    No automatic connectivity rules are generated over the installed network.
    """

    def __init__(self, path, *, source):
        super().__init__(path)
        self.source = source if isinstance(source, InstalledNetworkProject) else InstalledNetworkProject(source)
        if self.path == self.source.path or self.path.is_relative_to(self.source.path) or self.source.path.is_relative_to(self.path):
            raise ValueError('Source and replanning project must be separate directories')

    def _settings(self):
        settings = tomlkit.parse((self.path / 'replanning.toml').read_text(encoding='utf-8')).unwrap()
        poles = settings.get('poles', {})
        path = (self.path / poles.get('source', 'poles.csv')).resolve()
        if not path.is_relative_to(self.path) or not path.is_file():
            raise ValueError('The pole CSV must exist inside the replanning project')
        radius = poles.get('corridor_m', 1500.0)
        if isinstance(radius, bool) or not isinstance(radius, (float, int)) or not isfinite(radius) or radius <= 0:
            raise ValueError('poles.corridor_m must be positive and finite')
        return settings, path

    def _session(self, client, name, config):
        previous = client.project
        try:
            client.project = self
            session = client.define_scenario(name=name, scenario=config, planner='graph')
        finally:
            client.project = previous
        if session.validation_errors:
            raise ValueError(f'Invalid study: {session.validation_errors}')
        # Stage outputs are written explicitly; a link study must not replace
        # the whole-network result or modify the source project's cache.
        session.project = None
        if self.features_path.exists():
            session.import_feature_cache(self.load_features())
        return session

    def prepare(self, client, *, geo_base_url, reconfiguration=None):
        """Prepare a separate project, preserving installed costs and fixed ranks.

        Existing preparation is reused only for identical source/selection and
        settings. Changed inputs require a new project; results are not reset.
        """
        settings, poles_path = self._settings()
        selection = self.source.load_reconfiguration(reconfiguration or self.source.path / 'reconfiguration.json')
        if selection['remove_devices'] or any(r['mode'] != 'link' for r in selection['requests']):
            raise ValueError('Link replanning requires link mode only, without radio removals; revise the selection')
        source_config = self.source.export_scenario()
        source_result = self.source.load_result()
        source_edges = self.source._installed_links()
        by_id = {e['link_id']: e for e in source_edges}
        if any(by_id[key].get('kind') == 'internal' for key in selection['remove_links']):
            raise ValueError('Cable connections cannot be replaced by an RF link search')
        if any(len(s.get('interfaces', [])) != 1 or len(s.get('devices', [])) != 1 for s in source_config['sites']):
            raise ValueError('This preparation requires one device/interface per installed site')
        signature = hashlib.sha256(json.dumps({'selection': selection, 'settings': settings}, sort_keys=True).encode()
                                   + poles_path.read_bytes()).hexdigest()
        state_path = self.path / 'replanning_state.json'
        if state_path.exists():
            state = _read(state_path)
            if state['input_sha256'] != signature:
                raise ValueError('Replanning inputs changed; use a new destination project')
            return state['summary']
        if self.scenario_path.exists() or self.result_path.exists():
            raise ValueError('Destination already contains unrecognized planning outputs')
        if self.source.features_path.exists():
            self.save_features(self.source.load_features())
        session = self._session(client, self.name + '_installed', source_config)
        session.build_candidate_edges(preserve_existing=True)
        session.compute_metrics(geo_base_url=geo_base_url)
        self.save_features(session.export_feature_cache())
        session.solve()
        original = session.export_result(include_features=False)
        original_nodes = {n['node_id']: n for n in original['planned_nodes']}
        expected_parents = {n['node_id']: n['parent'] for n in source_result['planned_nodes']}
        if {iid: n['parent'] for iid, n in original_nodes.items()} != expected_parents:
            raise ValueError('Cost evaluation changed installed routes; preparation stopped')
        removed = set(selection['remove_links'])
        cut_pairs = {frozenset((by_id[key]['src'], by_id[key]['dst'])) for key in removed}
        connected = set()
        for iid in original_nodes:
            path = self.source.path_to_backbone(iid)
            if not any(frozenset(pair) in cut_pairs for pair in zip(path, path[1:])):
                connected.add(iid)
        remaining = [deepcopy(e) for e in source_edges if e['link_id'] not in removed]
        config = tomlkit.parse(self.source.scenario_path.read_text(encoding='utf-8')).unwrap()
        instances = config.get('instances', {})
        source_csv = (self.source.path / instances.get('source', 'nodes.csv')).resolve()
        if not source_csv.is_relative_to(self.source.path):
            raise ValueError('Installed nodes CSV must remain inside its project')
        with source_csv.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            fields, rows = list(reader.fieldnames or []), list(reader)
        for name in ('connected', 'rank'):
            if name not in fields:
                fields.append(name)
        by_site = {n['site_id']: n for n in original_nodes.values()}
        for row in rows:
            node = by_site[row['site_id']]
            row['connected'] = str(node['node_id'] in connected)
            row['rank'] = node['rank'] if node['node_id'] in connected else 'inf'
        config['instances']['source'] = 'nodes.csv'
        config['net'] = {'connectivity': [], 'candidate_edges': remaining}
        config.pop('mesh', None)
        # The inventory is copied, never interpreted as permission to remove radios.
        if config.get('attachments'):
            inventory = self.source.attached_equipment()
            with (self.path / 'attached_equipment.csv').open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(inventory[0]) if inventory else ['equipment_id', 'equipment_type', 'device_id'])
                writer.writeheader(); writer.writerows(inventory)
            config['attachments'] = {'source': 'attached_equipment.csv', 'format': 'csv'}
        with (self.path / 'nodes.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        save_scenario(config, self.scenario_path)
        save_result(selection, self.path / 'reconfiguration.json')
        save_result({'scenario': source_config, 'result': original}, self.path / 'installed_network.json')
        prepared = deepcopy(original)
        prepared['planned_nodes'] = [n for n in prepared['planned_nodes'] if n['node_id'] in connected]
        prepared['planned_edges'] = [e for e in prepared['planned_edges'] if frozenset((e['src'], e['dst'])) not in cut_pairs]
        prepared['metric_edges'] = [e for e in prepared['metric_edges'] if frozenset((e['src'], e['dst'])) not in cut_pairs]
        prepared['candidate_edges'] = deepcopy(remaining)
        prepared['counts'].update(candidate_edges=len(remaining), metric_edges=len(remaining), planned_nodes=len(connected), planned_edges=len(prepared['planned_edges']))
        prepared['attached_equipment'] = self.source.attached_equipment()
        save_result(prepared, self.path / 'prepared_network.json')
        self.save_result(prepared)
        summary = {'installed_radios': len(original_nodes), 'removed_links': len(removed),
                   'preserved_links': len(remaining), 'connected_interfaces': len(connected),
                   'floating_interfaces': len(original_nodes) - len(connected)}
        save_result({'input_sha256': signature, 'summary': summary}, state_path)
        return summary

    def _poles(self, config, child, parent, link_id):
        settings, path = self._settings()
        controls = settings['poles']
        radius = float(controls.get('corridor_m', 1500))
        endpoints = {i['interface_id']: s for s in config['sites'] for i in s['interfaces']}
        a, b = endpoints[child], endpoints[parent]
        dx, dy = b['x'] - a['x'], b['y'] - a['y']
        norm = dx * dx + dy * dy
        selected, positions = [], {(a['lat'], a['lon']), (b['lat'], b['lon'])}
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for row in csv.DictReader(handle):
                x, y = float(row['x']), float(row['y'])
                t = max(0, min(1, ((x-a['x'])*dx + (y-a['y'])*dy)/norm)) if norm else 0
                if hypot(x - a['x'] - t*dx, y - a['y'] - t*dy) > radius:
                    continue
                position = (float(row['lat']), float(row['lon']))
                if position in positions:
                    continue
                positions.add(position)
                item = dict(row)
                item['site_id'] = 'R-' + _key(link_id) + '-' + row['pole_id']
                item['original_pole_id'] = row['pole_id']
                item['device_profile'] = row.get('device_profile') or controls['device_profile']
                item['mount_height_m'] = row.get('mount_height_m') or controls.get('mount_height_m', 7)
                item.update(connected=False, candidate=True, rank=None)
                selected.append(item)
        return _json_value(_instantiate_sites(config, selected, {'profile_column': 'device_profile'}))

    def replan_links(self, client, *, geo_base_url, on_link=None, verbose=True):
        """Search each removed pair, consolidate only proposals meeting the threshold.

        Existing edges keep their costs. Final route computation receives only
        preserved edges plus accepted replacement paths, with original roots.
        """
        snapshot = _read(self.path / 'installed_network.json')
        selection = _read(self.path / 'reconfiguration.json')
        config, original = snapshot['scenario'], snapshot['result']
        nodes = {n['node_id']: n for n in original['planned_nodes']}
        by_id = {e['link_id']: e for e in config['net']['candidate_edges']}
        removed = set(selection['remove_links'])
        final_config = deepcopy(config)
        final_edges = [deepcopy(e) for e in config['net']['candidate_edges'] if e['link_id'] not in removed]
        pair_costs = {frozenset((e['src'], e['dst'])): e['metric'] for e in original['metric_edges']}
        metrics = [{'src': e['src'], 'dst': e['dst'], 'metric': pair_costs[frozenset((e['src'], e['dst']))]} for e in final_edges]
        settings, _ = self._settings()
        reports = []
        for identifier in selection['remove_links']:
            edge = by_id[identifier]
            a, b = edge['src'], edge['dst']
            child, parent = (a, b) if nodes[a]['parent'] == b else (b, a)
            study_config = deepcopy(config)
            study_config['net'] = {}
            study_config.pop('overrides', None)
            study_config['sites'] = [s for s in study_config['sites'] if any(i['interface_id'] in (child, parent) for i in s['interfaces'])]
            for site in study_config['sites']:
                for device in site['devices']:
                    device['connected'] = any(i['interface_id'] == parent for i in site['interfaces'])
                    device['rank'] = 0 if device['connected'] else None
            poles = self._poles(config, child, parent, identifier)
            if verbose:
                print(f'Planejando {identifier}: {len(poles)} postes no corredor', flush=True)
            session = self._session(client, self.name + '_' + _key(identifier), study_config)
            result = session.plan_mesh_link(child, parent, poles=poles, geo_base_url=geo_base_url, **settings.get('search', {}))
            self.save_features(session.export_feature_cache())
            directory = self.path / 'link_studies' / _key(identifier)
            save_result(result, directory / 'result.json')
            accepted = result['mesh_search']['meets_segment_threshold']
            if verbose:
                print(f'{identifier}: {"aceito" if accepted else "não atende ao limite"}; '
                      f'{len(result["mesh_search"]["repeaters"])} repetidores; '
                      f'{result["mesh_search"]["evaluated_edges"]} enlaces avaliados', flush=True)
            if accepted:
                selected = set(result['mesh_search']['repeaters'])
                final_config['sites'].extend({**s, 'candidate': False} for s in poles if s['site_id'] in selected)
                for index, new_edge in enumerate(result['planned_edges']):
                    record = {**new_edge, 'link_id': 'repair-' + _key(identifier) + '-' + str(index), 'origin': 'link_replanning'}
                    final_edges.append(record)
                    metrics.append({'src': record['src'], 'dst': record['dst'], 'metric': record['metric']})
            reports.append({'link_id': identifier, 'accepted': accepted, **result['mesh_search']})
            save_result({'links': reports}, self.path / 'replanning_result.json')
            if on_link:
                on_link(deepcopy(reports[-1]))
        final_config['net'] = {'candidate_edges': final_edges, 'connectivity': []}
        session = self._session(client, self.name + '_consolidated', final_config)
        session.build_candidate_edges(preserve_existing=True)
        session.set_edge_metrics(metrics)
        session.solve()
        final = session.export_result(include_features=False)
        # Disconnected preserved components remain visible even though the
        # solver correctly excludes their nodes from the served forest.
        planned = {frozenset((e['src'], e['dst'])) for e in final['planned_edges']}
        final['planned_edges'].extend(e for e in final_edges if frozenset((e['src'], e['dst'])) not in planned)
        final['counts']['served_edges'] = len(planned)
        final['counts']['planned_edges'] = len(final['planned_edges'])
        final['attached_equipment'] = _read(self.path / 'prepared_network.json').get('attached_equipment', [])
        final['replanning'] = {'links': reports, 'preserved_links': len(by_id) - len(removed)}
        self.save_result(final)
        save_scenario(final_config, self.path / 'solution_scenario.toml')
        return {'requested_links': len(reports), 'accepted_links': sum(r['accepted'] for r in reports),
                'unresolved_links': [r['link_id'] for r in reports if not r['accepted']],
                'served_interfaces': len(final['planned_nodes']), 'total_interfaces': len(final['rpl_nodes'])}

    def show_prepared_network(self):
        from .visualization import plot_network
        result = _read(self.path / 'prepared_network.json')
        result['replanning_stage'] = 'prepared'
        return plot_network(result, save_path=self.path / 'prepared_network.html')

    def show_network(self, *, save_path=None):
        from .visualization import plot_network
        result = self.load_result()
        snapshot = _read(self.path / 'installed_network.json')
        removed = set(_read(self.path / 'reconfiguration.json')['remove_links'])
        preserved = {frozenset((e['src'], e['dst'])) for e in snapshot['scenario']['net']['candidate_edges'] if e['link_id'] not in removed}
        for edge in result['planned_edges']:
            edge['origin'] = 'installed_network' if frozenset((edge['src'], edge['dst'])) in preserved else 'link_replanning'
        result['replanning_stage'] = 'consolidated'
        return plot_network(result, save_path=save_path or self.path / 'solution_network.html')

    def show_link_candidates(self, link_id):
        from .visualization import plot_network
        result = _read(self.path / 'link_studies' / _key(link_id) / 'result.json')
        return plot_network(result['mesh_candidates'], kind='candidate', save_path=self.path / ('candidates_' + _key(link_id) + '.html'))

    def show_link_solution(self, link_id):
        from .session import ScenarioSession
        result = _read(self.path / 'link_studies' / _key(link_id) / 'result.json')
        session = ScenarioSession(None, self.name, 'graph', '', project=self, result=result)
        return session.show_mesh_link()
