"""Persist service-produced mesh snapshots; no topology decisions here."""
import csv

from cisei_planning_sdk.local_io import save_result, save_scenario


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


def save_mesh_artifacts(project, result):
    report = result['mesh_network']
    for index, snapshot in enumerate(report['iterations']):
        _snapshot(project.path / 'mesh_iterations' / f'{index:04d}', snapshot)
    if report.get('expanded_scenario'):
        _snapshot(project.path / 'mesh_expanded', {'scenario': report['expanded_scenario'],
                  'nodes': report.get('expanded_nodes'), 'result': {k: v for k, v in result.items() if k != 'mesh_network'}})
    if report.get('solution_scenario'):
        _snapshot(project.path / 'mesh_solution', {'scenario': report['solution_scenario'],
                  'nodes': report['solution_nodes'], 'result': {k: v for k, v in result.items() if k != 'mesh_network'}})
    project.save_result(result)
