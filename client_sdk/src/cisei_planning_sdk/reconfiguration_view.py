"""Browser-only review controls shared by notebook and standalone network views."""

SCRIPT = r'''
const review=r.reconfiguration_review;
let reviewSaving=false;
let reviewRequests=new Map(),reviewRepeaters=new Set(review?.base.repeater_devices||[]),reviewImpact=null;
function reviewEdgeClick(e){
 if(reviewSaving)return;
 const id=e.link_id;
 if(reviewRequests.has(id)&&reviewRequests.get(id)===$('#review-mode').value)reviewRequests.delete(id);
 else reviewRequests.set(id,$('#review-mode').value);
 const info=linkDiagnosis(e);
 $('#details').textContent=(info?describeLink(info):e.src+' → '+e.dst)+'\n\n'+(info?.category==='review_design'?'Recomendado para revisão de projeto.':'Sem recomendação automática de remoção.')+'\n'+(reviewRequests.has(id)?'Selecionado: '+(reviewRequests.get(id)==='subtree'?'subárvore':'somente enlace'):'Sem seleção explícita.');
 $('#review-status').textContent='Prévia alterada. Confirme após revisar.';draw();
}
function reviewCompute(){
 const removed=new Set(),subtree=new Set();
 for(const[id,mode]of reviewRequests){for(const key of review.choices[id][mode].removed_links)removed.add(key);for(const iid of review.choices[id][mode].subtree_interfaces)subtree.add(iid)}
 const kept=edges.filter(e=>!removed.has(e.link_id)),adj=new Map([...nodes.keys()].map(i=>[i,[]]));
 for(const e of kept){adj.get(e.src).push(e.dst);adj.get(e.dst).push(e.src)}
 const connected=new Set(),todo=[...review.roots];while(todo.length){const i=todo.pop();if(!connected.has(i)){connected.add(i);todo.push(...adj.get(i))}}
 const protectedSet=new Set(review.protected_devices),eligible=Object.entries(review.devices).filter(([d,ids])=>!protectedSet.has(d)&&ids.every(i=>subtree.has(i)&&!connected.has(i))&&!kept.some(e=>ids.includes(e.src)||ids.includes(e.dst))).map(([d])=>d);
 const removedDevices=[...reviewRepeaters].filter(d=>eligible.includes(d));
 const deleted=new Set(removedDevices.flatMap(d=>review.devices[d]));
 return {removed_links:[...removed].sort(),preserved_links:kept.map(e=>e.link_id).sort(),subtree_interfaces:[...subtree].sort(),removed_devices:removedDevices.sort(),removed_interfaces:[...deleted].sort(),connected_interfaces:[...connected].sort(),floating_interfaces:[...nodes.keys()].filter(i=>!connected.has(i)&&!deleted.has(i)).sort(),eligible_repeater_devices:eligible.sort(),protected_devices:review.protected_devices.filter(d=>review.devices[d].some(i=>subtree.has(i))).sort()};
}
function reviewPaint(){
 $('#review-approved').checked=false;
 $('#review-status').textContent='Prévia alterada. Confirme após revisar.';
 reviewImpact=reviewCompute();const removed=new Set(reviewImpact.removed_links),deleted=new Set(reviewImpact.removed_devices),floating=new Set(reviewImpact.floating_interfaces);
 for(const line of scene.querySelectorAll('line.edge')){const id=line.dataset.reviewEdge,chosen=removed.has(id),e=edges.find(e=>e.link_id===id),info=e&&linkDiagnosis(e),recommended=info?.category==='review_design';line.setAttribute('stroke',chosen?'#c14236':recommended?'#a96112':'#7895a8');line.setAttribute('opacity','1');line.setAttribute('stroke-width',chosen||recommended?'3':'1.5');line.setAttribute('stroke-dasharray',chosen?'7 4':e?.kind==='internal'?'3 4':'none');line.dataset.recommended=String(recommended);const title=line.querySelector('title');if(title)title.textContent=(info?describeLink(info):e.src+' → '+e.dst)+'\n'+(chosen?'Prévia: remover':recommended?'Recomendado para revisão de projeto':'Sem recomendação automática de remoção')}
 for(const marker of scene.querySelectorAll('circle.point')){const ids=JSON.parse(marker.dataset.reviewInterfaces),allDeleted=ids.every(i=>deleted.has(nodes.get(i).device_id)),partDeleted=ids.some(i=>deleted.has(nodes.get(i).device_id)),isFloating=ids.some(i=>floating.has(i));let color=allDeleted?'#c14236':ids.some(i=>review.protected_devices.includes(nodes.get(i).device_id))?'#7c3aed':isFloating?'#d77a23':'#245caf';marker.setAttribute('fill',color);marker.setAttribute('opacity','1');marker.setAttribute('stroke',partDeleted?'#c14236':isFloating?'#d77a23':'white');marker.setAttribute('stroke-width',partDeleted||isFloating?'3':'1.2')}
 const area=$('#review-requests');area.replaceChildren();
 for(const[id,mode]of reviewRequests){const row=document.createElement('div'),name=document.createElement('span'),button=document.createElement('button');name.textContent=id+' — '+(mode==='link'?'enlace':'subárvore')+' ';button.textContent='Desfazer';button.onclick=()=>{reviewRequests.delete(id);draw()};row.append(name,button);area.append(row)}
 const summary=$('#review-summary');summary.textContent=reviewRequests.size+' seleção(ões) · '+removed.size+' enlace(s) removido(s) · '+reviewImpact.removed_devices.length+' rádio(s) removido(s) · '+reviewImpact.floating_interfaces.length+' interface(s) sem acesso ao backbone';
 const repeaters=$('#review-repeaters');repeaters.replaceChildren();
 for(const d of reviewImpact.eligible_repeater_devices){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=reviewRepeaters.has(d);input.onchange=()=>{if(input.checked)reviewRepeaters.add(d);else reviewRepeaters.delete(d);draw()};label.append(input,document.createTextNode(d+' — confirmo que é repetidor removível'));label.style.display='block';repeaters.append(label)}
 $('#review-radio-removals').textContent=reviewImpact.removed_devices.length?'Rádios que serão removidos: '+reviewImpact.removed_devices.join(', '):'Nenhum rádio marcado para remoção. No modo subárvore, marque os repetidores removíveis abaixo; rádios com equipamentos associados permanecem.';
 $('#review-panel details').open=reviewImpact.eligible_repeater_devices.length>0;
 $('#review-confirm').disabled=!reviewRequests.size;
}
function reviewDestination(){
 try{
  const host=window.parent,configElement=host.document.getElementById('jupyter-config-data');
  const config=configElement?JSON.parse(configElement.textContent):null;
  const base=config?.baseUrl||host.document.body?.dataset.baseUrl;
  if(!['http:','https:'].includes(host.location.protocol))return null;
  return (review.jupyter_destinations||[]).find(d=>base?d.base_url===base:host.location.pathname.startsWith(d.base_url+'files/'))||null;
 }catch{return null}
}
async function saveReview(doc){
 const destination=reviewDestination(),text=JSON.stringify(doc,null,2)+'\n';
 if(!destination){const a=document.createElement('a');a.href='data:application/json;charset=utf-8,'+encodeURIComponent(text);a.download='reconfiguration.json';document.body.append(a);a.click();a.remove();return 'Download enviado ao navegador. Esta visualização externa não gravou no projeto; abra a revisão no Jupyter para salvar diretamente.'}
 const host=window.parent,cookie=host.document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('_xsrf='));
 const headers={'Content-Type':'application/json'};if(cookie)headers['X-XSRFToken']=decodeURIComponent(cookie.slice(6));
 const url=new URL(destination.base_url+'api/contents/'+destination.path.split('/').map(encodeURIComponent).join('/'),host.location.origin);
 const response=await host.fetch(url,{method:'PUT',credentials:'same-origin',headers,body:JSON.stringify({type:'file',format:'text',content:text})});
 if(!response.ok)throw Error('Jupyter recusou a gravação (HTTP '+response.status+'). O arquivo não foi salvo.');
 const saved=await response.json();if(saved.path!==destination.path)throw Error('Jupyter retornou um caminho inesperado. Verifique o arquivo antes de continuar.');
 return 'Salvo no projeto: '+destination.display_path;
}
if(review){
 $('#diagnosis-legend')?.remove();
 $('#title').textContent='Revisão de reconfiguração — '+(installedLayout==='logical'?'topologia lógica':'topologia geográfica');
 const panel=document.createElement('section');panel.id='review-panel';panel.style.cssText='padding:12px 18px;background:white;border-bottom:1px solid #d7e1e9';
 panel.innerHTML='<label>Modo para o próximo clique: <select id="review-mode"><option value="link">Somente enlace</option><option value="subtree">Subárvore: enlaces e repetidores marcados</option></select></label> <button id="review-suggest">Selecionar sugestões de revisão de projeto</button> <button id="review-empty">Limpar seleções</button><p id="review-mode-description"></p><p id="review-summary"></p><p id="review-radio-removals"></p><details><summary>Seleções e repetidores</summary><div id="review-requests"></div><p>Marque somente rádios que são repetidores e podem ser retirados. Rádios com equipamento associado são preservados. Ausência no inventário não confirma que um rádio seja repetidor.</p><div id="review-repeaters"></div></details><p><label><input id="review-approved" type="checkbox">Revisei os enlaces e repetidores destacados</label> <button id="review-confirm" disabled>Confirmar e baixar reconfiguration.json</button></p><div id="review-status" role="status"></div>';
 $('header').after(panel);
 function explainMode(){const subtree=$('#review-mode').value==='subtree';$('#review-mode-description').textContent=subtree?'Subárvore: retira o enlace escolhido, os enlaces descendentes e os rádios identificados como repetidores removíveis. Rádios com equipamento associado são mantidos para reconexão.':'Somente enlace: retira o enlace escolhido. Todos os rádios e os demais enlaces são mantidos.'}
 $('#review-mode').onchange=explainMode;explainMode();
 const suggested=(diagnosis?.installed_links||[]).filter(e=>e.category==='review_design'),unmeasured=suggested.filter(e=>e.measured==null).length;
 const legend=document.createElement('div');legend.id='review-legend';legend.style.cssText='display:flex;flex-wrap:wrap;gap:16px;margin:12px 0';
 for(const[color,text,dashed]of [['#a96112','Recomendado para revisão de projeto',false],['#c14236','Selecionado para remoção na prévia',true],['#7895a8','Sem recomendação automática de remoção',false]]){const item=document.createElement('span');item.style.cssText='border-left:5px '+(dashed?'dashed':'solid')+' '+color+';padding-left:7px';item.textContent=text;legend.append(item)}
 const count=document.createElement('p');count.id='review-recommendations';count.textContent=suggested.length+' enlace(s) recomendado(s) para revisão de projeto'+(unmeasured?' · '+unmeasured+' sem medição (indicação baseada na previsão)':'')+'. As recomendações não são selecionadas automaticamente.';
 panel.prepend(count,legend);
 $('#review-suggest').onclick=()=>{for(const e of diagnosis?.installed_links||[])if(e.category==='review_design')reviewRequests.set(e.link_id,$('#review-mode').value);draw()};
 $('#review-empty').onclick=()=>{reviewRequests.clear();draw()};
 const destination=reviewDestination();
 $('#review-confirm').textContent=destination?'Confirmar e salvar reconfiguration.json no projeto':'Confirmar e baixar reconfiguration.json';
 $('#review-confirm').onclick=async()=>{if(!$('#review-approved').checked){$('#review-status').textContent='Marque a confirmação de revisão antes de salvar.';return}const doc={format:review.base.format,version:2,confirmed:true,source:review.base.source,requests:[...reviewRequests].sort(([a],[b])=>a<b?-1:a>b?1:0).map(([link_id,mode])=>({link_id,mode})),remove_links:reviewImpact.removed_links,remove_devices:reviewImpact.removed_devices};reviewSaving=true;const controls=[...panel.querySelectorAll('input,button,select')];for(const el of controls)el.disabled=true;$('#review-status').textContent='Salvando reconfiguração…';try{$('#review-status').textContent=await saveReview(doc)}catch(error){$('#review-status').textContent='Erro ao salvar: '+error.message}finally{reviewSaving=false;for(const el of controls)el.disabled=false;$('#review-confirm').disabled=!reviewRequests.size}};
 $('#hint').textContent='Clique em enlaces para selecionar ou desfazer. Escolha o modo antes do clique. O painel de inspeção mostra o diagnóstico e os caminhos da rede original.';
 $('#footer').textContent='Enlace âmbar: recomendado para revisão de projeto · Vermelho tracejado: remover na prévia · Cinza: sem recomendação automática de remoção · Cabo: tracejado curto. Nó vermelho: repetidor removido · Nó ou contorno laranja: mantido sem acesso ao backbone · Roxo: equipamento associado preservado · Azul: mantido com acesso. Contorno vermelho: posição com algum rádio removido. A seleção só produz um arquivo de reconfiguração; não altera a rede.';
}
'''
