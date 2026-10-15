"""Offline interactive SVG views; rendering never calls a planning service."""
import copy
import html
import json
from math import isfinite
from pathlib import Path


class NetworkView:
    def __init__(self, result, *, kind="final"):
        if kind not in {"candidate", "final"}:
            raise ValueError("kind must be 'candidate' or 'final'")
        edge_key = "candidate_edges" if kind == "candidate" else "planned_edges"
        for key in ("rpl_nodes", edge_key, *(["planned_nodes"] if kind == "final" else [])):
            if key not in result:
                raise ValueError(f"Result is missing {key!r}; export it before plotting")
        data = copy.deepcopy(result)
        for node in data["rpl_nodes"]:
            if any(node.get(k) is None or not isfinite(float(node[k])) for k in ("x", "y")):
                raise ValueError(f"Node {node.get('node_id')!r} has no finite projected coordinates")
        self.result = data
        self.kind = kind

    def to_html(self):
        # Prevent arbitrary result strings from terminating the script element.
        payload = json.dumps({"result": self.result, "kind": self.kind}, ensure_ascii=True, allow_nan=False)
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return _DOCUMENT.replace("__NETWORK_DATA__", payload)

    def _repr_html_(self):
        # An iframe isolates event handlers and IDs when several views coexist.
        return ('<iframe title="Interactive network" sandbox="allow-scripts" '
                'style="width:100%;height:760px;border:1px solid #d8e2ec;border-radius:10px" '
                f'srcdoc="{html.escape(self.to_html(), quote=True)}"></iframe>')

    def save_html(self, path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_html(), encoding="utf-8")
        return path


_DOCUMENT = r'''<!doctype html><html><head><meta charset="utf-8"><title>Interactive network</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui;color:#20374c;background:#f5f8fb}header{padding:14px 18px;background:white;border-bottom:1px solid #d7e1e9}h2{margin:0 0 10px;font-size:20px}.tools{display:flex;align-items:center;flex-wrap:wrap;gap:12px}input,button{font:inherit;padding:6px}input[type=checkbox]{padding:0}main{display:grid;grid-template-columns:minmax(0,1fr) 290px;height:570px}svg{width:100%;height:100%;touch-action:none}aside{overflow:auto;background:white;border-left:1px solid #d7e1e9;padding:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 system-ui}footer{padding:10px 18px;font-size:12px;color:#53687c}.point,.edge{cursor:pointer}.label{font:12px system-ui;paint-order:stroke;stroke:white;stroke-width:3px;pointer-events:none}#hint{margin-top:8px;font-size:12px;color:#586e80}@media(max-width:700px){main{grid-template-columns:1fr;height:auto}svg{height:420px}aside{height:180px}}
</style></head><body><header><h2 id="title"></h2><div class="tools">
<input id="endpoint" list="endpoints" placeholder="Search / select endpoint" aria-label="Endpoint"><datalist id="endpoints"></datalist>
<button id="clear">All endpoints</button><button id="reset">Reset zoom</button>
<label><input id="links" type="checkbox" checked>Links</label>
<label><input id="sectors" type="checkbox" checked>Sector guides</label>
<label><input id="labels" type="checkbox">All labels</label></div><div id="hint"></div></header>
<main><svg viewBox="0 0 1000 700" aria-label="Network diagram"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7895a8"/></marker></defs><g id="scene"></g></svg>
<aside><strong>Selection details</strong><pre id="details">Select an endpoint or link. Wheel to zoom; drag to pan. Coincident sites share a marker with all IDs listed.</pre></aside></main>
<footer id="footer">Blue: connected tower · Teal: client · Red: unserved · Orange: colocated sites. North is up. Projected coordinates; no geographic basemap. Sector guides show orientation, not predicted coverage.</footer>
<script>
const {result:r,kind}=__NETWORK_DATA__;
const $=s=>document.querySelector(s), ns='http://www.w3.org/2000/svg',svg=$('svg'),scene=$('#scene');
const nodes=new Map(r.rpl_nodes.map(n=>[n.node_id,n])), planned=new Map((r.planned_nodes||[]).map(n=>[n.node_id,n]));
const sites=new Map(), groups=new Map();for(const n of nodes.values()){let id=n.site_id||n.node_id;if(!sites.has(id))sites.set(id,{id,x:+n.x,y:+n.y,connected:false,served:false,nodes:[]});let s=sites.get(id);s.connected ||= !!n.connected;s.served ||= planned.has(n.node_id);s.nodes.push(n)}
for(const s of sites.values()){let key=JSON.stringify([s.x,s.y]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(s)}
// Quality is taken from the saved evaluation, never inferred from link metrics.
const poorSites=new Set();let qualityAvailable=false;
function collectQuality(value){if(Array.isArray(value)){for(const row of value){if(!row||typeof row!=='object')continue;if(['good','poor','unserved','connected'].includes(row.quality)){qualityAvailable=true;const id=row.site_id||(nodes.has(row.node_id)?(nodes.get(row.node_id).site_id||row.node_id):null);if(row.quality==='poor'&&id&&sites.has(id)&&!sites.get(id).connected)poorSites.add(id)}}}else if(value&&typeof value==='object'){for(const child of Object.values(value))collectQuality(child)}}
if(kind==='final')collectQuality(r.evaluation||{});
const metrics=new Map((r.metric_edges||[]).map(e=>[JSON.stringify([e.src,e.dst]),e]));
let edges=(kind==='candidate'?r.candidate_edges:r.planned_edges).filter(e=>nodes.has(e.src)&&nodes.has(e.dst)&&e.kind!=='internal'&&(nodes.get(e.src).site_id||e.src)!==(nodes.get(e.dst).site_id||e.dst));
// Older exports may carry final routes only through planned-node parents.
if(kind==='final'&&!edges.length){edges=(r.planned_nodes||[]).filter(n=>n.parent&&nodes.has(n.parent)&&nodes.has(n.node_id)).map(n=>({src:n.node_id,dst:n.parent})).filter(e=>(nodes.get(e.src).site_id||e.src)!==(nodes.get(e.dst).site_id||e.dst))}
const values=[...sites.values()];
let loX=values.length?Math.min(...values.map(s=>s.x)):0,hiX=values.length?Math.max(...values.map(s=>s.x)):1,loY=values.length?Math.min(...values.map(s=>s.y)):0,hiY=values.length?Math.max(...values.map(s=>s.y)):1;
const scale=Math.min(850/Math.max(hiX-loX,1),570/Math.max(hiY-loY,1)),cx=(loX+hiX)/2,cy=(loY+hiY)/2;
function xy(s){return[500+(s.x-cx)*scale,350-(s.y-cy)*scale]}function site(n){return sites.get(n.site_id||n.node_id)}
const guideRadius=Math.max(hiX-loX,hiY-loY,1)*.09, antennas=r.antennas||{};
$('#title').textContent=(kind==='candidate'?'Candidate':'Solution')+' network';
for(const id of [...sites.keys()].sort()){let o=document.createElement('option');o.value=id;$('#endpoints').append(o)}
const hasSectors=kind==='candidate'&&[...nodes.values()].some(n=>antennas[n.antenna_id]?.kind==='sector');$('#sectors').disabled=!hasSectors;$('#sectors').checked=hasSectors;
$('#hint').textContent=`${sites.size} sites · ${edges.length} interface links · ${groups.size} distinct positions`+(kind==='candidate'&&!hasSectors?' · Sector metadata unavailable in this export.':'');
if(kind==='final'){
$('#hint').textContent+=qualityAvailable?` · ${poorSites.size} sites with poor-quality interfaces`:' · No saved quality evaluation; export with rank_threshold to highlight poor quality.';
$('#footer').textContent='Blue: connected tower · Teal: served client · Magenta + ring: poor quality · Red: unserved · Orange outline: colocated sites. A site is highlighted if any evaluated interface is poor. North is up; projected coordinates; no basemap.';
}

function details(v){$('#details').textContent=JSON.stringify(v,null,2)}
function selectedQuality(id){
const ids=new Set(sites.get(id).nodes.map(n=>n.node_id));
function filter(value){if(Array.isArray(value))return value.filter(row=>row&&typeof row==='object'&&(row.site_id===id||ids.has(row.node_id)));if(value&&typeof value==='object'){const out={};for(const[k,v]of Object.entries(value)){const matching=filter(v);if(matching&&(Array.isArray(matching)?matching.length:Object.keys(matching).length))out[k]=matching}return out}return null}
return filter(r.evaluation||{})}
function select(ids){$('#endpoint').value=ids[0];details(ids.map(id=>({site:sites.get(id),solution_nodes:sites.get(id).nodes.map(n=>planned.get(n.node_id)).filter(Boolean),quality:selectedQuality(id)})));draw()}
function make(tag,attrs,parent=scene){const e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);parent.append(e);return e}
function draw(){scene.replaceChildren();const selected=$('#endpoint').value, valid=sites.has(selected);let neighbors=new Set(valid?[selected]:[]);
for(const e of edges){let a=site(nodes.get(e.src)),b=site(nodes.get(e.dst));if(!valid||a.id===selected||b.id===selected){neighbors.add(a.id);neighbors.add(b.id)}}
if($('#sectors').checked)for(const n of nodes.values()){let s=site(n),a=antennas[n.antenna_id];if(!s.connected||!a||a.kind!=='sector'||a.azimuth_deg==null||a.beamwidth_deg==null||(valid&&s.id!==selected))continue;let coords=[xy(s)];for(let i=0;i<=30;i++){let t=(a.azimuth_deg-a.beamwidth_deg/2+a.beamwidth_deg*i/30)*Math.PI/180;coords.push(xy({x:s.x+guideRadius*Math.sin(t),y:s.y+guideRadius*Math.cos(t)}))}let poly=make('polygon',{points:coords.map(p=>p.join(',')).join(' '),fill:['#2765bc','#8054b8','#309a6a'][Math.round(a.azimuth_deg/120)%3]||'#2765bc','fill-opacity':.12,stroke:'#7594ac','stroke-width':.6});poly.onclick=()=>details({interface:n.node_id,antenna:a,guide_radius_projected_units:guideRadius,note:'Orientation only; not coverage'})}
if($('#links').checked)for(const e of edges){let a=site(nodes.get(e.src)),b=site(nodes.get(e.dst));if(valid&&a.id!==selected&&b.id!==selected)continue;let[p,q]=[xy(a),xy(b)];let line=make('line',{x1:p[0],y1:p[1],x2:q[0],y2:q[1],stroke:valid?'#d77a23':'#7895a8','stroke-width':valid?2:1,opacity:valid?.8:.35,class:'edge',...(kind==='final'?{'marker-end':'url(#arrow)'}:{})});line.onclick=()=>details({link:e,metric:metrics.get(JSON.stringify([e.src,e.dst]))||metrics.get(JSON.stringify([e.dst,e.src]))||null,source:nodes.get(e.src),destination:nodes.get(e.dst),source_solution:planned.get(e.src)||null,destination_solution:planned.get(e.dst)||null});let title=make('title',{},line);title.textContent=e.src+' → '+e.dst}
for(const group of groups.values()){let s=group[0],[x,y]=xy(s),duplicate=group.length>1,poor=kind==='final'&&group.some(s=>poorSites.has(s.id)),color=poor?'#b51c69':duplicate?'#e47a16':group.some(s=>s.connected)?'#245caf':kind==='final'&&!group.some(s=>s.served)?'#d65f5f':'#229c92';if(poor)make('circle',{cx:x,cy:y,r:11,fill:'none',stroke:'#b51c69','stroke-width':2,'pointer-events':'none'});let marker=make('circle',{cx:x,cy:y,r:group.some(s=>s.connected)?7:duplicate?7:4.5,fill:color,stroke:duplicate?'#e47a16':'white','stroke-width':duplicate?2:1.2,opacity:valid&&!group.some(s=>neighbors.has(s.id))?.25:1,class:'point'});marker.onclick=()=>select(group.map(s=>s.id));let title=make('title',{},marker);title.textContent=group.map(s=>s.id).join(' / ')+(poor?' — poor quality':'');if($('#labels').checked){let t=make('text',{x:x+8,y:y-8,class:'label',fill:color});t.textContent=group.map(s=>s.id).join(' / ')}}
}
let z=1,tx=0,ty=0;function transform(){scene.setAttribute('transform',`translate(${tx} ${ty}) scale(${z})`)}
svg.onwheel=e=>{e.preventDefault();let p=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse()),nz=Math.max(.5,Math.min(30,z*(e.deltaY<0?1.2:1/1.2)));tx=p.x-(p.x-tx)*nz/z;ty=p.y-(p.y-ty)*nz/z;z=nz;transform()};
let drag=null;svg.onpointerdown=e=>{if(e.target!==svg)return;svg.setPointerCapture(e.pointerId);drag=[e.clientX,e.clientY,tx,ty]};svg.onpointermove=e=>{if(!drag)return;let m=svg.getScreenCTM().inverse(),a=new DOMPoint(drag[0],drag[1]).matrixTransform(m),b=new DOMPoint(e.clientX,e.clientY).matrixTransform(m);tx=drag[2]+b.x-a.x;ty=drag[3]+b.y-a.y;transform()};svg.onpointerup=()=>drag=null;svg.onpointercancel=()=>drag=null;
$('#endpoint').onchange=()=>{if(sites.has($('#endpoint').value))select([$('#endpoint').value]);else draw()};$('#clear').onclick=()=>{$('#endpoint').value='';details('Select an endpoint or link.');draw()};$('#reset').onclick=()=>{z=1;tx=ty=0;transform()};for(const id of ['links','sectors','labels'])$('#'+id).onchange=draw;draw();
</script></body></html>'''
