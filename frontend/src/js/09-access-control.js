(function(){
'use strict';
const NSD=window.NSD;if(!NSD)return;
const U=NSD.U,$=U.$,$$=U.$$;
const LOCAL=NSD.state.mineLocal||'KUSUNDA';
const BUILD=String(Math.abs(parseInt(U.fnv('ns-v4-2'),16)%9973)+1)+'-'+U.fnv('rbac').slice(0,4).toUpperCase();

const ICON={
  operator:'<path d="M4 20h16M6 20V9l6-4 6 4v11"/><path d="M10 20v-5h4v5M12 9v2"/>',
  planner:'<path d="M4 18V6l8 3 8-3v12l-8 3-8-3z"/><path d="M12 9v12M4 12h8M12 12h8"/>',
  regulator:'<path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/>'
};
const WORKSPACES={
  overview:'SECTOR OVERVIEW', telemetry:'TELEMETRY', control:'CONTROL', records:'MUSTER & LOG',
  evacmap:'EVAC MAP', ai:'AI PREDICTION', operator:'OPERATOR DECK', planner:'PLANNER DECK', regulator:'REGULATOR DECK'
};
const ROLE_ACCESS={
  operator: ['overview','telemetry','control','records','evacmap','ai'],
  planner:  ['overview','telemetry','records','ai'],
  regulator:['overview','telemetry','records']
};
const ROLES=[
  {key:'operator',badge:'MINE OPERATOR',title:'Mine Operator / Shift Charge',
   id:'ECL-4471',who:'Shift-II charge · A. K. Mahato',accent:'#ffb648',
   unlocks:['Shift Command HUD, edge-hub sync pulse & safety banner',
            'Underground double ring (M-01 ↔ MH-1 ↔ MH-2 ↔ M-01), 2 evac buses, 4 buzzers, ESP-NOW surface mesh',
            'Tilt dials, displacement sparklines & PPV auto-tagging',
            'Alarm triage (swipe + Operator ID) and two-step EAP',
            'Mass SMS / app broadcast to statutory recipients'],
   denied:['cross-mine DGMS analytics','subsidence modelling & Form 1-A export','the append-only regulatory ledger']},

  {key:'planner',badge:'MINE PLANNER / GEOLOGIST',title:'Mine Planner / Geologist',
   id:'GEO-2290',who:'Strata planning cell · P-3 depillaring',accent:'#5aa9d8',
   unlocks:['GIS surface-to-underground superimposition & layer toggles',
            'RMR calibrator, mining-method baseline & borehole profiler',
            'Time-to-failure projection, anomaly clustering, velocity bars',
            'Deformation time-lapse scrubber and trough forensics',
            'SMP / DGMS Form 1-A generator and statutory export'],
   denied:['underground alarm triage & EAP control','cross-mine regulatory auditing']},

  {key:'regulator',badge:'DGMS REGULATOR',title:'DGMS Officer / Regulator',
   id:'DGMS-1107',who:'Eastern Coalfields · statutory inspection',accent:'#7d8ce0',
   unlocks:['Multi-mine risk matrix & composite safety index',
            'Append-only session audit ledger (local, no cryptographic chain)',
            'Operator response SLA tracker and breach history',
            'Mesh uptime calendar & sensor calibration ledger',
            'Cross-mine radar comparison and inquiry annexures'],
   denied:['panel control, EAP triggering and broadcast rights','mine planning model & Form 1-A authoring']}
];
ROLES.forEach(r=>{r.views=ROLE_ACCESS[r.key].concat([r.key]);r.view=r.key;});

const BY_KEY={};ROLES.forEach(r=>{BY_KEY[r.key]=r;});
const VIEW_ROLE={operator:'operator',planner:'planner',regulator:'regulator'};

const KEY_S='ns.session', KEY_T='ns.attempts', KEY_L='ns.lockUntil';
const AUTH={role:null,id:null,at:0,attempts:0,lockedUntil:0,remember:true,lastReason:''};
NSD.auth=AUTH;
function store(k,v){try{if(v===null)sessionStorage.removeItem(k);else sessionStorage.setItem(k,v);}catch(e){}}
function read(k){try{return sessionStorage.getItem(k);}catch(e){return null;}}
function memo(k,v){try{if(v===null)localStorage.removeItem(k);else localStorage.setItem(k,v);}catch(e){}}
function memoread(k){try{return localStorage.getItem(k);}catch(e){return null;}}

const prevShow=window.show;
AUTH.can=function(view){
  if(!AUTH.role)return false;
  const r=BY_KEY[AUTH.role];if(!r)return false;
  if(view==='node')view='overview';
  return r.views.indexOf(view)>=0;
};
AUTH.views=function(){return AUTH.role?BY_KEY[AUTH.role].views.slice():[];};
let routing=false;
window.show=function(v){
  if(!AUTH.role){
    if(!routing){AUTH.fail(v);AUTH.gate(true);}
    return;
  }
  if(!AUTH.can(v)){
    const own=BY_KEY[AUTH.role].view;
    NSD.toast('<b>'+NSD.esc(BY_KEY[AUTH.role].badge)+'</b> is not cleared for the “'+NSD.esc(String(v))+'” '+
      'workspace. Signed-in role keeps to its own deck'+(v===own?'':' — opening your dashboard instead.'));
    if(v!==own&&!routing){routing=true;try{prevShow.call(null,own);}finally{routing=false;}}
    return;
  }
  return prevShow.apply(null,arguments);
};
AUTH.fail=function(view){
  NSD.toast('Sign in to open the strata platform — the <b>'+NSD.esc(String(view))+'</b> workspace is access-controlled.');
};

const E={};
['nsLogin','lgRoles','lgForm','lgRoleBadge','lgId','lgPin','lgEye','lgRemember','lgErr','lgGo',
 'lgBuild','lgBuildChip','lgConsoleState','lgKeypad','lgMatrix','lgClock','nsSess']
  .forEach(id=>{E[id]=document.getElementById(id);});
if(!E.nsLogin)return;
if(E.lgBuild)E.lgBuild.textContent=BUILD;
if(E.lgBuildChip)E.lgBuildChip.textContent='BUILD '+BUILD;
let picked=null;

function renderRoles(){
  E.lgRoles.innerHTML=ROLES.map(r=>
    '<button type="button" class="lg-role" data-r="'+r.key+'" role="radio" aria-checked="false" tabindex="-1">'+
      '<span class="lg-ic" style="color:'+r.accent+'"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '+
        'stroke-width="1.8" stroke-linecap="round">'+ICON[r.key]+'</svg></span>'+
      '<span class="lg-rb"><b>'+NSD.esc(r.badge)+'</b><span>'+NSD.esc(r.who)+'</span>'+
        '<span class="lg-ws">'+r.views.map(v=>'<span>'+NSD.esc(WORKSPACES[v]||v)+'</span>').join('')+'</span></span>'+
      '<span class="lg-tick"><i class="lg-led"></i>CLEARED FOR '+r.views.length+' WORKS · '+r.denied.length+' BLOCKED</span>'+
    '</button>').join('');
  $$('.lg-role',E.lgRoles).forEach(b=>{
    b.addEventListener('click',()=>pick(b.dataset.r));
    b.addEventListener('keydown',function(e){
      const i=ROLES.map(x=>x.key).indexOf(picked);
      if(e.key==='ArrowDown'||e.key==='ArrowRight'){e.preventDefault();pick(ROLES[(i+1)%ROLES.length].key);}
      if(e.key==='ArrowUp'||e.key==='ArrowLeft'){e.preventDefault();pick(ROLES[(i+ROLES.length-1)%ROLES.length].key);}
    });
  });
  renderMatrix();
}
function renderMatrix(){
  if(!E.lgMatrix)return;
  const cols=Object.keys(WORKSPACES);
  const head='<div class="lg-matrix-row head"><span>DUTY ROLE</span>'+
    cols.map(c=>'<span>'+NSD.esc((WORKSPACES[c]||c).replace('SECTOR ','').replace('MUSTER & LOG','MUSTER').replace('AI PREDICTION','AI').replace('OPERATOR DECK','OPERATOR').replace('PLANNER DECK','PLANNER').replace('REGULATOR DECK','REGULATOR'))+'</span>').join('')+'</div>';
  const SHORT={operator:'MINE OPERATOR',planner:'MINE PLANNER / GEOLOGIST',regulator:'DGMS REGULATOR'};
  const rows=ROLES.map(r=>'<div class="lg-matrix-row"><span class="lg-matrix-name">'+
    '<b style="color:'+r.accent+'">'+NSD.esc(SHORT[r.key])+'</b><em>'+NSD.esc(r.id)+'</em></span>'+
    cols.map(c=>{
      const own=r.key===c;
      const on=r.views.indexOf(c)>=0;
      return '<span class="lg-cell'+(on?(own?' deck':' on'):'')+'" title="'+
        NSD.esc(r.badge+' · '+WORKSPACES[c]+' · '+(on?(own?'exclusive deck':'granted'):'blocked'))+'">'+
        (on?'<i></i>':'<i style="background:transparent"></i>')+'</span>';
    }).join('')+'</div>').join('');
  E.lgMatrix.innerHTML=head+rows;
}
function padTo(n){return String(n).padStart(2,'0');}
function consoleState(txt,cls){
  if(!E.lgConsoleState)return;
  E.lgConsoleState.textContent=txt;
  E.lgConsoleState.style.color=cls==='ok'?'#8ff0b0':cls==='bad'?'#ff9d9d':'#ffc061';
  E.lgConsoleState.style.borderColor=cls==='ok'?'rgba(74,222,128,.45)':cls==='bad'?'rgba(255,77,77,.5)':'rgba(255,182,72,.4)';
  E.lgConsoleState.style.background=cls==='ok'?'rgba(74,222,128,.1)':cls==='bad'?'rgba(255,77,77,.12)':'rgba(255,182,72,.1)';
}
function pick(key){
  picked=key;
  const r=BY_KEY[key];
  $$('.lg-role',E.lgRoles).forEach(b=>{
    const on=b.dataset.r===key;
    b.classList.toggle('on',on);
    b.setAttribute('aria-checked',on?'true':'false');
    b.tabIndex=on?0:-1;
  });
  E.lgRoleBadge.innerHTML='<b style="color:'+r.accent+'">'+NSD.esc(r.badge)+'</b><span>'+
    r.views.length+' workspaces · '+NSD.esc(r.denied.join(', '))+' blocked</span>';
  if(!E.lgId.value||ROLES.some(x=>x.id===E.lgId.value.trim().toUpperCase()))E.lgId.value=r.id;
  E.lgErr.textContent='';
  E.lgPin.focus();
  consoleState(r.badge+' · AWAITING PIN','ok');
}
function say(msg,kind){
  E.lgErr.className='lg-err'+(kind?' '+kind:'');
  E.lgErr.innerHTML=msg;
}
E.lgEye.addEventListener('click',function(){
  const p=E.lgPin.type==='password';
  E.lgPin.type=p?'text':'password';
  E.lgEye.textContent=p?'HIDE':'SHOW';
  E.lgPin.focus();
});
if(E.lgKeypad){
  $$('button',E.lgKeypad).forEach(b=>{
    b.addEventListener('click',function(){
      const k=b.dataset.k;
      if(k==='clr')E.lgPin.value='';
      else if(k==='del')E.lgPin.value=E.lgPin.value.slice(0,-1);
      else if(E.lgPin.value.length<24)E.lgPin.value+=k;
      if(E.lgErr.textContent)say('');
      consoleState('PIN BUFFERED · '+E.lgPin.value.length+' DIGIT'+(E.lgPin.value.length===1?'':'S'));
      E.lgPin.focus();
    });
  });
}
[['lgId',1],['lgPin',1]].forEach(([id])=>{
  E[id].addEventListener('input',()=>{if(E.lgErr.textContent)say('');});
});
E.lgForm.addEventListener('submit',function(e){
  e.preventDefault();submit();
});
function submit(){
  const now=Date.now();
  if(AUTH.lockedUntil>now){
    say('Too many failed attempts — this terminal is held for another <b>'+Math.ceil((AUTH.lockedUntil-now)/1000)+' s</b>.','bad');
    return;
  }
  const id=(E.lgId.value||'').trim().toUpperCase();
  const pin=(E.lgPin.value||'').trim();
  consoleState('READING CREDENTIALS…');
  if(!picked){say('Choose the duty role you are signing in as.','bad');consoleState('NO DUTY STATION SELECTED','bad');return;}
  if(!id||!pin){say('Enter both the officer ID and the password / PIN.','bad');return;}
  consoleState('VERIFYING · '+id);
  const ok=AUTH.signIn(picked,id,pin,E.lgRemember.checked);
  if(!ok){
    AUTH.attempts++;
    const shown=AUTH.attempts;
    if(shown>=5){AUTH.lockedUntil=Date.now()+30000;AUTH.attempts=0;store(KEY_T,'0');store(KEY_L,String(AUTH.lockedUntil));}
    else store(KEY_T,String(shown));
    E.lgPin.value='';
    E.nsLogin.classList.remove('shake');
    void E.nsLogin.offsetWidth;
    E.nsLogin.classList.add('shake');
    consoleState(AUTH.lockedUntil>Date.now()?'TERMINAL HELD · 30 s':'ACCESS DENIED · ATTEMPT '+shown+' OF 5','bad');
    say('Credentials rejected for <b>'+NSD.esc(id)+'</b> as '+NSD.esc(BY_KEY[picked].badge)+'. '+
      (AUTH.lockedUntil>Date.now()?'Terminal held for 30 s after five failures.':
       'Attempt '+(AUTH.attempts)+' of 5 — check the role ID. PIN entry is local-only in '+
       'this build; the server guard is not yet wired.'),'bad');
  }
}

AUTH.signIn=function(roleKey,id,pin,remember){
  const r=BY_KEY[roleKey];
  if(!r)return false;
  if(String(id||'').trim().toUpperCase()!==r.id)return false;
  /* **CR-08 — no credential ships in this bundle.**
   *
   * What used to be here compared the typed PIN against a literal in `BY_KEY`.
   * A secret compiled into a file the browser must download is not access
   * control, it is a ritual — and a convincing one, which is worse, because it
   * reads as a real guard to anyone auditing the screen. The literals are gone.
   *
   * What remains is a role *selection*: choose the role, enter its ID, and the
   * terminal records who is at the console. The PIN field stays because the
   * operators expect one and because the server guard will take it as its input;
   * until that guard exists the header says LOCAL ROLE SELECT so nobody mistakes
   * this for authentication, and `AUTH.pendingServerGuard` carries that fact to
   * anything that renders the session.
   */
  AUTH.pendingServerGuard=true;
  if(String(pin||'').trim().length<4)return false;
  AUTH.role=r.key;AUTH.id=r.id;AUTH.at=Date.now();AUTH.attempts=0;AUTH.lockedUntil=0;
  store(KEY_T,'0');store(KEY_L,null);
  consoleState('ACCESS GRANTED · '+r.badge,'ok');
  AUTH.remember=!!remember;
  AUTH.lastReason='';
  if(remember)store(KEY_S,JSON.stringify({r:r.key,id:r.id,at:AUTH.at}));else store(KEY_S,null);
  ledger('USER SIGNED IN · '+r.badge+' · '+r.id,{actor:r.id,sev:'INFO'});
  apply();
  return true;
};
AUTH.signOut=function(quiet){
  const r=AUTH.role?BY_KEY[AUTH.role]:null;
  if(r)ledger('USER SIGNED OUT · '+r.badge+' · '+r.id,{actor:r.id,sev:'INFO'});
  AUTH.role=null;AUTH.id=null;AUTH.at=0;
  store(KEY_S,null);
  gate(true);
  if(!quiet)NSD.toast('Signed out. The session ledger keeps every action taken under your ID.');
};
function ledger(ev,opt){
  try{NSD.ledger.add(ev,Object.assign({mine:LOCAL},opt||{}));}catch(e){}
}
function apply(){
  const r=BY_KEY[AUTH.role];
  document.body.classList.remove('nsLocked');
  document.body.setAttribute('data-role',AUTH.role);
  E.nsLogin.classList.remove('on');
  $$('.ri[data-view]').forEach(el=>{
    const v=el.dataset.view;
    const allowed=AUTH.can(v);
    el.style.display=allowed?'':'none';
    el.setAttribute('aria-hidden',allowed?'false':'true');
  });
  // #region agent log
  fetch('http://127.0.0.1:7737/ingest/5fc2ba1e-4d66-4628-a84e-f3d6dbbf8bcb',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'83ae1d'},body:JSON.stringify({sessionId:'83ae1d',location:'09-access-control.js:apply',message:'rail views after role apply',data:{role:AUTH.role,views:AUTH.views(),aiShown:AUTH.can('ai'),runId:'post-fix'},timestamp:Date.now(),hypothesisId:'A'})}).catch(()=>{});
  // #endregion
  $$('.rb[data-deck]').forEach(b=>{
    const own=b.dataset.deck===AUTH.role;
    b.style.display=own?'':'none';
    if(own){b.classList.add('own');b.disabled=true;b.style.cursor='default';}
  });
  guardOperatorControls(AUTH.role==='operator');
  buildPill();
  $$('.vhr').forEach(vhr=>{
    if(vhr.querySelector('#nsOutBtn'))return;
    const b=document.createElement('button');
    b.className='vclose';b.id='nsOutBtn';b.textContent='Sign out';
    b.style.marginLeft='6px';
    b.addEventListener('click',()=>AUTH.signOut());
    vhr.appendChild(b);
  });
  const home=r.view;
  routing=true;try{prevShow.call(null,home);}finally{routing=false;}
  NSD.toast('<b>'+NSD.esc(r.badge)+'</b> signed in as '+NSD.esc(r.id)+' · cleared for '+
    r.views.length+' workspaces. Everything else stays hidden.');
}
function paintLiveNow(){
  try{
    const set=(id,txt)=>{const el=document.getElementById(id);if(el)el.textContent=txt;};
    const op=window.__OP;
    if(op&&op.state&&op.nodes){
      const alive=op.nodes.filter(n=>n.alive).length;
            const net=op.net?op.net():null;
      const ringTxt=net?(net.cuts.length?(net.iso.length?'RING ISOLATED':'RING WRAPPED'):'RING OK'):'RING OK';
      set('lgStatMesh',alive+' / '+op.nodes.length+' surface nodes · '+ringTxt+' · 2 evac buses');
      const e2=document.getElementById('lgStatEdge');
      if(e2)e2.textContent='ESP-NOW · offline buffer '+fmt(op.state.bufPct,0)+' %';
      const e3=document.getElementById('lgStatIdx');
      if(e3)e3.textContent='Panel P-3 · '+((op.state.failed&&op.state.failed.length)?'DEGRADED':'STABLE')+' · mesh '+alive+'/'+op.nodes.length;
    }
    const led=document.getElementById('lgStatLed');
    if(led&&NSD.ledger){
      const bl=NSD.ledger.blocks||[];
      const tip=bl.length?(bl[bl.length-1].hash||''):'';
      led.textContent=bl.length+' blocks sealed · head '+(tip.slice(0,8)||'—');
    }
  }catch(e){}
}
function buildPill(){
  let p=document.getElementById('nsSess');
  if(!p){
    p=document.createElement('div');p.id='nsSess';p.className='sess';
    document.body.appendChild(p);
  }
  const r=BY_KEY[AUTH.role];
  p.style.display='flex';
  p.innerHTML='<i style="background:'+r.accent+';box-shadow:0 0 10px '+r.accent+'"></i>'+
    '<span class="who"><b>'+NSD.esc(r.badge)+'</b><em>'+NSD.esc(AUTH.id)+' · '+NSD.esc(r.who)+'</em></span>'+
    '<button type="button" id="nsSessOut">SIGN OUT</button>';
  const b=document.getElementById('nsSessOut');
  if(b)b.addEventListener('click',()=>AUTH.signOut());
}

let opRights=true;
function guardOperatorControls(isOperator){
  opRights=isOperator;
  ['doTrigger','fireScenario','rearm','openScenarios','Siren'].forEach(fn=>{
    const orig=window[fn];
    if(typeof orig!=='function'||orig.__g)return;
    const wrapped=function(){
      if(!opRights){
        const r=BY_KEY[AUTH.role];
        NSD.toast('That is an <b>operator-only</b> hardware control — the '+(r?NSD.esc(r.badge):'signed-out')+
          ' role has no rights over the panel.');
        ledger('BLOCKED HARDWARE CONTROL · '+fn.toUpperCase()+' ATTEMPTED BY '+(AUTH.role||'SIGNED-OUT').toUpperCase(),
          {actor:AUTH.id||'UNKNOWN',sev:'WARNING'});
        return;
      }
      return orig.apply(this,arguments);
    };
    wrapped.__g=1;
    window[fn]=wrapped;
  });
  $$('#evacBtn, .panic button, .panic').forEach(el=>{
    if(el.tagName!=='BUTTON')return;
    el.disabled=!isOperator;
    el.title=isOperator?'':'Operator-only hardware control';
  });
  $$('.tcard, .mini, .mini *').forEach(el=>{
    if(el.tagName!=='BUTTON')return;
    if(/TRIGGER|FIRE|CRITICAL|RE-ARM|REARM|DRILL/i.test(el.textContent||'')){
      el.disabled=!isOperator;
      el.title=isOperator?'':'Operator-only hardware control';
    }
  });
}

function gate(on){
  if(on){
    document.body.classList.add('nsLocked');
    document.body.removeAttribute('data-role');
    E.nsLogin.classList.add('on');
    const p=document.getElementById('nsSess');if(p)p.style.display='none';
    $$('.ri[data-view]').forEach(el=>{el.style.display='none';el.setAttribute('aria-hidden','true');});
    $$('.rb[data-deck]').forEach(b=>{b.style.display='none';});
    $$('.vhr #nsOutBtn').forEach(b=>b.remove());
    const r=picked?BY_KEY[picked]:null;
    E.lgPin.value='';
    consoleState(r?(r.badge+' · AWAITING PIN'):'AWAITING CREDENTIALS',r?'ok':'');
    say('');
    AUTH.attempts=Math.max(AUTH.attempts,parseInt(read(KEY_T)||'0',10)||0);
    AUTH.lockedUntil=Math.max(AUTH.lockedUntil,parseInt(read(KEY_L)||'0',10)||0);
    if(AUTH.lockedUntil>Date.now()){
      const left=Math.ceil((AUTH.lockedUntil-Date.now())/1000);
      consoleState('TERMINAL HELD · '+left+' s','bad');
      say('Too many failed attempts — this terminal is held for another <b>'+left+' s</b>.','bad');
    }else if(AUTH.attempts>0)say('Failed attempts on this terminal: <b>'+AUTH.attempts+'</b> of 5 before a 30-second hold.','warn');
    setTimeout(()=>{if(!AUTH.role)(picked?E.lgId:E.lgRoles).focus();},60);
  }else{
    document.body.classList.remove('nsLocked');
    E.nsLogin.classList.remove('on');
  }
}
AUTH.gate=gate;

const railOut=$('.rail .ri[title="Sign out"]');
if(railOut){
  railOut.id='nsRailOut';
  railOut.setAttribute('role','button');
  railOut.addEventListener('click',()=>{if(AUTH.role)AUTH.signOut();else gate(true);});
  railOut.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();railOut.click();}});
}
document.addEventListener('keydown',function(e){
  if(e.metaKey||e.ctrlKey||e.altKey)return;
  const t=e.target;
  if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable))return;
  const MAP={'1':'overview','2':'telemetry','3':'control','4':'records','5':'evacmap','6':'ai'};
  const v=MAP[e.key];
  if(!v)return;
  if(!AUTH.role||!AUTH.can(v)){
    e.preventDefault();e.stopImmediatePropagation();
    if(!AUTH.role)AUTH.fail(v);
    else NSD.toast('“'+NSD.esc(v)+'” is not part of your cleared workspaces — '+
      (AUTH.views().length?('you have: '+AUTH.views().join(', ')):'no views'));
  }
},true);

(function boot(){
  renderRoles();
  paintLiveNow();setInterval(paintLiveNow,3000);
  document.body.setAttribute('data-auth','ready');
  const raw=read(KEY_S);
  let restored=null;
  if(raw){try{restored=JSON.parse(raw);}catch(e){restored=null;}}
  if(restored&&BY_KEY[restored.r]){
    AUTH.role=restored.r;AUTH.id=BY_KEY[restored.r].id;AUTH.at=restored.at||Date.now();AUTH.remember=true;
    pick(restored.r);
    ledger('SESSION RESTORED ON THIS TERMINAL · '+BY_KEY[restored.r].badge,{actor:AUTH.id,sev:'INFO'});
    apply();
    return;
  }
  pick('operator');
  E.lgId.value='';
  gate(true);
})();
})();
