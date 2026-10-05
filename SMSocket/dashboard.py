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

_NAV = """
.nav{display:flex;gap:6px;flex-wrap:wrap}
.navbtn{font-size:13px;font-weight:600;padding:6px 13px}
.navbtn.on{background:var(--accent);color:var(--accentfg);box-shadow:var(--lit),var(--bevel)}
.page{display:none}
.page.on{display:block}
.cred{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px}
.cred em{font-style:normal;opacity:.7}
.cred code{font:12px/1.5 ui-monospace,Consolas,monospace;padding:3px 8px;border-radius:var(--rad);
 background:var(--well);border:1px solid var(--edge);box-shadow:var(--inset);word-break:break-all}
"""
_NAV += """
.btns{display:flex;gap:6px;align-items:end;flex-wrap:wrap}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;align-items:end;
 background:var(--glass);border:1px solid var(--edge);border-radius:var(--rad);padding:12px;
 box-shadow:var(--drop),var(--bevel)}
label{display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600;color:var(--h2)}
label input,label select,label textarea{font:inherit;font-weight:400;color:var(--fg)}
label.wide{grid-column:1/-1}
textarea{font:12px/1.5 ui-monospace,Consolas,monospace;padding:6px 8px;background:var(--well);
 color:var(--fg);border:1px solid var(--edge);border-radius:var(--rad);box-shadow:var(--inset);resize:vertical}
td button{font-size:11px;padding:3px 8px;margin-right:4px}
@media(max-width:780px){.cred{width:100%}.nav{width:100%}}
"""
_NET = """
/* discreet net plane: footer glyph + slide-over panel, skin-agnostic (vars only) */
.netglyph{position:fixed;right:12px;bottom:10px;z-index:40;font-size:11px;opacity:.45;
 background:transparent;border:0;color:var(--fg);cursor:pointer;padding:3px 6px}
.netglyph:hover{opacity:.95}
.netglyph.off{display:none}
.hide{display:none}
.reach td{padding:2px 6px;font-size:12px}
.v-healthy{background:rgba(46,160,67,.22)}.v-slow{background:rgba(212,160,23,.22)}
.v-blocked{background:rgba(226,68,68,.26)}.v-unstable{background:rgba(120,140,255,.22)}
.v-unknown{opacity:.5}
.netmask{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:41;display:none}
.netmask.on{display:block}
.netpanel{position:fixed;top:0;right:0;bottom:0;width:min(430px,94vw);z-index:42;
 background:var(--bg);border-left:1px solid var(--edge);box-shadow:var(--drop);
 padding:12px 14px;overflow:auto;transform:translateX(100%);transition:transform .18s ease}
.netpanel.on{transform:translateX(0)}
.netpanel h2{margin:0 0 8px;font-size:15px}
.netrow{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:6px 0}
.netpanel table{width:100%;font-size:12px}
.netpanel .muted{opacity:.65;font-size:11px}
"""
CSS = _BASE + _A + _B + _C + _D + _NAV + _NET
JS = """
const K=()=>document.getElementById('key').value.trim();
const hdr=()=>K()?{Authorization:'Bearer '+K()}:{};
const L={alias:'别名',calls:'调用数',tokens:'词元数',cost:'费用',provider:'提供方',key:'密钥',
 ok:'可用',fails:'失败数',cooldown_left:'冷却剩余',ts:'时间',status:'状态',ms:'耗时',
 total:'总词元',stream:'流式',d:'日期',errors:'错误数'};
const th=c=>L[c]||c;
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const gv=id=>document.getElementById(id).value.trim();
function toast(s){document.getElementById('msg').textContent=s||''}
function tbl(id,rows,cols){const t=document.getElementById(id);
 if(!rows||!rows.length){t.innerHTML='<tr><td class=empty colspan='+cols.length+'>暂无数据 · 等待网关产生记录</td></tr>';return}
 t.innerHTML='<tr>'+cols.map(c=>'<th>'+th(c)+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+
  cols.map(c=>'<td>'+esc(r[c]==null?'-':r[c])+'</td>').join('')+'</tr>').join('')}
async function j(u){const r=await fetch(u,{headers:hdr()});
 if(!r.ok)throw new Error(u+' -> '+r.status);return r.json()}
async function send(u,m,b){const r=await fetch(u,{method:m,headers:Object.assign(
 {'Content-Type':'application/json'},hdr()),body:JSON.stringify(b||{})});
 let d={};try{d=await r.json()}catch(e){}
 if(!r.ok){const e=d&&d.detail;const msg=(e&&e.error&&e.error.message)||(typeof e==='string'?e:u+' -> '+r.status);
  throw new Error(msg)}return d}
"""
JS += """
const PAGES=['overview','usage','providers','socket','discover','settings'];
const PKEY='SMSocket.page';
function go(p){if(PAGES.indexOf(p)<0)p='overview';
 document.querySelectorAll('.page').forEach(el=>el.classList.toggle('on',el.id==='page-'+p));
 document.querySelectorAll('.navbtn').forEach(b=>b.classList.toggle('on',b.dataset.p===p));
 try{localStorage.setItem(PKEY,p)}catch(e){}
 if(location.hash!=='#'+p)try{history.replaceState(null,'','#'+p)}catch(e){}
 if(p==='providers')loadConfig();
 if(p==='socket'){loadSocket();renderStack({});loadReach();tickSocket()}
 if(p==='discover'){loadProvidersForProbe();loadCatalog()}

 if(p==='settings'){loadSelf();loadSettings()}}
let SELF={},REVEAL=false;
function paintSelf(){document.getElementById('baseurl').textContent=SELF.base_url||'-';
 document.getElementById('apikey').textContent=REVEAL?(SELF.key||''):(SELF.key_masked||'-');
 document.getElementById('reveal').textContent=REVEAL?'隐藏':'显示';
 document.getElementById('modelcount').textContent=(SELF.models||[]).length;
 const h=document.getElementById('hint');
 if(h)h.textContent='base_url = '+(SELF.base_url||location.origin+'/v1')+
  String.fromCharCode(10)+'model    = <取自 /v1/models 的别名>'+
  String.fromCharCode(10)+'api_key  = '+(REVEAL?(SELF.key||''):(SELF.key_masked||''))+
  String.fromCharCode(10)+'密钥文件 = '+(SELF.key_file||'-')}
async function loadSelf(){try{SELF=await j('/admin/self');REVEAL=false;paintSelf()}
 catch(e){SELF={};document.getElementById('baseurl').textContent='需主密钥';
  document.getElementById('apikey').textContent='需主密钥'}}
async function revealKey(){try{SELF=await j('/admin/self?reveal=1');REVEAL=true;paintSelf();
 toast('已显示明文密钥，请勿外泄')}catch(e){toast('取明文失败 · '+e.message)}}
function copyText(txt,tag){if(!txt||txt==='-'){toast('没有可复制的内容');return}
 const done=()=>toast('已复制'+tag);
 if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(done,
  ()=>fallback(txt,done))}else fallback(txt,done)}
function fallback(txt,done){const ta=document.createElement('textarea');ta.value=txt;
 ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();
 let ok=false;try{ok=document.execCommand('copy')}catch(e){}
 document.body.removeChild(ta);ok?done():toast('浏览器拒绝复制，请手动选取')}
"""
JS += """
async function refresh(){toast('');
 try{const [s,p,mods,x]=await Promise.all([j('/stats'),j('/pool'),j('/v1/models'),j('/concurrency')]);
  document.getElementById('calls').textContent=s.calls;
  document.getElementById('tokens').textContent=s.tokens;
  document.getElementById('cost').textContent=(+s.cost||0).toFixed(4)+' '+(s.currency||'');
  document.getElementById('slots').textContent=p.slots.length;
  document.getElementById('models').textContent=mods.data.length;
  document.getElementById('ucalls').textContent=s.calls;
  document.getElementById('utokens').textContent=s.tokens;
  document.getElementById('ucost').textContent=(+s.cost||0).toFixed(4)+' '+(s.currency||'');
  tbl('bymodel',s.by_model,['alias','calls','tokens','cost']);
  tbl('pool',p.slots,['provider','key','ok','fails','cooldown_left']);
  tbl('byprov',s.by_provider,['provider','calls','tokens','cost','avg_ms','errors']);
  tbl('recent',s.recent.map(r=>({...r,ts:new Date(r.ts*1000).toLocaleTimeString(),
   stream:r.stream?'是':'否'})),['ts','alias','provider','status','ms','total','cost','stream']);
  try{const u=await j('/v1/usage?days=14');tbl('daily',u.daily,['d','calls','tokens','cost','errors'])}
  catch(e){tbl('daily',[],['d','calls','tokens','cost','errors'])}
  document.getElementById('xactive').textContent=x.active;
  document.getElementById('xpeak').textContent=x.peak;
  document.getElementById('xcurrency').textContent=(s.currency||'-');
  document.getElementById('strategy').textContent=p.strategy;
  toast('已刷新 · '+new Date().toLocaleTimeString())
 }catch(e){toast('数据加载失败 · '+e.message)}}
"""
JS += """
let CFG={providers:[]},EDIT='';
function provRow(p){const b=(a,t)=>'<button data-a='+a+'>'+t+'</button>';
 return '<tr data-n='+esc(p.name)+'><td><b>'+esc(p.name)+'</b></td><td>'+esc(p.base_url)+
  '</td><td>'+esc(p.style)+'</td><td>'+keyCell(p)+
  '</td><td>'+esc(p.priority)+' / '+esc(p.weight)+'</td><td>'+esc(p.timeout)+' / '+esc(p.max_rpm||0)+
  '</td><td>'+(p.enabled===false?'停用':'启用')+'</td><td>'+b('edit','编辑')+b('key','加密钥')+
  b('del','删除')+'</td></tr>'}
async function loadConfig(){try{const [c,p]=await Promise.all([j('/admin/config'),j('/pool')]);
  CFG=c;const rows=c.providers||[];
  const hd='<tr><th>名称</th><th>基础URL</th><th>协议</th><th>密钥（已掩码）</th><th>优先级/权重</th>'+
   '<th>超时/RPM</th><th>状态</th><th>操作</th></tr>';
  document.getElementById('provs').innerHTML=hd+
   (rows.length?rows.map(provRow).join(''):'<tr><td class=empty colspan=8>暂无提供方</td></tr>');
  tbl('poolprov',p.slots,['provider','key','ok','fails','cooldown_left']);
 }catch(e){toast('配置加载失败 · '+e.message)}}
function mapParse(s){const o={};String(s||'').split(/[,;]+/).forEach(t=>{const i=t.indexOf('=');
 if(i>0)o[t.slice(0,i).trim()]=t.slice(i+1).trim()});return o}
const lines=s=>String(s||'').split(String.fromCharCode(10)).map(x=>x.trim()).filter(Boolean);
function formBody(){return {name:gv('p_name'),base_url:gv('p_url'),style:gv('p_style'),
 models:mapParse(gv('p_models')),embeddings:mapParse(gv('p_emb')),
 priority:+gv('p_prio')||0,weight:+gv('p_w')||1,timeout:+gv('p_to')||120,max_rpm:+gv('p_rpm')||0,
 enabled:document.getElementById('p_on').checked}}
function fillForm(p){['name|p_name','base_url|p_url','style|p_style'].forEach(x=>{const [k,id]=x.split('|');
 document.getElementById(id).value=p[k]||''});
 document.getElementById('p_models').value=Object.keys(p.models||{}).map(k=>k+'='+p.models[k]).join(',');
 document.getElementById('p_emb').value=Object.keys(p.embeddings||{}).map(k=>k+'='+p.embeddings[k]).join(',');
 document.getElementById('p_prio').value=p.priority;document.getElementById('p_w').value=p.weight;
 document.getElementById('p_to').value=p.timeout;document.getElementById('p_rpm').value=p.max_rpm||0;
 document.getElementById('p_on').checked=p.enabled!==false;document.getElementById('p_keys').value=''}
function cancelEdit(){EDIT='';document.getElementById('pf').reset();
 document.getElementById('p_on').checked=true;
 document.getElementById('p_submit').textContent='新增提供方';
 document.getElementById('p_note').textContent=''}
async function submitP(){const b=formBody();
 if(!b.name||!b.base_url){toast('名称与基础URL必填');return}
 try{if(EDIT){await send('/admin/providers/'+encodeURIComponent(EDIT),'PATCH',b);
   toast('已更新提供方 '+EDIT);cancelEdit()}
  else{b.keys=lines(gv('p_keys'));await send('/admin/providers','POST',b);
   toast('已新增提供方 '+b.name);cancelEdit()}
  await loadConfig()}catch(e){toast('保存失败 · '+e.message)}}
"""
JS += """
function keyCell(p){const ks=p.keys||[];
 return '<b>'+ks.length+'</b> 把 '+ks.map((k,i)=>'<span>'+esc(k)+
  '<button data-a=delkey data-i='+i+'>×</button></span>').join(' ')}
async function addKeyTo(n){const v=window.prompt('为提供方 '+n+' 追加一把 API Key（sk-…）：');
 if(!v)return;try{await send('/admin/providers/'+encodeURIComponent(n)+'/keys','POST',{key:v.trim()});
  toast('已为 '+n+' 追加密钥');await loadConfig()}catch(e){toast('追加失败 · '+e.message)}}
async function delKey(n,i){try{await send('/admin/providers/'+encodeURIComponent(n)+'/keys/'+i,'DELETE');
  toast('已删除 '+n+' 的第 '+(+i+1)+' 把密钥');await loadConfig()}catch(e){toast('删除密钥失败 · '+e.message)}}
async function delProv(n){if(!window.confirm('确认删除提供方 '+n+' ？此操作写回 config.yaml'))return;
 try{await send('/admin/providers/'+encodeURIComponent(n),'DELETE');toast('已删除提供方 '+n);
  if(EDIT===n)cancelEdit();await loadConfig()}catch(e){toast('删除失败 · '+e.message)}}
function editProv(n){const p=(CFG.providers||[]).filter(x=>x.name===n)[0];if(!p)return;
 EDIT=n;fillForm(p);document.getElementById('p_submit').textContent='保存修改';
 document.getElementById('p_note').textContent='正在编辑：'+n+'（密钥请用下方按钮增删）';
 document.getElementById('pf').scrollIntoView({block:'center'})}
async function saveGlobals(){const body={listen:gv('g_listen'),strategy:gv('g_strategy'),
 retry:+gv('g_retry')||0,cooldown:+gv('g_cd')||0,currency:gv('g_cur'),
 max_concurrency:+gv('g_maxc')||0,queue_wait:+gv('g_qw')||0,
 per_provider_concurrency:+gv('g_ppc')||0};
 const pr={};lines(gv('g_pricing')).forEach(t=>{const a=t.split('=');if(a.length===2){
  const v=(a[1]||'').split('/');pr[a[0].trim()]={prompt:+v[0]||0,completion:+(v[1]||0)||0}}});
 body.pricing=pr;
 try{const d=await send('/admin/config','PUT',body);CFG=d.config||CFG;
  document.getElementById('strategy').textContent=body.strategy;
  toast('全局设置已保存并热加载 · '+JSON.stringify(d.applied))}catch(e){toast('保存失败 · '+e.message)}}
"""
JS += """
const SKIN_KEY='SMSocket.skin';
function setSkin(k){if(!/[abcd]/.test(k))k='a';
 document.body.className='skin-'+k;
 try{localStorage.setItem(SKIN_KEY,k)}catch(e){}
 document.querySelectorAll('.skinbtn').forEach(b=>b.classList.toggle('on',b.dataset.k===k))}
async function loadSettings(){try{const c=await j('/admin/config');CFG=c;
 document.getElementById('g_listen').value=c.listen||'';
 document.getElementById('g_strategy').value=c.strategy||'priority';
 document.getElementById('g_retry').value=c.retry;
 document.getElementById('g_cd').value=c.cooldown;
 document.getElementById('g_cur').value=c.currency||'USD';
 document.getElementById('g_maxc').value=c.max_concurrency||0;
 document.getElementById('g_qw').value=c.queue_wait==null?30:c.queue_wait;
 document.getElementById('g_ppc').value=c.per_provider_concurrency||0;
 fillBilling(c.billing||{});
 document.getElementById('g_pricing').value=Object.keys(c.pricing||{}).map(k=>{
  const r=c.pricing[k]||{};return k+'='+r.prompt+'/'+r.completion}).join(String.fromCharCode(10));
 document.getElementById('g_path').textContent=c.path||'-';
 document.getElementById('g_slots').textContent=c.slots;
 document.getElementById('g_models').textContent=(c.models||[]).join(', ')||'无';
 }catch(e){toast('设置加载失败 · '+e.message)}}
document.addEventListener('click',ev=>{const b=ev.target.closest('button[data-a]');
 if(!b)return;const tr=b.closest('tr[data-n]');if(!tr)return;const n=tr.dataset.n;
 const a=b.dataset.a;
 if(a==='edit')editProv(n);
 else if(a==='key')addKeyTo(n);
 else if(a==='del')delProv(n);
 else if(a==='delkey')delKey(n,b.dataset.i)});
"""
JS += """
// ---- 并发与批量 ----------------------------------------------------------
let AUTO=false,TMR=null,LASTJOB='';
function num(x,d){return (x==null||x===''||isNaN(x))?(d===undefined?'-':d):x}
async function loadSocket(){try{const x=await j('/concurrency');
 [['active','s_active'],['queued','s_queued'],['peak','s_peak'],['total','s_total'],
  ['rejected','s_rej'],['rps','s_rps'],['errors','s_err'],['avg_wait_ms','s_wait']]
  .forEach(([k,id])=>{const el=document.getElementById(id);if(el)el.textContent=num(x[k],0)});
 document.getElementById('s_max').textContent=x.max_concurrency||'不限';
 document.getElementById('s_ppc').textContent=x.per_provider_concurrency||'不限';
 document.getElementById('s_qw').textContent=(x.queue_wait||0)+'s';
 document.getElementById('s_sat').textContent=x.saturated?'已饱和（新请求排队/429）':'有余量';
 renderStack(x);
 const rows=x.by_provider||{},keys=Object.keys(rows),bp=document.getElementById('sprov');
 bp.innerHTML=keys.length?('<tr><th>提供方</th><th>在途</th><th>峰值</th><th>累计</th><th>失败</th></tr>'+
  keys.map(k=>'<tr><td><b>'+esc(k)+'</b></td><td>'+rows[k].active+'</td><td>'+rows[k].peak+
   '</td><td>'+rows[k].total+'</td><td>'+rows[k].errors+'</td></tr>').join(''))
  :'<tr><td class=empty colspan=5>暂无在途数据</td></tr>';
 }catch(e){toast('并发数据加载失败 · '+e.message)}}
const ASSESS_ON=__ASSESS_ON__;
function renderStack(x){const st=(x&&x.stack)||null,blk=document.getElementById('stackblk');
 if(!blk)return; if(!st||!st.enabled){blk.classList.add('hide');return}
 blk.classList.remove('hide');
 const set=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v};
 set('k_policy',st.policy);set('k_depth',st.depth);set('k_peak',st.peak);
 set('k_popped',st.popped);set('k_expired',st.expired);
 set('k_avg',num(st.avg_parked_ms,0));set('k_pmax',num(st.parked_ms_peak,0));
 const es=st.entries||[],t=document.getElementById('kentries');
 t.innerHTML=es.length?('<tr><th>别名</th><th>提供方</th><th>原因</th><th>已等待ms</th></tr>'+
  es.map(e=>'<tr><td><b>'+esc(e.alias||'-')+'</b></td><td>'+esc(e.provider||'-')+
   '</td><td>'+esc(e.reason)+'</td><td>'+e.waited_ms+'</td></tr>').join(''))
  :'<tr><td class=empty colspan=4>栈内没有等待中的请求</td></tr>'}
async function loadReach(){const blk=document.getElementById('reachblk');
 if(!blk||!ASSESS_ON){return}
 try{const d=await j('/assess'),rows=d.rows||[];
  blk.classList.remove('hide');
  document.getElementById('r_window').textContent=Math.round((d.window_s||0)/60)+' 分钟';
  document.getElementById('r_rows').textContent=(d.counts||{}).rows||0;
  const eg=[...new Set(rows.map(r=>r.egress))].sort();
  const cell=(m,e)=>rows.filter(r=>r.model===m&&r.egress===e)[0];
  const t=document.getElementById('reach');
  t.innerHTML=rows.length?('<tr><th>模型</th>'+eg.map(e=>'<th>'+esc(e)+'</th>').join('')+
   '</tr>'+[...new Set(rows.map(r=>r.model))].sort().map(m=>'<tr><td><b>'+esc(m)+'</b>'+
   eg.map(e=>{const c=cell(m,e);if(!c)return '<td class=v-unknown>-</td>';
    return '<td class=v-'+c.verdict+' title="p50 '+c.p50_ms+'ms · 成功率 '+
     Math.round(c.ok_ratio*100)+'% · '+(c.last_error||'ok')+'">'+
     c.p50_ms+'ms</td>'}).join('')+'</tr>').join(''))
   :'<tr><td class=empty colspan=6>暂无评估数据（等待定时探测或点“立即探测”）</td></tr>';
  try{const st=await j('/assess/status');
   const nx=st.next_run?new Date(st.next_run*1000).toLocaleTimeString():'未排程';
   document.getElementById('r_next').textContent=nx}catch(e){}}
 catch(e){blk.classList.add('hide')}}
async function runReach(){try{toast('探测中…');const d=await send('/assess/run','POST',
 {async:true});toast(d.accepted?'已排入探测':'探测未启动：'+(d.reason||''))}
 catch(e){toast('探测失败 · '+e.message)}}
function tickSocket(){if(!AUTO)return;loadSocket();TMR=setTimeout(tickSocket,1000)}
function toggleAuto(){AUTO=!AUTO;document.getElementById('s_auto').textContent='自动刷新：'+(AUTO?'开':'关');
 clearTimeout(TMR);if(AUTO)tickSocket()}
function batchItems(){return lines(gv('q_body')).map((ln,i)=>{
 if(ln[0]==='{'){try{const o=JSON.parse(ln);o.custom_id=o.custom_id||('c'+i);return o}catch(e){return null}}
 return {custom_id:'c'+i,model:(CFG.models||[])[0]||'demo',
  messages:[{role:'user',content:ln}]}}).filter(Boolean)}
function resRows(rs){return (rs||[]).map(r=>({index:r.index,custom_id:r.custom_id,model:r.model,
 status:r.status,ms:r.ms,tokens:(r.usage||{}).total_tokens||0,
 cost:r.cost?(+r.cost.display).toFixed(6)+' '+r.cost.display_currency:'-',
 err:(r.error||{}).message||(r.cancelled?'已取消':(r.skipped?'已跳过':''))}))}
async function runBatch(){const items=batchItems();
 if(!items.length){toast('请求列表为空（每行一条提示词或 JSON）');return}
 const body={requests:items,concurrency:+gv('q_conc')||8,fail_fast:gv('q_ff')==='1'};
 const note=document.getElementById('q_note');note.textContent='发送中 '+items.length+' 条…';
 try{
  if(gv('q_mode')==='async'){const d=await send('/v1/batch?async=1','POST',body);LASTJOB=d.job;
   note.textContent='作业已受理 '+d.job+' · 点「轮询作业」看进度';
   tbl('qres',[],['index','custom_id','model','status','ms','tokens','cost','err']);showJobs();return}
  const d=await send('/v1/batch','POST',body),sm=d.summary||{};
  note.textContent='完成：成功 '+sm.ok+' / 失败 '+sm.failed+' / 跳过 '+(sm.skipped||0)+
   ' · 词元 '+sm.tokens+' · 费用 '+(+sm.cost||0).toFixed(6)+' '+(sm.currency||'')+
   ' · 墙钟 '+sm.wall_ms+'ms（平均单条 '+sm.avg_ms+'ms）';
  tbl('qres',resRows(d.results),
   ['index','custom_id','model','status','ms','tokens','cost','err']);
 }catch(e){note.textContent='失败 · '+e.message}}
async function showJobs(){try{const d=await j('/v1/batches');
 tbl('qjobs',d.jobs.map(x=>({job:x.job,status:x.status,done:x.completed+' / '+x.total,
  failed:x.failed,concurrency:x.concurrency,elapsed:x.elapsed+'s'})),
  ['job','status','done','failed','concurrency','elapsed'])}catch(e){}}
async function pollJob(){if(!LASTJOB){toast('还没有异步作业，先用「异步作业」模式发送');return}
 try{const d=await j('/v1/batches/'+LASTJOB+'?wait=5');
  document.getElementById('q_note').textContent=d.status+' · '+d.completed+'/'+d.total+
   ' · 进度 '+(d.progress*100).toFixed(0)+'%';
  tbl('qres',resRows(d.results),
   ['index','custom_id','model','status','ms','tokens','cost','err']);
  showJobs()}catch(e){toast('轮询失败 · '+e.message)}}
"""
JS += """
// ---- 计费货币 ------------------------------------------------------------
function optList(id,arr,cur){const s=document.getElementById(id);if(!s)return;
 s.innerHTML=(arr||[]).map(c=>'<option value='+c+'>'+c+'</option>').join('');
 if(cur)s.value=cur}
function fillBilling(b){b=b||{};const cs=Object.keys(b.rates||{}).sort();
 optList('b_cur',cs,b.currency||'USD');optList('b_base',cs,b.base||'USD');
 optList('c_from',cs,'USD');optList('c_to',cs,b.currency||'USD');
 const g=(id,v)=>{const el=document.getElementById(id);if(el)el.value=v};
 g('b_prec',b.precision==null?6:b.precision);g('b_url',b.rates_url||'');
 g('b_rates',Object.keys(b.rates||{}).map(k=>k+'='+b.rates[k]).join(String.fromCharCode(10)))}
async function loadBilling(){try{const d=await j('/admin/billing');fillBilling(d.billing);
  const n=document.getElementById('b_note');
  if(n)n.textContent='当前显示币种 '+d.currency+' '+d.symbol}catch(e){toast('计费设置加载失败 · '+e.message)}}
async function saveBilling(){const rates={};
 lines(gv('b_rates')).forEach(x=>{const a=x.split('=');
  if(a.length===2)rates[a[0].trim().toUpperCase()]=+a[1]||0});
 const body={billing:{currency:gv('b_cur'),base:gv('b_base'),precision:+gv('b_prec')||6,
  rates_url:gv('b_url'),rates:rates}};
 try{const d=await send('/admin/billing','PUT',body);fillBilling(d.billing);
  document.getElementById('b_note').textContent='已保存 · 显示币种 '+d.billing.currency+
   ' · '+Object.keys(d.billing.rates).length+' 条汇率';refresh()}
 catch(e){document.getElementById('b_note').textContent='保存失败 · '+e.message}}
async function refreshRates(){try{const d=await send('/admin/billing/rates/refresh','POST',
  gv('b_url')?{url:gv('b_url')}:{});fillBilling(d.billing);
  document.getElementById('b_note').textContent='已更新 '+d.updated+' 条汇率（'+d.source+'）'}
 catch(e){document.getElementById('b_note').textContent='拉取失败 · '+e.message}}
async function convertTry(){try{const d=await j('/admin/billing/convert?amount='+
   encodeURIComponent(gv('c_amt')||1)+'&frm='+gv('c_from')+'&to='+gv('c_to'));
  document.getElementById('c_out').textContent=d.converted+' '+d.to+
   '（1 '+d.from+' = '+d.rate+' '+d.to+'）'}
 catch(e){document.getElementById('c_out').textContent='换算失败'}}
"""
JS += """
// ---- 模型探测 ------------------------------------------------------------
let DREP=[];
async function loadProvidersForProbe(){try{const c=await j('/admin/config');CFG=c;
 const s=document.getElementById('d_prov');
 s.innerHTML=(c.providers||[]).map(p=>'<option value='+esc(p.name)+'>'+esc(p.name)+
  '（'+(p.key_count||0)+' 密钥）</option>').join('')||'<option value=adhoc>无提供方</option>';
 }catch(e){toast('提供方加载失败 · '+e.message)}}
async function startDiscover(){const body={providers:[gv('d_prov')],
 concurrency:+gv('d_conc')||8,timeout:+gv('d_to')||30,max_models:+gv('d_max')||200,
 test_prompt:gv('d_prompt'),probe_params:gv('d_params')==='1',
 probe_stream:gv('d_stream')==='1',probe_embeddings:gv('d_emb')==='1',async:1};
 const ms=gv('d_models');if(ms)body.models=ms.split(/[,;\\s]+/).filter(Boolean);
 try{const d=await send('/admin/discover','POST',body);
  document.getElementById('d_note').textContent='探测已启动 · '+(d.providers||[]).join(',');
  pollDiscover()}catch(e){document.getElementById('d_note').textContent='启动失败 · '+e.message}}
async function pollDiscover(){try{const s=await j('/admin/discover/status');
 const el=document.getElementById('d_prog');
 if(s.status==='idle'){el.textContent='未开始';return}
 el.textContent=(s.status||'-')+' · 已探测 '+s.done+' 个模型 · '+s.elapsed+'s';
 if(s.report){DREP=s.report;paintDiscover();
  document.getElementById('d_note').textContent='探测完成 · 可用 '+
   DREP.reduce((a,p)=>a+(p.ok||0),0)+' / 共 '+DREP.reduce((a,p)=>a+(p.probed||0),0)}
 if(s.running)setTimeout(pollDiscover,1000)}catch(e){el=document.getElementById('d_prog');
  if(el)el.textContent='进度查询失败'}}
function paintDiscover(){const rows=[];
 DREP.forEach(p=>(p.models||[]).forEach(m=>rows.push({provider:m.provider,
  model:m.model,ok:m.ok?'可用':'不可用',status:m.status,latency_ms:m.latency_ms,
  context:m.context||'-',tokens:(m.usage||{}).total_tokens||0,
  params:Object.keys(m.params||{}).filter(k=>m.params[k]==='supported').join(','),
  rejected:Object.keys(m.params||{}).filter(k=>m.params[k]==='rejected').join(','),
  err:(m.error||'').slice(0,80)})));
 const t=document.getElementById('dres');
 if(!rows.length){t.innerHTML='<tr><td class=empty colspan=10>还没有探测结果</td></tr>';return}
 const hd=['提供方','模型','状态','HTTP','延迟ms','上下文','测试词元','支持参数','不支持参数','错误'];
 t.innerHTML='<tr>'+hd.map(x=>'<th>'+x+'</th>').join('')+'</tr>'+rows.map(r=>
  '<tr><td>'+esc(r.provider)+'</td><td><b>'+esc(r.model)+'</b></td><td>'+esc(r.ok)+
  '</td><td>'+r.status+'</td><td>'+r.latency_ms+'</td><td>'+esc(r.context)+'</td><td>'+
  r.tokens+'</td><td>'+esc(r.params)+'</td><td>'+esc(r.rejected)+'</td><td>'+
  esc(r.err)+'</td></tr>').join('')}
async function applyDiscovered(){try{const d=await send('/admin/models/apply','POST',
  {providers:[gv('d_prov')]});
  document.getElementById('d_note').textContent='别名总数 '+
   ((d.applied||{}).models||'?')+' · 新增 '+(d.added||[]).length+' · 跳过 '+(d.skipped||[]).length;
  loadProvidersForProbe()}
 catch(e){document.getElementById('d_note').textContent='写入失败 · '+e.message}}
async function clearCatalog(){try{await send('/admin/models?provider='+
  encodeURIComponent(gv('d_prov')||''),'DELETE');loadCatalog();
  document.getElementById('d_note').textContent='缓存已清空'}catch(e){toast('清空失败 · '+e.message)}}
async function loadCatalog(){try{const d=await j('/admin/models');
 const el=document.getElementById('d_seen');
 if(el)el.textContent=d.count+' 个模型缓存'+
  (d.last_seen?' · 最近 '+new Date(d.last_seen*1000).toLocaleString():'');
 if(d.count&&!DREP.length){const by={};
  (d.data||[]).forEach(m=>{(by[m.provider]=by[m.provider]||[]).push(m)});
  DREP=Object.keys(by).map(k=>({provider:k,models:by[k],probed:by[k].length,
   ok:by[k].filter(x=>x.ok).length}));paintDiscover()}}catch(e){}}
"""
JS += """
 (function(){let k='a';try{k=localStorage.getItem(SKIN_KEY)||'a'}catch(e){}setSkin(k);
 const h=(location.hash||'').replace('#','');let p=h;
 if(PAGES.indexOf(p)<0){try{p=localStorage.getItem(PKEY)||'overview'}catch(e){p='overview'}}
 go(p);loadSelf();refresh()})();
"""
JS += """
const NET_ON=__NET_ON__;
const NET='/internal/net';
let netOpen=false;
function netHdr(){return Object.assign({'content-type':'application/json'},hdr())}
function netRow(k,v){return '<div class=netrow><em>'+k+'</em><code>'+(v==null?'-':esc(v))+'</code></div>'}
async function netGet(u){const r=await fetch(NET+u,{headers:netHdr()});
 const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error((d.detail&&d.detail.message)||u);return d}
async function netSend(u,m,b){const r=await fetch(NET+u,{method:m,headers:netHdr(),
 body:JSON.stringify(b||{})});const d=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error((d.detail&&d.detail.message)||u+' -> '+r.status);return d}
function netPanel(on){netOpen=on;
 document.getElementById('net_mask').classList.toggle('on',on);
 document.getElementById('net_panel').classList.toggle('on',on);
 if(on)netRefresh()}
async function netRefresh(){const box=document.getElementById('net_body');
 try{const [s,sc]=await Promise.all([netGet('/state'),netGet('/scores')]);
  box.innerHTML=netRow('模式',s.mode)+netRow('控制器',s.controller)+netRow('在线',s.alive)+
   netRow('出口',((s.paths||[]).join(' '))||'direct')+netRow('打分',s.scores)+
   netRow('上次探测',s.last_refresh)+netRow('错误',s.error||'')+
   '<h2>出口打分</h2><table id=net_t></table>'
  const rows=(sc.scores||[]).map(x=>'<tr><td>'+esc(x.provider)+'</td><td>'+esc(x.egress)+
   '</td><td>'+(x.ok?'通':'败')+'</td><td>'+x.delay_ms+'</td><td>'+x.fail_ratio+'</td></tr>');
  document.getElementById('net_t').innerHTML=rows.length?
   ('<tr><th>提供方</th><th>出口</th><th>状态</th><th>ms</th><th>失败率</th></tr>'+rows.join(''))
   :'<tr><td class=empty colspan=5>尚无数据，点探测</td></tr>';
  document.getElementById('net_note').textContent=''
 }catch(e){box.innerHTML='<div class=muted>不可用：'+esc(e.message)+'</div>'}}
async function netProbe(){document.getElementById('net_note').textContent='探测中…';
 try{const d=await netSend('/probes','POST',{});
  document.getElementById('net_note').textContent='探测 '+d.probes+' 次';netRefresh()}
 catch(e){document.getElementById('net_note').textContent=e.message}}
async function netMode(m){try{await netSend('/mode','POST',{mode:m});netRefresh()}
 catch(e){document.getElementById('net_note').textContent=e.message}}
async function netSelect(){const g=document.getElementById('net_g').value.trim(),
 n=document.getElementById('net_n').value.trim();if(!g||!n)return;
 try{await netSend('/select','PUT',{group:g,node:n});netRefresh()}
 catch(e){document.getElementById('net_note').textContent=e.message}}
async function netFlush(){try{await netSend('/flush','POST',{dns:true});
 document.getElementById('net_note').textContent='DNS 已刷新'}
 catch(e){document.getElementById('net_note').textContent=e.message}}
function netEgress(){const p=document.getElementById('net_p').value.trim();if(!p)return;
 netGet('/egress/'+encodeURIComponent(p)).then(d=>{document.getElementById('net_note').textContent=
  p+' -> '+d.pick}).catch(e=>{document.getElementById('net_note').textContent=e.message})}
if(NET_ON){document.getElementById('net_glyph').classList.remove('off');
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&netOpen)netPanel(false)})}
"""
BODY = """
<body class="skin-a">
<header>
<h1>SMSocket 控制台</h1>
<div class="nav">
<button class="navbtn" data-p="overview" onclick="go('overview')">概览</button>
<button class="navbtn" data-p="usage" onclick="go('usage')">流量与费用</button>
<button class="navbtn" data-p="providers" onclick="go('providers')">提供商与API</button>
<button class="navbtn" data-p="socket" onclick="go('socket')">并发与批量</button>
<button class="navbtn" data-p="discover" onclick="go('discover')">模型探测</button>
<button class="navbtn" data-p="settings" onclick="go('settings')">总设置</button>
</div>
<div class="cred"><em>基础URL</em><code id="baseurl">-</code>
<button onclick="copyText(document.getElementById('baseurl').textContent,' 基础URL')">复制</button>
<em>API Key</em><code id="apikey">-</code>
<button id="reveal" onclick="revealKey()">显示</button>
<button onclick="copyText(document.getElementById('apikey').textContent,' API Key')">复制</button>
</div>
<input id="key" type="password" size="20" placeholder="主密钥（可选）">
<button onclick="refresh()">刷新</button>
<span>调度策略：<b id="strategy">-</b></span>
<span id="msg"></span>
</header>
"""
BODY += """
<main>
<div class="page" id="page-overview">
<div class="cards">
<div class="card"><b id="calls">-</b><i>调用总数</i></div>
<div class="card"><b id="tokens">-</b><i>词元总量</i></div>
<div class="card"><b id="cost">-</b><i>累计费用</i></div>
<div class="card"><b id="slots">-</b><i>密钥槽位</i></div>
<div class="card"><b id="models">-</b><i>可用模型</i></div>
<div class="card"><b id="xactive">-</b><i>当前并发</i></div>
<div class="card"><b id="xpeak">-</b><i>峰值并发</i></div>
<div class="card"><b id="xcurrency">-</b><i>计费币种</i></div>
</div>
<h2>按模型统计</h2><table id="bymodel"></table>
<h2>密钥池摘要</h2><table id="pool"></table>
</div>
<div class="page" id="page-usage">
<div class="cards">
<div class="card"><b id="ucalls">-</b><i>调用总数</i></div>
<div class="card"><b id="utokens">-</b><i>词元总量</i></div>
<div class="card"><b id="ucost">-</b><i>累计费用</i></div>
</div>
<h2>按提供方统计</h2><table id="byprov"></table>
<h2>近 14 天用量</h2><table id="daily"></table>
<h2>最近调用记录</h2><table id="recent"></table>
</div>
"""
BODY += """
<div class="page" id="page-providers">
<h2>提供方列表</h2><table id="provs"></table>
<h2>新增 / 编辑提供方</h2>
<form id="pf" onsubmit="submitP();return false">
<label>名称<input id="p_name" placeholder="my-provider"></label>
<label>基础URL<input id="p_url" placeholder="https://api.xx.com/v1"></label>
<label>协议<select id="p_style"><option value="openai">openai</option><option value="anthropic">anthropic</option></select></label>
<label>优先级<input id="p_prio" type="number" value="0"></label>
<label>权重<input id="p_w" type="number" value="1"></label>
<label>超时(秒)<input id="p_to" type="number" value="120"></label>
<label>最大RPM（0=不限）<input id="p_rpm" type="number" value="0"></label>
<label>启用<input id="p_on" type="checkbox" checked></label>
<label class="wide">模型别名（别名=真实名,别名2=真实2）<input id="p_models" placeholder="gpt-x=gpt-4o-mini"></label>
<label class="wide">嵌入模型（同上格式）<input id="p_emb"></label>
<label class="wide">密钥（每行一个，仅新增时生效）<textarea id="p_keys" rows="3" placeholder="sk-..."></textarea></label>
<div class="btns wide"><button id="p_submit" type="submit">新增提供方</button>
<button type="button" onclick="cancelEdit()">清空</button>
<span id="p_note"></span></div>
</form>
<h2>密钥池实时状态</h2><table id="poolprov"></table>
</div>
"""
BODY += """
<div class="page" id="page-settings">
<h2>界面皮肤</h2>
<div class="skinbar"><em>选择皮肤（存 localStorage）</em>
<button class="skinbtn" data-k="a" onclick="setSkin('a')">A · Aero 玻璃</button>
<button class="skinbtn" data-k="b" onclick="setSkin('b')">B · Fluent 云母</button>
<button class="skinbtn" data-k="c" onclick="setSkin('c')">C · 金属玻璃</button>
<button class="skinbtn" data-k="d" onclick="setSkin('d')">D · 简洁双栏</button>
</div>
<h2>全局设置</h2>
<form id="gf" onsubmit="saveGlobals();return false">
<label>监听地址<input id="g_listen"></label>
<label>调度策略<select id="g_strategy"><option value="priority">priority</option>
<option value="round_robin">round_robin</option><option value="weighted">weighted</option></select></label>
<label>重试次数<input id="g_retry" type="number"></label>
<label>冷却秒数<input id="g_cd" type="number"></label>
<label>最大并发（0=不限）<input id="g_maxc" type="number" value="0"></label>
<label>排队等待(秒)<input id="g_qw" type="number" value="30"></label>
<label>单提供方并发<input id="g_ppc" type="number" value="0"></label>
<label>计费币种<input id="g_cur"></label>
<label class="wide">定价（别名=每百万prompt/每百万completion，每行一条，* 为默认）
<textarea id="g_pricing" rows="4"></textarea></label>
<div class="btns wide"><button type="submit">保存并热加载</button>
<button type="button" onclick="loadSettings()">重新读取</button>
<span>配置文件：<code id="g_path">-</code></span></div>
</form>
<h2>计费货币与汇率</h2>
<form id="bf" onsubmit="saveBilling();return false">
<label>显示币种<select id="b_cur"></select></label>
<label>汇率基准<select id="b_base"></select></label>
<label>小数位<input id="b_prec" type="number" value="6"></label>
<label>汇率源URL<input id="b_url" placeholder="https://...（留空=手工汇率）"></label>
<label class="wide">汇率表（币种=每1基准的数量，每行一条）
<textarea id="b_rates" rows="6" placeholder="USD=1"></textarea></label>
<div class="btns wide"><button type="submit">保存计费设置</button>
<button type="button" onclick="refreshRates()">拉取在线汇率</button>
<button type="button" onclick="loadBilling()">重新读取</button>
<span id="b_note"></span></div></form>
<h2>换算试算</h2>
<div class="btns">
<input id="c_amt" type="number" value="1" size="6">
<select id="c_from"></select><em>→</em><select id="c_to"></select>
<button onclick="convertTry()">试算</button><b id="c_out">-</b></div>
<h2>客户端接入信息</h2>
<pre id="hint">-</pre>
<div class="cards">
<div class="card"><b id="modelcount">-</b><i>已索引模型</i></div>
<div class="card"><b id="g_slots">-</b><i>密钥槽位</i></div>
</div>
<h2>可用模型别名</h2><pre id="g_models">-</pre>
</div>
<div class="page" id="page-socket">
<div class="cards">
<div class="card"><b id="s_active">-</b><i>在途请求</i></div>
<div class="card"><b id="s_queued">-</b><i>排队中</i></div>
<div class="card"><b id="s_peak">-</b><i>历史峰值</i></div>
<div class="card"><b id="s_total">-</b><i>累计请求</i></div>
<div class="card"><b id="s_rej">-</b><i>拒绝(429)</i></div>
<div class="card"><b id="s_rps">-</b><i>每秒请求</i></div>
<div class="card"><b id="s_wait">-</b><i>平均排队ms</i></div>
<div class="card"><b id="s_err">-</b><i>失败数</i></div>
</div>
<h2>并发闸门</h2>
<div class="cred"><em>上限</em><code id="s_max">-</code><em>单提供方</em><code id="s_ppc">-</code>
<em>排队等待</em><code id="s_qw">-</code><em>状态</em><code id="s_sat">-</code>
<button onclick="loadSocket()">刷新</button>
<button id="s_auto" onclick="toggleAuto()">自动刷新：关</button></div>
<h2>按提供方并发</h2><table id="sprov"></table>
<div id="stackblk" class="hide">
<h2>压栈调度</h2>
<div class="cred"><em>策略</em><code id="k_policy">-</code><em>深度</em><code id="k_depth">-</code>
<em>峰值</em><code id="k_peak">-</code><em>唤醒</em><code id="k_popped">-</code>
<em>超时</em><code id="k_expired">-</code><em>平均滞留</em><code id="k_avg">-</code>
<em>最长滞留</em><code id="k_pmax">-</code></div>
<table id="kentries"></table></div>
<div id="reachblk" class="hide">
<h2>模型通达性</h2>
<div class="cred"><em>窗口</em><code id="r_window">-</code><em>下次探测</em><code id="r_next">-</code>
<em>样本</em><code id="r_rows">-</code>
<button onclick="loadReach()">刷新</button>
<button onclick="runReach()">立即探测</button></div>
<table id="reach" class="reach"></table>
<div class="muted">绿=健康 · 黄=慢 · 红=不通 · 蓝=抖动；行=模型，列=出口路径</div></div>
<h2>并行批量发送</h2>
<form id="bf2" onsubmit="runBatch();return false">
<label>并发数<input id="q_conc" type="number" value="8"></label>
<label>模式<select id="q_mode"><option value="sync">同步等待</option>
<option value="async">异步作业（可轮询）</option></select></label>
<label>失败即停<select id="q_ff"><option value="0">否</option><option value="1">是</option></select></label>
<label class="wide">请求列表（每行一个提示词，或一行一个 JSON 对象）
<textarea id="q_body" rows="5">你好
介绍一下你自己</textarea></label>
<div class="btns wide"><button type="submit">发送</button>
<button type="button" onclick="pollJob()">轮询作业</button>
<span id="q_note"></span></div></form>
<h2>批量结果</h2><table id="qres"></table>
<h2>作业列表</h2><table id="qjobs"></table>
</div>
<div class="page" id="page-discover">
<h2>探测提供商（发送测试消息获取真实模型与参数）</h2>
<form id="df" onsubmit="startDiscover();return false">
<label>目标<select id="d_prov"></select></label>
<label>并发数<input id="d_conc" type="number" value="8"></label>
<label>超时(秒)<input id="d_to" type="number" value="30"></label>
<label>最多模型数<input id="d_max" type="number" value="200"></label>
<label>测试提示词<input id="d_prompt" value="Reply with exactly one word: ok"></label>
<label>指定模型（逗号分隔，留空=全部）<input id="d_models"></label>
<label>探测参数<select id="d_params"><option value="1">是</option><option value="0">否</option></select></label>
<label>探测流式<select id="d_stream"><option value="1">是</option><option value="0">否</option></select></label>
<label>探测嵌入<select id="d_emb"><option value="0">否</option><option value="1">是</option></select></label>
<div class="btns wide"><button type="submit">开始探测</button>
<button type="button" onclick="pollDiscover()">刷新进度</button>
<button type="button" onclick="applyDiscovered()">写入别名</button>
<button type="button" onclick="clearCatalog()">清空缓存</button>
<span id="d_note"></span></div></form>
<div class="cred"><em>进度</em><code id="d_prog">未开始</code><em>缓存</em><code id="d_seen">-</code></div>
<h2>探测结果（模型 · 可用性 · 参数）</h2><table id="dres"></table>
</div>
</main>
<div class="netglyph off" id="net_glyph" title="net plane" onclick="netPanel(true)">⌁ net</div>
<div class="netmask" id="net_mask" onclick="netPanel(false)"></div>
<div class="netpanel" id="net_panel">
<h2>网络出口（内部）</h2>
<div class="netrow"><button onclick="netRefresh()">状态</button>
<button onclick="netProbe()">探测</button><button onclick="netMode('auto')">auto</button>
<button onclick="netMode('direct')">direct</button><button onclick="netMode('proxy')">proxy</button>
<button onclick="netFlush()">flush dns</button><span id="net_note" class="muted"></span></div>
<div class="netrow"><em>分组</em><input id="net_g" placeholder="PROXY-GRP">
<em>节点</em><input id="net_n" placeholder="node"><button onclick="netSelect()">固定</button></div>
<div class="netrow"><em>预览出口</em><input id="net_p" placeholder="provider">
<button onclick="netEgress()">查看</button></div>
<div id="net_body" class="muted">点“状态”读取 /internal/net/state</div>
</div>
"""
PAGE = ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>SMSocket 控制台</title><style>" + CSS + "</style></head>"
        + BODY + "<script>" + JS + "</script></body></html>")
