import json
import shutil
from pathlib import Path

import pytest

from cisei_planning_sdk import InstalledNetworkProject
from cisei_planning_sdk.reconfiguration import _impact, review_data


def test_jupyter_destination_contains_no_authentication_secrets(project, monkeypatch):
    from jupyter_server import serverapp
    from cisei_planning_sdk.reconfiguration import jupyter_destinations
    monkeypatch.setattr(serverapp, 'list_running_servers', lambda: iter([
        {'root_dir': str(project.path.parent), 'base_url': '/user/example/', 'token': 'must-not-export'},
        {'root_dir': str(project.path / 'outside'), 'base_url': '/wrong/'}]))
    destinations = jupyter_destinations(project)
    assert destinations == [{'base_url': '/user/example/', 'path': 'bro/reconfiguration.json',
                             'display_path': str(project.path / 'reconfiguration.json')}]
    assert 'must-not-export' not in json.dumps(review_data(project))


@pytest.fixture
def project(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'examples/projects/e09_diagnostico_bro'
    shutil.copytree(source, tmp_path / 'bro')
    project = InstalledNetworkProject(tmp_path / 'bro')
    project.import_topology()
    return project


def target(project, child='BRO-S-GE-026:d0:i0'):
    nodes = {n['node_id']: n for n in project.load_result()['planned_nodes']}
    parent = nodes[child]['parent']
    return next(e['link_id'] for e in project._installed_links()
                if {e['src'], e['dst']} == {child, parent})


def test_link_cut_retains_descendant_edges_but_disconnects_them(project):
    before = {p: p.read_bytes() for p in project.path.iterdir() if p.is_file()}
    review = project.preview_reconfiguration([{'link_id': target(project), 'mode': 'link'}])
    impact = review['impact']
    assert impact['removed_links'] == [target(project)]
    assert 'BRO-S-GE-006:d0:i0' in impact['floating_interfaces']
    assert 'BRO-S-GE-006:d0:i0' not in impact['connected_interfaces']
    assert not impact['removed_devices']
    assert len(impact['preserved_links']) == 23
    assert not review['confirmed']
    assert before == {p: p.read_bytes() for p in before}


def test_subtree_requires_explicit_repeater_identification(project):
    requests = [{'link_id': target(project), 'mode': 'subtree'}]
    preview = project.preview_reconfiguration(requests)
    assert len(preview['impact']['removed_links']) > 1
    assert preview['impact']['removed_devices'] == []
    preview = project.preview_reconfiguration(requests, repeater_devices=['BRO-S-GE-026:d0'])
    assert preview['impact']['removed_devices'] == ['BRO-S-GE-026:d0']
    assert 'BRO-S-GE-026:d0:i0' not in preview['impact']['floating_interfaces']
    assert 'BRO-S-GE-006:d0:i0' in preview['impact']['floating_interfaces']


def test_attachments_are_protected_and_missing_rows_not_assumed_repeaters(project):
    (project.path / 'attached.csv').write_text(
        'equipment_id,equipment_type,device_id\n001,DA,BRO-S-GE-026:d0\n', encoding='utf-8')
    with project.scenario_path.open('a', encoding='utf-8') as f:
        f.write('\n[attachments]\nsource="attached.csv"\n')
    request = [{'link_id': target(project), 'mode': 'subtree'}]
    impact = project.preview_reconfiguration(request)['impact']
    assert 'BRO-S-GE-026:d0' in impact['protected_devices']
    assert 'BRO-S-GE-026:d0' not in impact['eligible_repeater_devices']
    assert 'BRO-S-GE-026:d0:i0' in impact['floating_interfaces']
    with pytest.raises(ValueError, match='attached equipment'):
        project.preview_reconfiguration(request, repeater_devices=['BRO-S-GE-026:d0'])


def test_overlapping_subtrees_consolidated_and_selection_order_independent(project):
    outer = {'link_id': target(project), 'mode': 'subtree'}
    inner = {'link_id': target(project, 'BRO-S-GE-006:d0:i0'), 'mode': 'link'}
    a = project.preview_reconfiguration([outer, inner])
    assert a == project.preview_reconfiguration([inner, outer])
    assert a['impact'] == project.preview_reconfiguration([outer])['impact']


@pytest.mark.parametrize('requests', [None, [{}], [{'link_id': 'missing', 'mode': 'link'}],
                                      [{'link_id': 'missing', 'mode': []}]])
def test_invalid_requests_rejected(project, requests):
    with pytest.raises(ValueError):
        project.preview_reconfiguration(requests)


def test_invalid_mode_and_duplicate_selection_rejected(project):
    request = {'link_id': target(project), 'mode': 'link'}
    with pytest.raises(ValueError, match='mode must'):
        project.preview_reconfiguration([{**request, 'mode': 'whole network'}])
    with pytest.raises(ValueError, match='duplicate'):
        project.preview_reconfiguration([request, request])


def confirmed_file(project):
    document = project.preview_reconfiguration([{'link_id': target(project), 'mode': 'subtree'}],
                                              repeater_devices=['BRO-S-GE-026:d0'])
    document['confirmed'] = True
    path = project.path / 'reconfiguration.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path, document


def test_confirmed_review_roundtrip(project):
    path, document = confirmed_file(project)
    compact = project.load_reconfiguration(path)
    assert compact['version'] == 2
    assert compact['remove_links'] == document['impact']['removed_links']
    assert compact['remove_devices'] == document['impact']['removed_devices']
    assert 'impact' not in compact and 'repeater_devices' not in compact
    assert 'preserved_links' not in json.dumps(compact)
    path.write_text(json.dumps(compact), encoding='utf-8')
    assert project.load_reconfiguration(path) == compact
    assert len(review_data(project)['choices']) == 24


@pytest.mark.parametrize('field', ['remove_links', 'remove_devices'])
def test_compact_removal_lists_are_checked_against_requests(project, field):
    path, _ = confirmed_file(project)
    compact = project.load_reconfiguration(path)
    compact[field] = [] if field == 'remove_links' else ['BRO-S-P7-001:d0']
    path.write_text(json.dumps(compact), encoding='utf-8')
    with pytest.raises(ValueError, match='removals do not match'):
        project.load_reconfiguration(path)


@pytest.mark.parametrize('change', ['confirmation', 'impact', 'source', 'inventory'])
def test_reject_unconfirmed_stale_or_tampered_document(project, change):
    path, document = confirmed_file(project)
    if change == 'confirmation':
        document['confirmed'] = False
    elif change == 'impact':
        document['impact']['removed_devices'] = []
    elif change == 'source':
        document['source']['scenario_sha256'] = 'changed'
    else:
        (project.path / 'attached.csv').write_text('equipment_id,equipment_type,device_id\n', encoding='utf-8')
        with project.scenario_path.open('a', encoding='utf-8') as f:
            f.write('\n[attachments]\nsource="attached.csv"\n')
        # An explicitly empty inventory describes the same associations.
        (project.path / 'attached.csv').write_text('equipment_id,equipment_type,device_id\n001,DA,BRO-S-GE-003:d0\n', encoding='utf-8')
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError):
        project.load_reconfiguration(path)


def test_multi_interface_radio_with_preserved_interface_cannot_disappear():
    nodes = {'root': {'device_id': 'r', 'parent': None, 'connected': True},
             'a': {'device_id': 'd', 'parent': 'root', 'connected': False},
             'b': {'device_id': 'd', 'parent': 'root', 'connected': False}}
    edges = {'ra': {'src': 'a', 'dst': 'root'}, 'rb': {'src': 'b', 'dst': 'root'}}
    impact = _impact(nodes, edges, [], [{'link_id': 'ra', 'mode': 'subtree'}], ['d'])
    assert impact['removed_devices'] == []
    assert impact['connected_interfaces'] == ['b', 'root']
    impact = _impact(nodes, edges, [], [{'link_id': 'ra', 'mode': 'subtree'},
                                       {'link_id': 'rb', 'mode': 'subtree'}], ['d'])
    assert impact['removed_devices'] == ['d']
