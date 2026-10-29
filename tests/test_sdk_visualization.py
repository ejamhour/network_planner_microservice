import copy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from cisei_planning_sdk import load_result, plot_network, save_result


def test_loaded_graphs_preserve_unserved_sites_and_collapse_interfaces(tmp_path, monkeypatch):
    import requests
    def no_network(*args, **kwargs):
        pytest.fail("Plotting a saved result must not contact a service")
    monkeypatch.setattr(requests.Session, "request", no_network)
    nodes = [
        {"node_id": "a1", "site_id": "tower", "x": 0, "y": 0, "connected": True},
        {"node_id": "a2", "site_id": "tower", "x": 0, "y": 0, "connected": True},
        {"node_id": "b", "site_id": "served", "x": 100, "y": 100},
        {"node_id": "c", "site_id": "unserved", "x": 200, "y": 0},
    ]
    result = {"rpl_nodes": nodes, "planned_nodes": nodes[:3],
              "candidate_edges": [{"src": "a1", "dst": "b"}, {"src": "a2", "dst": "b"},
                                  {"src": "a1", "dst": "a2"}, {"src": "a1", "dst": "c"}],
              "planned_edges": [{"src": "a1", "dst": "b"}]}
    result["planned_nodes"] = [dict(row, parent="a1" if row["node_id"] == "b" else None)
                               for row in nodes[:3]]
    loaded = load_result(save_result(result, tmp_path / "result.json"))
    original = copy.deepcopy(loaded)
    for kind, expected_arrows in (("candidate", 0), ("final", 1)):
        _, ax = plt.subplots()
        ax = plot_network(loaded, kind=kind, ax=ax)
        assert sum(len(c.get_offsets()) for c in ax.collections) == 3
        assert sum(t.arrow_patch is not None for t in ax.texts) == expected_arrows
        if kind == "candidate":
            assert len(ax.lines) == 2
        if kind == "final":
            assert "Unserved client / relay" in ax.get_legend_handles_labels()[1]
        ax.figure.canvas.draw()
        plt.close(ax.figure)
    assert loaded == original


def test_html_view_is_offline_isolated_and_keeps_saved_information(tmp_path, monkeypatch):
    import requests
    from cisei_planning_sdk.network_view import NetworkView
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("Unexpected network call"))
    result = {
        "rpl_nodes": [
            {"node_id": "tower:i0", "site_id": "tower", "x": 10, "y": 20, "connected": True, "antenna_id": "sector"},
            {"node_id": "client:i0", "site_id": "client", "x": 30, "y": 40},
        ],
        "antennas": {"sector": {"kind": "sector", "azimuth_deg": 120, "beamwidth_deg": 120}},
        "candidate_edges": [{"src": "tower:i0", "dst": "client:i0"}],
        "planned_edges": [{"src": "client:i0", "dst": "tower:i0", "metric": 1.5}],
        "planned_nodes": [{"node_id": "client:i0", "parent": "tower:i0", "rank": 1.5}],
        "evaluation": {"quality": [{"site_id": "client", "quality": "served"}]},
    }
    original = copy.deepcopy(result)
    for kind in ("candidate", "final"):
        view = plot_network(result, kind=kind, display=False, save_path=tmp_path / f"auto-{kind}.html")
        assert (tmp_path / f"auto-{kind}.html").read_text(encoding="utf-8") == view.to_html()
        assert isinstance(view, NetworkView)
        page = view.to_html()
        assert 'src="http' not in page and 'fetch(' not in page
        assert '"azimuth_deg": 120' in page
        assert '"metric": 1.5' in page
        assert '"quality": "served"' in page
        assert 'sandbox="allow-scripts"' in view._repr_html_()
        assert 'srcdoc=' in view._repr_html_()
        path = view.save_html(tmp_path / f"{kind}.html")
        assert path.read_text(encoding="utf-8") == page
    assert result == original


def test_html_view_escapes_script_termination_and_rejects_bad_coordinates():
    from cisei_planning_sdk.network_view import NetworkView
    result = {"rpl_nodes": [{"node_id": "</script><script>alert(1)</script>", "x": 0, "y": 0}], "candidate_edges": []}
    page = NetworkView(result, kind="candidate").to_html()
    assert "</script><script>alert(1)</script>" not in page
    result["rpl_nodes"][0]["x"] = None
    with pytest.raises(ValueError, match="coordinates"):
        NetworkView(result, kind="candidate")


def test_solution_quality_highlights_only_saved_poor_sites(tmp_path):
    import re
    import shutil
    import subprocess
    from cisei_planning_sdk.network_view import NetworkView
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the browser rendering check")
    result = {
        "rpl_nodes": [
            {"node_id": "t", "site_id": "tower", "x": 0, "y": 0, "connected": True},
            {"node_id": "a", "site_id": "poor-client", "x": 100, "y": 100},
            {"node_id": "b", "site_id": "good-client", "x": 200, "y": 100},
            {"node_id": "c", "site_id": "unserved", "x": 100, "y": 200},
        ],
        "planned_nodes": [{"node_id": "t"}, {"node_id": "a"}, {"node_id": "b"}],
        "planned_edges": [],
        "evaluation": {"quality": [
            {"node_id": "a", "quality": "poor"},
            {"site_id": "good-client", "quality": "good"},
            {"site_id": "unserved", "quality": "unserved"},
        ]},
    }
    script = re.search(r"<script>(.*?)</script>", NetworkView(result).to_html(), re.S).group(1)
    harness = r"""
const elements=new Map();
class Element {constructor(){this.children=[];this.checked=true;this.value=''}setAttribute(k,v){this[k]=v}append(e){this.children.push(e)}replaceChildren(){this.children=[]}}
global.document={querySelector(s){if(!elements.has(s))elements.set(s,new Element());return elements.get(s)},createElementNS(){return new Element()},createElement(){return new Element()}};
"""
    checks = r"""
const markers=elements.get('#scene').children.filter(e=>e.class==='point');
if(markers.filter(e=>e.fill==='#b51c69').length!==1)throw Error('Poor classification');
if(markers.filter(e=>e.fill==='#d65f5f').length!==1)throw Error('Unserved classification');
if(!elements.get('#scene').children.some(e=>e.stroke==='#b51c69'&&e.fill==='none'))throw Error('Missing poor ring');
elements.get('#endpoint').value='poor-client';elements.get('#endpoint').onchange();
if(!elements.get('#details').textContent.includes('poor'))throw Error('Missing quality details');
if(elements.get('#details').textContent.includes('good-client'))throw Error('Unrelated evaluation leaked');
elements.get('#labels').checked=false;elements.get('#labels').onchange();
if(elements.get('#scene').children.some(e=>e.class==='label'))throw Error('Labels remain when disabled');
elements.get('#labels').checked=true;elements.get('#labels').onchange();
if(!elements.get('#scene').children.some(e=>e.class==='label'))throw Error('Labels not enabled');

"""
    path = tmp_path / "quality.cjs"
    path.write_text(harness + script + checks, encoding="utf-8")
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True, timeout=20)


def test_selected_link_displays_solution_ranks_and_edge_cost(tmp_path):
    import re
    import shutil
    import subprocess
    from cisei_planning_sdk.network_view import NetworkView
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for the browser rendering check')
    result = {
        'rpl_nodes': [
            {'node_id': 'a', 'site_id': 'A', 'x': 0, 'y': 0, 'rank': None},
            {'node_id': 'b', 'site_id': 'B', 'x': 100, 'y': 100, 'rank': None},
        ],
        'planned_nodes': [{'node_id': 'a', 'rank': 0}, {'node_id': 'b', 'rank': 3.7, 'parent': 'a'}],
        'planned_edges': [{'src': 'a', 'dst': 'b', 'metric': 3.7}],
        'mesh_search': {},
    }
    script = re.search(r'<script>(.*?)</script>', NetworkView(result).to_html(), re.S).group(1)
    harness = r"""
const elements=new Map();
class Element {constructor(){this.children=[];this.checked=true;this.value=''}setAttribute(k,v){this[k]=v}append(e){this.children.push(e)}replaceChildren(){this.children=[]}}
global.document={querySelector(s){if(!elements.has(s))elements.set(s,new Element());return elements.get(s)},createElementNS(){return new Element()},createElement(){return new Element()}};
"""
    checks = r"""
const edge=elements.get('#scene').children.find(e=>e.class==='edge');edge.onclick();
let text=elements.get('#details').textContent;
for(const s of ['Custo do segmento: 3.7','Rank de A: 0','Rank de B: 3.7','Ranks locais'])if(!text.includes(s))throw Error('Missing '+s);
select(['B']);if(!elements.get('#details').textContent.includes('Rank: 3.7'))throw Error('Node rank missing');
if(edgeMetric({src:'a',dst:'b',metric:0}).metric!==0)throw Error('Zero cost lost');
metrics.set(JSON.stringify(['b','a']),{metric:2});
if(edgeMetric({src:'a',dst:'b'}).metric!==2)throw Error('Reverse metric missing');
planned.delete('b');edge.onclick();if(!elements.get('#details').textContent.includes('Rank de B: sem caminho na solução'))throw Error('Unserved rank invented');
"""
    path = tmp_path / 'selected_link.cjs'
    path.write_text(harness + script + checks, encoding='utf-8')
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True, timeout=20)
