"""Browse recorded proposals by source node and connected parent, offline."""
import html
import json

from .mesh_iterations_view import MeshSolutionView, _DOCUMENT
from .network_view import NetworkView


def proposal_view(record, *, accepted, total_rank):
    search = record['mesh_search']
    view = MeshSolutionView(record)
    view.roots = [search['dst_interface']]
    view.repeaters = search['repeaters']
    view.study = dict(path=search['node_path'], accepted=accepted,
                      cost=search['path_cost'], totalRank=total_rank,
                      threshold=search['segment_threshold'])
    return view


class MeshStudiesView:
    def __init__(self, result, *, node=None):
        self.steps = []
        for step in result.get('mesh_network', {}).get('steps', []):
            attempts = []
            for index, attempt in enumerate(step['attempts'], 1):
                record = attempt['result']
                search = record['mesh_search']
                view = proposal_view(record, accepted=attempt['accepted'], total_rank=attempt['total_rank'])
                attempts.append(dict(index=index, parent=attempt['destination'],
                                     accepted=attempt['accepted'], rank=attempt['total_rank'],
                                     view=view.payload()))
            if attempts:
                label = (' · applied path' if step.get('applied') else ' · path proposal'
                         ) if step.get('kind') == 'improve_path' else ''
                self.steps.append(dict(step=step['step'], source=step['source'], attempts=attempts,
                                       label=label))
        if not self.steps:
            raise ValueError('No saved mesh studies are available')
        sources = sorted({s['source'] for s in self.steps})
        if node is None:
            self.node = sources[0]
        else:
            matches = [s for s in sources if s == node or s.rsplit(':d', 1)[0] == node]
            if len(matches) != 1:
                raise ValueError(f'Unknown or ambiguous mesh study node: {node}')
            self.node = matches[0]

    def to_html(self):
        payload = json.dumps(dict(steps=self.steps, node=self.node, template=_DOCUMENT), allow_nan=False)
        payload = payload.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        return _BROWSER.replace('__DATA__', payload, 1)

    save_html = NetworkView.save_html

    def _repr_html_(self):
        return ('<iframe title="Mesh studies by node" sandbox="allow-scripts" '
                'style="width:100%;height:920px;border:1px solid #d8e2ec" '
                f'srcdoc="{html.escape(self.to_html(), quote=True)}"></iframe>')


_BROWSER = r'''<!doctype html><meta charset="utf-8"><title>Mesh studies</title>
<style>body{margin:0;font:14px system-ui;color:#20374c}header{padding:12px;background:#f5f8fb;display:flex;flex-wrap:wrap;gap:12px}select{font:inherit;max-width:100%;padding:6px}iframe{width:100%;height:800px;border:0}</style>
<header><label>Node <select id="source"></select></label>
<label>Recorded study <select id="step"></select></label>
<label>Candidate parent <select id="parent"></select></label></header>
<iframe id="view" title="Recorded connection proposal" sandbox="allow-scripts"></iframe>
<script>
const data=__DATA__,source=document.querySelector('#source'),step=document.querySelector('#step'),parent=document.querySelector('#parent');
function score(value){const p=String(value).match(/^(-?\d+)(?:\.(\d+))?$/);return p?p[1]+'.'+(p[2]||'').padEnd(2,'0').slice(0,2):value.toFixed(2)}
function option(list,value,label){const o=document.createElement('option');o.value=value;o.textContent=label;list.append(o)}
function chooseParent(){const s=data.steps[Number(step.value)],a=s.attempts[Number(parent.value)];
const payload=JSON.stringify(a.view).replace(/</g,'\\u003c').replace(/>/g,'\\u003e').replace(/&/g,'\\u0026');
document.querySelector('#view').srcdoc=data.template.replace('__DATA__',payload)}
function chooseStep(){const s=data.steps[Number(step.value)];parent.replaceChildren();
s.attempts.forEach((a,i)=>option(parent,i,a.parent+' — '+(a.accepted?'acceptable':'rejected')+' · rank '+score(a.rank)));
let best=0;s.attempts.forEach((a,i)=>{const b=s.attempts[best];if((a.accepted&&!b.accepted)||(a.accepted===b.accepted&&a.rank<b.rank))best=i});
parent.value=best;chooseParent()}
function chooseNode(){step.replaceChildren();data.steps.forEach((s,i)=>{if(s.source===source.value)option(step,i,'Study '+s.step+' — '+s.attempts.length+' parents tested'+s.label)});
step.selectedIndex=step.options.length-1;chooseStep()}
[...new Set(data.steps.map(s=>s.source))].sort().forEach(s=>option(source,s,s));
source.value=data.node;source.onchange=chooseNode;step.onchange=chooseStep;parent.onchange=chooseParent;chooseNode();
</script>'''
