"""Offline baseline views; charts and readable link details, no raw JSON panels."""
import html
import json
from uuid import uuid4


def training_view(report):
    rows = []
    for stage in report["stages"]:
        bars = []
        maximum = max([abs(v) for v in stage["coefficients"].values()] or [1]) or 1
        for key, value in stage["coefficients"].items():
            bars.append(f'<div>{html.escape(key)}: {value:.4f}</div>'
                        f'<div style="height:9px;background:#276992;width:{abs(value)/maximum*90:.1f}%"></div>')
        rows.append(f'<section style="margin:18px 0"><h3>{html.escape(stage["name"])}</h3>'
                    f'<p>{stage["training"]["n_training_records"]} medições no ajuste</p>{"".join(bars)}</section>')
    cache_info = (f'{report["feature_entries"]} entradas de features; '
                  if "feature_entries" in report else "")
    return (f'<div style="font:15px Arial;color:#172b39;max-width:900px">'
            f'<h2>Ajuste do modelo da tecnologia</h2><p>{cache_info}{report["input_records"]} medições; '
            f'{report["selected_records"]} selecionadas. Coeficientes das expressões do modelo.</p>'
            + ''.join(rows) + '</div>')


def evaluation_view(report):
    uid = 'baseline_' + uuid4().hex
    data = json.dumps(report, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    return f'''<div id="{uid}" style="font:15px Arial;color:#172b39;max-width:1100px">
<h2>RSSI previsto e medido</h2><p>Selecione um ponto para inspecionar o enlace.
O limiar é uma escolha do engenheiro; os quatro grupos orientam a intervenção.</p>
<div class="summary"></div><div style="display:flex;gap:20px;flex-wrap:wrap">
<svg viewBox="0 0 650 540" style="width:650px;max-width:100%" role="img" aria-label="Comparação de RSSI previsto e medido"></svg>
<aside style="width:300px;overflow-wrap:anywhere"><p>Selecione um enlace.</p></aside></div></div>
<script>(()=>{{
const root=document.getElementById('{uid}'),d={data};
const names={{investigate:'Investigar instalação ou defeito',satisfactory:'Atendimento satisfatório',
measured_satisfactory:'Medição satisfatória; sem correção',review_design:'Revisar projeto'}};
const colors={{investigate:'#c14236',satisfactory:'#247753',measured_satisfactory:'#467db1',review_design:'#a96112'}};
const summary=root.querySelector('.summary');
Object.entries(d.categories).forEach(([k,v])=>{{const p=document.createElement('p');p.style.color=colors[k];p.textContent=names[k]+': '+v;summary.append(p);}});
const p=document.createElement('p');p.textContent='Erro absoluto médio: '+Number(d.summary.mae||0).toFixed(2)+' dB · Limiar: '+d.threshold_dbm+' dBm';summary.append(p);
const svg=root.querySelector('svg'),ns='http://www.w3.org/2000/svg';
function add(tag,a,text){{let e=document.createElementNS(ns,tag);Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;svg.append(e);return e;}}
const vals=d.links.flatMap(l=>[l.predicted,l.measured]).concat([d.threshold_dbm]);
const lo=Math.floor(Math.min(...vals)/10)*10-5,hi=Math.ceil(Math.max(...vals)/10)*10+5;
const x=v=>65+(v-lo)/(hi-lo)*545,y=v=>470-(v-lo)/(hi-lo)*425;
for(let v=Math.ceil(lo/10)*10;v<=hi;v+=10){{add('line',{{x1:x(v),y1:45,x2:x(v),y2:470,stroke:'#e0e4e7'}});add('line',{{x1:65,y1:y(v),x2:610,y2:y(v),stroke:'#e0e4e7'}});add('text',{{x:x(v),y:490,'text-anchor':'middle','font-size':12}},v);add('text',{{x:55,y:y(v)+4,'text-anchor':'end','font-size':12}},v);}}
add('line',{{x1:x(lo),y1:y(lo),x2:x(hi),y2:y(hi),stroke:'#667788','stroke-dasharray':'4 4'}});
add('line',{{x1:x(d.threshold_dbm),x2:x(d.threshold_dbm),y1:45,y2:470,stroke:'#333'}});
add('line',{{x1:65,x2:610,y1:y(d.threshold_dbm),y2:y(d.threshold_dbm),stroke:'#333'}});
add('text',{{x:330,y:525,'text-anchor':'middle'}},'RSSI previsto (dBm)');
add('text',{{x:18,y:255,transform:'rotate(-90 18 255)','text-anchor':'middle'}},'RSSI medido (dBm)');
function detail(l){{const a=root.querySelector('aside');a.replaceChildren();function line(t,b=false){{let e=document.createElement(b?'h3':'p');e.textContent=t;a.append(e);}}
line(l.tx+' → '+l.rx,true);line(names[l.category]);line('Previsto: '+l.predicted.toFixed(2)+' dBm');line('Medido: '+l.measured.toFixed(2)+' dBm');line('Diferença: '+l.residual.toFixed(2)+' dB');
line('Contribuições à previsão',true);line('Termo independente: '+l.explanation.intercept.toFixed(2)+' dB');
for(const group of ['fixed','learned'])Object.entries(l.explanation[group]).forEach(([k,v])=>line(k+': '+v.contribution.toFixed(2)+' dB'));}}
d.links.forEach(l=>{{const c=add('circle',{{cx:x(l.predicted),cy:y(l.measured),r:3.4,fill:colors[l.category],opacity:.65,tabindex:0,role:'button','aria-label':l.tx+' para '+l.rx}});c.style.cursor='pointer';c.addEventListener('click',()=>detail(l));c.addEventListener('keydown',e=>{{if(e.key==='Enter')detail(l);}});}});
}})();</script>'''
