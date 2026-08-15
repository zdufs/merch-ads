#!/usr/bin/env python3
"""
Generate a rich, interactive profitability dashboard (outputs/dashboard.html)
from the local data. Read-only. Filters: product type, snapshot date, text
search, sortable columns, per-tab toggles. Tabs: Overview, Products, Campaigns,
Search Terms, Harvest, Actions log.

Run:  python3 dashboard.py   ->   open outputs/dashboard.html
"""

import json
import os

import paths
import sqlite3
from collections import Counter, defaultdict
import db
import markets
import products

OUTDIR = os.path.join(paths.REPO_ROOT, "outputs")
os.makedirs(OUTDIR, exist_ok=True)
OUT = os.path.join(OUTDIR, "dashboard.html" if markets.is_default()
                   else f"dashboard_{markets.current()}.html")

conn = db.connect()
cur = conn.cursor()
snaps = [r[0] for r in cur.execute("SELECT DISTINCT date FROM campaign_perf ORDER BY date")]
latest = snaps[-1] if snaps else None

# campaign -> dominant product type
cv = defaultdict(Counter)
for cid, pt in cur.execute("""SELECT a.campaign_id,p.product_type FROM ad_groups a
        JOIN ad_group_product p ON a.ad_group_id=p.ad_group_id WHERE p.product_type<>''"""):
    cv[cid][pt] += 1
camp_type = {cid: c.most_common(1)[0][0] for cid, c in cv.items() if c}

# precise per-type metrics per snapshot (from targeting_perf)
byType = []
for d, pt, impr, clk, cost, orders, sales in cur.execute("""
    SELECT t.date, COALESCE(NULLIF(p.product_type,''),'unmapped') pt,
           SUM(t.impressions),SUM(t.clicks),ROUND(SUM(t.cost),2),SUM(t.orders),ROUND(SUM(t.sales),2)
    FROM targeting_perf t LEFT JOIN ad_group_product p ON t.ad_group_id=p.ad_group_id
    GROUP BY t.date, pt"""):
    byType.append(dict(date=d, type=pt, impr=impr or 0, clicks=clk or 0, cost=cost or 0,
                       orders=orders or 0, sales=sales or 0,
                       target=round(products.get_econ(pt if pt != "unmapped" else None)["target_acos"] * 100)))

# campaigns (all snapshots) + meta
meta = {r[0]: r[1:] for r in cur.execute(
    "SELECT campaign_id,name,state,targeting_type,daily_budget FROM campaigns")}
camps = []
for d, cid, name, impr, clk, cost, orders, sales in cur.execute(
    "SELECT date,campaign_id,campaign_name,impressions,clicks,cost,orders,sales FROM campaign_perf"):
    m = meta.get(cid, (name, "", "", None))
    camps.append(dict(date=d, name=name or m[0], type=camp_type.get(cid, "?"),
                      state=m[1] or "", budget=m[3], impr=impr or 0, clicks=clk or 0,
                      cost=round(cost or 0, 2), orders=orders or 0, sales=round(sales or 0, 2)))

# search terms — that table's OWN latest snapshot. `latest` above comes from
# campaign_perf, which a different report job fills; using it here returned an
# empty term list on every day the search-term report had failed.
st_latest = db.latest_snapshot(conn, "search_term_perf")
agp = {r[0]: r[1] for r in cur.execute("SELECT ad_group_id,product_type FROM ad_group_product")}
cname = {r[0]: r[1] for r in cur.execute("SELECT campaign_id,name FROM campaigns")}
sterms = []
if st_latest:
    for st, cid, agid, impr, clk, cost, orders, sales in cur.execute("""
        SELECT search_term,campaign_id,ad_group_id,impressions,clicks,cost,orders,sales
        FROM search_term_perf WHERE date=? AND search_term IS NOT NULL ORDER BY cost DESC LIMIT 2000""", (st_latest,)):
        sterms.append(dict(term=st, camp=cname.get(cid, ""), type=agp.get(str(agid), "?"),
                           impr=impr or 0, clicks=clk or 0, cost=round(cost or 0, 2),
                           orders=orders or 0, sales=round(sales or 0, 2)))

harv = [dict(term=r[0], kind=r[1], type=r[2], clicks=r[3], orders=r[4],
             sales=round(r[5] or 0, 2), acos=round((r[6] or 0) * 100, 1), cpc=r[7],
             promoted=r[8], last=r[9])
        for r in cur.execute("""SELECT search_term,kind,product_type,clicks,orders,sales,acos,cpc,promoted,last_seen
                                FROM harvest_log ORDER BY sales DESC""")]
acts = [dict(when=r[0], action=r[1], entity=str(r[2])[:46], detail=str(r[3] or "")[:90])
        for r in cur.execute("SELECT applied_at,action,entity_id,detail FROM writes_log ORDER BY rowid DESC LIMIT 800")]

payload = dict(snaps=snaps, latest=latest, types=sorted({b["type"] for b in byType}),
               byType=byType, camps=camps, sterms=sterms, harv=harv, acts=acts)

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Merch Ads Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{box-sizing:border-box}body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
header{padding:16px 24px;border-bottom:1px solid #262a31;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0}.muted{color:#9aa0a6;font-size:12px}
select,input{background:#1a1d23;color:#e6e6e6;border:1px solid #2d323b;border-radius:8px;padding:7px 10px;font-size:13px}
.tabs{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid #262a31;flex-wrap:wrap}
.tab{padding:10px 14px;cursor:pointer;color:#9aa0a6;border-bottom:2px solid transparent;font-size:13px}
.tab.on{color:#fff;border-bottom-color:#4dabf7}
.wrap{padding:20px 24px}.hidden{display:none}
.kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
.kpi{background:#1a1d23;border:1px solid #262a31;border-radius:10px;padding:12px 16px;min-width:120px}
.kpi .v{font-size:22px;font-weight:600}.kpi .l{color:#9aa0a6;font-size:11px;text-transform:uppercase}
.card{background:#1a1d23;border:1px solid #262a31;border-radius:10px;padding:16px;margin-bottom:18px}
.row{display:flex;gap:18px;flex-wrap:wrap}.row>.card{flex:1;min-width:340px}
table{width:100%;border-collapse:collapse;font-size:12.5px}th,td{text-align:left;padding:6px 9px;border-bottom:1px solid #23272e;white-space:nowrap}
th{color:#9aa0a6;font-weight:500;cursor:pointer;position:sticky;top:0;background:#1a1d23}
td.num,th.num{text-align:right}.bad{color:#ff6b6b}.good{color:#51cf66}.dim{color:#9aa0a6}
.scroll{max-height:64vh;overflow:auto}.controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.pill{font-size:11px;padding:2px 7px;border-radius:20px;background:#23272e;color:#9aa0a6}
</style></head><body>
<header>
 <h1>Merch Ads</h1>
 <span class="muted" id="sub"></span>
 <span style="flex:1"></span>
 <label class="muted">Product <select id="fType"></select></label>
 <label class="muted">Snapshot <select id="fSnap"></select></label>
</header>
<div class="tabs" id="tabs"></div>
<div class="wrap">
 <div id="t_overview"><div class="kpis" id="kpis"></div>
   <div class="row"><div class="card"><h3>Spend vs Sales by snapshot</h3><canvas id="c1" height="150"></canvas></div>
   <div class="card"><h3>ACOS & CVR trend</h3><canvas id="c2" height="150"></canvas></div></div>
   <div class="card"><h3>By product type (selected snapshot)</h3><div id="ptmini"></div></div></div>
 <div id="t_focus" class="hidden">
   <div class="card"><h3>🚀 Niches to expand — high CVR, profitable (≥20 clicks)</h3>
     <div class="muted" style="margin-bottom:8px">These campaigns convert well and have ad headroom — make MORE designs in these niches.</div>
     <div id="focusNiche"></div></div>
   <div class="card"><h3>✏️ Search terms to design toward — proven converters (≥2 orders)</h3>
     <div class="muted" style="margin-bottom:8px">Real demand you're capturing. New designs aimed at these phrases ride proven intent. ⚠️ skip any brand/IP/trademark terms.</div>
     <div id="focusTerms"></div></div></div>
 <div id="t_products" class="hidden"><div class="card"><div id="products"></div></div></div>
 <div id="t_campaigns" class="hidden"><div class="controls">
   <input id="campSearch" placeholder="search campaign…" size="22">
   <select id="campState"><option value="">all states</option></select>
   <span class="pill" id="campCount"></span></div>
   <div class="card scroll"><div id="campaigns"></div></div></div>
 <div id="t_terms" class="hidden"><div class="controls">
   <input id="stSearch" placeholder="search term…" size="22">
   <select id="stFilter"><option value="">all</option><option value="conv">converting (orders>0)</option><option value="zero">zero-sale w/ spend</option></select>
   <span class="pill" id="stCount"></span></div>
   <div class="card scroll"><div id="terms"></div></div></div>
 <div id="t_harvest" class="hidden"><div class="controls"><input id="hvSearch" placeholder="search…" size="22"><span class="pill" id="hvCount"></span></div>
   <div class="card scroll"><div id="harvest"></div></div></div>
 <div id="t_actions" class="hidden"><div class="controls">
   <select id="actFilter"><option value="">all actions</option></select>
   <input id="actSearch" placeholder="search…" size="22"><span class="pill" id="actCount"></span></div>
   <div class="card scroll"><div id="actions"></div></div></div>
</div>
<script>
const D=__DATA__;
const $=id=>document.getElementById(id);
const money=x=>'$'+(+x).toLocaleString(undefined,{maximumFractionDigits:2});
const pct=x=>x==null?'—':(+x).toFixed(1)+'%';
let ST={type:'',snap:D.latest,tab:'overview'};

// ---- controls ----
$('fType').innerHTML='<option value="">all types</option>'+D.types.map(t=>`<option>${t}</option>`).join('');
$('fSnap').innerHTML=D.snaps.map(s=>`<option ${s==D.latest?'selected':''}>${s}</option>`).join('');
$('fType').onchange=e=>{ST.type=e.target.value;renderAll()};
$('fSnap').onchange=e=>{ST.snap=e.target.value;renderAll()};
const TABS=[['overview','Overview'],['focus','🎯 Focus'],['products','Products'],['campaigns','Campaigns'],['terms','Search Terms'],['harvest','Harvest'],['actions','Actions']];
$('tabs').innerHTML=TABS.map(([k,l])=>`<div class="tab" data-k="${k}">${l}</div>`).join('');
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{ST.tab=t.dataset.k;
 document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.k==ST.tab));
 TABS.forEach(([k])=>$('t_'+k).classList.toggle('hidden',k!=ST.tab));renderAll()});
document.querySelector('.tab').classList.add('on');

// ---- helpers ----
function sortable(elId,cols,rows,key){ // cols:[{k,l,num,fmt,cls}]
 let s=key||{c:cols.find(c=>c.num)?.k,d:-1};
 function draw(){
  rows.sort((a,b)=>{let v=(a[s.c]??-1),w=(b[s.c]??-1);return (v>w?1:v<w?-1:0)*s.d});
  let h='<table><thead><tr>'+cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.l}${s.c==c.k?(s.d>0?' ▲':' ▼'):''}</th>`).join('')+'</tr></thead><tbody>'
   +rows.map(r=>'<tr>'+cols.map(c=>{let v=r[c.k];let f=c.fmt?c.fmt(v,r):(v??'');let cl=c.cls?c.cls(v,r):(c.num?'num':'');return `<td class="${cl}">${f}</td>`}).join('')+'</tr>').join('')+'</tbody></table>';
  $(elId).innerHTML=h;
  $(elId).querySelectorAll('th').forEach(th=>th.onclick=()=>{let k=th.dataset.k;s.d=(s.c==k)?-s.d:-1;s.c=k;draw()});
 } draw(); return rows.length;
}
const acosCls=(v,r)=>v==null?'num dim':(v> (r.target??30)?'num bad':'num good');

// ---- overview ----
let chart1,chart2;
function renderOverview(){
 const bt=D.byType.filter(b=>!ST.type||b.type==ST.type);
 const bySnap={};D.snaps.forEach(s=>bySnap[s]={impr:0,clicks:0,cost:0,orders:0,sales:0});
 bt.forEach(b=>{let o=bySnap[b.date];o.impr+=b.impr;o.clicks+=b.clicks;o.cost+=b.cost;o.orders+=b.orders;o.sales+=b.sales});
 const cur=bySnap[ST.snap]||{impr:0,clicks:0,cost:0,orders:0,sales:0};
 const acos=cur.sales?cur.cost/cur.sales*100:null, cvr=cur.clicks?cur.orders/cur.clicks*100:null, ctr=cur.impr?cur.clicks/cur.impr*100:null;
 $('kpis').innerHTML=[['Spend',money(cur.cost)],['Sales',money(cur.sales)],['ACOS',pct(acos)],['CVR',pct(cvr)],['CTR',pct(ctr)],['Orders',cur.orders],['Clicks',cur.clicks.toLocaleString()],['Impr',cur.impr.toLocaleString()]]
   .map(([l,v])=>`<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join('');
 const labels=D.snaps,spend=labels.map(s=>+bySnap[s].cost.toFixed(2)),sales=labels.map(s=>+bySnap[s].sales.toFixed(2));
 const acosT=labels.map(s=>bySnap[s].sales?+(bySnap[s].cost/bySnap[s].sales*100).toFixed(1):null);
 const cvrT=labels.map(s=>bySnap[s].clicks?+(bySnap[s].orders/bySnap[s].clicks*100).toFixed(1):null);
 chart1&&chart1.destroy();chart2&&chart2.destroy();
 chart1=new Chart(c1,{type:'bar',data:{labels,datasets:[{label:'Spend',data:spend,backgroundColor:'#ff922b'},{label:'Sales',data:sales,backgroundColor:'#51cf66'}]},options:CH});
 chart2=new Chart(c2,{type:'line',data:{labels,datasets:[{label:'ACOS %',data:acosT,borderColor:'#4dabf7',tension:.3},{label:'CVR %',data:cvrT,borderColor:'#cc5de8',tension:.3}]},options:CH});
 // mini by-type for selected snapshot
 const rows=D.byType.filter(b=>b.date==ST.snap&&(!ST.type||b.type==ST.type)).map(b=>({type:b.type,cost:b.cost,sales:b.sales,
   acos:b.sales?+(b.cost/b.sales*100).toFixed(1):null,cvr:b.clicks?+(b.orders/b.clicks*100).toFixed(1):null,target:b.target,orders:b.orders}));
 sortable('ptmini',[{k:'type',l:'Type'},{k:'cost',l:'Spend',num:1,fmt:money},{k:'sales',l:'Sales',num:1,fmt:money},
   {k:'acos',l:'ACOS',num:1,fmt:pct,cls:acosCls},{k:'target',l:'Target',num:1,fmt:v=>v+'%'},{k:'cvr',l:'CVR',num:1,fmt:pct},{k:'orders',l:'Orders',num:1}],rows);
}
const CH={plugins:{legend:{labels:{color:'#ccc'}}},scales:{x:{ticks:{color:'#9aa0a6'}},y:{ticks:{color:'#9aa0a6'}}}};

function renderProducts(){
 const rows=D.byType.filter(b=>b.date==ST.snap&&(!ST.type||b.type==ST.type)).map(b=>({type:b.type,impr:b.impr,clicks:b.clicks,cost:b.cost,sales:b.sales,orders:b.orders,
   acos:b.sales?+(b.cost/b.sales*100).toFixed(1):null,cvr:b.clicks?+(b.orders/b.clicks*100).toFixed(1):null,ctr:b.impr?+(b.clicks/b.impr*100).toFixed(2):null,target:b.target}));
 sortable('products',[{k:'type',l:'Type'},{k:'impr',l:'Impr',num:1,fmt:v=>v.toLocaleString()},{k:'clicks',l:'Clicks',num:1},{k:'ctr',l:'CTR',num:1,fmt:pct},
   {k:'cost',l:'Spend',num:1,fmt:money},{k:'sales',l:'Sales',num:1,fmt:money},{k:'orders',l:'Orders',num:1},
   {k:'cvr',l:'CVR',num:1,fmt:pct},{k:'acos',l:'ACOS',num:1,fmt:pct,cls:acosCls},{k:'target',l:'Target',num:1,fmt:v=>v+'%'}],rows);
}
function renderCampaigns(){
 const q=$('campSearch').value.toLowerCase(),stt=$('campState').value;
 let rows=D.camps.filter(c=>c.date==ST.snap&&(!ST.type||c.type==ST.type)&&(!stt||c.state==stt)&&(!q||(c.name||'').toLowerCase().includes(q)))
  .map(c=>({name:c.name,type:c.type,state:c.state,budget:c.budget,impr:c.impr,clicks:c.clicks,cost:c.cost,sales:c.sales,orders:c.orders,
   acos:c.sales?+(c.cost/c.sales*100).toFixed(1):null,cvr:c.clicks?+(c.orders/c.clicks*100).toFixed(1):null}));
 $('campCount').textContent=rows.length+' campaigns';
 sortable('campaigns',[{k:'name',l:'Campaign'},{k:'type',l:'Type'},{k:'state',l:'State'},{k:'cost',l:'Spend',num:1,fmt:money},
   {k:'sales',l:'Sales',num:1,fmt:money},{k:'acos',l:'ACOS',num:1,fmt:pct,cls:acosCls},{k:'cvr',l:'CVR',num:1,fmt:pct},
   {k:'orders',l:'Orders',num:1},{k:'clicks',l:'Clicks',num:1},{k:'budget',l:'Budget',num:1,fmt:v=>v?money(v):'—'}],rows);
}
function renderTerms(){
 const q=$('stSearch').value.toLowerCase(),f=$('stFilter').value;
 let rows=D.sterms.filter(t=>(!ST.type||t.type==ST.type)&&(!q||t.term.toLowerCase().includes(q)||(t.camp||'').toLowerCase().includes(q))
   &&(f!='conv'||t.orders>0)&&(f!='zero'||(t.orders==0&&t.cost>0)))
  .map(t=>({term:t.term,camp:t.camp,type:t.type,clicks:t.clicks,cost:t.cost,sales:t.sales,orders:t.orders,
   acos:t.sales?+(t.cost/t.sales*100).toFixed(1):null}));
 $('stCount').textContent=rows.length+' terms (top 2000 by spend, '+D.latest+')';
 sortable('terms',[{k:'term',l:'Search term'},{k:'camp',l:'Campaign'},{k:'type',l:'Type'},{k:'clicks',l:'Clicks',num:1},
   {k:'cost',l:'Spend',num:1,fmt:money},{k:'sales',l:'Sales',num:1,fmt:money},{k:'orders',l:'Orders',num:1},{k:'acos',l:'ACOS',num:1,fmt:pct}],rows);
}
function renderHarvest(){
 const q=$('hvSearch').value.toLowerCase();
 let rows=D.harv.filter(h=>(!ST.type||h.type==ST.type)&&(!q||(h.term||'').toLowerCase().includes(q)))
  .map(h=>({term:h.term,kind:h.kind,type:h.type,orders:h.orders,sales:h.sales,acos:h.acos,cpc:h.cpc,promoted:h.promoted?'✓':'',last:h.last}));
 $('hvCount').textContent=rows.length+' winners';
 sortable('harvest',[{k:'term',l:'Search term'},{k:'kind',l:'Kind'},{k:'type',l:'Type'},{k:'orders',l:'Orders',num:1},
   {k:'sales',l:'Sales',num:1,fmt:money},{k:'acos',l:'ACOS',num:1,fmt:v=>v+'%'},{k:'cpc',l:'CPC',num:1,fmt:v=>v?money(v):'—'},{k:'promoted',l:'Promoted'},{k:'last',l:'Last seen'}],rows);
}
function renderFocus(){
 let niche=D.camps.filter(c=>c.date==ST.snap&&(!ST.type||c.type==ST.type)&&c.clicks>=20&&c.orders>0)
  .map(c=>({name:c.name,type:c.type,clicks:c.clicks,orders:c.orders,sales:c.sales,
    cvr:c.clicks?+(c.orders/c.clicks*100).toFixed(1):0,acos:c.sales?+(c.cost/c.sales*100).toFixed(1):null}));
 niche.sort((a,b)=>b.cvr-a.cvr);niche=niche.slice(0,20);
 sortable('focusNiche',[{k:'name',l:'Campaign / niche'},{k:'type',l:'Type'},{k:'cvr',l:'CVR',num:1,fmt:pct},
   {k:'acos',l:'ACOS',num:1,fmt:pct},{k:'orders',l:'Orders',num:1},{k:'sales',l:'Sales',num:1,fmt:money},{k:'clicks',l:'Clicks',num:1}],niche,{c:'cvr',d:-1});
 let terms=D.sterms.filter(t=>(!ST.type||t.type==ST.type)&&t.orders>=2&&!/^b0[0-9a-z]{8}$/i.test(t.term))
  .map(t=>({term:t.term,camp:t.camp,type:t.type,orders:t.orders,sales:t.sales,
    cvr:t.clicks?+(t.orders/t.clicks*100).toFixed(1):0,acos:t.sales?+(t.cost/t.sales*100).toFixed(1):null}));
 terms.sort((a,b)=>b.cvr-a.cvr||b.orders-a.orders);terms=terms.slice(0,40);
 sortable('focusTerms',[{k:'term',l:'Search term (design idea)'},{k:'type',l:'Type'},{k:'cvr',l:'CVR',num:1,fmt:pct},
   {k:'orders',l:'Orders',num:1},{k:'sales',l:'Sales',num:1,fmt:money},{k:'acos',l:'ACOS',num:1,fmt:pct},{k:'camp',l:'From campaign'}],terms,{c:'cvr',d:-1});
}
function renderActions(){
 const q=$('actSearch').value.toLowerCase(),f=$('actFilter').value;
 let rows=D.acts.filter(a=>(!f||a.action==f)&&(!q||(a.entity+a.detail).toLowerCase().includes(q)));
 $('actCount').textContent=rows.length+' writes';
 sortable('actions',[{k:'when',l:'When'},{k:'action',l:'Action'},{k:'entity',l:'Entity'},{k:'detail',l:'Detail'}],rows,{c:'when',d:-1});
}
function renderAll(){
 $('sub').textContent='snapshot '+ST.snap+(ST.type?(' · '+ST.type):' · all types');
 if(ST.tab=='overview')renderOverview();if(ST.tab=='focus')renderFocus();if(ST.tab=='products')renderProducts();
 if(ST.tab=='campaigns')renderCampaigns();if(ST.tab=='terms')renderTerms();
 if(ST.tab=='harvest')renderHarvest();if(ST.tab=='actions')renderActions();
}
// populate dependent dropdowns + wire search
$('campState').innerHTML='<option value="">all states</option>'+[...new Set(D.camps.map(c=>c.state).filter(Boolean))].map(s=>`<option>${s}</option>`).join('');
$('actFilter').innerHTML='<option value="">all actions</option>'+[...new Set(D.acts.map(a=>a.action))].map(s=>`<option>${s}</option>`).join('');
['campSearch','campState'].forEach(i=>$(i).oninput=renderCampaigns);
['stSearch','stFilter'].forEach(i=>$(i).oninput=renderTerms);
$('hvSearch').oninput=renderHarvest;['actSearch','actFilter'].forEach(i=>$(i).oninput=renderActions);
renderAll();
</script></body></html>"""

with open(OUT, "w") as f:
    f.write(HTML.replace("__DATA__", json.dumps(payload)))
print(f"Dashboard written: {OUT}")
print(f"  {len(payload['camps'])} campaign rows · {len(payload['sterms'])} search terms · "
      f"{len(payload['harv'])} harvest · {len(payload['acts'])} actions · {len(snaps)} snapshots")
conn.close()
