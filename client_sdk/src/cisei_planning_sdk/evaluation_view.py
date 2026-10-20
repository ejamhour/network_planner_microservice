"""A local, self-contained evaluation browser for notebook and standalone use."""
import copy
import html
import json
from pathlib import Path


class EvaluationView:
    def __init__(self, evaluation):
        self.evaluation = copy.deepcopy(evaluation)

    def to_html(self):
        from .local_io import _json_value
        payload = json.dumps(_json_value(self.evaluation), ensure_ascii=True, allow_nan=False)
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return _DOCUMENT.replace("__EVALUATION_DATA__", payload)

    def _repr_html_(self):
        return ('<iframe title="Planning evaluation report" sandbox="allow-scripts" '
                'style="width:100%;height:800px;border:1px solid #d6e0ea;border-radius:10px" '
                f'srcdoc="{html.escape(self.to_html(), quote=True)}"></iframe>')

    def save_html(self, path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_html(), encoding="utf-8")
        return path


_DOCUMENT = r'''<!doctype html><html><head><meta charset="utf-8"><title>Planning evaluation</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui;color:#20384e;background:#f4f7fb}header{padding:18px;background:white;border-bottom:1px solid #dce4ec}h1{font-size:22px;margin:0 0 6px}#context,#explanation{color:#51697d;font-size:13px}.summary{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}.stat{padding:10px 16px;border:1px solid #dce4ec;border-radius:8px;min-width:110px}.stat b{display:block;font-size:23px}.tools,nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}button,input,select{font:inherit;border:1px solid #bacbd9;border-radius:5px;padding:7px;background:white}button{cursor:pointer}button[aria-selected=true]{background:#245caf;color:white}button:disabled{opacity:.45;cursor:default}main{display:grid;grid-template-columns:minmax(0,1fr) 300px}section{padding:14px;min-width:0}#records{height:440px;overflow:auto}.record{display:block;width:100%;text-align:left;padding:12px;margin-bottom:7px;border-color:#dce4ec}.record:hover,.record:focus{border-color:#245caf}.headline{display:flex;justify-content:space-between;gap:12px;overflow-wrap:anywhere}.meta{font-size:12px;color:#51697d;margin-top:5px;overflow-wrap:anywhere}.badge{font-size:12px;white-space:nowrap}.good{color:#16705c}.poor{color:#a52b63}.unserved{color:#b53e32}aside{background:white;border-left:1px solid #dce4ec;padding:16px;overflow:auto;max-height:560px}dl{font-size:12px}dt{font-weight:600;margin-top:10px}dd{margin:2px 0;overflow-wrap:anywhere;white-space:pre-wrap}.pager{display:flex;align-items:center;gap:10px;margin-top:10px}#count{padding-bottom:10px;color:#51697d}footer{padding:12px 18px;font-size:12px;color:#51697d}@media(max-width:700px){main{grid-template-columns:1fr}aside{max-height:230px;border-top:1px solid #dce4ec}#records{height:360px}}
</style></head><body><header><h1>Planning evaluation</h1><div id="context"></div><div id="summary" class="summary"></div>
<nav aria-label="Report sections"><button id="targets" aria-selected="true">Targets</button><button id="anchors" aria-selected="false">Preset interfaces</button><button id="links" aria-selected="false">Selected links</button></nav>
<div class="tools"><input id="search" type="search" placeholder="Search site, device or interface" aria-label="Search records"><select id="tech" aria-label="Technology"></select><select id="quality" aria-label="Quality"></select><select id="sort" aria-label="Sort"><option value="site">Sort by site</option><option value="rank-desc">Highest rank / cost first</option><option value="rank-asc">Lowest rank / cost first</option></select><select id="size" aria-label="Page size"><option>20</option><option>50</option><option>100</option></select><label><input id="internal" type="checkbox">Include internal links</label><button id="clear">Clear filters</button></div></header>
<main><section><p id="explanation"></p><div id="count" aria-live="polite"></div><div id="records"></div><div class="pager"><button id="previous">Previous</button><span id="page"></span><button id="next">Next</button></div></section><aside><strong>Selected record</strong><div id="details">Select a record to inspect its details.</div></aside></main>
<footer>All filtering runs locally. Preset interfaces are initial connectivity assumptions, not links validated by this run. Counts refer to interfaces, not distinct physical sites. No geographic extraction or planning is triggered here.</footer>
<script>
const evaluation=__EVALUATION_DATA__;
const metricsMode=evaluation._report_kind==='metrics';
const $=s=>document.querySelector(s), qualityRows=evaluation.quality||[], byId=new Map(qualityRows.map(r=>[r.node_id,r]));
const dependents=new Map();for(const row of qualityRows){if(row.parent){let child=row.site_id||row.node_id,parent=byId.get(row.parent);if(parent&&(parent.site_id||parent.node_id)!==child){if(!dependents.has(row.parent))dependents.set(row.parent,new Set());dependents.get(row.parent).add(child)}}}
const targets=qualityRows.filter(r=>!r.connected),anchors=qualityRows.filter(r=>r.connected).map(r=>({...r,direct_dependents:(dependents.get(r.node_id)||new Set()).size}));
const links=(evaluation.result_edges||[]).map(e=>({...e,src_site:e.src_site||byId.get(e.src)?.site_id||e.src,dst_site:e.dst_site||byId.get(e.dst)?.site_id||e.dst}));
let section='targets',page=0,filtered=[];
function el(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node}
const summary=evaluation.quality_summary||{},threshold=summary.rank_threshold??qualityRows.find(r=>r.rank_threshold!=null)?.rank_threshold;
$('#context').textContent=metricsMode?'Saved link costs · lower is preferred · no quality threshold is inferred':threshold==null?'Saved evaluation — no rank threshold supplied':`Rank threshold: ${threshold} · applied to accumulated route cost`;
const totals=metricsMode?[['Computed links',links.length],['Radio links',links.filter(r=>r.kind!=='internal').length],['Internal links',links.filter(r=>r.kind==='internal').length]]:[['Targets',targets.length],['Good',targets.filter(r=>r.quality==='good').length],['Poor',targets.filter(r=>r.quality==='poor').length],['Unserved',targets.filter(r=>r.quality==='unserved').length],['Preset interfaces',anchors.length]];
if(metricsMode){document.title='Link metrics';document.querySelector('h1').textContent='Link metrics';$('#targets').hidden=true;$('#anchors').hidden=true;$('#links').textContent='Computed links';$('#quality').hidden=true;document.querySelector('footer').textContent='Saved metric records only. Search, technology filters, sorting, pagination and record selection are local. This view does not recompute metrics, solve the network or extract geographic features.';}
for(const[label,n]of totals){const card=el('div');card.className='stat';card.append(el('b',n),el('span',label));$('#summary').append(card)}
function options(node,values,title){node.replaceChildren();let all=el('option',title);all.value='';node.append(all);for(const v of [...new Set(values.filter(Boolean))].sort()){let o=el('option',v);o.value=v;node.append(o)}node.value=''}
function technologies(row){return section==='links'?[row.src_tech||byId.get(row.src)?.tech,row.dst_tech||byId.get(row.dst)?.tech]:[row.tech]}
function records(){return section==='targets'?targets:section==='anchors'?anchors:links}
function inspect(row){$('#details').replaceChildren();const list=el('dl');for(const[key,value]of Object.entries(row)){if(['rank_threshold','planned','node_index'].includes(key))continue;list.append(el('dt',key.replaceAll('_',' ')),el('dd',value==null?(key==='parent'||key==='parent_site_id'?(row.connected?'Not assigned — preset interface':'No route'):'Not available'):typeof value==='object'?JSON.stringify(value,null,2):String(value)))}$('#details').append(list)}
function changeSection(name){section=name;page=0;for(const id of ['targets','anchors','links'])$('#'+id).setAttribute('aria-selected',String(id===name));options($('#tech'),records().flatMap(technologies),'All technologies');options($('#quality'),name==='targets'?targets.map(r=>r.quality):[],'All qualities');$('#quality').disabled=name!=='targets';$('#internal').disabled=name!=='links';$('#explanation').textContent=name==='targets'?'Interfaces requiring a planned route, across all technologies.':name==='anchors'?'Interfaces supplied as already connected. Direct dependents counts selected cross-site child connections; it does not prove upstream radio quality.':(metricsMode?'Computed candidate-link costs, not selected solution links. Internal same-device forwarding links are hidden by default.':'Connections selected by the solver. Internal same-device forwarding links are hidden by default.');$('#details').textContent='Select a record to inspect its details.';render()}
function rank(row){const n=section==='links'?row.metric:row.rank;return n==null||!Number.isFinite(Number(n))?null:Number(n)}
function render(){const search=$('#search').value.trim().toLowerCase(),tech=$('#tech').value,state=$('#quality').value;
filtered=records().filter(r=>(section!=='links'||$('#internal').checked||r.kind!=='internal')&&(!search||JSON.stringify(r).toLowerCase().includes(search))&&(!tech||technologies(r).includes(tech))&&(!state||r.quality===state));
filtered.sort((a,b)=>{if($('#sort').value!=='site'){let x=rank(a),y=rank(b);if(x==null&&y!=null)return 1;if(y==null&&x!=null)return -1;if(x!=null&&y!=null&&x!==y)return $('#sort').value==='rank-asc'?x-y:y-x}return String(a.site_id||a.src_site||a.node_id||'').localeCompare(String(b.site_id||b.src_site||b.node_id||''),undefined,{numeric:true})});
const size=Number($('#size').value)||20,pages=Math.max(1,Math.ceil(filtered.length/size));page=Math.min(page,pages-1);const start=page*size,end=Math.min(start+size,filtered.length);$('#count').textContent=`${filtered.length} matching / ${records().length} total · showing ${filtered.length?start+1:0}–${end}`;$('#page').textContent=`Page ${page+1} of ${pages}`;$('#previous').disabled=page===0;$('#next').disabled=page+1>=pages;$('#records').replaceChildren();
for(const row of filtered.slice(start,end)){let card=el('button');card.className='record';let head=el('div');head.className='headline';head.append(el('strong',section==='links'?`${row.src_site} → ${row.dst_site}`:row.site_id||row.node_id||'Unknown site'));let status=el('span',section==='anchors'?'Preset / fixed':section==='links'?row.kind==='internal'?'Internal forwarding':'Radio link':row.quality||'Unknown');status.className='badge '+(row.quality||'');head.append(status);card.append(head);let text=section==='links'?`${technologies(row).filter(Boolean).join(' → ')} · Cost: ${rank(row)??'unavailable'}`:`${row.tech||'Unknown technology'} · Rank: ${rank(row)??'unreachable'}`;text+=section==='anchors'?` · Direct dependents: ${row.direct_dependents}`:section==='targets'?` · Parent: ${row.parent_site_id|| (row.parent?byId.get(row.parent)?.site_id||row.parent:'no route')}`:'';let meta=el('div',text);meta.className='meta';card.append(meta);card.onclick=()=>inspect(row);$('#records').append(card)}
if(!filtered.length)$('#records').append(el('p',records().length?'No records match these filters.':'No records in this section.'));
}
$('#sort').value='site';$('#size').value='20';
for(const id of ['targets','anchors','links'])$('#'+id).onclick=()=>changeSection(id);
$('#search').oninput=()=>{page=0;render()};for(const id of ['tech','quality','sort','size','internal'])$('#'+id).onchange=()=>{page=0;render()};$('#previous').onclick=()=>{page--;render()};$('#next').onclick=()=>{page++;render()};$('#clear').onclick=()=>{$('#search').value='';$('#tech').value='';$('#quality').value='';$('#sort').value='site';$('#internal').checked=false;page=0;render()};changeSection(metricsMode?'links':'targets');
</script></body></html>'''
