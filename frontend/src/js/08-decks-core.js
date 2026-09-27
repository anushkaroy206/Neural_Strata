(function(){
'use strict';

const U={};
U.$=(s,r)=>(r||document).querySelector(s);
U.$$=(s,r)=>Array.prototype.slice.call((r||document).querySelectorAll(s));
U.clamp=(v,a,b)=>v<a?a:(v>b?b:v);
U.lerp=(a,b,t)=>a+(b-a)*t;
U.fmt=(v,d)=>v==null||!isFinite(v)?'—':Number(v).toFixed(d===undefined?1:d);
U.MONO='ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace';
U.rgba=function(hex,a){
  const h=String(hex).replace('#','');
  const s=h.length===3?h[0]+h[0]+h[1]+h[1]+h[2]+h[2]:h;
  const n=parseInt(s,16);
  return 'rgba('+((n>>16)&255)+','+((n>>8)&255)+','+(n&255)+','+a+')';
};
U.pad2=n=>String(n).padStart(2,'0');
U.hhmmss=d=>U.pad2(d.getHours())+':'+U.pad2(d.getMinutes())+':'+U.pad2(d.getSeconds());
U.iso=d=>d.getFullYear()+'-'+U.pad2(d.getMonth()+1)+'-'+U.pad2(d.getDate());
let _seed=987654321;
U.rnd=()=>{_seed=(Math.imul(_seed,1664525)+1013904223)>>>0;return _seed/4294967296;};
U.rr=(a,b)=>a+U.rnd()*(b-a);
U.fnv=function(s){
  let h=2166136261;
  for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)>>>0;}
  return ('0000000'+h.toString(16)).slice(-8);
};
U.ctx=function(c){
  if(!c||!c.getBoundingClientRect)return null;
  const b=c.getBoundingClientRect();
  if(b.width<4||b.height<4)return null;
  let x=null;
  try{x=c.getContext('2d');}catch(e){NSD.paintFail(c.id,'getContext threw: '+e.message);return null;}
  if(!x){NSD.paintFail(c.id,'this browser refused a 2D canvas context');return null;}
  const r=Math.min(window.devicePixelRatio||1,2);
  const w=Math.round(b.width*r),h=Math.round(b.height*r);
  if(c.width!==w||c.height!==h){c.width=w;c.height=h;}
  x.setTransform(r,0,0,r,0,0);
  x.clearRect(0,0,b.width,b.height);
  NSD.paintOk(c.id);
  return {x:x,w:b.width,h:b.height};
};
U.rrect=function(c,x,y,w,h,r){
  r=Math.min(r,Math.abs(w)/2,Math.abs(h)/2);
  c.beginPath();
  c.moveTo(x+r,y);c.lineTo(x+w-r,y);c.quadraticCurveTo(x+w,y,x+w,y+r);
  c.lineTo(x+w,y+h-r);c.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
  c.lineTo(x+r,y+h);c.quadraticCurveTo(x,y+h,x,y+h-r);
  c.lineTo(x,y+r);c.quadraticCurveTo(x,y,x+r,y);c.closePath();
};
U.dl=function(name,text,type){
  const b=new Blob([text],{type:type||'text/html;charset=utf-8'});
  const u=URL.createObjectURL(b), a=document.createElement('a');
  a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(u),2000);
  if(EMBED)NSD.toast('<b>'+NSD.esc(name)+'</b> sent to your downloads. An embedded preview frame can block file saves — '+
    'if it did not appear, open <b>index.html</b> in its own tab (or save it and open it locally) and try again.');
  else NSD.toast('<b>'+NSD.esc(name)+'</b> saved to your downloads folder.');
};
U.num=function(v,d){return Number(v).toFixed(d===undefined?1:d);};
const SEG_KEYS=['m','v','lvlf','busf','w','cf','rn','win','k','f','cf'];
U.seg=function(el,cb){
  if(!el)return null;
  const btns=U.$$('button',el);
  const val=function(b){
    for(let i=0;i<SEG_KEYS.length;i++){
      if(b.dataset[SEG_KEYS[i]]!==undefined)return b.dataset[SEG_KEYS[i]];
    }
    return b.textContent.trim();
  };
  const pick=function(b,fire){
    if(!b)return;
    btns.forEach(x=>{
      const on=x===b;
      x.classList.toggle('on',on);
      x.setAttribute('aria-pressed',on?'true':'false');
    });
    if(fire&&cb)cb(val(b),b,el);
  };
  btns.forEach(b=>b.addEventListener('click',()=>pick(b,true)));
  const cur=btns.filter(b=>b.classList.contains('on'))[0]||btns[0];
  if(cur&&cb)cb(val(cur),cur,el);
  return {pick:pick,val:val,get:()=>btns.filter(b=>b.classList.contains('on'))[0]};
};
U.frame=function(c,pad){
  const ctx=U.ctx(c); if(!ctx)return null;
  const c2=ctx.x,W=ctx.w,H=ctx.h;
  const p=Object.assign({l:34,r:8,t:10,b:20},pad||{});
  c2.strokeStyle='rgba(255,255,255,.06)';c2.lineWidth=1;
  const gy=4,gx=4;
  for(let i=0;i<=gy;i++){const y=Math.round(p.t+(H-p.t-p.b)*i/gy)+.5;
    c2.beginPath();c2.moveTo(p.l,y);c2.lineTo(W-p.r,y);c2.stroke();}
  for(let i=0;i<=gx;i++){const x=Math.round(p.l+(W-p.l-p.r)*i/gx)+.5;
    c2.beginPath();c2.moveTo(x,p.t);c2.lineTo(x,H-p.b);c2.stroke();}
  return {c:c2,w:W,h:H,p:p};
};

const NSD=window.NSD={U:U,ticks:[],enters:{},state:{view:'overview',now:Date.now()}};

const HEALTH={painted:{},notes:{},errs:[],fails:{}};
NSD.health=HEALTH;
function noteEl(id){return document.querySelector('.canvasnote[data-canvas="'+id+'"]');}
NSD.paintOk=function(id){
  if(HEALTH.painted[id]||!id)return;
  HEALTH.painted[id]=Date.now();
  if(HEALTH.notes[id]!==false){
    const n=noteEl(id);
    if(n){n.style.opacity='0';n.style.pointerEvents='none';HEALTH.notes[id]=false;}
  }
};
NSD.paintFail=function(id,why){
  HEALTH.fails[id]=why;
  const n=noteEl(id);
  if(!n)return;
  n.style.opacity='1';n.style.pointerEvents='auto';
  n.innerHTML='<b>CANVAS BLOCKED</b> · '+NSD.esc(why)+'<br>'+
    '<button type="button" class="nb" style="margin-top:6px;font:inherit;padding:4px 9px;border-radius:8px;'+
    'border:1px solid rgba(255,149,56,.6);background:rgba(255,149,56,.12);color:#ffc061;cursor:pointer">'+
    'RENDERER CHECK</button>';
  const b=n.querySelector('.nb');
  if(b)b.addEventListener('click',function(){NSD.diag(true);});
};
window.addEventListener('error',function(e){
  if(HEALTH.errs.length<12)HEALTH.errs.push(String(e.message).slice(0,160)+' · '+String(e.filename||'').split('/').pop()+':'+e.lineno);
},{capture:false});
window.addEventListener('unhandledrejection',function(e){
  if(HEALTH.errs.length<12)HEALTH.errs.push('unhandled rejection · '+String(e.reason&&e.reason.message||e.reason).slice(0,120));
});
NSD.diag=function(force){
  let d=document.getElementById('nsDiag');
  const can2d=(function(){try{const c=document.createElement('canvas');return !!c.getContext('2d');}catch(e){return false;}})();
  const ua=navigator.userAgent;
  const canvasRows=[];
  U.$$('canvas').forEach(function(c){
    const r=c.getBoundingClientRect();
    canvasRows.push('  '+(c.id||'(anon)')+' · buffer '+c.width+'x'+c.height+' · css '+Math.round(r.width)+'x'+Math.round(r.height)+
      ' · '+(HEALTH.painted[c.id]?'painted':'NOT PAINTED'));
  });
  const txt=[
    'Neural Strata renderer check — '+new Date().toLocaleString(),
    'UA          : '+ua,
    'viewport    : '+window.innerWidth+'x'+window.innerHeight+' · dpr '+window.devicePixelRatio+' · embedded '+EMBED,
    'canvas 2d   : '+(can2d?'available':'BLOCKED by the browser'),
    'webgl       : '+(function(){try{const c=document.createElement('canvas');return !!(c.getContext('webgl')||c.getContext('experimental-webgl'));}catch(e){return false;}})(),
    'deck scripts: NSD='+(typeof window.NSD)+' OP='+(typeof window.__OP)+' PL='+(typeof window.__PL)+' RG='+(typeof window.__RG),
    'active view : '+NSD.st.view,
    'raf loop    : '+(NSD.rafTicks||0)+' ticks · timer watchdog '+NSD.wdTicks+' passes',
    HEALTH.errs.length?('errors ('+HEALTH.errs.length+'):\n  '+HEALTH.errs.join('\n  ')):'errors      : none',
    Object.keys(HEALTH.fails).length?('canvas fails:\n  '+Object.entries(HEALTH.fails).map(([k,v])=>k+': '+v).join('\n  ')):'canvas fails: none',
    'canvases:',
    canvasRows.join('\n')
  ].join('\n');
  if(d){d.remove();return;}
  d=document.createElement('div');d.id='nsDiag';
  d.style.cssText='position:fixed;inset:12px 12px auto 12px;z-index:99998;max-height:80vh;overflow:auto;'+
    'background:rgba(8,11,16,.97);border:1px solid rgba(124,198,240,.5);border-radius:12px;padding:12px 14px;'+
    'font:11.5px/1.5 ui-monospace,Menlo,Consolas,monospace;color:#cfe2f5;white-space:pre-wrap';
  d.textContent=txt;
  const b=document.createElement('button');
  b.textContent='CLOSE';b.style.cssText='position:sticky;top:0;float:right;margin:-4px -4px 6px 10px;font:inherit;'+
    'padding:3px 10px;border-radius:8px;border:1px solid rgba(124,198,240,.5);background:rgba(124,198,240,.12);color:#bfe4fb;cursor:pointer';
  b.addEventListener('click',function(){d.remove();});
  d.appendChild(b);
  document.body.appendChild(d);
};

const EMBED=(function(){try{return window.self!==window.top;}catch(e){return true;}})();
NSD.embedded=EMBED;
let toastT=0;
NSD.toast=function(msg,ms){
  let t=document.getElementById('nsToast');
  if(!t){
    t=document.createElement('div');t.id='nsToast';
    t.style.cssText='position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:9999;'+
      'max-width:min(560px,92vw);padding:11px 16px;border-radius:12px;font-size:12.5px;line-height:1.45;'+
      'background:rgba(10,14,20,.96);color:#dbe7f5;border:1px solid rgba(120,170,240,.45);'+
      'box-shadow:0 12px 34px rgba(0,0,0,.55);pointer-events:none;transition:opacity .35s;opacity:0';
    document.body.appendChild(t);
  }
  t.innerHTML=msg;t.style.opacity='1';
  clearTimeout(toastT);
  toastT=setTimeout(()=>{t.style.opacity='0';},ms||5200);
};

NSD.st=NSD.state;
NSD.onTick=fn=>NSD.ticks.push(fn);
NSD.onEnter=(view,fn)=>{NSD.enters[view]=fn;};
NSD.tabHooks={};
NSD.onTab=(view,fn)=>{(NSD.tabHooks[view]=NSD.tabHooks[view]||[]).push(fn);};
NSD.fireTab=function(view){
  const hs=NSD.tabHooks[view]||[];
  for(let i=0;i<hs.length;i++){try{hs[i]();}catch(e){if(!e._ns){e._ns=1;console.error(e);}}}
};
NSD.esc=s=>String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));

const METHODS={LW:'Longwall',BP:'Bord & Pillar'};
const MINES=[
  {n:'KUSUNDA',z:'ECL',x:0.782,y:0.352,m:'BP',idx:71,l3:1,mesh:96.4,ack:8.4,cal:2,officer:'R. K. Singh',last:'2026-09-04'},
  {n:'DHANSAR',z:'ECL',x:0.771,y:0.363,m:'LW',idx:88,l3:0,mesh:99.2,ack:4.1,cal:0,officer:'R. K. Singh',last:'2026-09-11'},
  {n:'SUDAMDIH',z:'ECL',x:0.793,y:0.344,m:'BP',idx:62,l3:2,mesh:91.8,ack:12.6,cal:3,officer:'R. K. Singh',last:'2026-08-29'},
  {n:'BHOWRAH',z:'ECL',x:0.787,y:0.337,m:'BP',idx:79,l3:0,mesh:97.5,ack:6.2,cal:1,officer:'S. Mahato',last:'2026-09-08'},
  {n:'JEENAGORA',z:'BCCL',x:0.760,y:0.362,m:'LW',idx:58,l3:2,mesh:88.3,ack:14.9,cal:4,officer:'S. Mahato',last:'2026-08-27'},
  {n:'KATRAS',z:'BCCL',x:0.748,y:0.373,m:'BP',idx:84,l3:0,mesh:98.1,ack:5.0,cal:0,officer:'S. Mahato',last:'2026-09-12'},
  {n:'GOVINDPUR',z:'BCCL',x:0.769,y:0.373,m:'LW',idx:91,l3:0,mesh:99.6,ack:3.4,cal:0,officer:'A. Bhanja',last:'2026-09-14'},
  {n:'ARGADA',z:'CCL',x:0.700,y:0.408,m:'BP',idx:76,l3:0,mesh:95.9,ack:7.1,cal:1,officer:'A. Bhanja',last:'2026-09-06'},
  {n:'SAYAL',z:'CCL',x:0.713,y:0.419,m:'LW',idx:66,l3:1,mesh:92.7,ack:10.2,cal:2,officer:'A. Bhanja',last:'2026-09-02'},
  {n:'GIDI',z:'CCL',x:0.690,y:0.425,m:'BP',idx:81,l3:0,mesh:96.8,ack:6.6,cal:1,officer:'N. Tirkey',last:'2026-09-09'},
  {n:'WANI',z:'WCL',x:0.470,y:0.452,m:'LW',idx:87,l3:0,mesh:98.9,ack:4.8,cal:0,officer:'N. Tirkey',last:'2026-09-13'},
  {n:'KAMPTEE',z:'WCL',x:0.452,y:0.419,m:'BP',idx:74,l3:0,mesh:94.6,ack:9.3,cal:2,officer:'N. Tirkey',last:'2026-09-05'},
  {n:'KORBA',z:'SECL',x:0.612,y:0.452,m:'LW',idx:82,l3:0,mesh:97.9,ack:5.7,cal:1,officer:'P. Verma',last:'2026-09-10'},
  {n:'BISHRAMPUR',z:'SECL',x:0.628,y:0.436,m:'BP',idx:69,l3:1,mesh:93.4,ack:11.4,cal:2,officer:'P. Verma',last:'2026-09-03'}
];
MINES.forEach(m=>{m.live={l3:m.l3,mesh:m.mesh,ack:m.ack};m.name=m.n.charAt(0)+m.n.slice(1).toLowerCase();});
NSD.mines=MINES;
NSD.state.mineLocal='KUSUNDA';
NSD.mine=function(name){return MINES.filter(m=>m.n===name)[0];};
NSD.escalate=function(sev){
  const m=NSD.mine(NSD.state.mineLocal);
  if(!m)return;
  if(sev==='critical'){m.live.l3=Math.max(1,m.live.l3);m.live.mesh=Math.min(m.live.mesh,94.2);}
  else if(sev==='warning'){m.live.l3=Math.max(0,m.live.l3);}
  m.live.mesh=Math.max(85,+(m.live.mesh-0.35).toFixed(1));
};
NSD.deescalate=function(){const m=NSD.mine(NSD.state.mineLocal);if(m)m.live.mesh=Math.min(m.mesh,m.live.mesh+0.3);};
NSD.status=function(m){
  if(m.live.l3>=2||m.live.mesh<92)return 'crit';
  if(m.live.l3===1||m.live.mesh<97)return 'warn';
  return 'ok';
};
NSD.liveIndex=function(m){
  return Math.round(U.clamp(m.idx-(m.live.l3*4)-(m.mesh<99?(99-m.live.mesh)*1.6:0),5,100));
};

const LED={blocks:[],head:'0000000000000000',subs:[]};
function hashBlock(b,prev){
  const s=prev+'|'+b.i+'|'+b.t+'|'+b.mine+'|'+b.ev+'|'+b.actor;
  const h1=U.fnv(s);
  const h2=U.fnv(s.split('').reverse().join(''));
  return (h1+h2).slice(0,16);
}
LED.add=function(ev,opts){
  opts=opts||{};
  const d=opts.at?new Date(opts.at):new Date();
  const b={i:LED.blocks.length+1,t:U.iso(d)+' '+U.pad2(d.getHours())+':'+U.pad2(d.getMinutes())+':'+U.pad2(d.getSeconds()),
    mine:opts.mine||NSD.mine(NSD.state.mineLocal).n,ev:ev,actor:opts.actor||'SYSTEM',
    sev:opts.sev||'INFO',prev:LED.head};
  b.hash=hashBlock(b,b.prev);
  LED.blocks.push(b);LED.head=b.hash;
  LED.subs.forEach(f=>{try{f(b)}catch(e){}});
  return b;
};
LED.verify=function(){
  let prev='0000000000000000';
  for(let i=0;i<LED.blocks.length;i++){
    const b=LED.blocks[i];
    if(b.prev!==prev)return {ok:false,at:b.i,why:'previous-hash mismatch'};
    if(hashBlock(b,prev)!==b.hash)return {ok:false,at:b.i,why:'payload hash mismatch'};
    prev=b.hash;
  }
  return {ok:true,at:LED.blocks.length,why:'chain intact'};
};
LED.sub=fn=>LED.subs.push(fn);
NSD.ledger=LED;

(function seedLedger(){
  const now=Date.now();
  const rows=[
    [0,'DHANSAR','SHIFT OPEN · STRATA MONITORING PLAN VERIFIED',  'COLLIERY MANAGER','INFO', 2.2],
    [0,'KUSUNDA','PANEL P-3 SECOND CUT · MONITORING ARMED',      'OVERMAN S. DAS','INFO', 5.6],
    [0,'GOVINDPUR','PERIODIC CALIBRATION CHECK · 12 NODES PASS','SYSTEM','INFO', 8.1],
    [0,'JEENAGORA','RING SEGMENT CUT 22 min · WRAPPED · ALL HUBS UP','SYSTEM','WARNING', 13.9],
    [0,'SUDAMDIH','TILT 5.8 mm/m · EXCEEDS 5 mm/m LIMIT',        'SYSTEM','CRITICAL', 21.5],
    [0,'SUDAMDIH','OPERATOR ACK · 22.4 min AFTER ALERT',         'R. SEN (SIRDAR)','WARNING', 22.1],
    [0,'KUSUNDA','PPV SPIKE 3.1 mm/s · TAGGED BLASTING',         'SYSTEM','WARNING', 27.4],
    [0,'KATRAS','WEEKLY RETURN FILED · FORM 1-A',                'COLLIERY MANAGER','INFO', 31.2],
    [1,'KORBA','NODE K-14 BATTERY CRITICAL · DEEP SLEEP',        'SYSTEM','WARNING', 38.5],
    [1,'ARGADA','SURFACE CRACK VECTOR REGISTERED · 42 m',        'GEOLOGIST','WARNING', 44.0],
    [1,'SAYAL','TILT 5.4 mm/m · 60 s SUSTAINED',                 'SYSTEM','CRITICAL', 49.7],
    [1,'SUDAMDIH','CALIBRATION CERT EXPIRED · TL-07',            'SYSTEM','WARNING', 55.3],
    [1,'KUSUNDA','EAP SILENT DRILL COMPLETED · 38 s',            'DGM OFFICER','INFO', 60.4],
    [2,'BISHRAMPUR','MANUAL SILENCE · NO OPERATOR ID LOGGED',    'UNKNOWN','CRITICAL', 66.9],
    [2,'GIDI','INQUIRY REPORT PACK GENERATED',                    'DGMS OFFICER','INFO', 71.6],
    [2,'KUSUNDA','MONTHLY MESH UPTIME 96.4 % · OBSERVATION',     'SYSTEM','INFO', 76.2]
  ];
  rows.forEach(function(r){
    LED.add(r[2],{mine:r[1],actor:r[3],sev:r[4],at:now-r[0]*86400000-r[5]*3600000});
  });
})();

let mdl=null,mdlOpen=false,mdlCb=null;
function ensureModal(){
  if(mdl)return;
  mdl=document.createElement('div');
  mdl.className='mdl';
  mdl.id='nsMdl';
  mdl.setAttribute('role','dialog');mdl.setAttribute('aria-modal','true');
  mdl.innerHTML='<div class="mdl-card">'+
    '<div class="mdl-head"><h4 id="nsMdlT"><b>—</b></h4>'+
    '<button class="btnx" id="nsMdlX" aria-label="Close dialog">CLOSE ✕</button></div>'+
    '<div class="mdl-body" id="nsMdlB"></div><div class="mdl-foot" id="nsMdlF"></div></div>';
  document.body.appendChild(mdl);
  mdl.addEventListener('mousedown',e=>{if(e.target===mdl)NSD.modal.close();});
  U.$('#nsMdlX',mdl).addEventListener('click',()=>NSD.modal.close());
}
function mdlEsc(e){
  if(e.key!=='Escape')return;
  e.preventDefault();e.stopPropagation();
  NSD.modal.close();
}
NSD.modal={
  get isOpen(){return mdlOpen;},
  open:function(cfg){
    ensureModal();
    const card=U.$('.mdl-card',mdl);
    card.classList.toggle('wide',!!cfg.wide);
    U.$('#nsMdlT',mdl).innerHTML=NSD.esc(cfg.title||'')+(cfg.sub?'<b>'+NSD.esc(cfg.sub)+'</b>':'');
    const b=U.$('#nsMdlB',mdl);
    if(typeof cfg.body==='string')b.innerHTML=cfg.body;
    else {b.innerHTML='';if(cfg.body)b.appendChild(cfg.body);}
    const f=U.$('#nsMdlF',mdl);
    f.innerHTML='';
    (cfg.buttons||[]).forEach(function(bt){
      const el=document.createElement('button');
      el.className='btnx '+(bt.cls||'');
      el.textContent=bt.label;
      if(bt.disabled)el.disabled=true;
      el.addEventListener('click',()=>{if(bt.onClick)bt.onClick(el);else NSD.modal.close();});
      f.appendChild(el);
    });
    if(cfg.onOpen)setTimeout(()=>cfg.onOpen(b,f),0);
    mdlCb=cfg.onClose||null;
    mdlOpen=true;mdl.classList.add('on');
    window.removeEventListener('keydown',mdlEsc,true);
    window.addEventListener('keydown',mdlEsc,true);
    const first=f.querySelector('button:not([disabled])')||b.querySelector('input,select,button');
    if(first)setTimeout(()=>first.focus(),40);
  },
  close:function(){
    if(!mdlOpen)return;
    mdlOpen=false;if(mdl)mdl.classList.remove('on');
    window.removeEventListener('keydown',mdlEsc,true);
    if(mdlCb){const c=mdlCb;mdlCb=null;try{c();}catch(e){}}
  }
};

const DOC_CSS="body{margin:0;background:#e9e5e0;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1c1a18}"+
 ".doc{max-width:900px;margin:22px auto;background:#f7f5f2;padding:34px 40px;border-radius:10px;font-size:12.5px;line-height:1.62;box-shadow:0 20px 50px rgba(0,0,0,.25)}"+
 ".doc h1{font-size:19px;margin:0 0 4px}.doc h2{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#6b625a;margin:20px 0 8px;border-bottom:1px solid #d9d2ca;padding-bottom:5px}"+
 ".doc .sub{color:#6b625a;font-size:11.5px}.doc table{width:100%;border-collapse:collapse;font-size:11.5px;margin:6px 0}"+
 ".doc th{background:#ece7e1;text-align:left;padding:6px 8px;font-size:9.5px;letter-spacing:.12em;border:1px solid #ddd5cc}"+
 ".doc td{padding:6px 8px;border:1px solid #e4ded7;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px}"+
 ".doc .grid2d{display:grid;grid-template-columns:1fr 1fr;gap:16px}.doc .fig{border:1px solid #ddd5cc;border-radius:9px;padding:8px;background:#fff}"+
 ".doc .fig img{width:100%;display:block}.doc .fig .cap{font-size:9.5px;color:#6b625a;margin-top:6px}"+
 ".doc .figs{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}"+
 ".doc .note{background:#efe9e2;border-left:3px solid #b9a88f;padding:9px 12px;font-size:11.5px;margin-top:8px}"+
 ".doc .sig{display:flex;justify-content:space-between;margin-top:28px;font-size:11px}"+
 ".doc .sig div{border-top:1px solid #b7afa7;padding-top:6px;width:44%;text-align:center}"+
 ".doc .stamp{display:inline-block;border:2px solid #b23b3b;color:#b23b3b;border-radius:8px;padding:7px 13px;font-weight:800;letter-spacing:.14em;font-size:10px;transform:rotate(-2deg)}";
NSD.report={
  img:function(canvas,caption){
    if(!canvas||!canvas.width)return '';
    let d='';
    try{d=canvas.toDataURL('image/png');}catch(e){return '';}
    return '<div class="fig"><img src="'+d+'" alt=""><div class="cap">'+NSD.esc(caption||'')+'</div></div>';
  },
  table:function(cols,rows){
    return '<table><thead><tr>'+cols.map(c=>'<th>'+NSD.esc(c)+'</th>').join('')+'</tr></thead><tbody>'+
      rows.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
  },
  doc:function(cfg){
    return '<div class="doc"><h1>'+NSD.esc(cfg.title)+'</h1>'+
      (cfg.sub?'<div class="sub">'+NSD.esc(cfg.sub)+'</div>':'')+
      (cfg.stamp?'<div style="margin:10px 0"><span class="stamp">'+NSD.esc(cfg.stamp)+'</span></div>':'')+
      (cfg.meta?NSD.report.table(['FIELD','VALUE'],cfg.meta):'')+
      cfg.html+(cfg.sign!==false?('<div class="sig"><div>'+NSD.esc(cfg.signL||'Colliery Manager')+'</div><div>'+
        NSD.esc(cfg.signR||'DGMS Officer')+'</div></div>'):'')+'</div>';
  },
  standalone:function(title,inner){
    return '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'+
      '<meta name="viewport" content="width=device-width,initial-scale=1">'+
      '<title>'+NSD.esc(title)+'</title><style>'+DOC_CSS+'</style></head><body>'+inner+'</body></html>';
  },
  print:function(inner){
    let r=document.getElementById('printRoot');
    if(!r){r=document.createElement('div');r.id='printRoot';document.body.appendChild(r);}
    r.innerHTML=inner;
    setTimeout(function(){
      try{window.print();}catch(e){ }
      if(EMBED)NSD.toast('If the print dialog did not open, this preview frame is blocking it — use '+
        '<b>DOWNLOAD .HTML</b> here, then print that file from a normal browser tab.');
    },60);
  },
  save:function(filename,title,inner){
    U.dl(filename,NSD.report.standalone(title,inner));
  },
  modal:function(cfg){
    NSD.modal.open({
      wide:true,title:'STATUTORY DOCUMENT PREVIEW',sub:cfg.sub,
      body:(cfg.note?'<div class="sub" style="margin:0 0 10px">'+cfg.note+'</div>':'')+
        '<div style="max-height:62vh;overflow:auto;border-radius:12px">'+cfg.html+'</div>',
      buttons:[
        {label:'PRINT / SAVE AS PDF',cls:'pri',onClick:()=>NSD.report.print(cfg.html)},
        {label:'DOWNLOAD .HTML',cls:'blue',onClick:()=>NSD.report.save(cfg.file,cfg.title,cfg.html)},
        {label:'CLOSE'}
      ],
      onClose:cfg.onClose
    });
  }
};

const DECKS={operator:1,planner:1,regulator:1};
function syncRoles(view){
  U.$$('.rb[data-deck]').forEach(b=>b.classList.toggle('on',b.dataset.deck===view));
  U.$$('.rb[data-deck]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.deck===view?'true':'false'));
}
const _show=window.show;
window.show=function(v){
  if(typeof _show==='function')_show.apply(null,arguments);
  NSD.st.view=v;syncRoles(v);
  if(DECKS[v]&&NSD.enters[v]){try{NSD.enters[v]();}catch(e){console.error(e);}}
};
if(U.$('#v-operator'))syncRoles('overview');
U.$$('.rb[data-deck]').forEach(b=>{
  b.addEventListener('click',()=>window.show(b.dataset.deck));
});
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'&&NSD.modal.isOpen){e.preventDefault();e.stopPropagation();NSD.modal.close();return;}
  if(e.metaKey||e.ctrlKey||e.altKey)return;
  const t=e.target;
  if(t&&(t.tagName==='INPUT'||t.tagName==='SELECT'||t.tagName==='TEXTAREA'||t.isContentEditable))return;
  const k={'7':'operator','8':'planner','9':'regulator'}[e.key];
  if(k){e.preventDefault();window.show(k);}
},true);

let last=0;
function runTicks(now,dt){
  for(let i=0;i<NSD.ticks.length;i++){
    try{NSD.ticks[i](now,dt);}catch(err){if(!err._ns){err._ns=1;console.error(err);}}
  }
}
function frame(now){
  const dt=Math.min(0.06,(now-last)/1000||0.016);last=now;
  if(!document.hidden){
    NSD.rafTicks=(NSD.rafTicks||0)+1;
    runTicks(now,dt);
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

const TABKEY='ns.tab.';
const DEFAULT_TAB={operator:'shift',planner:'gis',regulator:'risk'};
NSD.tabOf=function(deck){
  const nav=document.querySelector('.dtabs[data-deck="'+deck+'"]');
  const on=nav&&nav.querySelector('.dtab.on');
  return on?on.dataset.tab:(DEFAULT_TAB[deck]||null);
};
NSD.tabsOf=function(deck){
  return [].slice.call(document.querySelectorAll('.dtabs[data-deck="'+deck+'"] .dtab')).map(b=>b.dataset.tab);
};
function fitRows(view){
  [].slice.call(view.querySelectorAll('.drow')).forEach(function(row){
    if(row.dataset.tab){
      if(row.classList.contains('taboff'))row.classList.remove('one');
      return;
    }
    const kids=[].slice.call(row.children).filter(c=>c.nodeType===1);
    const vis=kids.filter(c=>!c.classList.contains('taboff'));
    row.classList.toggle('one',vis.length===1&&kids.length>1);
    row.classList.toggle('taboff',vis.length===0);
  });
  [].slice.call(view.querySelectorAll('.deck .deck')).forEach(function(d){
    if(d.dataset.tab&&d.classList.contains('taboff')){d.classList.remove('one');return;}
    const kids=[].slice.call(d.children).filter(c=>c.nodeType===1);
    const vis=kids.filter(c=>!c.classList.contains('taboff'));
    d.classList.toggle('one',vis.length===1&&kids.length>1);
  });
}
NSD.setTab=function(deck,tab,opts){
  opts=opts||{};
  const view=document.getElementById('v-'+deck);
  if(!view)return false;
  const nav=view.querySelector('.dtabs[data-deck="'+deck+'"]');
  if(!nav)return false;
  const btn=nav.querySelector('.dtab[data-tab="'+tab+'"]');
  if(!btn)return false;
  view.dataset.tab=tab;
  [].slice.call(nav.querySelectorAll('.dtab')).forEach(function(b){
    const on=b.dataset.tab===tab;
    b.classList.toggle('on',on);
    b.setAttribute('aria-selected',on?'true':'false');
    b.tabIndex=on?0:-1;
  });
  [].slice.call(view.querySelectorAll('[data-tab]')).forEach(function(u){
    if(u.closest('.dtabs'))return;
    u.classList.toggle('taboff',u.dataset.tab!==tab);
  });
  fitRows(view);
  if(opts.quiet!==true){try{sessionStorage.setItem(TABKEY+deck,tab);}catch(e){}}
  NSD.tabStamp=Date.now();
  NSD.tabRepaints=(NSD.tabRepaints||0)+1;
  runTicks(performance.now(),0.016);
  NSD.fireTab(deck);
  requestAnimationFrame(function(){runTicks(performance.now(),0.016);NSD.fireTab(deck);});
  if(opts.toast){
    NSD.toast('<b>'+NSD.esc(btn.textContent.split('\n')[0].trim())+'</b> — '+
      '<span style="opacity:.85">'+(btn.querySelector('i')?NSD.esc(btn.querySelector('i').textContent):'')+'</span>');
  }
  return true;
};
[].slice.call(document.querySelectorAll('.dtabs')).forEach(function(nav){
  const deck=nav.dataset.deck;
  nav.setAttribute('role','tablist');
  [].slice.call(nav.querySelectorAll('.dtab')).forEach(function(b){
    b.setAttribute('role','tab');
    b.addEventListener('click',function(){NSD.setTab(deck,b.dataset.tab);});
    b.addEventListener('keydown',function(e){
      const list=NSD.tabsOf(deck);
      const i=list.indexOf(b.dataset.tab);
      if(e.key==='ArrowRight'||e.key==='ArrowDown'){e.preventDefault();NSD.setTab(deck,list[(i+1)%list.length]);}
      if(e.key==='ArrowLeft'||e.key==='ArrowUp'){e.preventDefault();NSD.setTab(deck,list[(i+list.length-1)%list.length]);}
    });
  });
  if(DEFAULT_TAB[deck])NSD.setTab(deck,DEFAULT_TAB[deck],{quiet:true});
});
document.addEventListener('keydown',function(e){
  if(!e.altKey||e.metaKey||e.ctrlKey||e.shiftKey)return;
  const i={'1':0,'2':1,'3':2,'4':3,'5':4}[e.key];
  if(i===undefined)return;
  const v=(NSD.st&&NSD.st.view)||null;
  if(!v||!DEFAULT_TAB[v])return;
  const list=NSD.tabsOf(v);
  if(list[i]){e.preventDefault();NSD.setTab(v,list[i],{toast:true});}
},true);

NSD.wdTicks=0;
const PRIMARY={operator:'opTopo',planner:'plMap',regulator:'rgMap'};
let wdView=null,wdSince=0;
setInterval(function(){
  if(document.hidden)return;
  NSD.wdTicks++;
  runTicks(performance.now(),0.016);
  const v=NSD.st.view;
  if(v!==wdView){wdView=v;wdSince=Date.now();}
  const id=PRIMARY[v];
  if(id&&Date.now()-Math.max(wdSince,NSD.tabStamp||0)>3000){
    const el=document.getElementById(id);
    const parked=el&&el.closest('.taboff');
    if(el&&!parked&&!HEALTH.painted[id]&&!HEALTH.fails[id])
      NSD.paintFail(id,'the deck could not draw this canvas in the current browser');
  }
},1200);
if(/#debug|\?debug/.test(location.hash+location.search))setTimeout(function(){NSD.diag(true);},1500);

NSD.swipeKeys=function(el,onStep){
  el.addEventListener('keydown',function(e){
    let d=0;
    if(e.key==='ArrowRight'||e.key==='ArrowUp')d=0.18;
    else if(e.key==='ArrowLeft'||e.key==='ArrowDown')d=-0.18;
    else if(e.key===' '||e.key==='Enter')d=1;
    else return;
    e.preventDefault();
    onStep(d);
  });
};
})();
