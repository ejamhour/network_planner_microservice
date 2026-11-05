"""Browse saved mesh construction snapshots without running planning."""
import copy
import html
import json

from .network_view import NetworkView


class MeshIterationsView:
    def __init__(self, result, *, iteration=None):
        report = result.get('mesh_network', {})
        snapshots = report.get('iterations', [])
        if not snapshots:
            raise ValueError('No saved mesh construction iterations; call add_repeaters first')
        if iteration is None:
            iteration = len(snapshots) - 1
        if isinstance(iteration, bool) or not isinstance(iteration, int) or not 0 <= iteration < len(snapshots):
            raise ValueError(f'iteration must be between 0 and {len(snapshots)-1}')
        self.iteration = iteration
        self.snapshots = []
        for snapshot in snapshots:
            # Reuse coordinate/schema validation, but retain only drawing data.
            data = NetworkView(snapshot['result']).result
            self.snapshots.append({key: data.get(key, []) for key in
                                   ('rpl_nodes', 'planned_nodes', 'planned_edges')})
        self.roots = copy.deepcopy(report.get('fixed_interfaces', []))
        self.repeaters = copy.deepcopy(report.get('selected_repeaters', []))

    def payload(self):
        return dict(iterations=self.snapshots, initial=self.iteration,
                               roots=self.roots, repeaters=self.repeaters,
                               final=getattr(self, 'final', False),
                               study=getattr(self, 'study', None))

    def to_html(self):
        data = json.dumps(self.payload(), allow_nan=False)
        data = data.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        return _DOCUMENT.replace('__DATA__', data)

    def _repr_html_(self):
        title = 'Mesh solution network' if getattr(self, 'final', False) else 'Mesh construction iterations'
        return (f'<iframe title="{title}" sandbox="allow-scripts" '
                'style="width:100%;height:800px;border:1px solid #d8e2ec;border-radius:10px" '
                f'srcdoc="{html.escape(self.to_html(), quote=True)}"></iframe>')

    save_html = NetworkView.save_html


class MeshSolutionView(MeshIterationsView):
    """Use the same path explorer for the top-level solved network only."""
    def __init__(self, result):
        phase = result.get('mesh_network', {}).get('phase')
        if phase not in (None, 'solved', 'pruned') or not result.get('planned_nodes'):
            raise ValueError('Solve and export the mesh result before viewing the final network')
        data = NetworkView(result).result
        self.snapshots = [{key: data.get(key, []) for key in
                           ('rpl_nodes', 'planned_nodes', 'planned_edges')}]
        self.iteration = 0
        report = result.get('mesh_network', {})
        self.roots = copy.deepcopy(report.get('fixed_interfaces',
            [n['node_id'] for n in data['rpl_nodes'] if n.get('connected')]))
        self.repeaters = copy.deepcopy(report.get('selected_repeaters', []))
        self.final = True


_DOCUMENT = r'''<!doctype html><html><head><meta charset="utf-8"><title>Mesh construction</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui;color:#20374c;background:#f5f8fb}
header{padding:14px 18px;background:white;border-bottom:1px solid #d7e1e9}h2{margin:0 0 12px;font-size:20px}
.tools{display:flex;gap:12px;align-items:center;flex-wrap:wrap}select,button{font:inherit;padding:6px}
main{display:grid;grid-template-columns:minmax(0,1fr) 310px}svg{width:100%;height:570px;touch-action:none}
aside{padding:14px;background:white;overflow:auto;height:570px}pre{white-space:pre-wrap;font:13px/1.6 system-ui;overflow-wrap:anywhere}
footer,#summary{padding:10px 18px}.label{font:12px system-ui;paint-order:stroke;stroke:white;stroke-width:3px;pointer-events:none}
.node,.edge{cursor:pointer}@media(max-width:700px){main{grid-template-columns:1fr}aside{height:220px}}
</style></head><body><header><h2>Mesh construction</h2><div class="tools">
<label>Iteration <select id="iteration" aria-label="Construction iteration"></select></label>
<label>Node <select id="node" aria-label="Select node"><option value="">All paths</option></select></label>
<button id="clear">All paths</button><button id="reset">Reset zoom</button>
<label><input id="labels" type="checkbox" checked>Names and ranks</label>
<label><input id="metrics" type="checkbox" checked>Link metrics</label></div></header>
<div id="summary"></div><main><svg viewBox="0 0 1000 700" aria-label="Mesh construction network"><defs><marker id="path-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="#71899a"/></marker></defs><g id="scene"></g></svg>
<aside><strong>Path and selection details</strong><pre id="details"></pre></aside></main>
<footer>■ Blue: original backbone access · ◆ Purple: added repeater · ● Teal: connected equipment leaf · ○ Red: unconnected equipment.
Orange: selected path. R = accumulated rank; m = link metric. North is up; projected coordinates, no basemap.</footer>
<script>
const data=__DATA__, $=s=>document.querySelector(s), ns='http://www.w3.org/2000/svg';
if(data.final){document.title='Mesh solution network';$('h2').textContent='Mesh solution network';$('#iteration').closest('label').hidden=true;$('svg').setAttribute('aria-label','Mesh solution network')}
if(data.study){document.title='Mesh connection study';$('h2').textContent='Proposed path to connected parent';$('footer').textContent='Square blue: connected reference parent · Diamond purple: proposed repeater · Circle teal: terminal. Arrows point toward the connected parent, whose upstream route belongs to the existing network. Red link: cost above the acceptance threshold. R = accumulated rank; m = segment cost. Projected coordinates; north is up.'}
const svg=$('svg'),scene=$('#scene'), roots=new Set(data.roots),repeaters=new Set(data.repeaters);
let current=data.initial,nodes,planned,edges,selected='',z=1,tx=0,ty=0;
const all=data.iterations.flatMap(s=>s.rpl_nodes),xs=all.map(n=>n.x),ys=all.map(n=>n.y);
// Stable bounds across iterations; avoid including the coordinate origin.
const bounds=all.length?[Math.min(...xs),Math.max(...xs),Math.min(...ys),Math.max(...ys)]:[0,1,0,1];
const scale=Math.min(840/Math.max(bounds[1]-bounds[0],1),550/Math.max(bounds[3]-bounds[2],1));
function xy(n){return[500+(n.x-(bounds[0]+bounds[1])/2)*scale,350-(n.y-(bounds[2]+bounds[3])/2)*scale]}
function name(id){return nodes.get(id)?.site_id||id}
function formatScore(value){
 if(!Number.isFinite(value))return '—';
 const parts=String(value).match(/^(-?\d+)(?:\.(\d+))?$/);
 return parts?parts[1]+'.'+(parts[2]||'').padEnd(2,'0').slice(0,2):value.toFixed(2);
}
function rank(id){return formatScore(planned.get(id)?.rank)}
function role(n){return roots.has(n.node_id)?'access':repeaters.has(n.site_id)?'repeater':'equipment'}
function key(a,b){return JSON.stringify([a,b].sort())}
function path(id){let out=[],seen=new Set();while(id&&nodes.has(id)&&!seen.has(id)){seen.add(id);out.push(id);id=planned.get(id)?.parent}return out}
function metric(a,b){return formatScore(edges.find(e=>key(e.src,e.dst)===key(a,b))?.metric)}
function describe(id){const p=path(id),n=nodes.get(id),kind=role(n);
let text=name(id)+'\n'+({access:'Original backbone access',repeater:'Added repeater',equipment:'Equipment leaf'}[kind]);
if(!planned.has(id))return text+'\nNot connected in this iteration.';
text+='\nAccumulated rank: '+rank(id)+(data.study?'\n\nPath toward connected parent:\n':'\n\nPath toward backbone:\n')+p.map(name).join(' → ');
text+='\n'+Math.max(0,p.length-1)+' hops';
for(let i=0;i<p.length-1;i++)text+='\n\n'+name(p[i])+' → '+name(p[i+1])+'\nLink metric: '+metric(p[i],p[i+1])+'\nRanks: '+rank(p[i])+' → '+rank(p[i+1]);
return text;
}
function make(tag,attrs,parent=scene){const e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);parent.append(e);return e}
function select(id){selected=id;$('#node').value=id;$('#details').textContent=id?describe(id):'Select a node to highlight its path toward the backbone, or a link to inspect its metric.';draw()}
function draw(){scene.replaceChildren();const p=selected?path(selected):[],focus=new Set(p),pairs=new Set(p.slice(1).map((n,i)=>key(p[i],n)));
for(const saved of edges){let e=saved;
if(planned.get(saved.dst)?.parent===saved.src)e={...saved,src:saved.dst,dst:saved.src};
if(!nodes.has(e.src)||!nodes.has(e.dst))continue;
const a=xy(nodes.get(e.src)),b=xy(nodes.get(e.dst)),active=!selected||pairs.has(key(e.src,e.dst));
const length=Math.hypot(b[0]-a[0],b[1]-a[1]),tip=length>20?[b[0]-(b[0]-a[0])*10/length,b[1]-(b[1]-a[1])*10/length]:b;
const color=data.study&&e.metric>data.study.threshold?'#c14236':selected&&active?'#d97706':'#71899a';
const line=make('line',{x1:a[0],y1:a[1],x2:tip[0],y2:tip[1],stroke:color,'marker-end':'url(#path-arrow)','stroke-width':selected&&active?3:1.7,opacity:active?1:.15,class:'edge','data-source':e.src,'data-destination':e.dst});
line.onclick=()=>{$('#details').textContent=name(e.src)+' → '+name(e.dst)+'\nLink metric: '+formatScore(e.metric)+'\nRank of '+name(e.src)+': '+rank(e.src)+'\nRank of '+name(e.dst)+': '+rank(e.dst)};
if($('#metrics').checked&&active){const t=make('text',{x:(a[0]+b[0])/2,y:(a[1]+b[1])/2+18,class:'label',fill:'#52697b'});t.textContent='m='+formatScore(e.metric)}
}
for(const n of nodes.values()){const[x,y]=xy(n),k=role(n),served=planned.has(n.node_id),active=!selected||focus.has(n.node_id);
const color=k==='access'?'#245caf':k==='repeater'?'#7c3aed':served?'#16857d':'#c14236';
const attrs={fill:served?color:'white',stroke:color,'stroke-width':2,opacity:active?1:.25,class:'node','data-node-id':n.node_id,'data-role':k};
const marker=k==='access'?make('rect',{...attrs,x:x-7,y:y-7,width:14,height:14}):k==='repeater'?make('polygon',{...attrs,points:`${x},${y-9} ${x+9},${y} ${x},${y+9} ${x-9},${y}`}):make('circle',{...attrs,cx:x,cy:y,r:6});
marker.onclick=()=>select(n.node_id);make('title',{},marker).textContent=describe(n.node_id);
if($('#labels').checked){const t=make('text',{x:x+11,y:y-10,class:'label',fill:color,opacity:active?1:.4});t.textContent=name(n.node_id)+' · R='+rank(n.node_id)}
}}
function load(index){current=index;const r=data.iterations[index];nodes=new Map(r.rpl_nodes.map(n=>[n.node_id,n]));planned=new Map(r.planned_nodes.map(n=>[n.node_id,n]));edges=r.planned_edges;
$('#node').replaceChildren();const option=document.createElement('option');option.value='';option.textContent='All paths';$('#node').append(option);
for(const n of nodes.values()){const o=document.createElement('option');o.value=n.node_id;o.textContent=name(n.node_id);$('#node').append(o)}
const prev=index?new Set(data.iterations[index-1].rpl_nodes.map(n=>n.node_id)):new Set();
const added=r.rpl_nodes.filter(n=>!prev.has(n.node_id)&&repeaters.has(n.site_id)).map(n=>n.site_id);
const prevServed=index?new Set(data.iterations[index-1].planned_nodes.map(n=>n.node_id)):new Set();
const newly=r.planned_nodes.filter(n=>!prevServed.has(n.node_id)&&!roots.has(n.node_id)&&!repeaters.has(n.site_id)).map(n=>name(n.node_id));
$('#summary').textContent=(data.final?'Final solution':`Iteration ${String(index).padStart(4,'0')}`)+` · ${planned.size}/${nodes.size} radios connected · ${r.rpl_nodes.filter(n=>role(n)==='repeater').length} repeaters · ${edges.length} selected links`+(data.final?'':index===0?' · Initial network':` · Equipment connected: ${newly.join(', ')||'none'} · Repeaters added: ${added.join(', ')||'none'}`);
if(data.study){const s=data.study;$('#summary').textContent=s.path.join(' → ')+' · '+(s.accepted?'Acceptable proposal':'Rejected proposal')+' · Path cost: '+formatScore(s.cost)+' · Total rank: '+formatScore(s.totalRank)+' · Maximum segment cost: '+formatScore(s.threshold)}
select(nodes.has(selected)?selected:'');
}
for(let i=0;i<data.iterations.length;i++){const o=document.createElement('option');o.value=i;o.textContent=String(i).padStart(4,'0')+(i===0?' — Initial network':i===data.iterations.length-1?' — Last iteration':'');$('#iteration').append(o)}
$('#iteration').value=current;$('#iteration').onchange=()=>load(Number($('#iteration').value));$('#node').onchange=()=>select($('#node').value);$('#clear').onclick=()=>select('');
for(const id of ['labels','metrics'])$('#'+id).onchange=draw;
function transform(){scene.setAttribute('transform',`translate(${tx} ${ty}) scale(${z})`)}
$('#reset').onclick=()=>{z=1;tx=ty=0;transform()};
svg.onwheel=e=>{e.preventDefault();const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse()),nz=Math.max(.5,Math.min(30,z*(e.deltaY<0?1.2:1/1.2)));tx=p.x-(p.x-tx)*nz/z;ty=p.y-(p.y-ty)*nz/z;z=nz;transform()};
let drag=null;svg.onpointerdown=e=>{if(e.target!==svg)return;svg.setPointerCapture(e.pointerId);drag=[e.clientX,e.clientY,tx,ty]};svg.onpointermove=e=>{if(!drag)return;const m=svg.getScreenCTM().inverse(),a=new DOMPoint(drag[0],drag[1]).matrixTransform(m),b=new DOMPoint(e.clientX,e.clientY).matrixTransform(m);tx=drag[2]+b.x-a.x;ty=drag[3]+b.y-a.y;transform()};svg.onpointerup=()=>drag=null;svg.onpointercancel=()=>drag=null;
load(current);
</script></body></html>'''
