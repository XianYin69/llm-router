"""Embedded dashboard page (plain HTML + fetch, no build step).

Aero 拟物化改造：4 套可切换皮肤，右上角拟物拨杆切换，选择存 localStorage，默认 A。
  A = Win7 Aero Glass（深蓝毛玻璃 + 蓝光晕）
  B = Win11 Fluent Mica（磨砂灰白 + 柔和阴影）
  C = 金属拉丝面板 + 玻璃窗框混搭（保留工业感）
  D = 仿成品简洁（ChatGPT/OpenWebUI 式浅色双栏）
零依赖 / 零构建 / 无外链字体与 CDN；元素 id 与 fetch 数据逻辑保持不变。
"""

_BASE = """
*{box-sizing:border-box}
body{font:14px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif;margin:0;min-height:100vh;
 background:var(--bg);color:var(--fg);background-attachment:fixed}
header{padding:12px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 14px 0;
 background:var(--glass);backdrop-filter:blur(var(--blur)) saturate(var(--sat));-webkit-backdrop-filter:blur(var(--blur)) saturate(var(--sat));
 border:1px solid var(--edge);border-radius:var(--rad);box-shadow:var(--drop),var(--bevel)}
h1{font-size:17px;margin:0;font-weight:600;white-space:nowrap;text-shadow:var(--glow)}
main{padding:14px 18px 34px;max-width:1220px;margin:0 auto;display:grid;grid-template-columns:1fr;gap:12px;align-items:start}
h2{font-size:12px;letter-spacing:.08em;margin:18px 0 6px;font-weight:700;color:var(--h2);text-shadow:var(--glow)}
table{border-collapse:separate;border-spacing:0;width:100%;overflow:hidden;
 background:var(--glass);backdrop-filter:blur(var(--blur)) saturate(var(--sat));-webkit-backdrop-filter:blur(var(--blur)) saturate(var(--sat));
 border:1px solid var(--edge);border-radius:var(--rad);box-shadow:var(--drop),var(--bevel)}
th,td{padding:6px 10px;text-align:left;font-size:13px}
th{background:var(--th);color:var(--thfg);font-weight:700}
td{border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:0}
tr:hover td{background:var(--hover)}
"""
_BASE += """
.cards{display:flex;gap:10px;flex-wrap:wrap}
.card{padding:10px 14px;min-width:120px;background:var(--glass);border:1px solid var(--edge);border-radius:var(--rad);
 backdrop-filter:blur(var(--blur)) saturate(var(--sat));-webkit-backdrop-filter:blur(var(--blur)) saturate(var(--sat));
 box-shadow:var(--drop),var(--bevel)}
.card b{display:block;font-size:22px;font-weight:700;color:var(--num);text-shadow:var(--glow)}
.card i{font-style:normal;font-size:11px;letter-spacing:.05em;opacity:.85}
pre{padding:12px;overflow:auto;font:12px/1.6 ui-monospace,Consolas,monospace;white-space:pre-wrap;
 background:var(--well);border:1px solid var(--edge);border-radius:var(--rad);box-shadow:var(--inset)}
input,button{font:inherit;color:var(--fg);border-radius:var(--rad);padding:6px 11px;border:1px solid var(--btnedge);
 background:var(--btn);box-shadow:var(--btnsh);cursor:pointer}
#key{min-width:210px;background:var(--well);color:var(--fg);box-shadow:var(--inset),var(--bevel)}
#key::placeholder{color:var(--muted)}
button:hover{filter:brightness(1.08)}
button:active{transform:translateY(1px);box-shadow:var(--pressed)}
.skinbar{margin-left:auto;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.skinbar em{font-style:normal;font-size:11px;opacity:.7;margin-right:2px}
.skinbtn{font-size:12px;font-weight:600;padding:5px 11px;line-height:1.35}
.skinbtn.on{background:var(--accent);color:var(--accentfg);box-shadow:var(--lit),var(--bevel)}
#msg{display:none;font-size:12px;font-weight:600;padding:7px 12px;border-radius:var(--rad);
 background:var(--warn);color:var(--warnfg);border:1px solid var(--warnedge);box-shadow:var(--drop),var(--bevel)}
#msg:not(:empty){display:block}
td.empty{text-align:center;font-weight:600;padding:16px;color:var(--muted);background:var(--empty)}
.side{display:contents}
::-webkit-scrollbar{width:15px;height:15px}
::-webkit-scrollbar-track{background:var(--track);border-radius:8px;box-shadow:var(--inset)}
::-webkit-scrollbar-thumb{background:var(--thumb);border:1px solid var(--edge);border-radius:8px;box-shadow:var(--bevel)}
::-webkit-scrollbar-thumb:active{background:var(--accent)}
@media(max-width:780px){.skinbar{width:100%;margin-left:0}header{margin:10px 8px 0}}
"""
_A = """
body.skin-a{
 --bg:radial-gradient(1300px 700px at 18% -12%,#2f68b4 0%,#14305a 42%,#070f1e 100%),#0a1526;
 --fg:#eaf3ff;--h2:#bcdcff;--muted:#9fc2e8;--num:#dbeeff;
 --glass:linear-gradient(180deg,rgba(255,255,255,.18),rgba(122,178,255,.11) 44%,rgba(6,16,34,.34));
 --edge:rgba(190,226,255,.5);--rad:12px;--blur:14px;--sat:170%;
 --drop:0 10px 28px rgba(0,8,26,.55);--bevel:inset 0 1px 0 rgba(255,255,255,.72),inset 0 -1px 0 rgba(120,180,255,.32);
 --inset:inset 0 2px 7px rgba(0,10,30,.6);--glow:0 0 14px rgba(120,190,255,.75);
 --th:linear-gradient(180deg,rgba(164,214,255,.32),rgba(60,120,200,.14));--thfg:#dceeff;
 --line:rgba(150,200,255,.18);--hover:rgba(140,200,255,.15);--well:rgba(4,14,32,.55);
 --btn:linear-gradient(180deg,rgba(255,255,255,.9),rgba(192,226,255,.5) 47%,rgba(70,132,212,.55));
 --btnedge:rgba(212,236,255,.85);--btnsh:0 3px 9px rgba(0,10,30,.55),inset 0 1px 0 #fff,inset 0 -2px 4px rgba(0,30,70,.25);
 --pressed:inset 0 3px 9px rgba(0,20,55,.7);
 --accent:linear-gradient(180deg,#93d2ff,#1f6fc4);--accentfg:#fff;--lit:0 0 16px rgba(90,180,255,.95);
 --warn:linear-gradient(180deg,rgba(255,214,124,.92),rgba(188,110,20,.88));--warnfg:#2a1600;--warnedge:#ffd98a;
 --empty:linear-gradient(180deg,rgba(255,255,255,.06),rgba(0,0,0,.12));
 --track:linear-gradient(180deg,rgba(8,22,46,.75),rgba(0,8,22,.8));--thumb:linear-gradient(180deg,#c6e6ff,#3f86d6)}
body.skin-a header{box-shadow:var(--drop),var(--bevel),0 0 30px rgba(70,150,255,.3)}
body.skin-a .card b{font-size:24px}
"""
_B = """
body.skin-b{
 --bg:radial-gradient(900px 500px at 85% -10%,rgba(120,180,255,.35),transparent 60%),
     radial-gradient(700px 420px at 5% 10%,rgba(200,160,255,.22),transparent 60%),
     linear-gradient(180deg,#eef1f5,#dfe4ea 60%,#d6dce4);
 --fg:#1b2028;--h2:#3a4658;--muted:#6b7686;--num:#0b57a4;
 --glass:linear-gradient(180deg,rgba(255,255,255,.72),rgba(240,244,250,.58));
 --edge:rgba(255,255,255,.85);--rad:9px;--blur:26px;--sat:130%;
 --drop:0 8px 22px rgba(20,30,55,.14),0 1px 2px rgba(20,30,55,.1);
 --bevel:inset 0 1px 0 rgba(255,255,255,.95),inset 0 0 0 1px rgba(255,255,255,.35);
 --inset:inset 0 2px 6px rgba(60,75,100,.16);--glow:none;
 --th:linear-gradient(180deg,rgba(255,255,255,.85),rgba(228,235,245,.7));--thfg:#2b3546;
 --line:rgba(120,140,170,.22);--hover:rgba(0,90,180,.07);--well:rgba(255,255,255,.78);
 --btn:linear-gradient(180deg,#fdfdfe,#e6ebf2);--btnedge:rgba(140,160,190,.5);
 --btnsh:0 2px 6px rgba(20,30,55,.14),inset 0 1px 0 #fff;
 --pressed:inset 0 3px 8px rgba(60,80,110,.28);
 --accent:linear-gradient(180deg,#3b9ae8,#0067c9);--accentfg:#fff;--lit:0 3px 10px rgba(0,103,201,.35);
 --warn:linear-gradient(180deg,#fff4d6,#ffe2a8);--warnfg:#7a4a00;--warnedge:#e8bf6a;
 --empty:linear-gradient(180deg,rgba(255,255,255,.6),rgba(225,232,242,.5));
 --track:rgba(210,218,230,.6);--thumb:linear-gradient(180deg,#f6f8fb,#b9c6d8)}
body.skin-b header{border-bottom-color:rgba(255,255,255,.9)}
body.skin-b .card{border-left:3px solid rgba(0,103,201,.55)}
"""
_C = """
body.skin-c{
 --bg:linear-gradient(180deg,#2a2e33,#1b1e22 55%,#14171a),#171a1d;
 --fg:#e8ecef;--h2:#c9d4dd;--muted:#98a3ad;--num:#ffd98a;
 --glass:linear-gradient(180deg,rgba(150,160,170,.30),rgba(70,78,86,.34) 40%,rgba(30,34,38,.46)),
   repeating-linear-gradient(90deg,rgba(255,255,255,.055) 0 1px,rgba(0,0,0,.06) 1px 3px);
 --edge:rgba(226,234,240,.42);--rad:6px;--blur:9px;--sat:120%;
 --drop:0 10px 24px rgba(0,0,0,.6),0 2px 0 rgba(255,255,255,.12);
 --bevel:inset 0 1px 0 rgba(255,255,255,.55),inset 0 -2px 4px rgba(0,0,0,.5),inset 0 0 0 1px rgba(0,0,0,.35);
 --inset:inset 0 3px 9px rgba(0,0,0,.7),inset 0 0 0 1px rgba(255,255,255,.12);--glow:0 0 8px rgba(255,200,110,.35);
 --th:linear-gradient(180deg,rgba(210,220,230,.26),rgba(60,68,76,.4));--thfg:#dfe8ef;
 --line:rgba(220,230,240,.14);--hover:rgba(255,214,138,.1);--well:rgba(6,10,14,.72);
 --btn:linear-gradient(180deg,#dfe4e9,#a8b1ba 48%,#7b848d 52%,#c3ccd4);--btnedge:rgba(20,24,28,.8);
 --btnsh:0 3px 7px rgba(0,0,0,.6),inset 0 1px 0 #fff,inset 0 -2px 3px rgba(0,0,0,.35);
 --pressed:inset 0 4px 10px rgba(0,0,0,.7);
"""
_C += """
 --accent:linear-gradient(180deg,#ffd98a,#c98b1f);--accentfg:#241703;
 --lit:0 0 14px rgba(255,200,90,.7),inset 0 1px 0 rgba(255,255,255,.7);
 --warn:linear-gradient(180deg,#ffd28a,#b96f14);--warnfg:#2b1600;--warnedge:#7a4a08;
 --empty:linear-gradient(180deg,rgba(255,255,255,.05),rgba(0,0,0,.25));
 --track:linear-gradient(180deg,#101316,#22262b);--thumb:linear-gradient(180deg,#e2e7ec,#8b949d 50%,#6a7178)}
body.skin-c input,body.skin-c button{color:#161a1e;text-shadow:0 1px 0 rgba(255,255,255,.55)}
body.skin-c #key{color:#ffe9b8;text-shadow:none}
body.skin-c header{border:1px solid rgba(226,234,240,.5);border-top-color:rgba(255,255,255,.75)}
body.skin-c .card,body.skin-c table,body.skin-c header,body.skin-c pre{position:relative}
body.skin-c .card::before,body.skin-c table::before,body.skin-c header::before{
 content:"";position:absolute;left:5px;top:5px;width:7px;height:7px;border-radius:50%;
 background:radial-gradient(circle at 30% 30%,#f2f6f9,#6b737b 60%,#23282d);box-shadow:inset 0 -1px 1px rgba(0,0,0,.6)}
body.skin-c .card::after,body.skin-c table::after,body.skin-c header::after{
 content:"";position:absolute;right:5px;bottom:5px;width:7px;height:7px;border-radius:50%;
 background:radial-gradient(circle at 30% 30%,#f2f6f9,#6b737b 60%,#23282d);box-shadow:inset 0 -1px 1px rgba(0,0,0,.6)}
body.skin-c .skinbtn{border-width:2px}
"""
_D = """
body.skin-d{
 --bg:linear-gradient(180deg,#f7f8fa,#eef0f4);
 --fg:#1f2328;--h2:#55606d;--muted:#8a94a2;--num:#0f6b4f;
 --glass:linear-gradient(180deg,rgba(255,255,255,.92),rgba(248,250,253,.8));
 --edge:rgba(255,255,255,.95);--rad:12px;--blur:18px;--sat:120%;
 --drop:0 6px 18px rgba(25,35,55,.09),0 1px 2px rgba(25,35,55,.07);
 --bevel:inset 0 1px 0 #fff,inset 0 0 0 1px rgba(180,195,215,.28);
 --inset:inset 0 2px 5px rgba(70,85,110,.12);--glow:none;
 --th:linear-gradient(180deg,#fbfcfe,#eef1f6);--thfg:#3a4453;
 --line:rgba(150,165,185,.2);--hover:rgba(15,107,79,.06);--well:#fff;
 --btn:linear-gradient(180deg,#ffffff,#eef1f5);--btnedge:rgba(150,165,185,.45);
 --btnsh:0 1px 3px rgba(25,35,55,.12),inset 0 1px 0 #fff;
 --pressed:inset 0 3px 7px rgba(70,85,110,.22);
 --accent:linear-gradient(180deg,#25b384,#0f6b4f);--accentfg:#fff;--lit:0 3px 10px rgba(15,107,79,.3);
 --warn:linear-gradient(180deg,#fff2f0,#ffd8d3);--warnfg:#8a2b18;--warnedge:#f0b3a6;
 --empty:linear-gradient(180deg,#fbfcfd,#f1f4f8);
 --track:#eef1f5;--thumb:linear-gradient(180deg,#f4f6f9,#c3ccd8)}
body.skin-d main{grid-template-columns:290px 1fr;gap:18px;align-items:start}
body.skin-d .side{display:block;position:sticky;top:12px}
body.skin-d .side .cards{flex-direction:column;gap:8px}
body.skin-d .card{min-width:0;display:flex;justify-content:space-between;align-items:baseline;gap:8px}
body.skin-d .card b{font-size:19px}
body.skin-d header{margin:10px 12px 0;border-radius:14px}
body.skin-d h2{margin-top:14px}
"""

CSS = _BASE + _A + _B + _C + _D
JS = """
const K=()=>document.getElementById('key').value.trim();
const hdr=()=>K()?{Authorization:'Bearer '+K()}:{};
const L={alias:'别名',calls:'调用数',tokens:'词元数',cost:'费用',provider:'提供方',key:'密钥',
 ok:'可用',fails:'失败数',cooldown_left:'冷却剩余',ts:'时间',status:'状态',ms:'耗时',
 total:'总词元',stream:'流式',d:'日期',errors:'错误数'};
const th=c=>L[c]||c;
function tbl(id,rows,cols){const t=document.getElementById(id);
 if(!rows||!rows.length){t.innerHTML='<tr><td class=empty colspan='+cols.length+'>暂无数据 · 等待网关产生记录</td></tr>';return}
 t.innerHTML='<tr>'+cols.map(c=>'<th>'+th(c)+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+
  cols.map(c=>'<td>'+(r[c]==null?'-':r[c])+'</td>').join('')+'</tr>').join('')}
async function j(u){const r=await fetch(u,{headers:hdr()});
 if(!r.ok)throw new Error(u+' -> '+r.status);return r.json()}
async function refresh(){const m=document.getElementById('msg');m.textContent='';
 try{const [s,p,mods]=await Promise.all([j('/stats'),j('/pool'),j('/v1/models')]);
  document.getElementById('calls').textContent=s.calls;
  document.getElementById('tokens').textContent=s.tokens;
  document.getElementById('cost').textContent=(+s.cost||0).toFixed(4)+' '+(s.currency||'');
  document.getElementById('slots').textContent=p.slots.length;
  document.getElementById('models').textContent=mods.data.length;
  tbl('bymodel',s.by_model,['alias','calls','tokens','cost']);
  tbl('byprov',s.by_provider,['provider','calls','tokens','cost','avg_ms','errors']);
  tbl('pool',p.slots,['provider','key','ok','fails','cooldown_left']);
  tbl('recent',s.recent.map(r=>({...r,ts:new Date(r.ts*1000).toLocaleTimeString()})),
      ['ts','alias','provider','status','ms','total','cost','stream']);
  try{const u=await j('/v1/usage?days=14');
    tbl('daily',u.daily,['d','calls','tokens','cost','errors']);
  }catch(e){tbl('daily',[],['d','calls','tokens','cost','errors']);}
  document.getElementById('strategy').textContent=p.strategy;
 }catch(e){m.textContent='数据加载失败 · '+e.message}}
refresh();
const SKIN_KEY='llm-router.skin';
function setSkin(k){if(!/[abcd]/.test(k))k='a';
 document.body.className='skin-'+k;
 try{localStorage.setItem(SKIN_KEY,k)}catch(e){}
 document.querySelectorAll('.skinbtn').forEach(b=>b.classList.toggle('on',b.dataset.k===k))}
(function(){let k='a';try{k=localStorage.getItem(SKIN_KEY)||'a'}catch(e){}setSkin(k)})();
"""
BODY = """
<body class="skin-a">
<header>
<h1>llm-router 控制台</h1>
<input id="key" type="password" size="22" placeholder="主密钥（可选）">
<button onclick="refresh()">刷新</button>
<span>调度策略：<b id="strategy">-</b></span>
<div class="skinbar"><em>界面皮肤</em>
<button class="skinbtn" data-k="a" onclick="setSkin('a')">A · Aero 玻璃</button>
<button class="skinbtn" data-k="b" onclick="setSkin('b')">B · Fluent 云母</button>
<button class="skinbtn" data-k="c" onclick="setSkin('c')">C · 金属玻璃</button>
<button class="skinbtn" data-k="d" onclick="setSkin('d')">D · 简洁双栏</button>
</div>
<span id="msg"></span>
</header>
<main>
<div class="side"><div class="cards">
<div class="card"><b id="calls">-</b><i>调用总数</i></div>
<div class="card"><b id="tokens">-</b><i>词元总量</i></div>
<div class="card"><b id="cost">-</b><i>累计费用</i></div>
<div class="card"><b id="slots">-</b><i>密钥槽位</i></div>
<div class="card"><b id="models">-</b><i>可用模型</i></div>
</div></div>
<div class="content">
<h2>按模型统计</h2><table id="bymodel"></table>
<h2>按提供方统计</h2><table id="byprov"></table>
<h2>密钥池状态</h2><table id="pool"></table>
<h2>近 14 天用量</h2><table id="daily"></table>
<h2>最近调用记录</h2><table id="recent"></table>
<h2>客户端配置</h2><pre id="hint"></pre>
</div>
</main>
<script>document.getElementById('hint').textContent =
 'base_url = ' + location.origin + '/v1\\nmodel    = <取自 /v1/models 的别名>';</script>
"""
PAGE = ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>llm-router 控制台</title><style>" + CSS + "</style></head>"
        + BODY + "<script>" + JS + "</script></body></html>")
