import copy
import json

import pytest

from cisei_planning_sdk.mesh_iterations_view import MeshIterationsView, MeshSolutionView
from cisei_planning_sdk.session import ScenarioSession


def construction():
    nodes = [dict(node_id='root', site_id='access', x=0, y=0, connected=True),
             dict(node_id='leaf', site_id='equipment', x=20, y=10, connected=False)]
    first = dict(rpl_nodes=copy.deepcopy(nodes), planned_nodes=[dict(nodes[0], rank=0)], planned_edges=[])
    nodes.append(dict(node_id='relay', site_id='pole', x=10, y=5, connected=False))
    last = dict(rpl_nodes=nodes, planned_nodes=[dict(nodes[0], rank=0),
                dict(nodes[1], parent='relay', rank=2), dict(nodes[2], parent='root', rank=1)],
                planned_edges=[dict(src='leaf', dst='relay', metric=1), dict(src='relay', dst='root', metric=1)])
    return {'mesh_network': dict(fixed_interfaces=['root'], selected_repeaters=['pole'],
                                iterations=[{'result': first}, {'result': last}])}


def test_browse_snapshots_without_changing_or_recomputing_inputs(tmp_path):
    result = construction()
    original = copy.deepcopy(result)
    session = ScenarioSession(None, 'mesh', 'mesh', 'id', result=result)
    view = session.show_mesh_iterations(save_path=tmp_path/'iterations.html')
    assert view.iteration == 1
    assert len(view.snapshots[0]['planned_edges']) == 0
    assert len(view.snapshots[1]['planned_edges']) == 2
    assert view.roots == ['root'] and view.repeaters == ['pole']
    assert (tmp_path/'iterations.html').read_text(encoding='utf-8') == view.to_html()
    assert 'sandbox="allow-scripts"' in view._repr_html_()
    assert session.show_mesh_iterations(iteration=0).iteration == 0
    assert result == original


@pytest.mark.parametrize('iteration', [-1, 2, True, 1.5])
def test_invalid_iteration(iteration):
    with pytest.raises(ValueError, match='iteration must'):
        MeshIterationsView(construction(), iteration=iteration)


def test_no_snapshots_and_escape_script_strings():
    with pytest.raises(ValueError, match='No saved'):
        MeshIterationsView({})
    result = construction()
    result['mesh_network']['iterations'][0]['result']['rpl_nodes'][0]['site_id'] = '</script><script>alert(1)</script>'
    assert '</script><script>' not in MeshIterationsView(result).to_html()


def test_real_snapshot_payload_excludes_large_study_evidence():
    result = construction()
    result['mesh_network']['steps'] = [{'attempts': ['not part of visualization']}]
    view = MeshIterationsView(result)
    assert 'not part of visualization' not in view.to_html()
    assert json.loads(json.dumps(view.snapshots)) == view.snapshots


def test_final_view_uses_solved_network_instead_of_construction_snapshots(tmp_path):
    result = construction()
    final = copy.deepcopy(result['mesh_network']['iterations'][-1]['result'])
    # Final selection can differ from the construction path.
    final['planned_nodes'][1].update(parent='root', rank=0.5)
    final['planned_edges'] = [dict(src='leaf', dst='root', metric=0.5),
                              dict(src='relay', dst='root', metric=1)]
    result.update(final)
    result['mesh_network']['phase'] = 'solved'
    original = copy.deepcopy(result)
    session = ScenarioSession(None, 'mesh', 'mesh', 'id', result=result)
    view = session.show_mesh_network(save_path=tmp_path/'final.html')
    assert isinstance(view, MeshSolutionView)
    assert view.final and len(view.snapshots) == 1
    assert view.snapshots[0]['planned_nodes'][1]['parent'] == 'root'
    assert session.show_mesh_iterations().snapshots[-1]['planned_nodes'][1]['parent'] == 'relay'
    assert (tmp_path/'final.html').exists()
    assert result == original


@pytest.mark.parametrize('phase', ['repeaters_added', 'candidates_built', 'metrics_computed'])
def test_final_view_rejects_unsolved_phases(phase):
    result = construction()
    result.update(result['mesh_network']['iterations'][-1]['result'])
    result['mesh_network']['phase'] = phase
    with pytest.raises(ValueError, match='Solve and export'):
        MeshSolutionView(result)


def test_study_browser_selects_nodes_and_preserves_saved_costs(tmp_path):
    from cisei_planning_sdk.mesh_studies_view import MeshStudiesView
    result = construction()
    study = copy.deepcopy(result['mesh_network']['iterations'][-1]['result'])
    study['mesh_search'] = dict(dst_interface='root', repeaters=['pole'],
                               node_path=['equipment', 'pole', 'access'],
                               path_cost=7.12345, segment_threshold=3.25)
    result['mesh_network']['steps'] = [
        dict(step=8, source='leaf', attempts=[
            dict(destination='root', result=study, accepted=False, total_rank=9.12345)])]
    original = copy.deepcopy(result)
    session = ScenarioSession(None, 'mesh', 'mesh', 'id', result=result)
    view = session.show_mesh_study(node='leaf', save_path=tmp_path/'studies.html')
    assert isinstance(view, MeshStudiesView)
    payload = view.steps[0]['attempts'][0]['view']
    assert payload['study']['cost'] == 7.12345
    assert payload['study']['accepted'] is False
    assert payload['roots'] == ['root']
    assert (tmp_path/'studies.html').exists()
    assert 'id="source"' in view.to_html()
    with pytest.raises(ValueError, match='Unknown or ambiguous'):
        session.show_mesh_study(node='missing')
    assert result == original
