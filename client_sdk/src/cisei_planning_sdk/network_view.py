"""Offline interactive SVG views; rendering never calls a planning service."""
import copy
import html
import json
from math import isfinite
from pathlib import Path


class NetworkView:
    def __init__(self, result, *, kind="final", dependency_mode=False, installed_layout=None):
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
        self.installed_layout = installed_layout
        if dependency_mode and kind != "final":
            raise ValueError("Dependency exploration requires a final solution")
        self.dependencies = None
        if dependency_mode:
            from cisei_planning_sdk.dependencies import network_dependencies
            self.dependencies = network_dependencies(data)

    def to_html(self):
        # Prevent arbitrary result strings from terminating the script element.
        payload = json.dumps({"result": self.result, "kind": self.kind, "dependencies": self.dependencies, "installedLayout": self.installed_layout}, ensure_ascii=True, allow_nan=False)
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        from .reconfiguration_view import SCRIPT
        script = SCRIPT if self.result.get("reconfiguration_review") else "const review=null;"
        return _DOCUMENT.replace("__NETWORK_DATA__", payload).replace("__NETWORK_REVIEW_SCRIPT__", script)

    def _repr_html_(self):
        # An iframe isolates event handlers and IDs when several views coexist.
        permissions = "allow-scripts allow-downloads" if self.result.get("reconfiguration_review") else "allow-scripts"
        if self.result.get("reconfiguration_review", {}).get("jupyter_destinations"):
            permissions += " allow-same-origin"
        height = 1100 if self.result.get("reconfiguration_review") else 760
        return (f'<iframe title="Interactive network" sandbox="{permissions}" '
                f'style="width:100%;height:{height}px;border:1px solid #d8e2ec;border-radius:10px" '
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
<label id="subtree-control" hidden><input id="subtree" type="checkbox">Descendant subnetwork</label>
<label id="path-control" hidden><input id="upstream" type="checkbox" checked>Caminho até o backbone</label>
<label><input id="links" type="checkbox" checked>Links</label>
<label><input id="sectors" type="checkbox" checked>Sector guides</label>
<label><input id="labels" type="checkbox">All labels</label></div><div id="hint"></div></header>
<main><svg viewBox="0 0 1000 700" aria-label="Network diagram"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7895a8"/></marker></defs><g id="scene"></g></svg>
<aside><section id="dependency-summary" hidden style="margin-bottom:16px;padding:12px;background:#edf5fa;border-radius:8px;white-space:pre-line"></section><strong>Selection details</strong><pre id="details">Select an endpoint or link. Wheel to zoom; drag to pan. Coincident sites share a marker with all IDs listed.</pre></aside></main>
<footer id="footer">Blue: connected tower · Teal: client · Red: unserved · Orange: colocated sites. North is up. Projected coordinates; no geographic basemap. Sector guides show orientation, not predicted coverage.</footer>
<script>
const {result:r,kind,dependencies,installedLayout}=__NETWORK_DATA__;
const $=s=>document.querySelector(s), ns='http://www.w3.org/2000/svg',svg=$('svg'),scene=$('#scene');
const nodes=new Map([...(r.interface_inventory||[]).filter(n=>n.enabled===false),...r.rpl_nodes].map(n=>[n.node_id,n])), planned=new Map((r.planned_nodes||[]).map(n=>[n.node_id,n]));
$("#subtree-control").hidden=!dependencies;$("#subtree").checked=!!dependencies;
const sites=new Map(), groups=new Map();for(const n of nodes.values()){let id=n.site_id||n.node_id;if(!sites.has(id))sites.set(id,{id,x:+n.x,y:+n.y,connected:false,served:false,disabled:true,nodes:[]});let s=sites.get(id);s.disabled &&= n.enabled===false;s.connected ||= n.enabled!==false&&!!n.connected;s.served ||= planned.has(n.node_id);s.nodes.push(n)}
for(const s of sites.values()){let key=JSON.stringify([s.x,s.y]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(s)}
const equipmentByDevice=new Map();for(const equipment of r.attached_equipment||[]){if(!equipmentByDevice.has(equipment.device_id))equipmentByDevice.set(equipment.device_id,[]);equipmentByDevice.get(equipment.device_id).push(equipment)}
function siteEquipment(id){const devices=new Set(sites.get(id).nodes.map(n=>n.device_id));return [...devices].flatMap(id=>equipmentByDevice.get(id)||[])}
function describeEquipment(ids){if(!Array.isArray(r.attached_equipment))return '';return ids.map(id=>{const equipment=siteEquipment(id);return id+' — equipamentos conectados:\n'+(equipment.length?equipment.map(e=>e.equipment_type+' — '+e.equipment_id+' (rádio '+e.device_id+')').join('\n'):'Nenhum equipamento associado informado.');}).join('\n\n')}
// Quality is taken from the saved evaluation, never inferred from link metrics.
const poorSites=new Set();let qualityAvailable=false;
function collectQuality(value){if(Array.isArray(value)){for(const row of value){if(!row||typeof row!=='object')continue;if(['good','poor','unserved','connected'].includes(row.quality)){qualityAvailable=true;const id=row.site_id||(nodes.has(row.node_id)?(nodes.get(row.node_id).site_id||row.node_id):null);if(row.quality==='poor'&&id&&sites.has(id)&&!sites.get(id).connected)poorSites.add(id)}}}else if(value&&typeof value==='object'){for(const child of Object.values(value))collectQuality(child)}}
if(kind==='final')collectQuality(r.evaluation||{});
const metrics=new Map((r.metric_edges||[]).map(e=>[JSON.stringify([e.src,e.dst]),e]));
let edges=(kind==='candidate'?r.candidate_edges:r.planned_edges).filter(e=>nodes.has(e.src)&&nodes.has(e.dst)&&((installedLayout)||e.kind!=='internal')&&(nodes.get(e.src).site_id||e.src)!==(nodes.get(e.dst).site_id||e.dst));
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
$('#footer').textContent='Blue: connected access · Gray: disabled site · Teal: served client · Magenta + ring: poor quality · Red: unserved · Orange outline: colocated sites. A site is highlighted if any evaluated interface is poor. North is up; projected coordinates; no basemap.';
}

if(kind==='candidate'&&r.mesh_search){
$('#hint').textContent=`${sites.size} posições compatíveis · ${edges.length} enlaces efetivamente avaliados`;
$('#footer').textContent='Nós: azul = destino de referência do estudo; verde = terminal ou candidato opcional. Enlaces: laranja = caminho escolhido; cinza = outras alternativas avaliadas. Candidato isolado não teve enlaces avaliados. Coordenadas projetadas; norte para cima.';
$('#sectors').closest('label').hidden=true;
}
if(kind==='final'&&r.mesh_search){
 $('#title').textContent='Proposta de conexão entre dois extremos';
 $('#footer').textContent='Azul: extremo de referência deste estudo de enlace; não implica criar um novo acesso ao backbone. Os demais nós são o terminal e os repetidores escolhidos. Consulte o atendimento ao limite por segmento no resumo da proposta. A rede completa é avaliada separadamente.';
}
function edgeMetric(e){const m=metrics.get(JSON.stringify([e.src,e.dst]))||metrics.get(JSON.stringify([e.dst,e.src]));return m?.metric!=null?m:(e.metric!=null?{...m,metric:e.metric}:m||null)}
function rankText(id){const n=planned.get(id);return n?.rank!=null?String(n.rank):(n?'indisponível no resultado':(kind==='final'?'sem caminho na solução':'não calculado para candidato'))}
function details(v){
const raw=JSON.stringify(v,null,2);
if(kind==='candidate'&&r.mesh_search&&v.link){const e=v.link,m=v.metric;
$('#details').textContent=`Enlace: ${e.src_site} → ${e.dst_site}\nInterfaces: ${e.src} → ${e.dst}\nTecnologia: ${e.src_tech} · ${e.src_freq_mhz} MHz\nCusto: ${m?.metric ?? 'indisponível'}\nAvaliado: ${e.evaluated?'sim':'não'}\nExpandido para buscar repetidores: ${e.expanded?'sim':'não'}\nSelecionado na solução: ${e.selected?'sim':'não'}\n\nDados completos:\n`+raw;
}else if(v.link){const e=v.link;
$('#details').textContent=`Enlace: ${shortName(e.src)} → ${shortName(e.dst)}\nInterfaces: ${e.src} → ${e.dst}\nCusto do segmento: ${v.metric?.metric ?? 'indisponível no resultado'}\nRank de ${shortName(e.src)}: ${rankText(e.src)}\nRank de ${shortName(e.dst)}: ${rankText(e.dst)}`+(r.mesh_search?'\n\nRanks locais ao estudo: o extremo de referência tem rank '+(r.mesh_search.reference_rank ?? 0)+'.':'')+'\n\nDados completos:\n'+raw;
}else if(kind==='final'&&Array.isArray(v)){
$('#details').textContent=v.map(row=>row.site.id+'\n'+row.site.nodes.map(n=>'Interface: '+n.node_id+'\nRank: '+rankText(n.node_id)+'\nPai: '+(planned.get(n.node_id)?.parent??(planned.has(n.node_id)?'ponto de acesso':'sem caminho na solução'))).join('\n\n')).join('\n\n')+(r.mesh_search?'\n\nRanks locais ao estudo: o extremo de referência tem rank '+(r.mesh_search.reference_rank ?? 0)+'.':'')+'\n\nDados completos:\n'+raw;
}else{$('#details').textContent=raw}
}
const diagnosis=r.link_diagnosis;
const categoryNames={investigate:'Investigar instalação ou defeito',satisfactory:'Atendimento satisfatório',measured_satisfactory:'Medição satisfatória; sem correção',review_design:'Revisar projeto',internal:'Conexão interna por cabo',predicted_satisfactory:'Previsão satisfatória; sem medição',unassessed:'Dados insuficientes'};
const categoryColors={investigate:'#c14236',satisfactory:'#247753',measured_satisfactory:'#467db1',review_design:'#a96112',internal:'#8795a3',predicted_satisfactory:'#8795a3',unassessed:'#8795a3'};
const pairKey=(a,b)=>JSON.stringify([a,b].sort());
const diagnosedLinks=new Map((diagnosis?.installed_links||[]).map(e=>[pairKey(e.src,e.dst),e]));
function linkDiagnosis(e){return diagnosedLinks.get(pairKey(e.src,e.dst))}
function shortName(iid){return nodes.get(iid)?.site_id||iid}
function describeLink(e){
const heading=shortName(e.src)+' → '+shortName(e.dst)+'\n'+(categoryNames[e.category]||e.category);
if(e.category==='internal')return heading+'\nSem avaliação de propagação ou RSSI.';
const value=v=>v==null?'indisponível':v.toFixed(1)+' dBm';
return heading+'\nPrevisto: '+value(e.predicted)+' · Medido: '+value(e.measured)+(e.measured==null?'\nSem medição: somente avaliação de projeto.':(e.residual==null?'':'\nDiferença: '+e.residual.toFixed(1)+' dB'));
}
function describePath(id){
return sites.get(id).nodes.map(n=>{const d=r.path_diagnoses?.[n.node_id];if(!d)return '';
const counts=d.categories;
return id+'\n\nCaminho até o backbone:\n'+d.path.map(shortName).join(' → ')+'\n\n'+d.hop_count+' conexões · Backbone: '+shortName(d.backbone)+'\nLimiar de RSSI: '+diagnosis.threshold_dbm+' dBm\n\n'+
(counts.investigate||0)+' enlace(s) para inspeção de instalação ou defeito\n'+(counts.review_design||0)+' enlace(s) para melhoria de projeto\n'+(counts.internal||0)+' conexão(ões) interna(s)\n'+
((counts.predicted_satisfactory||0)+(counts.unassessed||0))+' enlace(s) sem diagnóstico medido completo\n\nEnlaces ao longo do caminho (RSSI no sentido indicado):\n\n'+d.links.map(describeLink).join('\n\n');}).filter(Boolean).join('\n\n');
}

if(installedLayout){
$('#title').textContent='Topologia instalada — '+(installedLayout==='logical'?'lógica':'geográfica');
$('#path-control').hidden=false;
$('#hint').textContent=`${sites.size} sites · ${edges.length} enlaces instalados · selecione um nó para destacar seu caminho`;
$('#footer').textContent='Azul: acesso ao backbone · Verde: equipamento da rede · Laranja: caminho selecionado. '+(installedLayout==='logical'?'Posições organizadas pela hierarquia.':'Coordenadas projetadas; norte para cima, sem mapa de fundo.')+' Sem avaliação de qualidade nesta etapa.';
}
if(installedLayout&&diagnosis){
$('#arrow').setAttribute('markerUnits','userSpaceOnUse');$('#arrow').setAttribute('markerWidth','8');$('#arrow').setAttribute('markerHeight','8');
$('#hint').textContent+=` · Classificação E08 · limiar ${diagnosis.threshold_dbm} dBm`;
$('#footer').textContent='Cores dos enlaces: classificação E08. A seleção aumenta a espessura e esmaece o restante da rede, preservando as cores. Nós indicam apenas a topologia; cabo tracejado não recebe classificação de RSSI.';
const legend=document.createElement('div');legend.id='diagnosis-legend';legend.style.cssText='display:flex;flex-wrap:wrap;gap:12px;padding:12px 18px;background:white';
for(const k of ['satisfactory','investigate','measured_satisfactory','review_design','internal','unassessed']){const item=document.createElement('span');item.textContent=categoryNames[k];item.style.cssText='border-left:5px solid '+categoryColors[k]+';padding-left:6px';legend.append(item)}$('#footer').before(legend);
}
if(Array.isArray(r.attached_equipment)){
const legend=document.createElement('div');legend.id='equipment-legend';legend.style.cssText='padding:10px 18px;background:white';legend.textContent='● Roxo: rádio com equipamento(s) associado(s). A cor do nó não indica qualidade dos enlaces.';legend.style.color='#7c3aed';$('#footer').before(legend);
}
function upstreamSites(id){
const focus=new Set([id]);for(const n of sites.get(id).nodes){let iid=n.node_id,seen=new Set();while(iid&&!seen.has(iid)){seen.add(iid);const row=planned.get(iid);if(!row)break;focus.add(nodes.get(iid).site_id||iid);iid=row.parent}}return focus;
}
function selectedQuality(id){
const ids=new Set(sites.get(id).nodes.map(n=>n.node_id));
function filter(value){if(Array.isArray(value))return value.filter(row=>row&&typeof row==='object'&&(row.site_id===id||ids.has(row.node_id)));if(value&&typeof value==='object'){const out={};for(const[k,v]of Object.entries(value)){const matching=filter(v);if(matching&&(Array.isArray(matching)?matching.length:Object.keys(matching).length))out[k]=matching}return out}return null}
return filter(r.evaluation||{})}
function dependencyDetails(id){
const panel=$('#dependency-summary'), report=dependencies?.sites[id];
panel.hidden=!report;
if(report)panel.textContent=`${id}\n${report.child_count} direct children\n${report.descendant_site_count} dependent sites\n${report.descendant_device_count} dependent devices\n${report.descendant_count} descendant interfaces\n\nSelected-path dependencies; engineer decides whether cellular protection is needed. A leaf can still be important.`;
}
function focusSites(id){return new Set([id,...(dependencies?.sites[id]?.descendant_sites||[])])}
function select(ids){$('#endpoint').value=ids[0];dependencyDetails(ids[0]);if(installedLayout){const id=ids[0],path=[...upstreamSites(id)];$('#details').textContent=diagnosis?describePath(id):`${id}\n\nCaminho até o backbone:\n${path.join(' → ')}\n\n${Math.max(0,path.length-1)} saltos\n\nQualidade ainda não avaliada. As cores representam a topologia e a seleção.`;const equipmentText=describeEquipment(ids);if(equipmentText)$('#details').textContent=equipmentText+'\n\n'+$('#details').textContent}else details(ids.map(id=>({site:sites.get(id),attached_equipment:siteEquipment(id),solution_nodes:sites.get(id).nodes.map(n=>planned.get(n.node_id)).filter(Boolean),quality:selectedQuality(id)})));draw()}
function make(tag,attrs,parent=scene){const e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);parent.append(e);return e}
function draw(){scene.replaceChildren();const selected=$('#endpoint').value, valid=sites.has(selected);let upstream=valid&&!!installedLayout&&$('#upstream').checked,subtree=upstream||(valid&&!!dependencies&&$('#subtree').checked),focus=upstream?upstreamSites(selected):subtree?focusSites(selected):new Set();let neighbors=subtree?new Set(focus):new Set(valid?[selected]:[]);
for(const e of edges){let a=site(nodes.get(e.src)),b=site(nodes.get(e.dst));if(!subtree&&(!valid||a.id===selected||b.id===selected)){neighbors.add(a.id);neighbors.add(b.id)}}
if($('#sectors').checked)for(const n of nodes.values()){let s=site(n),a=antennas[n.antenna_id];if(!s.connected||!a||a.kind!=='sector'||a.azimuth_deg==null||a.beamwidth_deg==null||(valid&&s.id!==selected))continue;let coords=[xy(s)];for(let i=0;i<=30;i++){let t=(a.azimuth_deg-a.beamwidth_deg/2+a.beamwidth_deg*i/30)*Math.PI/180;coords.push(xy({x:s.x+guideRadius*Math.sin(t),y:s.y+guideRadius*Math.cos(t)}))}let poly=make('polygon',{points:coords.map(p=>p.join(',')).join(' '),fill:['#2765bc','#8054b8','#309a6a'][Math.round(a.azimuth_deg/120)%3]||'#2765bc','fill-opacity':.12,stroke:'#7594ac','stroke-width':.6});poly.onclick=()=>details({interface:n.node_id,antenna:a,guide_radius_projected_units:guideRadius,note:'Orientation only; not coverage'})}
if($('#links').checked)for(const e of edges){let a=site(nodes.get(e.src)),b=site(nodes.get(e.dst));const active=subtree?(focus.has(a.id)&&focus.has(b.id)):(!valid||a.id===selected||b.id===selected);if(!active&&!installedLayout)continue;let[p,q]=[xy(a),xy(b)];let line=make('line',{x1:p[0],y1:p[1],x2:q[0],y2:q[1],stroke:diagnosis?(categoryColors[linkDiagnosis(e)?.category]||'#8795a3'):(kind==='candidate'&&r.mesh_search?(e.selected?'#d77a23':'#7895a8'):(valid&&active?'#d77a23':'#7895a8')),'stroke-width':diagnosis?(valid&&active?3.5:2):(kind==='candidate'&&r.mesh_search?(e.selected?3:1):(valid&&active?2:1)),opacity:diagnosis?(valid?(active?1:.18):.9):(valid?(active?.8:.15):.35),'stroke-dasharray':diagnosis&&e.kind==='internal'?'6 4':'none',class:'edge','data-review-edge':e.link_id||'','data-replanning-origin':e.origin||'',...(kind==='final'?{'marker-end':'url(#arrow)'}:{})});line.onclick=()=>{if(review){reviewEdgeClick(e);return}if(diagnosis){$('#details').textContent=describeLink(linkDiagnosis(e));return}details({link:e,metric:edgeMetric(e),source:nodes.get(e.src),destination:nodes.get(e.dst),source_solution:planned.get(e.src)||null,destination_solution:planned.get(e.dst)||null})};let title=make('title',{},line);title.textContent=e.src+' → '+e.dst}
for(const group of groups.values()){let s=group[0],[x,y]=xy(s),duplicate=group.length>1,poor=kind==='final'&&group.some(s=>poorSites.has(s.id)),color=group.some(s=>siteEquipment(s.id).length)?'#7c3aed':diagnosis?'#617887':group.every(s=>s.disabled)?'#9aa7b2':poor?'#b51c69':duplicate?'#e47a16':group.some(s=>s.connected)?'#245caf':kind==='final'&&!group.some(s=>s.served)?'#d65f5f':'#229c92';if(poor)make('circle',{cx:x,cy:y,r:11,fill:'none',stroke:'#b51c69','stroke-width':2,'pointer-events':'none'});let marker=make('circle',{cx:x,cy:y,r:group.some(s=>s.connected)?7:duplicate?7:4.5,fill:color,stroke:duplicate?'#e47a16':'white','stroke-width':duplicate?2:1.2,opacity:valid&&!group.some(s=>neighbors.has(s.id))?.25:1,class:'point','data-review-interfaces':JSON.stringify(group.flatMap(s=>s.nodes.map(n=>n.node_id)))});marker.onclick=()=>select(group.map(s=>s.id));let title=make('title',{},marker);title.textContent=group.map(s=>s.id).join(' / ')+(poor?' — poor quality':'')+(group.some(s=>siteEquipment(s.id).length)?' — com equipamento associado':'');if($('#labels').checked||(installedLayout&&valid&&group.some(s=>neighbors.has(s.id)))){let t=make('text',{x:x+8,y:y-8,class:'label',fill:color});t.textContent=group.map(s=>s.id).join(' / ')}}
if(r.replanning_stage){
 for(const line of scene.querySelectorAll('line.edge'))line.setAttribute('stroke',line.dataset.replanningOrigin==='link_replanning'?'#d77a23':'#7895a8');
 for(const marker of scene.querySelectorAll('circle.point')){const ids=JSON.parse(marker.dataset.reviewInterfaces);if(ids.some(i=>!planned.has(i))){marker.setAttribute('stroke','#c14236');marker.setAttribute('stroke-width','3')}}
 $('#title').textContent=r.replanning_stage==='prepared'?'Rede preservada — antes do replanejamento':'Rede consolidada — replanejamento de enlaces';
 $('#hint').textContent=`${sites.size} sites · ${edges.length} enlaces · ${[...sites.values()].filter(s=>!s.served).length} site(s) sem serviço`;
 $('#footer').textContent='Enlace cinza: instalado e preservado · Laranja: novo segmento aceito. Marcador laranja: rádios colocalizados · Nó azul: acesso original · Vermelho ou contorno vermelho: sem serviço · Roxo: rádio com equipamento associado. Componentes desconectados mantêm seus enlaces preservados no desenho.';
}
if(review)reviewPaint();
}
__NETWORK_REVIEW_SCRIPT__
let z=1,tx=0,ty=0;function transform(){scene.setAttribute('transform',`translate(${tx} ${ty}) scale(${z})`)}
svg.onwheel=e=>{e.preventDefault();let p=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse()),nz=Math.max(.5,Math.min(30,z*(e.deltaY<0?1.2:1/1.2)));tx=p.x-(p.x-tx)*nz/z;ty=p.y-(p.y-ty)*nz/z;z=nz;transform()};
let drag=null;svg.onpointerdown=e=>{if(e.target!==svg)return;svg.setPointerCapture(e.pointerId);drag=[e.clientX,e.clientY,tx,ty]};svg.onpointermove=e=>{if(!drag)return;let m=svg.getScreenCTM().inverse(),a=new DOMPoint(drag[0],drag[1]).matrixTransform(m),b=new DOMPoint(e.clientX,e.clientY).matrixTransform(m);tx=drag[2]+b.x-a.x;ty=drag[3]+b.y-a.y;transform()};svg.onpointerup=()=>drag=null;svg.onpointercancel=()=>drag=null;
$('#endpoint').onchange=()=>{if(sites.has($('#endpoint').value))select([$('#endpoint').value]);else draw()};$('#clear').onclick=()=>{$('#endpoint').value='';$('#dependency-summary').hidden=true;details('Select an endpoint or link.');draw()};$('#reset').onclick=()=>{z=1;tx=ty=0;transform()};for(const id of ['links','sectors','labels','subtree','upstream'])$('#'+id).onchange=draw;draw();
</script></body></html>'''
