from copy import deepcopy
from unittest.mock import Mock, patch
import re
import shutil
import subprocess
import pytest
from cisei_planning_sdk.metrics_view import MetricsView
from cisei_planning_sdk.visualization import show_metrics
from cisei_planning_sdk import PlanningProject, ScenarioSession


def test_metrics_html_and_project_export_do_not_compute(tmp_path):
    report = {'metrics': [{'src': 'a', 'dst': 'b', 'src_site': 'one', 'dst_site': 'two', 'src_tech': 'wisun', 'dst_tech': 'wisun', 'metric': 2.5}]}
    original = deepcopy(report)
    client = Mock()
    client._request.return_value = {'data': report}
    project = PlanningProject(tmp_path)
    scenario = ScenarioSession(client, 'test', 'graph', 'test', project=project)
    with patch('IPython.display.display') as display:
        scenario.show_metrics()
    display.assert_called_once_with(scenario.metrics_view)
    client._request.assert_called_once_with('get', '/planning/scenarios/test/metrics', params={'include_features': False})
    assert (project.path / 'metrics.html').read_text(encoding='utf-8') == scenario.metrics_view.to_html()
    assert scenario.metrics is report and report == original
    assert 'title="Link metrics report"' in scenario.metrics_view._repr_html_()
    assert not project.result_path.exists()
    view = show_metrics(report, display=False)
    assert isinstance(view, MetricsView)
    assert 'fetch(' not in view.to_html()


def test_browser_metrics_filter_sort_pagination_and_selection(tmp_path):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js required')
    rows = [{'src': f'a{i}', 'dst': f'b{i}', 'src_site': f's{i}', 'dst_site': f'd{i}', 'src_tech': 'wisun' if i % 2 == 0 else 'lte', 'dst_tech': 'wisun' if i % 2 == 0 else 'lte', 'kind': 'rf', 'metric': i} for i in range(45)]
    rows.append({'src': 'a', 'dst': 'b', 'kind': 'internal', 'metric': 0})
    script = re.search(r'<script>(.*?)</script>', MetricsView({'metrics': rows}).to_html(), re.S).group(1)
    harness = r'''
const elements=new Map();
class Element{constructor(){this.children=[];this.value='';this.checked=false}setAttribute(k,v){this[k]=v}append(...items){this.children.push(...items)}replaceChildren(){this.children=[]}}
global.document={querySelector(s){if(!elements.has(s))elements.set(s,new Element());return elements.get(s)},createElement(){return new Element()}};
'''
    checks = r'''
if(document.title!=='Link metrics')throw Error('Wrong report title');
if(!elements.get('#targets').hidden||!elements.get('#anchors').hidden)throw Error('Evaluation tabs leaked');
if(!elements.get('#count').textContent.includes('45 matching / 46 total'))throw Error('Radio metrics missing');
if(elements.get('#records').children.length!==20)throw Error('Pagination missing');
elements.get('#next').onclick();if(elements.get('#page').textContent!=='Page 2 of 3')throw Error('Next page');
elements.get('#tech').value='wisun';elements.get('#tech').onchange();
if(!elements.get('#count').textContent.includes('23 matching'))throw Error('Technology filter');
elements.get('#sort').value='rank-desc';elements.get('#sort').onchange();
elements.get('#records').children[0].onclick();
const details=elements.get('#details').children[0].children.map(e=>e.textContent);
if(!details.includes('44'))throw Error('Cost sort or selection failed');
elements.get('#clear').onclick();elements.get('#internal').checked=true;elements.get('#internal').onchange();
if(!elements.get('#count').textContent.includes('46 matching'))throw Error('Internal links');
elements.get('#search').value='nonexistent';elements.get('#search').oninput();
if(!elements.get('#count').textContent.includes('0 matching'))throw Error('Search filter');
'''
    path = tmp_path / 'metrics.cjs'
    path.write_text(harness + script + checks, encoding='utf-8')
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True, timeout=20)


def test_metrics_validation_and_escaping():
    with pytest.raises(ValueError, match='metrics list'):
        MetricsView({})
    view = MetricsView({'metrics': [{'src': '</script><script>alert(1)</script>', 'dst': 'b'}]})
    assert '</script><script>alert(1)</script>' not in view.to_html()
