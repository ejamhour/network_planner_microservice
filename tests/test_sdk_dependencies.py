"""Saved topology analysis lives in the SDK, not in notebook implementations."""
import copy
import hashlib
import json

import pytest

from cisei_planning_sdk import PlanningProject
from cisei_planning_sdk.dependencies import network_dependencies
from cisei_planning_sdk.network_view import NetworkView


@pytest.fixture
def result():
    rows = [
        {"node_id": "root:i0", "site_id": "root", "device_id": "root:d0", "x": 0, "y": 0, "connected": True},
        {"node_id": "a:i0", "site_id": "a", "device_id": "a:d0", "x": 1, "y": 1, "parent": "root:i0", "rank": 1},
        {"node_id": "b:i0", "site_id": "b", "device_id": "b:d0", "x": 2, "y": 2, "parent": "a:i0", "rank": 2},
        {"node_id": "c:i0", "site_id": "c", "device_id": "c:d0", "x": 3, "y": 3, "parent": "b:i0", "rank": 3},
        {"node_id": "unserved:i0", "site_id": "unserved", "x": 4, "y": 4},
    ]
    return {"rpl_nodes": rows, "planned_nodes": rows[:4],
            "planned_edges": [{"src": r["node_id"], "dst": r["parent"]} for r in rows if "parent" in r],
            "evaluation": {"quality": [{"site_id": "c", "quality": "poor"}]}}


def test_dependencies_distinguish_children_descendants_and_leaves(result):
    original = copy.deepcopy(result)
    report = network_dependencies(result)
    assert report["sites"]["a"]["child_count"] == 1
    assert report["sites"]["a"]["descendant_sites"] == ["b", "c"]
    assert report["sites"]["c"]["descendant_count"] == 0
    assert report["sites"]["unserved"]["descendant_count"] == 0
    assert result == original


def test_site_counts_deduplicate_devices_and_interfaces(result):
    row = {"node_id": "c:i1", "site_id": "c", "device_id": "c:d0", "parent": "b:i0"}
    result["rpl_nodes"].append(row)
    result["planned_nodes"].append(row)
    report = network_dependencies(result)["sites"]["a"]
    assert report["descendant_count"] == 3
    assert report["descendant_site_count"] == report["descendant_device_count"] == 2


@pytest.mark.parametrize("parent", ["c:i0", "missing"])
def test_invalid_parent_assignments_are_rejected(result, parent):
    result["planned_nodes"][0]["parent"] = parent
    with pytest.raises(ValueError, match="cycle|Unknown parent"):
        network_dependencies(result)


def test_manual_selection_is_offline_preserves_result_and_allows_leaf(tmp_path, monkeypatch, result):
    import requests
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("Unexpected network call"))
    project = PlanningProject(tmp_path)
    project.save_result(result)
    original = project.result_path.read_bytes()
    selected = json.loads(project.save_redundancy_selection("a").read_text())
    assert selected["active_sites"] == ["a", "b", "c"]
    assert selected["source_result_sha256"] == hashlib.sha256(original).hexdigest()
    assert json.loads(project.save_redundancy_selection("c").read_text())["active_sites"] == ["c"]
    assert original == project.result_path.read_bytes()
    for site in ("root", "unserved", "missing"):
        with pytest.raises(ValueError):
            project.save_redundancy_selection(site)


def test_dependency_view_embeds_analysis_without_tables(result):
    view = NetworkView(result, dependency_mode=True)
    assert view.dependencies["sites"]["a"]["descendant_site_count"] == 2
    page = view.to_html()
    assert "Descendant subnetwork" in page and "dependent sites" in page
    assert "<table" not in page
    assert "fetch(" not in page
    assert 'sandbox="allow-scripts"' in view._repr_html_()
    assert NetworkView(result).dependencies is None


def test_dependency_view_selects_descendants_and_handles_leaf(tmp_path, result):
    import re
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the interactive rendering check")
    result["interface_inventory"] = [{
        "node_id": "disabled:i0", "site_id": "disabled", "x": 4, "y": 6,
        "enabled": False, "connected": True,
    }]
    script = re.search(r"<script>(.*?)</script>", NetworkView(result, dependency_mode=True).to_html(), re.S).group(1)
    harness = r'''
const elements=new Map();
class Element {constructor(){this.children=[];this.checked=true;this.value=''}setAttribute(k,v){this[k]=v}append(e){this.children.push(e)}replaceChildren(){this.children=[]}}
global.document={querySelector(s){if(!elements.has(s))elements.set(s,new Element());return elements.get(s)},createElementNS(){return new Element()},createElement(){return new Element()}};
'''
    checks = r'''
select(['a']);
if(!elements.get('#dependency-summary').textContent.includes('2 dependent sites'))throw Error('Incorrect descendants');
let lines=elements.get('#scene').children.filter(e=>e.class==='edge');
if(lines.length!==2)throw Error('Missing descendant links');
if(lines.some(e=>e.children.some(t=>t.textContent.includes('root:i0'))))throw Error('Upstream edge leaked into subtree');
select(['c']);
if(elements.get('#scene').children.some(e=>e.class==='edge'))throw Error('Leaf contains links');
if(!elements.get('#dependency-summary').textContent.includes('0 dependent sites'))throw Error('Leaf count');
elements.get('#clear').onclick();
if(!elements.get('#dependency-summary').hidden)throw Error('Summary not cleared');
if(elements.get('#scene').children.filter(e=>e.class==='edge').length!==3)throw Error('Network not restored');
if(!elements.get('#scene').children.some(e=>e.class==='point'&&e.fill==='#9aa7b2'))throw Error('Disabled site not gray');
if(sites.get('disabled').connected)throw Error('Disabled site shown as connected');
'''
    path = tmp_path / "dependencies.cjs"
    path.write_text(harness + script + checks, encoding="utf-8")
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("name", ["e07_01_projeto_original", "e07_02_projeto_redundancia"])
def test_notebook_contains_only_sdk_calls_and_assignments(name):
    import ast
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "examples/notebooks" / (name + ".ipynb")
    nb = json.loads(path.read_text(encoding="utf-8"))
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        tree = ast.parse("".join(cell["source"]))
        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom):
                assert statement.module == "cisei_planning_sdk"
            elif isinstance(statement, ast.Assign):
                assert isinstance(statement.value, ast.Call)
            else:
                assert isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
        assert not any(isinstance(node, (ast.For, ast.If, ast.ListComp, ast.FunctionDef, ast.Assert)) for node in ast.walk(tree))
