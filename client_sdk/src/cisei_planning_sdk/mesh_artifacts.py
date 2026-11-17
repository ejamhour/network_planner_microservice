"""Persist service-produced mesh snapshots; no topology decisions here."""
import csv

from cisei_planning_sdk.local_io import load_result, save_result, save_scenario


def _snapshot(path, snapshot):
    path.mkdir(parents=True, exist_ok=True)
    # Embedded sites preserve instance radio adjustments, disabled interfaces,
    # and complex sites exactly. CSV is a companion inventory, not a second
    # definition of the same sites.
    save_scenario(snapshot['scenario'], path / 'scenario.toml')
    save_result(snapshot['result'], path / 'result.json')
    rows = snapshot.get('nodes')
    if rows:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (path / 'nodes.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def load_mesh_artifact(project, reference):
    return load_result(project.path / reference['artifact'])


def compact_mesh_result(project, result):
    """Store detailed studies and older snapshots outside the resume checkpoint."""
    report = dict(result['mesh_network'])
    iterations = []
    for index, snapshot in enumerate(report['iterations']):
        folder = project.path / 'mesh_iterations' / f'{index:04d}'
        if 'artifact' not in snapshot:
            _snapshot(folder, snapshot)
        iterations.append(snapshot if index == len(report['iterations']) - 1 else {
            'artifact': (folder / 'result.json').relative_to(project.path).as_posix()})

    steps = []
    base = 0
    for step in report['steps']:
        base = step.get('base_iteration', base)
        attempts = []
        for index, attempt in enumerate(step['attempts'], 1):
            if 'artifact' in attempt:
                attempts.append(attempt)
                continue
            folder = project.path / 'mesh_iterations' / f'{base:04d}' / 'studies'
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"step_{step['step']:04d}_attempt_{index:02d}.json"
            detail = attempt['result']
            save_result(detail, path)
            # The offline browser only needs the selected path, not its search graph.
            preview = {key: detail[key] for key in
                       ('rpl_nodes', 'planned_nodes', 'planned_edges') if key in detail}
            preview['mesh_search'] = {key: detail['mesh_search'][key] for key in
                                      ('dst_interface', 'repeaters', 'node_path', 'path_cost',
                                       'segment_threshold')}
            attempts.append({**attempt, 'result': preview,
                             'artifact': path.relative_to(project.path).as_posix()})
        applied = step.get('applied_iteration',
                           base + 1 if step.get('selected_destination') else None)
        steps.append({**step, 'base_iteration': base, 'applied_iteration': applied,
                      'attempts': attempts})
        if applied is not None:
            base = applied
    report.update(steps=steps, iterations=iterations)
    return {**result, 'mesh_network': report}


def save_mesh_artifacts(project, result):
    result = compact_mesh_result(project, result)
    report = result['mesh_network']
    if report.get('expanded_scenario'):
        _snapshot(project.path / 'mesh_expanded', {'scenario': report['expanded_scenario'],
                  'nodes': report.get('expanded_nodes'), 'result': {k: v for k, v in result.items() if k != 'mesh_network'}})
    if report.get('solution_scenario'):
        _snapshot(project.path / 'mesh_solution', {'scenario': report['solution_scenario'],
                  'nodes': report['solution_nodes'], 'result': {k: v for k, v in result.items() if k != 'mesh_network'}})
    project.save_result(result)
    return result
