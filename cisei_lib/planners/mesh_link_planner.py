"""Adapt the pole-search algorithm to scenario interfaces and raw feature caching."""
from copy import deepcopy
from math import isfinite

from cisei_lib.planners.graph_planner import GraphPlanner
from cisei_lib.planners.link_planner import LinkPlanner
from cisei_lib.planners.planner_classes import PlannerNode, Pole
from cisei_lib.planners.planning_scenario import PlanningScenario


class MeshLinkPlanner(LinkPlanner):
    """Plan one bidirectional mesh link, without filesystem access.

    Endpoints select RF interfaces. Optional candidates are filtered by the
    endpoints' technology, frequency and routing capabilities.
    An undirected search edge uses the greater cost of its two directions.
    Candidate sites describe proposed equipment, not installed repeaters.
    """

    def __init__(self, scenario, *, src_site, dst_site, geo, pole_sites=None, poles=None, **options):
        if poles is not None:
            # Keep the stored network scenario unchanged. These hypothetical
            # interfaces exist only in this link study and its search snapshot.
            config = scenario.to_config_dict(include_sites=True)
            known = set(scenario.site_nodes)
            candidates = []
            for record in poles:
                record = deepcopy(record)
                identifier = record.get('site_id')
                if not identifier or identifier in known:
                    raise ValueError(f'Duplicate or conflicting pole ID: {identifier}')
                known.add(identifier)
                record['candidate'] = True
                for device in record.get('devices', []):
                    if device.get('connected', False):
                        raise ValueError('Candidate poles cannot be external accesses')
                candidates.append(record)
            config['sites'] = [*config.get('sites', []), *candidates]
            scenario = PlanningScenario.from_config_dict(config)
        for site_node in scenario.site_nodes.values():
            site_node.site.resolve(scenario.working_crs)
        self.input_graph = GraphPlanner(scenario)
        self.site_interfaces = {}
        by_id = {}
        for node in self.input_graph.rpl_nodes:
            self.site_interfaces.setdefault(node.extra['site_id'], []).append(node)
            by_id[node.node_id] = node

        def endpoint(identifier):
            nodes = [by_id[identifier]] if identifier in by_id else self.site_interfaces.get(identifier, [])
            nodes = [n for n in nodes if n.extra.get('medium', 'rf') == 'rf']
            if len(nodes) != 1:
                raise ValueError(f'{identifier}: select one enabled RF interface explicitly')
            return nodes[0]

        src, dst = endpoint(src_site), endpoint(dst_site)
        src_site, dst_site = src.extra['site_id'], dst.extra['site_id']
        if src_site == dst_site:
            raise ValueError('Endpoints must be distinct sites')
        if src.extra['tech'] != dst.extra['tech'] or src.extra['freq_mhz'] != dst.extra['freq_mhz']:
            raise ValueError('Endpoint interfaces must share technology and frequency')
        if not dst.connected:
            raise ValueError('The destination must be connected to an external access')
        if src.connected:
            raise ValueError('The source must be a node requiring connectivity')
        for site_id in (src_site, dst_site):
            if self.is_candidate(scenario.site_nodes[site_id]):
                raise ValueError('Endpoints cannot be optional candidate nodes')
        self.tech = src.extra['tech']
        self.excluded_candidates = []
        resolved = {src_site: src, dst_site: dst}
        if pole_sites is not None:
            if len(set(pole_sites)) != len(pole_sites):
                raise ValueError('Duplicate pole_sites')
            unknown = set(pole_sites) - set(scenario.site_nodes)
            if unknown:
                raise ValueError(f'Unknown candidate sites: {sorted(unknown)}')
        for site_id, site_node in scenario.site_nodes.items():
            if site_id in resolved or not self.is_candidate(site_node):
                continue
            if pole_sites is not None and site_id not in pole_sites:
                continue
            nodes = self.site_interfaces.get(site_id, [])
            compatible = [n for n in nodes if n.extra.get('medium', 'rf') == 'rf'
                          and n.extra['tech'] == self.tech
                          and n.extra['freq_mhz'] == src.extra['freq_mhz']
                          and n.extra.get('interface_can_relay')
                          and n.extra.get('device_can_route') and not n.connected]
            if not compatible:
                self.excluded_candidates.append({'site_id': site_id,
                    'reason': 'no_enabled_compatible_routing_interface'})
                continue
            if len(compatible) > 1:
                raise ValueError(f'{site_id}: multiple compatible candidate interfaces; enable only the intended one')
            resolved[site_id] = compatible[0]
        self.resolved = resolved
        self.src_site, self.dst_site = src_site, dst_site
        self.scenario = scenario
        if self.tech not in self.input_graph.metric_functions_by_tech:
            raise ValueError(f'Missing metric for {self.tech}')
        self.metric = self.input_graph.metric_functions_by_tech[self.tech]
        super().__init__(project='mesh-link', user_id='mesh-link', persist=False,
                         projected_crs=scenario.working_crs,
                         freq_mhz=float(src.extra['freq_mhz']), **options)
        self.geo = geo  # The service owns this resource.
        self.position_nodes = {}
        for site_id, node in resolved.items():
            site = self.input_graph.nodes[site_id]
            pos = (float(site.site.lat), float(site.site.lon))
            if pos in self.position_nodes:
                raise ValueError('Mesh search requires distinct site coordinates')
            self.position_nodes[pos] = node
            if site_id not in (src_site, dst_site):
                self.add_pole(Pole(site_id, *pos, ant_h=node.extra['mount_height_m']))
            else:
                self.add_node(PlannerNode(site_id, *pos, ant_h=node.extra['mount_height_m']))

    @staticmethod
    def is_candidate(site_node):
        from cisei_lib.planners.graph_planner import parse_bool
        return parse_bool(site_node.site.extra.get('candidate'), False)

    def scenario_for_sites(self, site_ids):
        config = self.scenario.to_config_dict(include_sites=True)
        selected = []
        for site in config['sites']:
            site_id = site['site_id']
            if site_id not in site_ids:
                continue
            node = self.resolved[site_id]
            site['interfaces'] = [i for i in site['interfaces'] if i['interface_id'] == node.node_id]
            site['devices'] = [d for d in site['devices'] if d['device_id'] == node.extra['device_id']]
            selected.append(site)
        config['sites'] = selected
        config['net'] = {}
        config.pop('overrides', None)
        return PlanningScenario.from_config_dict(config)

    async def edge_metric(self, src_pos, dst_pos, src_ha, dst_ha, max_attempts=3):
        src_pos, dst_pos = tuple(src_pos), tuple(dst_pos)
        if src_pos == dst_pos:
            raise ValueError('Coincident endpoints cannot form an RF link')
        src, dst = self.position_nodes[src_pos], self.position_nodes[dst_pos]
        raw = await self.geo.get_features(src_pos, dst_pos, tx_ha=src_ha,
                                          rx_ha=dst_ha, freq_mhz=self.context['freq_mhz'])
        reverse = await self.geo.get_features(dst_pos, src_pos, tx_ha=dst_ha,
                                              rx_ha=src_ha, freq_mhz=self.context['freq_mhz'])
        values = [float(self.metric(self.input_graph._metric_record(src, dst, raw))),
                  float(self.metric(self.input_graph._metric_record(dst, src, reverse)))]
        if any(not isfinite(v) or v < 1 for v in values):
            raise ValueError('Mesh search requires finite edge costs >= 1')
        record = deepcopy(raw)
        record['metric'] = max(values)
        record['obs_pos'] = self._top_peak_coordinates(src_pos, dst_pos, raw)
        return record

    def find_shortest_path(self, s_label, d_label):
        # Respect the requested repeater limit also in the final selection.
        paths = self.find_shortest_paths(s_label, d_label,
                    self.context['max_repeaters'], self.context['max_candidates'],
                    max_edge_metric=self.context.get('segment_threshold', 2.0))
        if not paths:
            paths = self.find_shortest_paths(s_label, d_label,
                        self.context['max_repeaters'], self.context['max_candidates'])
        if not paths:
            raise ValueError('No path within the repeater limit')
        edges = min(paths, key=lambda path: sum(e[2]['metric'] for e in path))
        path = [s_label, *[e[1] for e in edges]]
        return path, sum(e[2]['metric'] for e in edges), [(n, self.G.nodes[n]) for n in path], edges

    async def plan(self):
        await self.set_link_by_id(self.src_site, self.dst_site)
        if self.poles.empty:
            path = [self.src_site, self.dst_site]
        else:
            path = (await self.plan_link())['node_path']
        graph = GraphPlanner(self.scenario_for_sites(set(path)))
        metrics = {}
        for a, b in zip(path, path[1:]):
            src, dst = self.resolved[a].node_id, self.resolved[b].node_id
            cost = float(self.G.edges[a, b]['metric'])
            metrics[(dst, src)] = cost
        graph.set_candidate_edges(metrics, default_attrs={'kind': 'rf', 'tech': self.tech, 'origin': 'mesh_search'})
        graph.run_rpl(metrics)
        result = graph.to_result_dict()
        result['mesh_search'] = {
            'src_site': self.src_site, 'dst_site': self.dst_site,
            'node_path': path, 'repeaters': path[1:-1],
            'path_cost': sum(metrics.values()),
            'segment_threshold': self.context.get('segment_threshold', 2.0),
            'meets_segment_threshold': all(v <= self.context.get('segment_threshold', 2.0) for v in metrics.values()),
            'evaluated_edges': self.G.number_of_edges(),
            'cost_policy': 'maximum_of_both_directions',
            'path_selection': 'quality_first_then_cost',
            'src_interface': self.resolved[self.src_site].node_id,
            'dst_interface': self.resolved[self.dst_site].node_id,
            'reference_rank': self.resolved[self.dst_site].rank,
            'eligible_candidates': [s for s in self.resolved if s not in (self.src_site, self.dst_site)],
            'excluded_candidates': self.excluded_candidates,
        }
        # Snapshot every evaluated edge, not merely edges on the winning path.
        explored = GraphPlanner(self.scenario_for_sites(set(self.resolved)))
        costs, attributes = {}, {}
        selected_edges = {frozenset((a, b)) for a, b in zip(path, path[1:])}
        for a, b, data in self.G.edges(data=True):
            edge = (self.resolved[a].node_id, self.resolved[b].node_id)
            costs[edge] = float(data['metric'])
            attributes[edge] = {'kind': 'rf', 'tech': self.tech, 'origin': 'mesh_search',
                                'evaluated': True, 'expanded': bool(data.get('tested')),
                                'selected': frozenset((a, b)) in selected_edges}
        explored.set_candidate_edges(costs, attrs_by_edge=attributes)
        explored.edge_metrics = costs
        candidate_result = explored.to_result_dict()
        for row in candidate_result['rpl_nodes']:
            row['candidate'] = self.is_candidate(self.scenario.site_nodes[row['site_id']])
            row['evaluated'] = row['site_id'] in self.G
        candidate_result['mesh_search'] = deepcopy(result['mesh_search'])
        result['mesh_candidates'] = candidate_result
        return graph, result
