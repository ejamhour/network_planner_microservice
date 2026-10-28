"""Review installed-network changes without changing the source project."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from .installed_diagnosis import configuration_hash


def jupyter_destinations(project):
    """Locate project paths relative to running Jupyter roots, never export tokens."""
    try:
        from jupyter_server.serverapp import list_running_servers
        servers = list(list_running_servers())
    except (ImportError, OSError):
        return []
    destinations = []
    for server in servers:
        root = server.get('root_dir') or server.get('notebook_dir')
        if root and project.path.is_relative_to(Path(root).resolve()):
            relative = (project.path / 'reconfiguration.json').relative_to(Path(root).resolve())
            destinations.append({'base_url': server.get('base_url', '/'),
                                 'path': relative.as_posix(),
                                 'display_path': str(project.path / 'reconfiguration.json')})
    return destinations


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def review_context(project):
    """Bind a review to the installed forest, configuration and equipment inventory."""
    edges = project._installed_links()
    result = project.load_result()
    nodes = {n['node_id']: n for n in result['planned_nodes']}
    by_id = {e.get('link_id'): e for e in edges}
    if None in by_id or len(by_id) != len(edges) or any(not key for key in by_id):
        raise ValueError('Reconfiguration requires unique, non-empty link_id values')
    equipment = project.attached_equipment()
    source = {'project': project.name, 'scenario_sha256': configuration_hash(project),
              'topology_sha256': hashlib.sha256(project.result_path.read_bytes()).hexdigest(),
              'equipment_sha256': _digest(equipment)}
    return source, nodes, by_id, equipment


def _impact(nodes, edges, equipment, requests, repeater_devices):
    children = defaultdict(list)
    for iid, node in nodes.items():
        if node['parent'] is not None:
            children[node['parent']].append(iid)
    removed, subtree = set(), set()
    for request in requests:
        edge = edges[request['link_id']]
        a, b = edge['src'], edge['dst']
        child = a if nodes[a]['parent'] == b else b
        removed.add(request['link_id'])
        if request['mode'] == 'subtree':
            pending = [child]
            while pending:
                iid = pending.pop()
                if iid not in subtree:
                    subtree.add(iid)
                    pending.extend(children[iid])
    removed.update(key for key, edge in edges.items()
                   if edge['src'] in subtree and edge['dst'] in subtree)
    retained = set(edges) - removed
    adjacent = defaultdict(set)
    for key in retained:
        edge = edges[key]
        adjacent[edge['src']].add(edge['dst'])
        adjacent[edge['dst']].add(edge['src'])
    connected, pending = set(), [iid for iid, n in nodes.items() if n['connected']]
    while pending:
        iid = pending.pop()
        if iid not in connected:
            connected.add(iid)
            pending.extend(adjacent[iid] - connected)
    devices = defaultdict(set)
    for iid, node in nodes.items():
        devices[node['device_id']].add(iid)
    protected = {e['device_id'] for e in equipment}
    # Only entire devices in a removed subtree can disappear. A multi-interface
    # device participating in another preserved connection must stay.
    eligible = {device for device, interfaces in devices.items()
                if interfaces <= subtree and not interfaces & connected and device not in protected
                and not any(edges[key]['src'] in interfaces or edges[key]['dst'] in interfaces
                            for key in retained)}
    removable = set(repeater_devices) & eligible
    deleted = {iid for device in removable for iid in devices[device]}
    return {'removed_links': sorted(removed), 'preserved_links': sorted(retained),
            'subtree_interfaces': sorted(subtree),
            'removed_devices': sorted(removable), 'removed_interfaces': sorted(deleted),
            'connected_interfaces': sorted(connected),
            'floating_interfaces': sorted(set(nodes) - connected - deleted),
            'eligible_repeater_devices': sorted(eligible),
            'protected_devices': sorted(protected & {nodes[i]['device_id'] for i in subtree})}


def preview_reconfiguration(project, requests, *, repeater_devices=()):
    """Compute an unconfirmed proposal. Missing attachment rows never imply removal.

    requests is a list of {link_id, mode}, where mode is link or subtree.
    repeater_devices explicitly identifies disposable radio-only devices.
    """
    source, nodes, edges, equipment = review_context(project)
    if not isinstance(requests, list):
        raise ValueError('requests must be a list')
    seen, clean = set(), []
    for row in requests:
        if not isinstance(row, dict) or set(row) != {'link_id', 'mode'}:
            raise ValueError('Each request requires only link_id and mode')
        identifier, mode = row['link_id'], row['mode']
        if not isinstance(identifier, str) or identifier not in edges or identifier in seen:
            raise ValueError('Unknown or duplicate link_id')
        if mode not in ('link', 'subtree'):
            raise ValueError('mode must be link or subtree')
        seen.add(identifier)
        clean.append(dict(row))
    if isinstance(repeater_devices, (str, bytes)):
        raise ValueError('repeater_devices must be a sequence of device IDs')
    repeaters = list(repeater_devices)
    devices = {n['device_id'] for n in nodes.values()}
    if any(not isinstance(d, str) or d not in devices for d in repeaters):
        raise ValueError('Unknown repeater device')
    if len(set(repeaters)) != len(repeaters):
        raise ValueError('Duplicate repeater device')
    impact = _impact(nodes, edges, equipment, clean, repeaters)
    protected = {e['device_id'] for e in equipment}
    if set(repeaters) & protected:
        raise ValueError('A device with attached equipment cannot be removed as a repeater')
    return {'format': 'cisei-network-reconfiguration', 'version': 1, 'confirmed': False,
            'source': source, 'requests': sorted(clean, key=lambda r: r['link_id']),
            'repeater_devices': sorted(repeaters), 'impact': impact}


def load_reconfiguration(project, path):
    """Validate removals against the source forest; return only the saved decisions."""
    document = json.loads(Path(path).expanduser().read_text(encoding='utf-8'))
    if (not isinstance(document, dict) or document.get('format') != 'cisei-network-reconfiguration'
            or type(document.get('version')) is not int or document['version'] not in (1, 2)
            or document.get('confirmed') is not True or not document.get('requests')):
        raise ValueError('Expected a confirmed cisei-network-reconfiguration version 1 or 2')
    expected = preview_reconfiguration(project, document['requests'],
                                       repeater_devices=document.get('repeater_devices', ()) if document['version'] == 1
                                       else document.get('remove_devices', ()))
    if document.get('source') != expected['source']:
        raise ValueError('Source project changed; review the reconfiguration again')
    if document['version'] == 1:
        if document.get('impact') != expected['impact']:
            raise ValueError('Reconfiguration impact does not match the installed topology')
    elif (document.get('remove_links') != expected['impact']['removed_links']
          or document.get('remove_devices') != expected['impact']['removed_devices']):
        raise ValueError('Reconfiguration removals do not match the installed topology')
    return compact_reconfiguration(expected)


def compact_reconfiguration(preview):
    """Persist only reviewed requests, actual removals and source identification."""
    return {'format': 'cisei-network-reconfiguration', 'version': 2, 'confirmed': True,
            'source': preview['source'], 'requests': preview['requests'],
            'remove_links': preview['impact']['removed_links'],
            'remove_devices': preview['impact']['removed_devices']}


def review_data(project, *, repeater_devices=()):
    """Precompute per-link choices; the browser only unions reviewed effects."""
    base = preview_reconfiguration(project, [], repeater_devices=repeater_devices)
    _, nodes, edges, equipment = review_context(project)
    protected = {e['device_id'] for e in equipment}
    devices = defaultdict(list)
    for iid, node in nodes.items():
        devices[node['device_id']].append(iid)
    choices = {}
    for identifier in edges:
        choices[identifier] = {mode: _impact(nodes, edges, equipment,
                                [{'link_id': identifier, 'mode': mode}], ())
                               for mode in ('link', 'subtree')}
    return {'base': base, 'choices': choices, 'devices': dict(devices),
            'protected_devices': sorted(protected),
            'jupyter_destinations': jupyter_destinations(project),
            'roots': [iid for iid, n in nodes.items() if n['connected']]}
