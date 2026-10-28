"""Read an installed forest from existing scenario inputs, without planning."""
from collections import deque
from copy import deepcopy

from .local_io import load_scenario_bundle
from .project import PlanningProject


class InstalledNetworkProject(PlanningProject):
    """An installed network whose explicit links are the source of topology.

    This first version accepts a forest with one backbone access per component.
    It never selects among alternative routes or uses the informational
    next_hop column to create connections. Cycles and ambiguous roots fail.
    """

    def export_scenario(self):
        """Read the local input bundle; never contact a planning worker."""
        return load_scenario_bundle(self.scenario_path)

    @property
    def enriched_features_path(self):
        from .baseline import BaselineProject
        return BaselineProject(self.path).enriched_features_path

    def _installed_links(self):
        config = self.export_scenario()
        if config.get("net", {}).get("connectivity"):
            raise ValueError("Installed topology must not generate new connections")
        result = self.load_result()
        if result.get("topology_origin") != "installed":
            raise ValueError("Import the installed topology first")
        edges = config.get("net", {}).get("candidate_edges", [])
        original = {frozenset((e["src"], e["dst"])) for e in result["planned_edges"]}
        current = {frozenset((e["src"], e["dst"])) for e in edges}
        if current != original or len(edges) != len(original):
            raise ValueError("Connections changed; import the installed topology again")
        return deepcopy(edges)

    def extract_features(self, client, *, geo_base_url, batch_size=10, on_batch=None):
        """Reuse the service's batched extraction stage, preserving local routes.

        The existing service stage also computes configured link costs. They
        are not exported as installed ranks or diagnoses. No solve/evaluate
        call is made, and hop/path measurements are not read.
        """
        edges = self._installed_links()
        session = client.open_project(self, planner="graph")
        if session.validation_errors:
            raise ValueError(f"Invalid installed scenario: {session.validation_errors}")
        session.build_candidate_edges(preserve_existing=True)
        actual = session.get_candidate_edges()["candidate_edges"]
        if {(e["src"], e["dst"]) for e in actual} != {(e["src"], e["dst"]) for e in edges}:
            raise ValueError("Service changed installed link identities or direction")
        session.compute_metrics(geo_base_url=geo_base_url, batch_size=batch_size,
                                on_batch=on_batch)
        return {"links": len(edges), "rf_links": sum(e.get("kind") != "internal" for e in edges),
                "cache_entries": len(self.load_features()["entries"])}

    def link(self, geo, src_interface, dst_interface):
        """Prepare an installed link using the established features resolver."""
        from .link_browser import prepare_link
        return prepare_link(self, geo, src_interface, dst_interface)

    def inspect_links(self, geo):
        """Show the existing features inspector; load only on user selection."""
        from .link_panel import LinkInspectionPanel
        edges = [e for e in self._installed_links() if e.get("kind") != "internal"]
        panel = LinkInspectionPanel(self, geo, edges, auto_load=False, initial_endpoint="")
        panel.show()
        return panel

    def enrich_features(self, *, measurements=None, replace=False):
        """Reuse E08 enrichment, excluding installed internal connections.

        Installed observations identify their explicit connection with link_id.
        Its direction must match the input connection, not the upstream path.
        All records are checked before delegating the atomic cache update.
        """
        import json
        from pathlib import Path
        from .baseline import BaselineProject

        baseline = BaselineProject(self.path)
        if baseline.features_path != self.features_path:
            raise ValueError("baseline.json data must reference this project's feature cache")
        if measurements is None:
            if baseline.measurements_path is None:
                raise ValueError("Define measurements in baseline.json or provide measurements=")
            measurements = json.loads(baseline.measurements_path.read_text(encoding="utf-8"))
        elif isinstance(measurements, (str, Path)):
            measurements = json.loads(baseline._file(measurements).read_text(encoding="utf-8"))
        if (not isinstance(measurements, dict) or measurements.get("format") != "cisei-link-measurements"
                or type(measurements.get("version")) is not int or measurements["version"] != 1
                or not isinstance(measurements.get("records"), list)):
            raise ValueError("Expected cisei-link-measurements version 1 with a records list")
        edges = {}
        for edge in self._installed_links():
            identifier = edge.get("link_id")
            if not isinstance(identifier, str) or not identifier.strip() or identifier in edges:
                raise ValueError("Installed connections require unique link_id values for enrichment")
            edges[identifier] = edge
        config = self.export_scenario()
        endpoints = {i["interface_id"]: site["site_id"] for site in config["sites"]
                     for i in site["interfaces"]}
        samples, skipped, identifiers = [], [], set()
        for index, row in enumerate(measurements["records"]):
            if not isinstance(row, dict):
                raise ValueError(f"hop_metrics.records[{index}]: expected an object")
            identifier = row.get("id")
            if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
                raise ValueError(f"hop_metrics.records[{index}]: missing or duplicate observation id")
            identifiers.add(identifier)
            link_id = row.get("link_id")
            if not isinstance(link_id, str) or link_id not in edges:
                raise ValueError(f"hop_metrics.records[{index}]: unknown or missing link_id")
            edge = edges[link_id]
            for side, endpoint in (("tx", edge["src"]), ("rx", edge["dst"])):
                radio = row.get(side)
                if (not isinstance(radio, dict) or radio.get("name") != endpoints[endpoint]
                        or radio.get("interface_id", endpoint) != endpoint):
                    raise ValueError(f"hop_metrics.records[{index}]: {side} conflicts with installed link direction")
            if edge.get("kind") == "internal":
                skipped.append(identifier)
            else:
                samples.append(deepcopy(row))
        report = baseline.enrich_features(measurements={**measurements, "records": samples}, replace=replace)
        return {**report, "input_records": len(identifiers), "internal_skipped": len(skipped),
                "internal_observation_ids": skipped}

    def import_topology(self):
        config = load_scenario_bundle(self.scenario_path)
        net = config.get("net", {})
        if net.get("connectivity"):
            raise ValueError("Installed topology requires explicit links, without connectivity rules")
        nodes, neighbors = {}, {}
        for site in config.get("sites", []):
            devices = {d["device_id"]: d for d in site.get("devices", [])}
            for interface in site.get("interfaces", []):
                if not interface.get("enabled", True):
                    raise ValueError("Installed topology import requires enabled interfaces")
                iid = interface["interface_id"]
                if iid in nodes:
                    raise ValueError(f"Duplicate interface: {iid}")
                device = devices[interface["device_id"]]
                nodes[iid] = {**deepcopy(interface), "node_id": iid,
                              "x": site.get("x"), "y": site.get("y"),
                              "lat": site.get("lat"), "lon": site.get("lon"),
                              "connected": bool(device.get("connected", False)),
                              "metadata": {k: v for k, v in site.items()
                                           if k not in {"devices", "interfaces"}}}
                neighbors[iid] = set()
        if not nodes:
            raise ValueError("Installed topology has no interfaces")
        edges, pairs = [], set()
        for edge in net.get("candidate_edges", []):
            src, dst = edge["src"], edge["dst"]
            if src not in nodes or dst not in nodes:
                raise ValueError("Installed link references an unknown interface")
            pair = frozenset((src, dst))
            if src == dst or pair in pairs:
                raise ValueError("Self links and duplicate installed links are not supported")
            pairs.add(pair)
            neighbors[src].add(dst)
            neighbors[dst].add(src)
            edges.append(deepcopy(edge))
        pending = set(nodes)
        planned = []
        while pending:
            start = min(pending)
            component, stack = set(), [start]
            while stack:
                iid = stack.pop()
                if iid in component:
                    continue
                component.add(iid)
                stack.extend(neighbors[iid] - component)
            if sum(len(neighbors[i]) for i in component) != 2 * (len(component) - 1):
                raise ValueError("Installed topology contains a cycle; route selection is not supported")
            roots = [i for i in component if nodes[i]["connected"]]
            if len(roots) != 1:
                raise ValueError("Each installed component must have exactly one backbone access")
            queue = deque([(roots[0], None, 0)])
            while queue:
                iid, parent, hops = queue.popleft()
                planned.append({**nodes[iid], "parent": parent, "hop_count": hops,
                                "rank": None})
                queue.extend((child, iid, hops + 1) for child in sorted(neighbors[iid]) if child != parent)
            pending -= component
        parent_by_id = {n["node_id"]: n["parent"] for n in planned}
        for edge in edges:
            if parent_by_id[edge["dst"]] == edge["src"]:
                edge["src"], edge["dst"] = edge["dst"], edge["src"]
        result = {"topology_origin": "installed", "rpl_nodes": list(nodes.values()),
                  "planned_nodes": planned, "planned_edges": edges,
                  "antennas": config.get("antennas", {})}
        self.save_result(result)
        return {"interfaces": len(nodes), "links": len(edges),
                "backbone_accesses": sum(n["connected"] for n in nodes.values())}

    def path_to_backbone(self, interface_id):
        result = self.load_result()
        nodes = {n["node_id"]: n for n in result["planned_nodes"]}
        if interface_id not in nodes:
            raise ValueError(f"Unknown interface: {interface_id}")
        path, seen = [], set()
        current = interface_id
        while current is not None:
            if current in seen or current not in nodes:
                raise ValueError("Invalid installed parent assignments")
            seen.add(current)
            path.append(current)
            current = nodes[current]["parent"]
        if not nodes[path[-1]]["connected"]:
            raise ValueError("Path does not reach a backbone access")
        return path

    def evaluate_links(self, *, rssi_threshold_dbm):
        from .installed_diagnosis import evaluate_links
        return evaluate_links(self, rssi_threshold_dbm)

    def diagnose_path(self, interface_id):
        from .installed_diagnosis import load_diagnosis, path_diagnosis
        return path_diagnosis(self, interface_id, load_diagnosis(self))

    def attached_equipment(self):
        """Read equipment associated with each radio; no network or file mutation."""
        from .attachments import load_attached_equipment
        return load_attached_equipment(self.scenario_path) or []

    def preview_reconfiguration(self, requests, *, repeater_devices=()):
        """Compute link/subtree removal effects without modifying any project input."""
        from .reconfiguration import preview_reconfiguration
        return preview_reconfiguration(self, requests, repeater_devices=repeater_devices)

    def load_reconfiguration(self, path):
        """Read a confirmed review, checking source identity and recomputing impact."""
        from .reconfiguration import load_reconfiguration
        return load_reconfiguration(self, path)

    def review_reconfiguration(self, *, layout="logical", repeater_devices=(), save_path=None):
        """Review removals and save a confirmed JSON proposal in the project.

        Each link can be selected in link or subtree mode. Removable repeaters
        must be explicitly identified, either here or in the review controls.
        In Jupyter, use its authenticated Contents API. A standalone offline
        HTML view offers an explicitly labeled download instead.
        """
        return self.show_topology(layout=layout, diagnosis=True, save_path=save_path,
                                  _review_repeaters=repeater_devices)

    def show_topology(self, *, layout="geographic", diagnosis=False, save_path=None,
                      _review_repeaters=None):
        from IPython.display import display
        from .network_view import NetworkView
        if layout not in {"geographic", "logical"}:
            raise ValueError("layout must be geographic or logical")
        result = deepcopy(self.load_result())
        from .attachments import load_attached_equipment
        attached = load_attached_equipment(self.scenario_path)
        if attached is not None:
            result['attached_equipment'] = attached
        if diagnosis:
            from .installed_diagnosis import load_diagnosis, path_diagnosis
            report = load_diagnosis(self)
            result["link_diagnosis"] = report
            result["path_diagnoses"] = {n["node_id"]: path_diagnosis(self, n["node_id"], report)
                                        for n in result["planned_nodes"]}
        if _review_repeaters is not None:
            from .reconfiguration import review_data
            result['reconfiguration_review'] = review_data(self, repeater_devices=_review_repeaters)
        if layout == "logical":
            rows = {n["node_id"]: n for n in result["planned_nodes"]}
            children = {iid: [] for iid in rows}
            for iid, node in rows.items():
                if node["parent"] is not None:
                    children[node["parent"]].append(iid)
            positions = {}
            cursor = 0
            def place(iid, depth):
                nonlocal cursor
                xs = [place(child, depth + 1) for child in sorted(children[iid])]
                x = sum(xs) / len(xs) if xs else cursor
                if not xs:
                    cursor += 1
                positions[iid] = (x, -depth)
                return x
            for iid in sorted(i for i, n in rows.items() if n["parent"] is None):
                place(iid, 0)
                cursor += 1
            for node in result["rpl_nodes"]:
                node["x"], node["y"] = positions[node["node_id"]]
        view = NetworkView(result, installed_layout=layout)
        suffix = "_review" if _review_repeaters is not None else "_diagnosis" if diagnosis else ""
        view.save_html(save_path or self.path / f"topology_{layout}{suffix}.html")
        display(view)
        return view
