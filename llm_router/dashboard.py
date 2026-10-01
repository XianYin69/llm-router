"""Embedded dashboard page (plain HTML + fetch, no build step)."""
CSS = """
body{font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
header{padding:14px 20px;background:#161a22;border-bottom:1px solid #262b36;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
h1{font-size:16px;margin:0}main{padding:18px 20px;max-width:1100px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#8b93a7;margin:22px 0 8px}
table{border-collapse:collapse;width:100%;background:#141821;border:1px solid #262b36}
th,td{padding:6px 10px;border-bottom:1px solid #222735;text-align:left}
th{color:#8b93a7;font-weight:600}
.cards{display:flex;gap:12px;flex-wrap:wrap}
.card{background:#141821;border:1px solid #262b36;border-radius:8px;padding:10px 14px;min-width:110px}
.card b{display:block;font-size:20px}
input,button{background:#1b2029;color:#e6e6e6;border:1px solid #2c3342;border-radius:6px;padding:6px 10px}
pre{background:#141821;border:1px solid #262b36;padding:12px;border-radius:8px;overflow:auto}
"""

JS = """
const K=()=>document.getElementById('key').value.trim();
const hdr=()=>K()?{Authorization:'Bearer '+K()}:{};
function tbl(id,rows,cols){const t=document.getElementById(id);
 if(!rows||!rows.length){t.innerHTML='<tr><td colspan='+cols.length+'>no data</td></tr>';return}
 t.innerHTML='<tr>'+cols.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+
  cols.map(c=>'<td>'+(r[c]==null?'-':r[c])+'</td>').join('')+'</tr>').join('')}
async function j(u){const r=await fetch(u,{headers:hdr()});
 if(!r.ok)throw new Error(u+' -> '+r.status);return r.json()}
async function refresh(){const m=document.getElementById('msg');m.textContent='';
 try{const [s,p,mods]=await Promise.all([j('/stats'),j('/pool'),j('/v1/models')]);
  document.getElementById('calls').textContent=s.calls;
  document.getElementById('tokens').textContent=s.tokens;
  document.getElementById('slots').textContent=p.slots.length;
  document.getElementById('models').textContent=mods.data.length;
  tbl('bymodel',s.by_model,['alias','calls','tokens']);
  tbl('byprov',s.by_provider,['provider','calls','tokens','avg_ms','errors']);
  tbl('pool',p.slots,['provider','key','ok','fails','cooldown_left']);
  tbl('recent',s.recent.map(r=>({...r,ts:new Date(r.ts*1000).toLocaleTimeString()})),
      ['ts','alias','provider','status','ms','total','stream']);
  document.getElementById('strategy').textContent=p.strategy;
 }catch(e){m.textContent=e.message}}
refresh();
"""

BODY = """
<header><h1>llm-router</h1>
<input id="key" type="password" size="22" placeholder="master key (optional)">
<button onclick="refresh()">Refresh</button><span id="msg"></span>
<span>strategy: <b id="strategy">-</b></span></header>
<main><div class="cards">
<div class="card"><b id="calls">-</b>calls</div>
<div class="card"><b id="tokens">-</b>tokens</div>
<div class="card"><b id="slots">-</b>key slots</div>
<div class="card"><b id="models">-</b>models</div></div>
<h2>By model</h2><table id="bymodel"></table>
<h2>By provider</h2><table id="byprov"></table>
<h2>Key pool</h2><table id="pool"></table>
<h2>Recent calls</h2><table id="recent"></table>
<h2>Client config</h2><pre id="hint"></pre></main>
<script>document.getElementById('hint').textContent =
 'base_url = ' + location.origin + '/v1\\nmodel    = <alias from /v1/models>';</script>
"""

PAGE = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>llm-router</title><style>" + CSS + "</style></head><body>"
        + BODY + "<script>" + JS + "</script></body></html>")
