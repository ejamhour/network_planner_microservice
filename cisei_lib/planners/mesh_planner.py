"""Progressive mesh construction; no workspace or service-specific file access."""
from copy import deepcopy
from math import hypot, isfinite

from cisei_lib.planners.graph_planner import GraphPlanner
from cisei_lib.planners.mesh_link_planner import MeshLinkPlanner
from cisei_lib.planners.planning_scenario import PlanningScenario


class MeshPlanner:
    """Connect required interfaces progressively, proposing equipment on poles.

    Only original external accesses seed the global graph. Each local link
    study temporarily treats its destination as an access with the already
    accumulated rank. Optional poles never enter the network candidate graph
    until selected by a winning study. This is construction, not load balancing.
    """

    def __init__(self, scenario, *, geo, poles=None, parent_candidates=3,
                 corridor_m=1500.0, final_degree=3, **link_options):
        if not isinstance(scenario, PlanningScenario):
            raise TypeError('MeshPlanner requires a PlanningScenario')
        for name, value in [('parent_candidates', parent_candidates), ('final_degree', final_degree)]:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if isinstance(corridor_m, bool) or not isfinite(corridor_m) or corridor_m < 0:
            raise ValueError('corridor_m must be finite and nonnegative')
        self.geo = geo
        self.request_options = dict(poles=deepcopy(poles or []), parent_candidates=parent_candidates,
                                    corridor_m=corridor_m, final_degree=final_degree, **link_options)
        self.repeaters_added = False
        self.candidates_built = False
        self.pruned = False
        self.expanded_scenario = None
        self.result = None
        self.parent_candidates = parent_candidates
        self.corridor_m = float(corridor_m)
        self.final_degree = final_degree
        # Network construction must not discard collinear infrastructure just
        # because a single-link directional-sector heuristic excludes it.
        self.link_options = {'sector_exclusions': [], **link_options}
        self.threshold = float(link_options.get('segment_threshold', 2.0))
        if not isfinite(self.threshold) or self.threshold < 1:
            raise ValueError('segment_threshold must be finite and >= 1')
        # Canonical copies isolate both coordinate resolution and connectivity.
        self.config = scenario.to_config_dict(include_sites=True)
        self.config.pop('instances', None)
        self.config.pop('overrides', None)
        self.scenario = PlanningScenario.from_config_dict(self.config)
        for site in self.scenario.site_nodes.values():
            site.resolve_position(self.scenario.working_crs)
        self.config = self.scenario.to_config_dict(include_sites=True)
        self.graph = GraphPlanner(self.scenario)
        if any(MeshLinkPlanner.is_candidate(s) for s in self.scenario.site_nodes.values()):
            raise ValueError('Provide optional infrastructure separately as poles')
        self.original_sites = set(self.scenario.site_nodes)
        self.required = {n.node_id for n in self.graph.rpl_nodes}
        self.roots = {n.node_id for n in self.graph.rpl_nodes if n.connected}
        if not self.roots or not any(n.connected and n.rpl_relay for n in self.graph.rpl_nodes):
            raise ValueError('Mesh planning requires an enabled routing-capable external access')
        self.metrics = {}
        self.attrs = {}
        self.selected_poles = set()
        self.steps = []
        self.iterations = []
        self.poles = self._pole_records(poles or [])
        internal = [deepcopy(rule) for rule in self.scenario.connectivity_rules.values()
                    if rule.get('kind', 'rf') == 'internal']
        if internal:
            config = deepcopy(self.config)
            config['net']['connectivity'] = internal
            self.graph = GraphPlanner(PlanningScenario.from_config_dict(config))
            self.graph.build_candidate_edges_from_rules()

    def _pole_records(self, records):
        config = deepcopy(self.config)
        config['net'] = {}
        config['sites'] = deepcopy(records)
        # Validate profiles, identities, flags, and coordinates before RF calls.
        poles = PlanningScenario.from_config_dict(config)
        errors = poles.validate()
        if errors:
            raise ValueError('Invalid poles: ' + '; '.join(errors))
        if self.original_sites.intersection(poles.site_nodes):
            raise ValueError('Pole IDs must not collide with network site IDs')
        prepared = {}
        for sid, site in poles.site_nodes.items():
            site.resolve_position(poles.working_crs)
            if any(d.connected for d in site.devices):
                raise ValueError('Candidate poles cannot be external accesses')
            config = poles._site_node_to_config(site)
            config['candidate'] = True
            compatible = [n for n in site.rpl_nodes() if n.extra.get('enabled', True)
                          and n.extra.get('medium', 'rf') == 'rf'
                          and n.extra.get('interface_can_relay') and n.extra.get('device_can_route')]
            prepared[sid] = (config, compatible)
        return prepared

    @staticmethod
    def _pair(a, b):
        return tuple(sorted((a, b)))

    def _remember(self, a, b, cost, **attrs):
        key = self._pair(a, b)
        self.metrics[key] = float(cost)
        self.attrs[key] = {'kind': 'rf', 'origin': 'mesh_network', **attrs}

    def _solve(self):
        scenario = PlanningScenario.from_config_dict(self.config)
        graph = GraphPlanner(scenario)
        graph.set_candidate_edges(self.metrics, attrs_by_edge=self.attrs)
        graph.run_rpl(self.metrics)
        self.graph = graph
        return {n['node_id']: n for n in graph.to_result_dict()['planned_nodes']}

    def _destinations(self, source, served):
        return sorted((n for n in self.graph.rpl_nodes
                       if n.node_id in served and n.rpl_relay
                       and n.extra.get('medium', 'rf') == 'rf'
                       and n.extra['site_id'] != source.extra['site_id']
                       and self.graph._compatible(source, n)
                       and hypot(source.pos_utm[0]-n.pos_utm[0], source.pos_utm[1]-n.pos_utm[1]) > 0),
                      key=lambda n: (hypot(source.pos_utm[0]-n.pos_utm[0],
                                           source.pos_utm[1]-n.pos_utm[1]), n.node_id))[:self.parent_candidates]

    def _corridor(self, source, destination):
        ax, ay = source.pos_utm
        bx, by = destination.pos_utm
        dx, dy = bx-ax, by-ay
        norm = dx*dx + dy*dy
        positions = {tuple(n.pos_utm) for n in self.graph.rpl_nodes}
        selected = []
        for sid, (record, interfaces) in self.poles.items():
            if sid in self.selected_poles:
                continue
            matching = [n for n in interfaces if self.graph._compatible(source, n)]
            if not matching:
                continue
            if len(matching) != 1:
                raise ValueError(f'{sid}: enable one compatible repeater interface')
            node = matching[0]
            x, y = node.pos_utm
            t = max(0, min(1, ((x-ax)*dx+(y-ay)*dy)/norm))
            if hypot(x-ax-t*dx, y-ay-t*dy) > self.corridor_m or (x, y) in positions:
                continue
            # Keep one interface per proposed radio; other technologies remain
            # available in the infrastructure input for other link studies.
            item = deepcopy(record)
            item['interfaces'] = [i for i in item['interfaces'] if i['interface_id'] == node.node_id]
            item['devices'] = [d for d in item['devices'] if d['device_id'] == node.extra['device_id']]
            selected.append(item)
            positions.add((x, y))
        return selected

    def _study_scenario(self, source, destination, served):
        config = deepcopy(self.config)
        selected = []
        for node in (source, destination):
            item = deepcopy(next(s for s in config['sites'] if s['site_id'] == node.extra['site_id']))
            item['interfaces'] = [i for i in item['interfaces'] if i['interface_id'] == node.node_id]
            item['devices'] = [d for d in item['devices'] if d['device_id'] == node.extra['device_id']]
            for device in item['devices']:
                device['connected'] = node.node_id == destination.node_id
                device['rank'] = served[node.node_id]['rank'] if device['connected'] else None
            selected.append(item)
        config['sites'] = selected
        config['net'] = {}
        return PlanningScenario.from_config_dict(config)

    def _snapshot(self, served):
        # Full canonical configuration is authoritative; CSV rows are provided
        # for the ordinary one-device-per-site profile workflow.
        config = deepcopy(self.config)
        config['net'] = {'candidate_edges': [dict(src=a, dst=b, metric=v, **self.attrs[(a, b)])
                                            for (a, b), v in self.metrics.items()]}
        scenario = PlanningScenario.from_config_dict(config)
        try:
            rows = scenario.to_instance_records()
        except ValueError:
            rows = None
        if rows is not None:
            for row in rows:
                ids = [i.interface_id for i in scenario.site_nodes[row['site_id']].interfaces if i.enabled]
                row['served'] = all(i in served for i in ids)
                if len(ids) == 1 and ids[0] in served:
                    row['planned_rank'] = served[ids[0]]['rank']
                    row['parent'] = served[ids[0]].get('parent')
        return {'scenario': config, 'nodes': rows, 'result': self.graph.to_result_dict()}

    async def plan(self):
        """Compatibility shortcut over the explicit mesh and common stages."""
        await self.add_repeaters()
        self.build_candidate_edges()
        await self.graph.compute_edge_metrics(self.geo, bidirectional=True)
        self.graph.run_rpl(max_edge_metric=self.threshold)
        self.remove_unused_repeaters()
        return self.graph, self.to_result_dict()

    async def add_repeaters(self):
        """Only the progressive repeater-addition loop; no final candidates."""
        if self.repeaters_added:
            raise RuntimeError('Create a new MeshPlanner to repeat repeater addition')
        await self.graph.compute_edge_metrics(self.geo, list(self.graph.candidate_edges), bidirectional=True)
        for (a, b), cost in self.graph.edge_metrics.items():
            attrs = self.graph._edge_attrs(a, b)
            if attrs.get('kind') == 'internal' or cost <= self.threshold:
                self._remember(a, b, cost, **{k: v for k, v in attrs.items() if k != 'metric'})
        served = self._solve()
        self.iterations.append(self._snapshot(served))
        tried = set()
        while True:
            choices = []
            for source in self.graph.rpl_nodes:
                if source.node_id not in self.required or source.node_id in served or source.node_id in tried:
                    continue
                destinations = self._destinations(source, served) if source.extra.get('medium', 'rf') == 'rf' else []
                if destinations:
                    first = destinations[0]
                    choices.append((hypot(source.pos_utm[0]-first.pos_utm[0], source.pos_utm[1]-first.pos_utm[1]),
                                    source.node_id, source, destinations))
            if not choices:
                break
            _, _, source, destinations = min(choices, key=lambda row: row[:2])
            trials = []
            for destination in destinations:
                poles = self._corridor(source, destination)
                study = MeshLinkPlanner(self._study_scenario(source, destination, served),
                                        src_site=source.node_id, dst_site=destination.node_id,
                                        geo=self.geo, poles=poles, **self.link_options)
                _, result = await study.plan()
                search = result['mesh_search']
                trials.append({'destination': destination.node_id,
                               'destination_rank': served[destination.node_id]['rank'],
                               'total_rank': served[destination.node_id]['rank'] + search['path_cost'],
                               'accepted': search['meets_segment_threshold'], 'result': result,
                               'repeater_sites': [p for p in poles if p['site_id'] in search['repeaters']]})
            feasible = [t for t in trials if t['accepted']]
            winner = min(feasible, key=lambda t: (t['total_rank'], len(t['result']['mesh_search']['repeaters']),
                                                  t['destination'])) if feasible else None
            step = {'step': len(self.steps)+1, 'source': source.node_id, 'attempts': trials,
                    'selected_destination': winner['destination'] if winner else None}
            self.steps.append(step)
            if winner is None:
                tried.add(source.node_id)
                continue
            result = winner['result']
            for proposed in winner['repeater_sites']:
                item = deepcopy(proposed)
                item['candidate'] = False
                self.config['sites'].append(item)
                self.selected_poles.add(item['site_id'])
            for edge in result['planned_edges']:
                self._remember(edge['src'], edge['dst'], edge['metric'])
            served = self._solve()
            self.iterations.append(self._snapshot(served))
            # A new relay may make a previously unsuccessful terminal viable.
            tried.clear()
        # Keep only original fixed flags/ranks. Temporary reachability belongs
        # to the construction snapshots, not to the final solver's roots.
        config = self._snapshot(served)['scenario']
        internal = [deepcopy(r) for r in self.scenario.connectivity_rules.values()
                    if r.get('kind', 'rf') == 'internal']
        rf = [deepcopy(r) for r in self.scenario.connectivity_rules.values()
              if r.get('kind', 'rf') == 'rf']
        if not rf:
            techs = sorted({n.extra['tech'] for n in self.graph.rpl_nodes
                            if n.extra.get('medium', 'rf') == 'rf'})
            rf = [dict(kind='rf', tech=t, source='any', destination='any', degree=self.final_degree)
                  for t in techs]
        config['net']['connectivity'] = internal + rf
        self.expanded_scenario = PlanningScenario.from_config_dict(config)
        try:
            self.expanded_nodes = self.expanded_scenario.to_instance_records()
        except ValueError:
            self.expanded_nodes = None
        self.graph = GraphPlanner(self.expanded_scenario)
        self.repeaters_added = True
        return self.to_result_dict()

    def build_candidate_edges(self, *, preserve_existing=True):
        """Delegate candidate generation, with no RF or repeater search."""
        if not self.repeaters_added:
            raise RuntimeError('Call add_repeaters before building mesh candidates')
        self.graph = GraphPlanner(self.graph.scenario)
        self.graph.build_candidate_edges_from_rules(preserve_existing=preserve_existing)
        self.result = None
        self.pruned = False
        self.candidates_built = True
        return self.graph

    def _used_repeaters(self):
        rows = {n['node_id']: n for n in self.graph.to_result_dict()['planned_nodes']}
        used = set()
        for iid in self.required:
            while iid in rows and iid not in used:
                used.add(iid)
                iid = rows[iid].get('parent')
        return {n.extra['site_id'] for n in self.graph.rpl_nodes
                if n.node_id in used and n.extra['site_id'] in self.selected_poles}

    def remove_unused_repeaters(self):
        """Prune optional radios after the common solve; never add equipment."""
        if self.graph.rpl is None:
            raise RuntimeError('Solve the network before removing unused repeaters')
        used = self._used_repeaters()
        config = self.graph.scenario.to_config_dict(include_sites=True)
        removed = self.selected_poles-used
        config['sites'] = [s for s in config['sites'] if s['site_id'] not in removed]
        retained = {i['interface_id'] for s in config['sites'] for i in s['interfaces']}
        costs = {k: v for k, v in self.graph.edge_metrics.items() if set(k) <= retained}
        attrs = {k: dict(self.graph._edge_attrs(*k)) for k in costs}
        config.setdefault('net', {})['candidate_edges'] = [dict(src=a, dst=b, **attrs[(a, b)]) for a, b in costs]
        graph = GraphPlanner(PlanningScenario.from_config_dict(config))
        graph.set_candidate_edges(costs, attrs_by_edge=attrs)
        graph.edge_metrics = costs
        graph.edge_features = {k: v for k, v in self.graph.edge_features.items() if k in costs}
        graph.run_rpl(max_edge_metric=getattr(self.graph, 'last_max_edge_metric', None))
        self.graph = graph
        self.pruned = True
        return self.to_result_dict()

    def to_result_dict(self, *, include_features=False):
        """Describe the current phase and keep construction evidence separate."""
        result = self.graph.to_result_dict(include_features=include_features)
        solved = self.graph.rpl is not None
        served = {n['node_id']: n for n in result['planned_nodes']}
        current = self.graph.scenario.to_config_dict(include_sites=True)
        current['net'] = {'candidate_edges': [dict(src=a, dst=b, **self.graph._edge_attrs(a, b))
                                             for a, b in self.graph.candidate_edges],
                          'connectivity': list(self.graph.connectivity_rules.values())}
        try:
            rows = self.graph.scenario.to_instance_records()
        except ValueError:
            rows = None
        if rows is not None:
            for row in rows:
                ids = [i.interface_id for i in self.graph.scenario.site_nodes[row['site_id']].interfaces if i.enabled]
                row['served'] = solved and all(i in served for i in ids)
                if len(ids) == 1 and ids[0] in served:
                    row['planned_rank'] = served[ids[0]]['rank']
                    row['parent'] = served[ids[0]].get('parent')
        used = self._used_repeaters() if solved else set()
        present = set(self.graph.nodes)
        result['mesh_network'] = {
            'phase': (('pruned' if self.pruned else 'solved') if solved else
                      'metrics_computed' if self.graph.edge_metrics else
                      'candidates_built' if self.candidates_built else 'repeaters_added'),
            'parent_candidates': self.parent_candidates, 'corridor_m': self.corridor_m,
            'final_degree': self.final_degree, 'segment_threshold': self.threshold,
            'cost_policy': 'maximum_of_both_directions',
            'fixed_interfaces': sorted(self.roots), 'required_interfaces': sorted(self.required),
            'unserved_interfaces': sorted(self.required-set(served)) if solved else None,
            'selected_repeaters': sorted(self.selected_poles), 'used_repeaters': sorted(used) if solved else None,
            'removed_repeaters': sorted(self.selected_poles-present),
            'steps': self.steps, 'iterations': self.iterations,
            'expanded_scenario': self.expanded_scenario.to_config_dict() if self.expanded_scenario else current,
            'expanded_nodes': getattr(self, 'expanded_nodes', None),
            'solution_scenario': current if solved else None, 'solution_nodes': rows if solved else None,
        }
        self.result = GraphPlanner._json_value(result)
        return self.result
