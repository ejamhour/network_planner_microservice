from copy import deepcopy
from unittest.mock import Mock, patch
import re
import shutil
import subprocess
import pytest
from cisei_planning_sdk.session import ScenarioSession
from cisei_planning_sdk.visualization import show_evaluation
from cisei_planning_sdk.evaluation_view import EvaluationView
from cisei_planning_sdk.project import PlanningProject


def test_displays_one_html_view_without_mutation(tmp_path):
    report = {'quality_summary': {'rank_threshold': 4}, 'quality': [{'site_id': 'a', 'rank_threshold': 4}], 'service': [], 'result_edges': []}
    original = deepcopy(report)
    with patch('IPython.display.display') as display:
        view = show_evaluation(report, save_path=tmp_path / 'evaluation.html')
    assert isinstance(view, EvaluationView)
    display.assert_called_once_with(view)
    assert (tmp_path / 'evaluation.html').read_text(encoding='utf-8') == view.to_html()
    assert 'sandbox="allow-scripts"' in view._repr_html_()
    assert 'srcdoc=' in view._repr_html_()
    assert report == original


def test_session_evaluates_then_displays_without_saving_json():
    client = Mock()
    report = {'quality_summary': {'good': 1}}
    client._request.return_value = {'data': report}
    session = ScenarioSession(client=client, name='test', planner='cell', scenario_id='test')
    with patch('cisei_planning_sdk.visualization.show_evaluation') as render:
        assert session.show_evaluation(rank_threshold=2) is None
    assert session.evaluation is report
    render.assert_called_once_with(report)
    client._request.assert_called_once_with('get', '/planning/scenarios/test/evaluation', params={'rank_threshold': 2, 'primary_tech': 'lte', 'solution_kind': 'cell'})


def test_session_saves_html_in_active_project(tmp_path):
    client = Mock()
    client._request.return_value = {'data': {'quality': []}}
    project = PlanningProject(tmp_path)
    session = ScenarioSession(client=client, name='test', planner='graph', scenario_id='test', project=project)
    with patch('IPython.display.display'):
        session.show_evaluation(rank_threshold=4)
    assert (project.path / 'evaluation.html').exists()
    assert not project.result_path.exists()


def test_untrusted_strings_cannot_terminate_script():
    page = EvaluationView({'quality': [{'site_id': '</script><script>alert(1)</script>'}]}).to_html()
    assert '</script><script>alert(1)</script>' not in page
    assert 'src="http' not in page and 'fetch(' not in page


def test_browser_filters_all_technologies_paginates_and_selects(tmp_path):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js required for browser interaction test')
    rows = [{'node_id': f'n{i}', 'site_id': f's{i}', 'device_id': f'd{i}', 'tech': 'wisun' if i % 2 == 0 else 'lte', 'quality': 'poor' if i % 3 == 0 else 'good', 'rank': i / 10, 'rank_threshold': 4, 'connected': False, 'parent': 'root', 'parent_site_id': 'gateway'} for i in range(60)]
    rows.append({'node_id': 'root', 'site_id': 'gateway', 'tech': 'wisun', 'connected': True, 'quality': 'connected', 'rank': 1})
    report = {'quality_summary': {'rank_threshold': 4}, 'quality': rows, 'service': [], 'result_edges': [{'src': 'root', 'dst': 'n0', 'kind': 'rf', 'metric': 1}, {'src': 'root', 'dst': 'n1', 'kind': 'internal', 'metric': 0}]}
    script = re.search(r'<script>(.*?)</script>', EvaluationView(report).to_html(), re.S).group(1)
    harness = r'''
const elements=new Map();
class Element{constructor(){this.children=[];this.value='';this.checked=false}setAttribute(k,v){this[k]=v}append(...items){this.children.push(...items)}replaceChildren(){this.children=[]}}
global.document={querySelector(s){if(!elements.has(s))elements.set(s,new Element());return elements.get(s)},createElement(){return new Element()}};
'''
    checks = r'''
if(elements.get('#records').children.length!==20)throw Error('Pagination missing');
if(!elements.get('#count').textContent.includes('60 matching'))throw Error('All-tech targets missing');
elements.get('#next').onclick();if(elements.get('#page').textContent!=='Page 2 of 3')throw Error('Next page failed');
elements.get('#tech').value='wisun';elements.get('#tech').onchange();
if(!elements.get('#count').textContent.includes('30 matching'))throw Error('Tech filter');
elements.get('#quality').value='poor';elements.get('#quality').onchange();
if(!elements.get('#count').textContent.includes('10 matching'))throw Error('Quality filter');
elements.get('#records').children[0].onclick();
if(!elements.get('#details').children.length)throw Error('Record selection');
elements.get('#clear').onclick();elements.get('#anchors').onclick();
if(!elements.get('#count').textContent.includes('1 matching'))throw Error('Anchor view');
elements.get('#links').onclick();if(!elements.get('#count').textContent.includes('1 matching'))throw Error('Internal link filter');
elements.get('#internal').checked=true;elements.get('#internal').onchange();
if(!elements.get('#count').textContent.includes('2 matching'))throw Error('Include internal links');
elements.get('#search').value='impossible';elements.get('#search').oninput();
if(!elements.get('#count').textContent.includes('0 matching'))throw Error('Search filter');
'''
    path = tmp_path / 'browser.cjs'
    path.write_text(harness + script + checks, encoding='utf-8')
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True, timeout=20)
