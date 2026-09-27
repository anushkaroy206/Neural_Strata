/* Phase 1 (B1) brings the second pair alive: four nodes, not two.
   The level split below is the backend's — LEVELS{1:(n1,n2), 2:(n3,n4)} and
   tunnel_map.json — so a card can never claim a level whose route it does not
   actually drive. N1/N2 are Tunnel A (level 1); N3/N4 are Tunnel B (level 2). */
const NODES=['node_1','node_2','node_3','node_4'];

/* Phase 3 — the two nodes that carry the crack column (§3.7, H2).
   The list is the *server's* list: `contracts.CRACK_NODE_IDS` is ('node_2',
   'node_4'). It is written once here and read by the card template, the health
   strip summary and the seal on the column, so the dashboard cannot end up
   instrumenting one pair of nodes while the backend reasons about another. */
const CRACK_NODES=['node_2','node_4'];
const CRACK_STAGES=['NONE','INITIATION','PROPAGATION','CRITICAL','UNKNOWN'];
const CRACK_RANK={NONE:0,INITIATION:1,PROPAGATION:2,CRITICAL:3,UNKNOWN:4};
const COLOR={node_1:'#7cc6f0',node_2:'#c4a6ff',node_3:'#7ce0c0',node_4:'#f0b07c'};
const MAXPTS=240, MAXINV=80;
const RANGES={acceleration:[-1,5],displacement_mm:[0,100],tilt_x:[-45,45],inv_v:[0,3]};
const series={},invSeries={};
NODES.forEach(n=>{series[n]={acceleration:[],displacement_mm:[],tilt_x:[]};invSeries[n]={inv_v:[]};});

/* Cells are chosen so the mesh's own levelOf() agrees with the backend: E6 is
   level 1 and E9/E12 are level 2 (levelOf = n<=6 ? 1 : 2). Node 2 used to sit on
   E8, which the mesh calls level 2 while the backend calls node_2 a Tunnel A
   (level 1) sensor — the card said "LEVEL 2" over a node whose alarms block
   Route A. That is exactly the class of drift Phase 1 exists to remove. */
const NODE_META={
  node_1:{name:'Node 1 — Mesh E5',sub:'ESP32 NODE · NORTH GRID · TUNNEL A NEAR (LEVEL 1)',cell:'E5'},
  node_2:{name:'Node 2 — Mesh E6',sub:'ESP32 NODE · NORTH GRID · TUNNEL A COLLAPSE FACE (LEVEL 1)',cell:'E6',crack:true},
  node_3:{name:'Node 3 — Mesh E9',sub:'ESP32 NODE · CENTRE GRID · TUNNEL B NEAR (LEVEL 2)',cell:'E9'},
  node_4:{name:'Node 4 — Mesh E12',sub:'ESP32 NODE · SOUTH GRID · TUNNEL B FAR (LEVEL 2)',cell:'E12',crack:true}
};

const CELL_COUNT=NS_MESH.count;
const MESH_CELLS=NS_MESH.nums.map((n,i)=>{
  const id='E'+n, slot=n-1, col=slot%4, row=Math.floor(slot/4);
  const ns=['NORTH','CENTRE','SOUTH'][row];
  const ew=['WEST','WEST-CENTRE','EAST-CENTRE','EAST'][col];
  return {
    id, num:n, idx:i, slot, row, col,
    lvl:NS_MESH.levelOf(n),
    jb:NS_MESH.jbOf(n),
    grid:'R'+(row+1)+' / C'+(col+1),
    zone:ns+' GRID · '+ew,
    batt:(3.71+((slot*37)%23)/100).toFixed(2),
    rssi:-(62+((slot*29)%34))
  };
});
const CELL_BY_ID={}; MESH_CELLS.forEach(c=>CELL_BY_ID[c.id]=c);
const CELL_ADJ=MESH_CELLS.map(()=>[]);
MESH_CELLS.forEach(a=>{MESH_CELLS.forEach(b=>{
  if(a===b)return;
  if(Math.abs(a.row-b.row)+Math.abs(a.col-b.col)===1)CELL_ADJ[a.idx].push(b.id);
});});
const MSG={
  NOMINAL:'Slope stable. Deformation acceleration near zero across all instrumented cells.',
  WARNING:'Sustained acceleration detected. Time-to-failure countdown initiated, pre-evacuation protocols logged.',
  CRITICAL:'Terminal threshold breached (a ≥ 3.0 mm/s²). Structural failure predicted — drifts blocked, muster called.'
};

const STATUSES=['NOMINAL','WARNING','CRITICAL'];
function num(v,fb,lo,hi){
  if(typeof v==='string')v=v.trim()===''?NaN:Number(v);
  if(typeof v!=='number'||!isFinite(v))return fb;
  if(lo!==undefined&&v<lo)v=lo;
  if(hi!==undefined&&v>hi)v=hi;
  return v;
}
function status(v,fb){
  const s=String(v==null?'':v).trim().toUpperCase();
  return STATUSES.indexOf(s)>=0?s:(fb||'NOMINAL');
}
const routeState=v=>String(v==null?'':v).trim().toUpperCase()==='BLOCKED'?'BLOCKED':'OPEN';
function text(v,fb,maxLen){
  fb=(fb===undefined)?'—':fb;
  if(v==null||v===''||typeof v==='boolean')return fb;
  if(typeof v==='number')return isFinite(v)?String(v):fb;
  if(typeof v!=='string'){
    if(typeof v!=='object')return fb;
    return fb;
  }
  const s=v.replace(/[\u0000-\u001f\u007f]/g,'').trim();
  if(!s)return fb;
  return s.length>(maxLen||64)?s.slice(0,maxLen||64)+'…':s;
}
function cleanNode(n,fb){
  n=(n&&typeof n==='object')?n:{};
  fb=fb||{};
  return {
    status:status(n.status,fb.status),
    displacement_mm:num(n.displacement_mm,num(fb.displacement_mm,0),0,100),
    tilt_x:num(n.tilt_x,num(fb.tilt_x,0),-45,45),
    acceleration:num(n.acceleration,num(fb.acceleration,0),-50,50),
    /* Phase 2 — liveness is sanitised like everything else. `stale` is the
       server's verdict; the deck does not recompute it from `last_seen_s`,
       because two clocks disagreeing about whether a sensor is dead is worse
       than either one being wrong. */
    stale:n.stale===true,
    last_seen_s:num(n.last_seen_s,num(fb.last_seen_s,0),0,86400),

    /* Phase 3 — the crack column. Two rules, and they are the whole reason this
       block exists rather than four `num()` calls inline in the renderer:

       1 · **A null stays a null.** `micro` is null while the load cell is
           settling or faulted, and it survives sanitising as null all the way to
           the DOM. Substituting 0.0 would print "the seam is closed" from an
           instrument that has not said so — the same class of lie as a frozen
           displacement, and this column is the one that says a collapse is
           coming.
       2 · **The stage is the server's.** It is not recomputed here from the
           numbers on screen. The stage machine has the hysteresis, the settle
           window and the fault rules; a deck that re-derived it would disagree
           with the alarm log exactly when it matters, and the operator would
           have to decide which of the two to believe. */
    micro_um:(n.micro==null)?null:num(n.micro,null,-2000,100000),
    macro_mm:(n.macro==null)?null:num(n.macro,null,0,100),
    micro_rate_um_s:(n.micro_rate==null)?null:num(n.micro_rate,null,-1e6,1e6),
    macro_d30_mm:(n.macro_d30==null)?null:num(n.macro_d30,null,-100,100),
    micro_settled:n.micro_settled===true,
    coherence:(n.cross_sensor_coherence==null)?null:num(n.cross_sensor_coherence,null,0,1),
    crack_stage:CRACK_STAGES.indexOf(String(n.crack_stage==null?'':n.crack_stage).trim().toUpperCase())>=0
      ? String(n.crack_stage).trim().toUpperCase() : 'UNKNOWN'
  };
}
/* The crack stage's colour, in one place: the card strip and the health strip
   must agree, and two copies of a colour rule disagree on the day one is
   edited. */
const CRACK_COLOR={NONE:'var(--ok)',INITIATION:'var(--warn)',PROPAGATION:'#ff9538',
                   CRITICAL:'var(--crit)',UNKNOWN:'var(--faint)'};
/* What the stage means, in the operator's words rather than the machine's. */
const CRACK_LABEL={NONE:'no crack activity',INITIATION:'micro-crack initiating',
                   PROPAGATION:'crack propagating',CRITICAL:'CRACK CRITICAL',
                   UNKNOWN:'seam channel unknown'};
const ZONES=['Tunnel A','Tunnel B','Evacuating A','Evacuating B','Safe Exit','Headframe'];

/* Phase 2 (§2.3) — the four rungs the backend publishes, in ladder order.
   An unrecognised mode is deliberately NOT flattened to FULL: a build that
   sends a mode we do not know is a build whose health we cannot vouch for, and
   the chip says UNKNOWN rather than implying everything is fine. */
const DEGRADED_MODES=['FULL','SENSOR_PARTIAL','RING_SPLIT','NO_HARDWARE'];
const LINK_STATES=['UP','DOWN','UNKNOWN'];
function esc(t){return String(t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;',
  '>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function normLinks(raw){
  return (Array.isArray(raw)?raw:[]).slice(0,6).map(l=>{
    l=(l&&typeof l==='object')?l:{};
    const st=String(l.state||'').trim().toUpperCase();
    return {id:text(l.id,'—'),
            state:LINK_STATES.indexOf(st)>=0?st:'UNKNOWN',
            last_seen_s:(l.last_seen_s==null)?null:num(l.last_seen_s,null,0,86400)};
  });
}
function normWatchdog(w){
  if(!w||typeof w!=='object')return null;
  return {text:text(w.text,'Sensor lost'),
          nodes:(Array.isArray(w.nodes)?w.nodes:[]).slice(0,8).map(x=>text(x,'')).filter(Boolean),
          elapsed_s:num(w.elapsed_s,0,0,86400),
          suppressed:w.claims_suppressed===true};
}
function cleanTelemetry(d){
  d=(d&&typeof d==='object')?d:{};
  const nodes={};
  NODES.forEach(n=>{nodes[n]=cleanNode(d.nodes&&d.nodes[n],
    lastTelemetry&&lastTelemetry.nodes?lastTelemetry.nodes[n]:null);});
  const ug=(d.underground&&typeof d.underground==='object')?d.underground:{};
  const log=Array.isArray(d.rfid_log)?d.rfid_log.slice(0,64):[];
  const derived=NODES.reduce((w,n)=>
    STATUSES.indexOf(nodes[n].status)>STATUSES.indexOf(w)?nodes[n].status:w,'NOMINAL');
  const st=STATUSES.indexOf(String(d.system_status||'').trim().toUpperCase())>=0
    ? String(d.system_status).trim().toUpperCase() : derived;
  let ttf=d.ttf_seconds;
  ttf=(ttf==null||ttf==='N/A')?'N/A':num(ttf,'N/A',0,86400);
  const dm=String(d.degraded_mode||'').trim().toUpperCase();
  return {
    system_status:st,
    ttf_seconds:ttf,
    degraded_mode:DEGRADED_MODES.indexOf(dm)>=0?dm:'UNKNOWN',
    links:normLinks(d.links),
    watchdog:normWatchdog(d.watchdog),
    nodes,
    underground:{route_A_status:routeState(ug.route_A_status),
                 route_B_status:routeState(ug.route_B_status)},
    rfid_log:log.filter(r=>r&&typeof r==='object').map(r=>{
      const zone=ZONES.indexOf(String(r.zone))>=0?String(r.zone):'Headframe';
      const place=zone==='Tunnel A'||zone==='Evacuating A'
        ?{level:'Level 1',tunnel:'Tunnel A'}
        :zone==='Tunnel B'||zone==='Evacuating B'
        ?{level:'Level 2',tunnel:'Tunnel B'}
        :zone==='Safe Exit'?{level:'Surface',tunnel:'—'}:{level:'—',tunnel:'—'};
      return {
        id:text(r.id,'—'), name:text(r.name,'—'),
        level:text(r.level,place.level), tunnel:text(r.tunnel,place.tunnel),
        zone, time:text(r.time,'--:--:--')
      };
    }),
    rfid_readers:(Array.isArray(d.rfid_readers)?d.rfid_readers:[]).slice(0,4).map(r=>({
      reader:text(r.reader,'hub'),
      present:r.present===true,
      reads:num(r.reads,0,0,1000000),
      time:text(r.time,'--:--:--')
    })),
    timestamp:d.timestamp
  };
}
/* One pair block, sanitised. Every number is either a finite number or null —
   `num()` already refuses NaN and Infinity, and a null stays a null so the
   renderer can tell "no measurement" from "moved zero". */
function cleanPair(p){
  p=(p&&typeof p==='object')?p:{};
  const f=v=>(v==null||v==='')?null:num(v,null,-1e9,1e9);
  const l0=f(p.l0_m);
  return {level:num(p.level,0,0,9),
          a:text(p.a,'—'), b:text(p.b,'—'),
          label:text(p.label,('Level '+num(p.level,0,0,9))),
          l0_m:l0,
          elongation_mm:f(p.elongation_mm),
          strain_ue:f(p.strain_ue),
          settlement_mm:f(p.settlement_mm),
          warn:!!p.warn,
          threshold_ue:num(p.threshold_ue,400,0,1e9)};
}
function cleanGeomech(g){
  g=(g&&typeof g==='object')?g:{};
  const pairs={};
  PAIR_LEVELS.forEach(k=>{
    const raw=(g.pairs&&g.pairs[k]);
    if(raw) pairs[k]=cleanPair(raw);
  });
  const zones={};
  const zsrc=(g.zones&&typeof g.zones==='object')?g.zones:{};
  Object.keys(zsrc).forEach(id=>{
    const z=(zsrc[id]&&typeof zsrc[id]==='object')?zsrc[id]:{};
    const r=String(z.risk||'NOMINAL').toUpperCase();
    zones[id]={risk:(r in ZONE_RANK)?r:'NOMINAL',
               level:(z.level==null?null:num(z.level,null,0,9)),
               reason:text(z.reason,''),
               nodes:Array.isArray(z.nodes)?z.nodes.slice(0,8).map(n=>text(n,'')):[]};
  });
  const w=String(g.worst_zone_risk||'NOMINAL').toUpperCase();
  return {pairs,zones,worst_zone_risk:(w in ZONE_RANK)?w:'NOMINAL'};
}
/* The worst risk across the zones the server says belong to this level. A zone
   with no level is nobody's, and is not folded into anybody's label. */
function levelZoneRisk(level){
  const key=String(level);
  let worst='NOMINAL';
  Object.keys(GEO.zones).forEach(id=>{
    const z=GEO.zones[id];
    if(String(z.level)!==key) return;
    if(ZONE_RANK[z.risk]>ZONE_RANK[worst]) worst=z.risk;
  });
  return worst;
}
function cleanExtended(e){
  e=(e&&typeof e==='object')?e:{};
  const nodes={};
  NODES.forEach(n=>{const v=(e.nodes&&e.nodes[n])||{};
    nodes[n]={velocity:num(v.velocity,0,-100,100),
              peak_acceleration:num(v.peak_acceleration,0,-1,999)};});
  const mu=(e.muster&&typeof e.muster==='object')?e.muster:{};
  const total=num(mu.total,0,0,9999);
  const events=Array.isArray(e.events)?e.events:[];
  return {
    mode:(String(e.mode||'').toUpperCase()==='LIVE')?'LIVE':'SIM',
    nodes,
    geomech:cleanGeomech(e.geomech),
    muster:{total,underground:num(mu.underground,0,0,total||9999),
            safe:num(mu.safe,0,0,total||9999)},
    events:events.filter(v=>v&&typeof v==='object').slice(0,64).map(v=>({
      time:text(v.time,'--:--:--'), level:status(v.level), text:text(v.text,'')
    }))
  };
}
function esc(s){return String(s==null?'':s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  .replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

const SIM=(function(){
  const nowS=()=>new Date().toTimeString().slice(0,8);
  const offT=s=>new Date(Date.now()-s*1000).toTimeString().slice(0,8);
  const nodes={
    node_1:{base:5.2,tiltBase:1.1,d:5.2,tilt:1.1,v:0,a:0,status:'NOMINAL',scen:null,t:0,ph:1.3},
    node_2:{base:4.1,tiltBase:0.8,d:4.1,tilt:0.8,v:0,a:0,status:'NOMINAL',scen:null,t:0,ph:4.1}
  };
  const CREW=[['001','Deepak','Tunnel A'],['002','Anil','Tunnel A'],['003','Meera','Tunnel B'],
              ['004','Ravi','Tunnel A'],['005','Sunita','Tunnel B'],['006','Vikram','Safe Exit'],
              ['007','Pooja','Headframe'],['008','Arjun','Tunnel B'],['009','Isha','Safe Exit'],
              ['010','Kunal','Surface Camp']];
  const rfid=CREW.map((c,i)=>({id:c[0],name:c[1],zone:c[2],time:offT(4+i*11),_t:Date.now()-(4+i*11)*1000}));
  const events=[{time:nowS(),level:'NOMINAL',text:'System initialised — all cells stable, routes open.'}];
  const peak={node_1:0.05,node_2:0.02};
  let evacStage=0, evacT=0, lastT=performance.now(), T=0, warnLatch={};
  function pushEvt(level,text){events.unshift({time:nowS(),level,text}); if(events.length>40)events.pop();}
  function swipe(id,zone){
    const r=rfid.find(x=>x.id===id); if(!r)return;
    if(r.zone===zone)return;
    r.zone=zone; r.time=nowS(); r._t=Date.now();
    rfid.sort((a,b)=>b._t-a._t);
    if(rfid.length>10)rfid.length=10;
  }
  function accOf(s){
    if(!s.scen)return 0;
    const t=s.t;
    if(s.scen==='warning')return 0.95*(1-Math.exp(-t/1.6))*Math.exp(-t/10)*(t<34?1:Math.max(0,1-(t-34)/8));
    if(s.scen==='critical')return 3.6*(1-Math.exp(-t/1.4))*(1+0.12*Math.sin(t*2.1));
    return 0;
  }
  function step(h){
    T+=h;
    for(const n in nodes){
      const s=nodes[n];
      if(s.scen)s.t+=h;
      let a=s.scen?accOf(s):0.012*Math.sin(T*1.7+s.ph)+(Math.random()-0.5)*0.02;
      s.v+=a*h;
      if(!s.scen)s.v*=0.995;
      s.v=Math.max(-0.4,Math.min(12,s.v));
      s.d+=s.v*h;
      s.d=Math.max(0,Math.min(100,s.d));
      s.a+=(a-s.a)*0.55;
      s.tilt=s.tiltBase+0.5*Math.sin(T*0.07+s.ph)+s.a*0.9+0.1*Math.sin(T*0.21+s.ph*2);
      let st='NOMINAL';
      if(s.a>=3.0)st='CRITICAL';
      else if(s.scen&&s.a>0.25)st='WARNING';
      else if(!s.scen&&s.a>0.25)st='WARNING';
      if(s.scen==='warning'&&s.t>46){
        s.scen=null;s.v*=0.3;s.d=Math.min(s.d,s.base+2.5);st='NOMINAL';
        pushEvt('NOMINAL',n.toUpperCase()+' creep settled — node back to NOMINAL.');
      }
      if(s.status!=='CRITICAL'&&s.a<2.2)st=s.a<0.25?'NOMINAL':st;
      s.status=st;
      peak[n]=Math.max(peak[n]*0.999,Math.abs(s.a));
    }
    const sA=nodes.node_1.status, sB=nodes.node_2.status;
    const rA=(sA==='WARNING'||sA==='CRITICAL')?'BLOCKED':'OPEN';
    const rB=(sB==='WARNING'||sB==='CRITICAL')?'BLOCKED':'OPEN';
    if(rA==='BLOCKED'&&!warnLatch.A){warnLatch.A=true;pushEvt(sA,'ROUTE A BLOCKED — Level 1 drift LEDs red.');}
    if(rB==='BLOCKED'&&!warnLatch.B){warnLatch.B=true;pushEvt(sB,'ROUTE B BLOCKED — Level 2 drift LEDs red.');}
    if(rA==='OPEN')warnLatch.A=false; if(rB==='OPEN')warnLatch.B=false;
    const crit=sA==='CRITICAL'||sB==='CRITICAL';
    if(crit){
      if(evacStage===0){evacStage=1;evacT=T;pushEvt('CRITICAL','MUSTER CALL — crews in tunnels must evacuate.');}
      else{
        if(evacStage===1&&T-evacT>1.5){
          rfid.forEach(r=>{ if(r.zone==='Tunnel A')swipe(r.id,'Evacuating A');
                            if(r.zone==='Tunnel B')swipe(r.id,'Evacuating B'); });
          evacStage=2;evacT=T;pushEvt('CRITICAL','Crews evacuating — RFID gates live.');
        }
        if(evacStage===2&&T-evacT>6.5){
          rfid.forEach(r=>{ if(r.zone.startsWith('Evacuating'))swipe(r.id,'Safe Exit'); });
          evacStage=3;pushEvt('WARNING','All underground crews at Safe Exit.');
        }
      }
    }
    return{rA,rB};
  }
  function tick(){
    const now=performance.now();
    let dt=Math.min(1.2,(now-lastT)/1000); lastT=now;
    let routes={rA:'OPEN',rB:'OPEN'};
    let guard=0;
    while(dt>0.019&&guard++<40){ routes=step(0.02); dt-=0.02; }
    let ttf='N/A';
    for(const n in nodes){ const s=nodes[n];
      if(s.scen&&s.v>0.05&&s.a>0.5){
        const t=Math.max(3,Math.min(999,s.v/s.a));
        if(ttf==='N/A'||t<ttf)ttf=Math.round(t);
      }
    }
    const stWorst=(nodes.node_1.status==='CRITICAL'||nodes.node_2.status==='CRITICAL')?'CRITICAL':
                  (nodes.node_1.status==='WARNING'||nodes.node_2.status==='WARNING')?'WARNING':'NOMINAL';
    return{
      system_status:stWorst,
      ttf_seconds:ttf,
      nodes:{
        node_1:{status:nodes.node_1.status,tilt_x:+nodes.node_1.tilt.toFixed(2),
                displacement_mm:+nodes.node_1.d.toFixed(1),acceleration:+nodes.node_1.a.toFixed(3)},
        node_2:{status:nodes.node_2.status,tilt_x:+nodes.node_2.tilt.toFixed(2),
                displacement_mm:+nodes.node_2.d.toFixed(1),acceleration:+nodes.node_2.a.toFixed(3)}
      },
      underground:{route_A_status:routes.rA,route_B_status:routes.rB},
      rfid_log:rfid.slice(0,10)
    };
  }
  function extended(){
    const under=rfid.filter(r=>r.zone==='Tunnel A'||r.zone==='Tunnel B'||r.zone.startsWith('Evacuating')).length;
    const safe=rfid.filter(r=>r.zone==='Safe Exit').length;
    /* Phase 4 — the simulator has no baselines to draw (it moves two nodes
       independently and knows nothing about the map's geometry), so it says so
       with nulls. Inventing a ΔL here would put a number on the schematic that
       no sensor measured — the failure mode this phase exists to prevent. */
    return{mode:'SIM',
      nodes:{node_1:{velocity:+nodes.node_1.v.toFixed(2),peak_acceleration:+peak.node_1.toFixed(2)},
             node_2:{velocity:+nodes.node_2.v.toFixed(2),peak_acceleration:+peak.node_2.toFixed(2)}},
      geomech:{pairs:{'1':{level:1,a:'node_1',b:'node_2',label:'Tunnel A',l0_m:null,
                          elongation_mm:null,strain_ue:null,settlement_mm:null,
                          warn:false,threshold_ue:400},
                     '2':{level:2,a:'node_3',b:'node_4',label:'Tunnel B',l0_m:null,
                          elongation_mm:null,strain_ue:null,settlement_mm:null,
                          warn:false,threshold_ue:400}},
               zones:{},worst_zone_risk:'NOMINAL'},
      muster:{total:rfid.length,underground:under,safe},
      events:events.slice(0,24)};
  }
  function trigger(n,sev){
    const s=nodes[n];
    s.scen=sev;s.t=0;s.v=Math.max(s.v,0);
    pushEvt(sev==='critical'?'CRITICAL':'WARNING',
      n.toUpperCase()+' scenario injected — '+(sev==='critical'?'terminal creep':'sustained creep')+'.');
  }
  function rearm(){
    for(const n in nodes){const s=nodes[n];
      s.scen=null;s.v=0;s.a=0;s.d=s.base;s.tilt=s.tiltBase;s.status='NOMINAL';s.t=0;}
    evacStage=0; warnLatch={};
    const now=Date.now();
    rfid.forEach(r=>{
      const z=CREW.find(c=>c[0]===r.id);
      if(z&&r.zone!==z[2]){ r.zone=z[2]; r.time=nowS(); r._t=now; }
    });
    rfid.sort((a,b)=>b._t-a._t);
    pushEvt('NOMINAL','Drill complete — crew returning to posts, system re-armed.');
  }
  return{tick,extended,trigger,rearm};
})();

const Siren=(function(){
  let ctx=null,osc=null,lfo=null,lfoGain=null,gain=null,cur=null;
  function ensure(){
    try{
      if(!ctx)ctx=new (window.AudioContext||window.webkitAudioContext)();
      if(ctx&&ctx.state==='suspended')ctx.resume();
    }catch(e){}
  }
  function stop(){
    if(!ctx)return;
    if(gain){try{gain.gain.setTargetAtTime(0.0001,ctx.currentTime,0.05);}catch(e){}}
    const o=osc,l=lfo;
    setTimeout(()=>{try{o&&o.stop();}catch(e){} try{l&&l.stop();}catch(e){}},220);
    osc=lfo=lfoGain=gain=null; cur=null;
  }
  function play(level){
    ensure();
    if(!ctx||cur===level)return;
    stop();
    if(!ctx)return;
    cur=level;
    gain=ctx.createGain(); gain.gain.value=0.0001; gain.connect(ctx.destination);
    osc=ctx.createOscillator(); osc.type='sawtooth'; osc.frequency.value=520;
    lfo=ctx.createOscillator(); lfo.type='sine'; lfo.frequency.value=0.9;
    lfoGain=ctx.createGain(); lfoGain.gain.value=250;
    lfo.connect(lfoGain); lfoGain.connect(osc.frequency);
    osc.connect(gain); osc.start(); lfo.start();
    gain.gain.setTargetAtTime(0.07,ctx.currentTime,0.08);
  }
  ['click','touchstart','pointerdown','keydown'].forEach(ev=>window.addEventListener(ev,ensure,{capture:true,passive:true}));
  return{play,stop,get level(){return cur;}};
})();

const Horn=(function(){
  let ctx=null,osc=null,gain=null,timer=null,hi=true,on=false;
  function ensure(){
    try{
      if(!ctx)ctx=new (window.AudioContext||window.webkitAudioContext)();
      if(ctx&&ctx.state==='suspended')ctx.resume();
    }catch(e){}
  }
  function stop(){
    on=false;
    if(timer){clearInterval(timer);timer=null;}
    if(!ctx)return;
    if(gain){try{gain.gain.setTargetAtTime(0.0001,ctx.currentTime,0.05);}catch(e){}}
    const o=osc; setTimeout(()=>{try{o&&o.stop();}catch(e){}},220);
    osc=gain=null;
  }
  function play(){
    ensure();
    if(!ctx||on)return;
    on=true;
    gain=ctx.createGain(); gain.gain.value=0.0001; gain.connect(ctx.destination);
    osc=ctx.createOscillator(); osc.type='square'; osc.frequency.value=600;
    osc.connect(gain); osc.start();
    gain.gain.setTargetAtTime(0.045,ctx.currentTime,0.06);
    hi=true;
    timer=setInterval(()=>{ if(!osc||!ctx)return; hi=!hi;
      try{osc.frequency.setValueAtTime(hi?600:450,ctx.currentTime);}catch(e){} },420);
  }
  ['click','touchstart','pointerdown','keydown'].forEach(ev=>window.addEventListener(ev,ensure,{capture:true,passive:true}));
  return{play,stop,get on(){return on;}};
})();

const SURF_ALARM=(function(){
  const HOLD_MS=6000;
  const LV=[
    {lvl:1,nodes:['node_1','node_2'],s:[0,1],tunnel:'TUNNEL A',exitEl:'tcAexit'},
    {lvl:2,nodes:['node_3','node_4'],s:[2,3],tunnel:'TUNNEL B',exitEl:'tcBexit'}
  ];
  const STAGE_TXT=['WATCH','ALERT','EVACUATE'];
  const st={stage:[0,0],since:[0,0],lowerSince:[null,null],reason:['',''],ttf:[null,null],
            central:0,silenced:false,muteAt:0,last:null,dismissed:false};

  function evaluate(nd,cells,ttfSec){
    const status=nd?nd.status:'NOMINAL', acc=nd?+nd.acceleration||0:0;
    cells=cells||[];
    const critCell=cells.filter(c=>c.status==='CRITICAL')[0], warnCell=cells.filter(c=>c.status==='WARNING')[0];
    if(status==='CRITICAL')       return {stage:2,reason:'TERMINAL CREEP · a = '+acc.toFixed(2)+' mm/s² · CRITICAL'};
    if(critCell)                  return {stage:2,reason:'MESH CELL '+critCell.id+' CRITICAL · GROUND FAILING'};
    if(status==='WARNING')        return {stage:1,reason:'SUSTAINED CREEP · a = '+acc.toFixed(2)+' mm/s² · RESTRICT ACCESS'};
    if(warnCell)                  return {stage:1,reason:'MESH CELL '+warnCell.id+' WARNING · GROUND DISTURBANCE · RESTRICT ACCESS'};
    return {stage:0,reason:''};
  }

  function driver(d, ids){
    const rank={NOMINAL:0,WATCH:0,WARNING:1,CRITICAL:2};
    let pick=null, score=-1, id=null;
    (ids||[]).forEach(nid=>{
      const nd=d.nodes&&d.nodes[nid];
      if(!nd)return;
      const s=(rank[String(nd.status||'NOMINAL').toUpperCase()]||0)*1000+Math.abs(+nd.acceleration||0);
      if(s>score){score=s; pick=nd; id=nid;}
    });
    return {nd:pick,id:id};
  }
  function log(i,from,to,reason,nodeId){
    const L=LV[i];
    // #region agent log
    fetch('http://127.0.0.1:7737/ingest/5fc2ba1e-4d66-4628-a84e-f3d6dbbf8bcb',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'83ae1d'},body:JSON.stringify({sessionId:'83ae1d',hypothesisId:'H',location:'06-app.js:SURF_ALARM',message:'surface alarm stage',data:{level:L.lvl,from:from,to:to,node:nodeId,nodes:L.nodes},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
    const live=hornsOf(i);
    const who=live.length?live.map(k=>'S'+(k+1)).join(' + '):'';
    const act2=who?who+' HORN + STROBE':'NO LOCAL HORN (S'+(L.s[0]+1)+'/S'+(L.s[1]+1)+' OFF AIR) — M-01 SITE ALARM';
    const act1=who?who+' STROBE':'NO LOCAL STROBE — M-01 RELAY';
    const msg=to===2?'EVACUATION ALARM · LEVEL '+L.lvl+' · '+act2+' · '+reason+' · M-01 SITE RELAY':
              to===1?'SURFACE-NODE ALERT · LEVEL '+L.lvl+' · '+act1+' · '+reason:
                     'SURFACE-NODE ALARM CLEARED · LEVEL '+L.lvl+' · GROUND NOMINAL FOR '+(HOLD_MS/1000|0)+' s';
    if(window.NSD&&NSD.ledger)try{NSD.ledger.add(msg,{actor:'SURFACE NODE',sev:to===2?'CRITICAL':to===1?'WARNING':'INFO'});}catch(e){}
    if(window.NSD&&NSD.toast)try{NSD.toast(to===2
      ? '<b style="color:#ff9d9d">EVACUATE · LEVEL '+L.lvl+'</b> — '+(who?'surface node'+(live.length>1?'s ':' ')+who+' sounding horn + strobe.':'both surface nodes of this level are off air — M-01 is sounding the site alarm.')+' '+NSD.esc(reason)+'. Clear '+L.tunnel+' now.'
      : to===1 ? '<b style="color:#ffc061">ALERT · LEVEL '+L.lvl+'</b> — '+(who?who+' strobe on':'no local strobe, M-01 relaying')+' · '+NSD.esc(reason)+'.'
      : '<b style="color:#8ff0b0">LEVEL '+L.lvl+' alarm cleared</b> — surface nodes back on watch.');}catch(e){}
    try{document.dispatchEvent(new CustomEvent('ns:surface-alarm',{detail:{level:L.lvl,from,to,stage:STAGE_TXT[to],reason,horns:live.map(k=>'S'+(k+1))}}));}catch(e){}
    if(to===2&&window.NSD&&NSD.escalate)try{NSD.escalate('critical');}catch(e){}
  }
  function faults(){return (window.SURF&&SURF.api&&SURF.api.fault)||{s:[false,false,false,false],central:false};}
  function hornsOf(i){return LV[i].s.filter(k=>!faults().s[k]);}

  function update(d){
    const now=Date.now();
    const ttf=(d.ttf_seconds==='N/A'||d.ttf_seconds==null)?null:+d.ttf_seconds;
    const mesh=(window.SURF&&SURF.api&&SURF.api.mesh&&SURF.api.mesh.stat)||[];
    let changed=false;
    LV.forEach((L,i)=>{
      const picked=driver(d,L.nodes);
      const nd=picked.nd;
      const cells=MESH_CELLS.filter(c=>c.lvl===L.lvl&&!CELL_OF[c.id]).map(c=>({id:c.id,status:mesh[c.idx]||'NOMINAL'}));
      const active=nd&&nd.status!=='NOMINAL';
      const ev=evaluate(nd,cells,active?ttf:null);
      const cur=st.stage[i];
      if(ev.stage>=cur){
        st.lowerSince[i]=null;
        if(ev.stage>cur){log(i,cur,ev.stage,ev.reason,picked.id);st.since[i]=now;st.silenced=false;st.dismissed=false;changed=true;}
        st.stage[i]=ev.stage; if(ev.stage>0)st.reason[i]=ev.reason;
      }else{
        if(st.lowerSince[i]===null)st.lowerSince[i]=now;
        if(now-st.lowerSince[i]>=HOLD_MS){
          log(i,cur,ev.stage,ev.reason,picked.id);st.stage[i]=ev.stage;st.since[i]=now;st.lowerSince[i]=null;
          st.reason[i]=ev.reason;changed=true;
        }
      }
      st.ttf[i]=(st.stage[i]===2&&active)?ttf:null;
    });
    st.central=Math.max(st.stage[0],st.stage[1]);
    publish(d,changed);
  }

  function publish(d,changed){
    const f=faults();
    const s=[0,0,0,0];
    LV.forEach((L,i)=>L.s.forEach(k=>{s[k]=f.s[k]?0:st.stage[i];}));
    const worstI=st.stage[0]>=st.stage[1]?0:1;
    const levelsOn=LV.filter((L,i)=>st.stage[i]===st.central&&st.central>0).map(L=>L.lvl);
    const ttfLbl=st.ttf[worstI]!==null?fmtTTF(st.ttf[worstI]):null;
    const txt=st.central===2?('EVACUATE · LEVEL '+levelsOn.join(' + ')):st.central===1?('ALERT · LEVEL '+levelsOn.join(' + ')):'';
    if(window.SURF&&SURF.api){
      SURF.api.alarm={s,central:st.central,lvl:st.stage.slice(),txt,ttf:ttfLbl,since:st.since[worstI],
        exit:(document.getElementById(LV[worstI].exitEl)||{}).textContent||'EAST AIR SHAFT'};
      if(SURF.scene)SURF.scene.wantRender=true;
    }
    const ban=document.getElementById('evacBanner'), pill=document.getElementById('evacPill');
    if(ban){
      const on=st.central>0, shown=on&&!st.dismissed;
      const viewOpen=!!document.querySelector('.view.on');
      const lvTxt=on?((levelsOn.length>1?'LEVELS ':'LEVEL ')+levelsOn.join(' + ')):'';
      ban.classList.toggle('on',shown);ban.classList.toggle('evac',st.central===2);ban.classList.toggle('alert',st.central===1);
      ban.setAttribute('aria-hidden',shown?'false':'true');
      if(pill){
        pill.classList.toggle('on',on&&st.dismissed);pill.classList.toggle('evac',st.central===2);
        pill.classList.toggle('alert',st.central===1);pill.classList.toggle('mini',viewOpen);
        const pt=document.getElementById('evacPillTxt');
        if(pt&&on)pt.textContent='⚠ '+STAGE_TXT[st.central]+' · '+lvTxt+(ttfLbl?' · '+ttfLbl:'')+' · SHOW';
      }
      if(shown){
        ban.classList.toggle('mini',viewOpen);
        ban.classList.toggle('ug',!viewOpen&&typeof MODE!=='undefined'&&MODE==='underground');
        const parts=[], shortParts=[];
        LV.forEach((L,i)=>{ if(!st.stage[i])return;
          const live=hornsOf(i), exit=(document.getElementById(L.exitEl)||{}).textContent||'MAIN SHAFT';
          const who=live.length?live.map(k=>'S'+(k+1)).join('+'):'';
          const act=who?who+(st.stage[i]===2?' HORN+STROBE':' STROBE'):(st.stage[i]===2?'NO LOCAL HORN · M-01 SITE ALARM':'NO LOCAL STROBE · M-01 RELAY');
          parts.push('L'+L.lvl+': '+act+(st.stage[i]===2?' → EXIT '+exit:' · RESTRICT ACCESS'));
          if(st.stage[i]===st.central)shortParts.push(act);
        });
        const mini=document.getElementById('ebMini');
        if(mini)mini.textContent=lvTxt+(ttfLbl?' · '+ttfLbl:'')+' · '+shortParts.join(' · ');
        document.getElementById('ebStage').textContent=STAGE_TXT[st.central];
        document.getElementById('ebHead').textContent=lvTxt+
          (st.central===2?' · GROUND COLLAPSE PREDICTED':' · GROUND MOVEMENT')+(ttfLbl?' · '+ttfLbl:'');
        document.getElementById('ebSub').textContent=(st.reason[worstI]?st.reason[worstI]+' · ':'')+parts.join(' · ')+
          (f.central?' · M-01 OFF AIR — NODES SOUNDING LOCALLY':(st.central===2?' · M-01 SITE RELAY':''));
        ban.title=document.getElementById('ebHead').textContent+' — '+document.getElementById('ebSub').textContent;
        const sb=document.getElementById('ebSilence');
        if(sb){const mutable=st.central===2;sb.style.display=mutable?'':'none';
          sb.classList.toggle('done',st.silenced);
          sb.textContent=viewOpen?(st.silenced?'ROOM MUTED':'MUTE ROOM'):(st.silenced?'ROOM AUDIO SILENCED · MASTS STILL SOUNDING':'SILENCE ROOM AUDIO');}
      }
    }
    if(typeof surfChipPaint==='function')surfChipPaint();
    const ht=document.getElementById('hornTxt');
    if(ht){
      const on=[0,1,2,3].filter(k=>s[k]===2).map(k=>'S'+(k+1));
      const al=[0,1,2,3].filter(k=>s[k]===1).map(k=>'S'+(k+1));
      ht.textContent=on.length?('SOUNDING · '+on.join(' ')+(al.length?' · STROBE '+al.join(' '):'')):
                     al.length?('STROBE · '+al.join(' ')):'STANDBY · S1–S4';
      ht.style.color=on.length?'var(--crit)':al.length?'var(--amber-2)':'var(--faint)';
    }
    const tl=document.getElementById('tarpLive');
    if(tl)tl.textContent=st.central===0?'all four nodes on WATCH':
      LV.map((L,i)=>'Level '+L.lvl+' '+STAGE_TXT[st.stage[i]]+' ('+(hornsOf(i).map(k=>'S'+(k+1)).join('/')||'no horn')+')').join(' · ');
    const tg=document.getElementById('tgSiren');
    const audioOn=!tg||tg.classList.contains('on');
    if(st.central===2&&audioOn&&!st.silenced){Siren.stop();Horn.play();}
    else Horn.stop();
  }

  function rearm(){
    LV.forEach((L,i)=>{ if(st.stage[i]>0)log(i,st.stage[i],0,'RE-ARMED',null); st.stage[i]=0;st.lowerSince[i]=null;st.reason[i]='';st.ttf[i]=null; });
    st.central=0;st.silenced=false;st.dismissed=false;
    Horn.stop();
    const pill=document.getElementById('evacPill'); if(pill)pill.classList.remove('on','evac','alert');
    if(window.SURF&&SURF.api)SURF.api.alarm={s:[0,0,0,0],central:0,lvl:[0,0],txt:'',ttf:null,since:0};
    const ban=document.getElementById('evacBanner');
    if(ban){ban.classList.remove('on','evac','alert');ban.setAttribute('aria-hidden','true');}
    if(typeof surfChipPaint==='function')surfChipPaint();
  }
  function silence(){
    if(st.central!==2)return;
    st.silenced=true;st.muteAt=Date.now();Horn.stop();
    if(window.NSD&&NSD.ledger)try{NSD.ledger.add('EVACUATION HORN · CONTROL-ROOM AUDIO SILENCED · MAST HORNS REMAIN ON',{actor:'OPERATOR',sev:'INFO'});}catch(e){}
    const sb=document.getElementById('ebSilence');
    if(sb){sb.classList.add('done');sb.textContent=document.querySelector('.view.on')?'ROOM MUTED':'ROOM AUDIO SILENCED · MASTS STILL SOUNDING';}
  }
  function dismiss(){
    if(st.central===0)return;
    st.dismissed=true;
    const ban=document.getElementById('evacBanner'); if(ban){ban.classList.remove('on');ban.setAttribute('aria-hidden','true');}
    const pill=document.getElementById('evacPill'); if(pill)pill.classList.add('on');
    if(window.NSD&&NSD.ledger)try{NSD.ledger.add('EVACUATION POP-UP DISMISSED ON CONSOLE · ALARM REMAINS ACTIVE · HORNS + STROBES ON',{actor:'OPERATOR',sev:'INFO'});}catch(e){}
  }
  function restore(){
    st.dismissed=false;
    const pill=document.getElementById('evacPill'); if(pill)pill.classList.remove('on');
    if(st.central>0){const ban=document.getElementById('evacBanner'); if(ban){ban.classList.add('on');ban.setAttribute('aria-hidden','false');}}
  }
  (function bind(){
    const sb=document.getElementById('ebSilence'), mb=document.getElementById('ebMap'),
          xb=document.getElementById('ebClose'), pl=document.getElementById('evacPill');
    if(!sb||!mb||!xb||!pl){document.addEventListener('DOMContentLoaded',bind,{once:true});return;}
    sb.addEventListener('click',silence);
    mb.addEventListener('click',()=>{ if(MODE!=='underground')setMode('underground'); show('evacmap'); });
    xb.addEventListener('click',dismiss);
    pl.addEventListener('click',restore);
  })();
  document.addEventListener('visibilitychange',()=>{ if(document.hidden)Horn.stop(); });
  return {update,rearm,silence,dismiss,restore,evaluate,HOLD_MS,
          get state(){return {stage:st.stage.slice(),central:st.central,reason:st.reason.slice(),silenced:st.silenced,dismissed:st.dismissed};},
          hornOn(){return Horn.on;}};
})();
let LAST_DATA=null;

const cellsEl=document.getElementById('cells');
const CELL_OF={}; NODES.forEach(n=>CELL_OF[NODE_META[n].cell]=n);

const SURF_CHIPS=['S1','S2','S3','S4','M-01'];
function surfChipPaint(){
  SURF_CHIPS.forEach(id=>{
    const el=document.getElementById('sf_'+id); if(!el)return;
    const bad=!!(window.SURF&&SURF.surfaceState&&SURF.surfaceState(id));
    const A=(window.SURF&&SURF.api&&SURF.api.alarm)||{s:[0,0,0,0],central:0};
    const al=bad?0:(id==='M-01'?A.central:A.s[parseInt(id.slice(1),10)-1])||0;
    el.classList.toggle('live',!bad);
    el.classList.toggle('crit',bad);
    el.classList.toggle('evac',al===2);
    el.classList.toggle('alert',al===1);
    el.title=(id==='M-01'?'Main hub M-01 (surface gateway)':'Surface node '+id)+
      (bad?' — FAILED · click to restore':
       al===2?(id==='M-01'?' — RELAYING SITE EVACUATION ALARM':' — EVACUATION ALARM · HORN + STROBE ON')+' · click to run a failure':
       al===1?' — ALERT · STROBE ON · click to run a failure':' — healthy · click to run a failure');
  });
}
cellsEl.innerHTML=SURF_CHIPS.map(id=>{
  const nm=id==='M-01'?'Main hub M-01 (surface gateway)':'Surface node '+id;
  return `<button type="button" class="cell live${id==='M-01'?' wide':''}" id="sf_${id}" data-surf="${id}"
    title="${nm} — click to run a failure" aria-label="${nm}, run a failure or restore">${id}</button>`;
}).join('');
cellsEl.querySelectorAll('[data-surf]').forEach(b=>b.addEventListener('click',()=>surfaceFail(b.dataset.surf)));

function surfaceFail(id){
  if(!window.SURF||!SURF.toggleSurface)return;
  const state=SURF.toggleSurface(id);
  if(state===null)return;
  const bad=state==='failed';
  const nm=id==='M-01'?'Main hub M-01':'Surface node '+id;
  const lvl=(id==='S1'||id==='S2')?'Level 1':(id==='S3'||id==='S4')?'Level 2':'both levels';
  NSD.ledger.add(bad
    ? 'SURFACE NODE '+id+' SILENT · ESP-NOW DROP LOST · '+(id==='M-01'?'GATEWAY STORE-AND-FORWARD ONLY':lvl.toUpperCase()+' COVERAGE DEGRADED')
    : 'SURFACE NODE '+id+' RESTORED · RADIO DROP RE-ESTABLISHED',
    {actor:'OPERATOR',sev:bad?'WARNING':'INFO'});
  NSD.toast(bad
    ? ('<b>'+nm+'</b> failed — '+(id==='M-01'?'the surface gateway is down; nodes buffer locally.':
       'its radio drop to the main hub is dead, so '+lvl+' loses that instrument.'))
    : ('<b>'+nm+'</b> restored — radio drop and telemetry back.'));
  surfChipPaint();
  if(window.SURF.scene) SURF.scene.wantRender=true;
}
surfChipPaint();

/* Phase 3 — the two-stage column on the card.
 *
 * Deliberately *two* tiles rather than one "crack severity": they are two
 * instruments with resolutions three orders of magnitude apart, and the whole
 * claim of the phase is the lead time between them. A single merged number would
 * hide exactly the thing worth watching — the fine channel climbing while the
 * coarse one has not moved yet.
 *
 * The bars carry the phase's own thresholds as ticks: 1.5 mm on the macro bar is
 * where propagation is declared, 5.0 mm is where the seam alone is critical. A
 * bar without its thresholds is decoration.
 */
function crackBlock(n){
  return `<div class="metrics seamrow">
        <div class="m"><div class="k">MICRO-CRACK · LOAD CELL</div>
          <div class="v"><span id="mi_${n}">—</span><span class="u" id="mu_${n}">µm</span></div>
          <div class="bar"><i id="mib_${n}" style="left:0;width:0;background:linear-gradient(90deg,#8f5a1e,#ff9538)"></i><span class="tick" style="left:100%"></span></div>
          <div class="sc"><span id="mir_${n}">settling</span><span id="mic_${n}">coh —</span></div></div>
        <div class="m"><div class="k">MACRO SEAM · POT</div>
          <div class="v"><span id="ma_${n}">—</span><span class="u">mm</span></div>
          <div class="bar"><i id="mab_${n}" style="left:0;width:0;background:linear-gradient(90deg,#2a6f8f,#7cc6f0)"></i><span class="tick" style="left:6%"></span><span class="tick crit" style="left:20%"></span></div>
          <div class="sc"><span>0</span><span id="mad_${n}">Δ30 s —</span><span>25</span></div></div>
      </div>
      <div class="seamstrip" id="stg_${n}"><i id="stgd_${n}"></i><span id="stgt_${n}">NO CHANNEL</span></div>`;
}
document.getElementById('nodeCards').innerHTML=NODES.map(n=>{
  const m=NODE_META[n];
  return `<div class="panel" id="card_${n}">
    <h4><span><i class="nd-dot" id="dot_${n}"></i>${m.name}</span><span id="pill_${n}" style="color:var(--ok)">NOMINAL</span></h4>
    <div class="pb">
      <div style="font-size:9.5px;letter-spacing:.13em;color:var(--faint);margin-bottom:13px">${m.sub}</div>
      <div class="metrics">
        <div class="m"><div class="k">DISPLACEMENT</div>
          <div class="v"><span id="d_${n}">0.0</span><span class="u">mm</span></div>
          <div class="bar"><i id="db_${n}" style="left:0;width:1.5%;background:linear-gradient(90deg,#2a6f8f,#7cc6f0)"></i></div>
          <div class="sc"><span>0</span><span>50</span><span>100</span></div></div>
        <div class="m"><div class="k">TILT X</div>
          <div class="v"><span id="t_${n}">0.0</span><span class="u">°</span></div>
          <div class="bar"><span class="mid"></span><i id="tb_${n}" style="left:50%;width:.6%;background:linear-gradient(90deg,#6b5c8f,#c4a6ff)"></i></div>
          <div class="sc"><span>−45</span><span>0</span><span>+45</span></div></div>
        <div class="m"><div class="k">ACCELERATION</div>
          <div class="v"><span id="a_${n}">0.00</span><span class="u">mm/s²</span></div>
          <div class="bar"><i id="ab_${n}" style="left:0;width:1%;background:linear-gradient(90deg,#1c7a45,#4ade80)"></i><span class="tick" style="left:66.7%"></span></div>
          <div class="sc"><span>−1</span><span id="pk_${n}">PEAK 0.00</span><span>5</span></div></div>
      </div>
      ${m.crack?crackBlock(n):''}
    </div></div>`;
}).join('');

function fit(cv){const r=Math.min(window.devicePixelRatio||1,2),b=cv.getBoundingClientRect();
  cv.width=Math.max(1,b.width*r);cv.height=Math.max(1,b.height*r);
  const c=cv.getContext('2d');c.setTransform(r,0,0,r,0,0);return{c,w:b.width,h:b.height};}

function drawChart(cvId,key,opts={}){
  const cv=document.getElementById(cvId); if(!cv||!cv.getBoundingClientRect().width) return;
  if(NSD.paintOk)NSD.paintOk(cvId);
  const{c,w,h}=fit(cv);
  const pad={l:40,r:12,t:12,b:26},[lo,hi]=RANGES[key]||[0,1];
  const store=opts.store||series, N=Math.max(2,opts.max||MAXPTS);
  const X=i=>pad.l+(i/(N-1))*(w-pad.l-pad.r);
  const Y=v=>pad.t+(1-((Math.min(hi,Math.max(lo,num(v,lo)))-lo)/(hi-lo)))*(h-pad.t-pad.b);
  c.clearRect(0,0,w,h);
  c.font='9px ui-monospace,monospace'; c.textAlign='right'; c.textBaseline='middle';
  for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,y=Y(v);
    c.strokeStyle='rgba(255,255,255,.045)';c.lineWidth=1;
    c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();
    c.fillStyle='#6c645e';c.fillText(v.toFixed(opts.dp!==undefined?opts.dp:(key==='acceleration'?1:0)),pad.l-7,y);}
  c.textAlign='center';c.textBaseline='top';
  for(let i=0;i<=4;i++){const x=pad.l+(i/4)*(w-pad.l-pad.r);
    if(i>0&&i<4){c.strokeStyle='rgba(255,255,255,.035)';c.beginPath();c.moveTo(x,pad.t);c.lineTo(x,h-pad.b);c.stroke();}
    c.fillStyle='#5f574f';c.fillText(i===4?'NOW':'−'+Math.round((opts.window||120)*(1-i/4))+'s',x,h-pad.b+5);}
  if(lo<0){const y=Y(0);c.strokeStyle='rgba(255,255,255,.10)';c.setLineDash([3,3]);
    c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();c.setLineDash([]);}
  if(opts.band){const yT=Y(3),yB=Y(2.55);
    c.fillStyle='rgba(74,222,128,.06)';c.fillRect(pad.l,yT,w-pad.l-pad.r,yB-yT);
    c.strokeStyle='rgba(74,222,128,.25)';c.setLineDash([4,4]);
    c.beginPath();c.moveTo(pad.l,yB);c.lineTo(w-pad.r,yB);c.stroke();c.setLineDash([]);
    c.textAlign='left';c.fillStyle='rgba(74,222,128,.7)';
    c.fillText('STABLE · v ≈ 0 · no convergence',pad.l+8,yT+7);
    c.fillStyle='rgba(255,77,77,.75)';c.fillText('FAILURE',pad.l+8,Y(0)-14);}
  if(opts.threshold!==undefined){const y=Y(opts.threshold);
    c.strokeStyle='rgba(255,77,77,.75)';c.lineWidth=1.3;c.setLineDash([6,4]);
    c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();c.setLineDash([]);
    c.textAlign='left';c.fillStyle='rgba(255,77,77,.9)';c.fillText('CRITICAL 3.0',pad.l+6,y-14);}
  NODES.forEach(n=>{
    const d=(store[n]||{})[key]; if(!Array.isArray(d)||d.length<2)return;
    const off=N-d.length;
    c.lineWidth=1.9;c.strokeStyle=COLOR[n];c.lineJoin='round';
    c.beginPath();d.forEach((v,i)=>{const x=X(off+i),y=Y(v);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();
    const g=c.createLinearGradient(0,pad.t,0,h-pad.b);
    g.addColorStop(0,COLOR[n]+'33');g.addColorStop(1,COLOR[n]+'00');
    c.lineTo(X(off+d.length-1),h-pad.b);c.lineTo(X(off),h-pad.b);c.closePath();c.fillStyle=g;c.fill();
    const lx=X(off+d.length-1),ly=Y(d[d.length-1]);
    c.fillStyle=COLOR[n];c.beginPath();c.arc(lx,ly,2.8,0,7);c.fill();});
}
function push(n,s){const S=series[n]; if(!S||!s)return;
  ['acceleration','displacement_mm','tilt_x'].forEach(k=>{
    const [lo,hi]=RANGES[k], v=num(s[k],S[k].length?S[k][S[k].length-1]:0,lo,hi);
    if(S[k].length===0){for(let i=0;i<MAXPTS;i++)S[k].push(v);return;}
    S[k].push(v);if(S[k].length>MAXPTS)S[k].shift();});}
function pushInv(n,v){const S=invSeries[n]; if(!S)return; const a=S.inv_v;
  v=num(v,0,-1e6,1e6);
  const iv=v>0.03?Math.min(2.55,1/v):2.55;
  if(a.length===0){for(let i=0;i<MAXINV;i++)a.push(iv);return;}
  a.push(iv);if(a.length>MAXINV)a.shift();}

const ARC=Math.PI*1.5*58;
const gArc=document.getElementById('gArc'), gKnob=document.getElementById('gKnob');
function setGauge(frac,val,label,col){
  frac=Math.max(0,Math.min(1,frac));
  gArc.setAttribute('stroke-dasharray',`${ARC*frac} 999`);
  const a=(225-frac*270)*Math.PI/180;
  gKnob.setAttribute('cx',(91+58*Math.cos(a)).toFixed(1));
  gKnob.setAttribute('cy',(66-58*Math.sin(a)).toFixed(1));
  document.getElementById('defVal').textContent=val;
  document.getElementById('defLbl').textContent=label;
  document.getElementById('defDot').style.background=col;
  document.getElementById('defDot').style.boxShadow='0 0 10px '+col;
}
const liq=document.getElementById('liq');
let wavePhase=0;
function setLiquid(frac){
  frac=Math.max(0.04,Math.min(1,frac));
  const y=112-frac*112;
  wavePhase+=0.05;
  const a=2.4*Math.sin(wavePhase), b=2.4*Math.cos(wavePhase*0.8);
  liq.setAttribute('d',`M0 ${y+a} Q28 ${y-4+b} 56 ${y+a} T112 ${y-a} L112 112 L0 112 Z`);
}

const zoneClass=z=>z==='Safe Exit'?'z-safe':z.startsWith('Evacuating')?'z-evac':'z-tun';
function fmtTTF(v){ if(v==='N/A'||v==null) return null;
  const n0=num(v,null,0,86400); if(n0===null) return null;
  const s=Math.round(n0);
  return 'T−'+String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0'); }

let lastLog='';
let lastTelemetry=null;

/* Phase 2 (§2.5) — the degraded-mode chip, the three link indicators and the
   persistent watchdog line. One painter, called from the same render pass as
   the cards, so the chip and the greyed cards can never disagree about which
   fault is being reported. */
const MODE_CLASS={FULL:'full',SENSOR_PARTIAL:'partial',RING_SPLIT:'partial',
                  NO_HARDWARE:'offline',UNKNOWN:'partial'};
/* Phase 3 — one instrumented node's crack column, on screen.
 *
 * Every branch here is a *state*, not a formatting choice, and they are ordered
 * so the most alarming honest statement wins:
 *
 *   stale      → the numbers are struck through (the Phase 2 rule, reused): a
 *                seam reading from a node we have not heard from is history.
 *   fault      → the value is replaced by the fault word. A railed cell reading
 *                90 000 µm is not "a big crack", it is a broken wire, and the
 *                dashboard must not be the one place that pretends otherwise.
 *   unsettled  → "—", never 0.0. The gauge has no zero yet.
 *   otherwise  → the number, the rate, and the stage colour.
 */
function paintCrack(n,nd){
  const mi=document.getElementById('mi_'+n); if(!mi) return;   /* not instrumented */
  const stage=nd.crack_stage;
  const col=CRACK_COLOR[stage]||'var(--faint)';
  const faulted=(nd.micro_um!==null&&nd.micro_um>=90000);
  const settling=!nd.micro_settled;
  const unit=document.getElementById('mu_'+n);

  /* ── the fine channel ──────────────────────────────────────────────────── */
  if(faulted){
    /* The word, not the number: 90 000 µm is a rail, and a rail is a wire. */
    mi.textContent='FAULT';
    if(unit) unit.textContent='';
  } else if(nd.micro_um===null){
    mi.textContent='—';
    if(unit) unit.textContent='µm';
  } else {
    mi.textContent=nd.micro_um.toFixed(1);
    if(unit) unit.textContent='µm';
  }
  mi.style.color=(faulted||nd.stale)?'var(--crit)':(stage==='NONE'?'':'var(--fg)');
  /* 2 mm of opening fills the fine bar: beyond that the *coarse* channel is the
     one with something to say, and a bar that never leaves its first pixel is a
     bar nobody reads. */
  const mib=document.getElementById('mib_'+n);
  if(mib){
    const frac=(nd.micro_um===null||faulted)?0:Math.max(0,Math.min(100,nd.micro_um/2000*100));
    mib.style.width=(frac>0?Math.max(1,frac):0)+'%';
    mib.style.background=stage==='CRITICAL'?'linear-gradient(90deg,#7a1c1c,#ff4d4d)':
      stage==='PROPAGATION'?'linear-gradient(90deg,#8f5a1e,#ff9538)':
      'linear-gradient(90deg,#8f5a1e,#ff9538)';
  }
  const rate=document.getElementById('mir_'+n);
  if(rate){
    rate.textContent=faulted?'load cell railed':
      settling?'settling ('+Math.round(SETTLE_HINT)+'s)':
      (nd.micro_rate_um_s===null)?'rate —':
      nd.micro_rate_um_s.toFixed(1)+' µm/s'+(nd.micro_rate_um_s>=RATE_LIMIT?' ⚑':'');
    rate.style.color=(nd.micro_rate_um_s!==null&&nd.micro_rate_um_s>=RATE_LIMIT)
      ?'var(--warn)':'var(--faint)';
  }
  /* Coherence is printed, never used to soften anything — §3.4's rule that it is
     a *feature*, not a suppression. "coh —" while there is no rate at all is the
     honest reading: coherence with no structural evidence is undefined. */
  const coh=document.getElementById('mic_'+n);
  if(coh) coh.textContent=(nd.coherence===null)?'coh —':'coh '+nd.coherence.toFixed(2);

  /* ── the coarse channel ────────────────────────────────────────────────── */
  const ma=document.getElementById('ma_'+n);
  if(ma) ma.textContent=(nd.macro_mm===null)?'—':nd.macro_mm.toFixed(2);
  const mab=document.getElementById('mab_'+n);
  if(mab){
    const frac=(nd.macro_mm===null)?0:Math.max(0,Math.min(100,nd.macro_mm/25*100));
    mab.style.width=(frac>0?Math.max(1,frac):0)+'%';
    mab.style.background=(nd.macro_mm!==null&&nd.macro_mm>=5.0)
      ?'linear-gradient(90deg,#7a1c1c,#ff4d4d)':
      (nd.macro_mm!==null&&nd.macro_mm>=1.5)
      ?'linear-gradient(90deg,#8f5a1e,#ff9538)':'linear-gradient(90deg,#2a6f8f,#7cc6f0)';
  }
  const d30=document.getElementById('mad_'+n);
  if(d30){
    d30.textContent=(nd.macro_d30_mm===null)?'Δ30 s —':'Δ30 s '+nd.macro_d30_mm.toFixed(2);
    d30.style.color=(nd.macro_d30_mm!==null&&nd.macro_d30_mm>=1.5)?'var(--warn)':'var(--faint)';
  }

  /* ── the stage ─────────────────────────────────────────────────────────── */
  const strip=document.getElementById('stg_'+n);
  if(strip){
    const dot=document.getElementById('stgd_'+n), txt=document.getElementById('stgt_'+n);
    strip.style.borderColor=col;
    strip.style.background=(stage==='CRITICAL')?'rgba(255,77,77,.10)':
      (stage==='PROPAGATION')?'rgba(255,149,56,.09)':
      (stage==='INITIATION')?'rgba(255,149,56,.06)':'rgba(255,255,255,.03)';
    if(dot){ dot.style.background=col; dot.style.boxShadow='0 0 7px '+col; }
    if(txt){
      txt.textContent=(stage==='NONE'&&settling)?'SETTLING · Gauge zero not taken yet'
        : stage+' · '+CRACK_LABEL[stage];
      txt.style.color=col;
    }
    strip.title='Seam column on '+NODE_META[n].name+
      ' · stage '+stage+' (the server\'s own stage machine, not recomputed here)'+
      ' · micro '+((nd.micro_um===null)?'null':nd.micro_um.toFixed(1)+' µm')+
      ' · macro '+((nd.macro_mm===null)?'null':nd.macro_mm.toFixed(2)+' mm');
  }
}

/* Phase 4 (B8) — the pair layer.
 *
 * Two baselines, one per level: `Tunnel A` is N1↔N2 and `Tunnel B` is N3↔N4,
 * and the server publishes both the geometry (L0, in metres) and the two derived
 * numbers (elongation in mm, strain in µε). The deck's job is to show them
 * *beside the level they belong to* and to colour the schematic's level labels
 * from the zone risks — nothing here recomputes a strain, because a second
 * implementation of `ΔL / L0` in JavaScript is a second answer to the same
 * question, and the server's is the one an operator's decision rests on.
 *
 * The sparkline is the only stateful part: 60 samples at the extended poll's
 * ~2 s cadence is two minutes of history, which is the window a drill plays out
 * in. Nulls are NOT plotted as zero — the series simply skips them, because a
 * missing measurement drawn as 0.0 mm is the specific lie this whole phase is
 * built to avoid.
 */
const PAIR_LEVELS=['1','2'];
const PAIR_SHORT={'1':'TUNNEL A','2':'TUNNEL B'};
const PAIR_HIST_MAX=60;
const PAIR_HIST={'1':[],'2':[]};
let GEO={pairs:{},zones:{},worst_zone_risk:'NOMINAL'};
const ZONE_COLOR={NOMINAL:'#4ade80',WARNING:'#ff9538',CRITICAL:'#ff4d4d'};
const ZONE_RANK={NOMINAL:0,WARNING:1,CRITICAL:2};

/* A sparkline for the pair strain, as an inline SVG polyline.
 *
 * Inline rather than a chart call because it lives inside a chip in a single
 * flex row: 58×14 px, one stroke, `currentColor` so the chip's own risk colour
 * drives it and there is no second place to keep a colour in sync. Fewer than
 * two points draws nothing — a one-sample "trend" is a dot pretending to be a
 * line. */
function pairSpark(values){
  const w=58,h=14;
  if(!values||values.length<2) return '';
  let lo=Infinity,hi=-Infinity;
  values.forEach(v=>{if(v<lo)lo=v;if(v>hi)hi=v;});
  const span=(hi-lo)||1;
  const pts=values.map((v,i)=>{
    const x=(i/(values.length-1))*w;
    const y=h-2-((v-lo)/span)*(h-4);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  return '<svg class="spark" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+
         '" aria-hidden="true"><polyline points="'+pts+'"/></svg>';
}
/* The two per-level baseline readouts, beside the seam chip on the health strip.
   `mm` first because that is what an operator can picture, strain second because
   that is what the threshold is written in. */
function paintPairs(){
  PAIR_LEVELS.forEach(k=>{
    const el=document.getElementById('hsPair'+k);
    if(!el) return;
    const p=GEO.pairs[k];
    if(!p){
      el.className='hs-chip pair-chip nodata';
      el.textContent=PAIR_SHORT[k]+' ΔL —';
      el.title='No pair block from the server yet.';
      return;
    }
    const hist=PAIR_HIST[k];
    if(p.strain_ue!=null){
      hist.push(p.strain_ue);
      while(hist.length>PAIR_HIST_MAX) hist.shift();
    }else{
      hist.length=0;                       // a gap is a gap, not a flat line
    }
    const warn=!!p.warn;
    const set=(p.settlement_mm==null)?'—':p.settlement_mm.toFixed(1);
    const mm=(p.elongation_mm==null)?'—':(p.elongation_mm>=0?'+':'')+p.elongation_mm.toFixed(1);
    const ue=(p.strain_ue==null)?'—':(p.strain_ue>=0?'+':'')+Math.round(p.strain_ue);
    el.className='hs-chip pair-chip '+(warn?'warn':(p.strain_ue==null?'nodata':'ok'));
    el.innerHTML=esc(PAIR_SHORT[k])+' ΔL '+esc(mm)+'mm · '+esc(ue)+'µε · SET '+esc(set)+
                 pairSpark(hist);
    el.title=(p.a&&p.b?p.a+' ↔ '+p.b+' · ':'')+
             'L₀ '+(p.l0_m==null?'—':p.l0_m.toFixed(0)+' m')+
             ' · level threshold '+p.threshold_ue+' µε'+
             (warn?' · LEVEL WARNING: the ground between the two nodes is stretching':'')+
             ' · SET is the differential settlement across the level'+
             ' · stage comes from the server, nulls stay nulls';
  });
}

/* Phase 3 — the two thresholds the card needs to colour a rate, taken from the
   same numbers the backend's `config.describe()['crack']` publishes. They are
   constants of the phase (1.5 µm/s and a 15 s settle), not tunables: if they ever
   move, the server's block moves first and this line follows it. */
const RATE_LIMIT=1.5;
const SETTLE_HINT=15;

function paintHealth(d){
  const chip=document.getElementById('hsMode');
  if(chip){
    chip.textContent='DEGRADED MODE: '+d.degraded_mode;
    chip.className='hs-chip '+(MODE_CLASS[d.degraded_mode]||'partial');
  }
  const box=document.getElementById('hsLinks');
  if(box){
    box.innerHTML=d.links.map(l=>{
      const age=(l.last_seen_s==null)?'never':l.last_seen_s.toFixed(1)+'s';
      return '<span class="hs-link" title="'+esc(l.id)+' · '+l.state+
             ' · last '+age+'"><i class="'+l.state.toLowerCase()+'"></i>'+
             esc(l.id)+' '+l.state+'</span>';
    }).join('');
  }
  const note=document.getElementById('hsNote');
  if(note){
    const w=d.watchdog;
    note.textContent=w?(w.text+(w.elapsed_s?' · '+Math.round(w.elapsed_s)+' s':'')):'';
    note.classList.toggle('on',!!w);
  }
  /* Phase 3 — the seam summary.
   *
   * The worst stage across the two instrumented nodes, because that is the
   * question the strip answers: "is anything on this mine wrong that I have not
   * been told about". UNKNOWN ranks above NONE on purpose (the server's
   * STAGE_RANK does the same): a node we cannot see is not a node that is fine,
   * and this is the one place an operator scans for a summary. */
  const seam=document.getElementById('hsCrack');
  if(seam){
    let worst='NONE', seen=0, settling=0;
    CRACK_NODES.forEach(n=>{
      const nd=d.nodes[n]; if(!nd) return;
      seen++;
      if(!nd.micro_settled) settling++;
      if(CRACK_RANK[nd.crack_stage]>CRACK_RANK[worst]) worst=nd.crack_stage;
    });
    const col=CRACK_COLOR[worst]||'var(--faint)';
    seam.className='hs-chip crack-'+worst.toLowerCase();
    seam.style.color=col;
    seam.style.borderColor=col;
    seam.textContent='SEAM: '+worst+(settling?(' · '+settling+' settling'):'')+
                     ' · '+seen+'/'+CRACK_NODES.length+' instrumented';
    seam.title='Worst crack stage across the instrumented nodes ('+
      CRACK_NODES.join(', ')+') · '+(CRACK_LABEL[worst]||'')+
      ' · stages come from the server, nulls stay nulls';
  }
}

function render(d){
  d=cleanTelemetry(d);
  lastTelemetry=d;
  paintHealth(d);
  const st=d.system_status;
  const col=st==='CRITICAL'?'#ff4d4d':st==='WARNING'?'#ff9538':'#4ade80';

  const panic=st==='CRITICAL';
  document.body.classList.toggle('panic',panic);
  if(window.SURF)SURF.api.panic=panic;
  if(window.UG)UG.api.panic=panic;

  document.getElementById('attnDot').style.background=col;
  document.getElementById('attnDot').style.boxShadow='0 0 10px '+col;
  document.getElementById('attnTitle').textContent =
    st==='NOMINAL' ? 'All clear' : st==='WARNING' ? 'Attention!' : 'Emergency!';
  const m=document.getElementById('attnMsg');
  m.className='msg'+(st==='NOMINAL'?' ok':st==='CRITICAL'?' crit':'');
  const grace=Number(d.rearm_grace_s||0);
  document.getElementById('attnText').textContent=grace>0
    ?('Sensors on hold — readings count again in '+Math.ceil(grace)+' s')
    :MSG[st];

  const btn=document.getElementById('mainBtn');
  if(st==='NOMINAL'){ btn.textContent='Run failure drill'; btn.className='btn'; btn.dataset.act='drill'; }
  else { btn.textContent='Re-arm system'; btn.className='btn danger'; btn.dataset.act='rearm'; }

  let worstD=0, worstA=-1, worstT=0;
  NODES.forEach(n=>{
    const nd=d.nodes[n]; if(!nd) return;
    push(n,nd);
    worstD=Math.max(worstD,nd.displacement_mm);
    worstA=Math.max(worstA,nd.acceleration);
    if(Math.abs(nd.tilt_x)>Math.abs(worstT)) worstT=nd.tilt_x;

    const cell=document.getElementById('cl_'+NODE_META[n].cell);
    if(cell){
      cell.classList.add('live');
      cell.classList.toggle('crit',nd.status==='CRITICAL');
      cell.classList.toggle('sel',nd.status==='WARNING');
    }

    document.getElementById('d_'+n).textContent=nd.displacement_mm.toFixed(1);
    document.getElementById('t_'+n).textContent=nd.tilt_x.toFixed(1);
    document.getElementById('a_'+n).textContent=nd.acceleration.toFixed(2);
    /* Phase 2 — a stale node does not get to keep showing its last status.
       "NOMINAL" beside a sensor we have not heard from in eleven seconds is the
       single most dangerous line this dashboard can print, so the pill is
       replaced by the silence itself. The card greys and the value is struck
       through (see the .node-stale rule in core.css) — the number stays on
       screen, because hiding it loses the operator's last known position. */
    const pill=document.getElementById('pill_'+n);
    const card=document.getElementById('card_'+n);
    const dot=document.getElementById('dot_'+n);
    if(card) card.classList.toggle('node-stale',nd.stale);
    if(dot)  dot.classList.toggle('stale',nd.stale);
    if(nd.stale){
      pill.textContent='NO DATA · '+Math.round(nd.last_seen_s)+'s';
      pill.style.color='var(--crit)';
    } else {
      pill.textContent=nd.status;
      pill.style.color=nd.status==='CRITICAL'?'var(--crit)':nd.status==='WARNING'?'var(--amber)':'var(--ok)';
    }
    document.getElementById('db_'+n).style.width=Math.max(1.5,Math.min(100,nd.displacement_mm))+'%';
    const tb=document.getElementById('tb_'+n),tv=Math.max(-45,Math.min(45,nd.tilt_x)),tw=Math.abs(tv)/45*50;
    tb.style.left=(tv>=0?50:50-tw)+'%'; tb.style.width=Math.max(0.6,tw)+'%';
    tb.style.background=Math.abs(tv)>20?'linear-gradient(90deg,#8f5a1e,#ff9538)':'linear-gradient(90deg,#6b5c8f,#c4a6ff)';
    const ab=document.getElementById('ab_'+n);
    ab.style.width=Math.max(1,Math.min(100,(nd.acceleration+1)/6*100))+'%';
    ab.style.background=nd.acceleration>=3?'linear-gradient(90deg,#7a1c1c,#ff4d4d)':
      nd.acceleration>0.25?'linear-gradient(90deg,#8f5a1e,#ff9538)':'linear-gradient(90deg,#1c7a45,#4ade80)';
    if(NODE_META[n].crack) paintCrack(n,nd);
    if(window.SURF) SURF.api.nodes[n]={d:nd.displacement_mm,t:nd.tilt_x,s:nd.status};
  });

  setGauge(worstD/100, worstD.toFixed(1),
           st==='NOMINAL'?'Nominal':st==='WARNING'?'Elevated':'Critical', col);
  setLiquid((worstA+1)/6);
  document.getElementById('sphTilt').textContent=worstT.toFixed(1);
  document.getElementById('sphAcc').textContent=worstA.toFixed(2);
  const haveAi=aiModel&&aiModel.severity&&Object.keys(aiModel.severity).length;
  if(!haveAi){
    const ttf=fmtTTF(d.ttf_seconds);
    document.getElementById('ttfPill').textContent =
      ttf ? 'Predicted failure in '+ttf : 'No failure convergence detected';
  }

  const rA=d.underground.route_A_status, rB=d.underground.route_B_status;
  [['rA',rA],['rB',rB]].forEach(([el,v])=>{
    const e=document.getElementById(el);
    e.textContent=v; e.style.color=v==='BLOCKED'?'var(--crit)':'var(--ok)';
  });
  if(window.SURF) SURF.api.routes.route_A_status=rA, SURF.api.routes.route_B_status=rB;
  if(window.UG)   UG.api.routes.route_A_status=rA,    UG.api.routes.route_B_status=rB;

  if(window.SURF){
    SURF.api.mesh.stat=MESH_CELLS.map(cm=>CELL_OF[cm.id]
      ? d.nodes[CELL_OF[cm.id]].status : cellReading(cm.id,d).status);
    if(window.UG)UG.api.meshStat=SURF.api.mesh.stat.slice();
  }
  {
    const lost=(d.nodes.node_1.status==='CRITICAL')+(d.nodes.node_2.status==='CRITICAL');
    const online=CELL_COUNT-lost;
    const sFault=(window.SURF&&SURF.api&&SURF.api.fault)?SURF.api.fault.s.filter(Boolean).length:0;
    const cFault=(window.SURF&&SURF.api&&SURF.api.fault)?!!SURF.api.fault.central:false;

    if(window.SURF)SURF.api.hub={s:st,online,total:CELL_COUNT,reroute:lost>0};
    let ugStat=Array(CELL_COUNT).fill('NOMINAL');
    if(window.SURF&&SURF.api&&SURF.api.mesh)ugStat=SURF.api.mesh.stat.slice();
    const stOf=arr=>arr.indexOf('CRITICAL')>=0?'CRITICAL':arr.indexOf('WARNING')>=0?'WARNING':'NOMINAL';
    const jbStat=[1,2,3,4].map(j=>stOf(MESH_CELLS.filter(c=>c.jb===j).map(c=>ugStat[c.idx])));
    const hubStat=[1,2].map(h=>stOf(MESH_CELLS.filter(c=>c.lvl===h).map(c=>ugStat[c.idx])));
    const surfLive=hubStat.filter(x=>x==='NOMINAL').length;
    const jbLive=jbStat.filter(x=>x==='NOMINAL').length;
    const hubLive=surfLive;
    const ugLive=ugStat.filter(x=>x!=='CRITICAL').length;
    const stEff=cFault?'CRITICAL':((sFault&&st==='NOMINAL')?'WARNING':st);
    const colEff=cFault?'var(--crit)':((sFault&&st==='NOMINAL')?'var(--warn)':col);
    const cc=document.getElementById('ccNode');
    if(cc){
      cc.className='ccn'+(stEff==='CRITICAL'?' crit':stEff==='WARNING'?' warn':'');
      document.getElementById('ccState').textContent=stEff;
      document.getElementById('ccSub').textContent =
        cFault ? 'M-01 OFFLINE' :
        sFault ? (4-sFault)+'/4 NODES · OFF AIR' :
        lost ? surfLive+'/4 NODES · TAP LOST' :
        st==='WARNING' ? 'NODES · ALERT HELD' :
                         '4 SURFACE NODES · BUS';
      const ccd=document.getElementById('ccDot');
      ccd.style.background=colEff; ccd.style.boxShadow='0 0 10px '+colEff;
      const sdl=document.getElementById('stabDotL');
      if(sdl){sdl.style.background=colEff;sdl.style.boxShadow='0 0 7px '+colEff;}
      cc.title='Main hub M-01 — parent node of the sensor mesh · '+stEff+
               (cFault?' · MAIN HUB FAULT INJECTED':(sFault?' · '+sFault+' SURFACE NODE(S) OFF AIR':''))+
               ' · '+online+'/'+CELL_COUNT+' children reporting';
    }
    const mp=document.getElementById('meshParent');
    if(mp){
      mp.textContent=cFault?'OFFLINE · UPLINK LOST':lost?'POLL GAP':st==='WARNING'?'ALERT':'ONLINE';
      mp.style.color=(cFault||lost)?'var(--crit)':st==='WARNING'?'var(--warn)':'var(--ok)';
    }

    const mo=document.getElementById('meshOn');
    const snLive=4-sFault;
    mo.textContent=snLive+' / 4';
    mo.style.color=cFault?'var(--crit)':(sFault?'var(--warn)':'var(--ok)');
    const mhu=document.getElementById('meshHub');
    if(mhu){mhu.textContent=hubLive+' / 2';mhu.style.color=hubLive<2?'var(--warn)':'var(--ok)';}
    const mjb=document.getElementById('meshJb');
    if(mjb){mjb.textContent=jbLive+' / 4';mjb.style.color=jbLive<4?'var(--warn)':'var(--ok)';}
    const mrng=document.getElementById('meshRing');
    if(mrng){mrng.textContent=hubLive<2?'WRAPPED / ALERT':'INTACT (↻ ↺)';mrng.style.color=hubLive<2?'var(--warn)':'var(--ok)';}
    const mbuzz=document.getElementById('meshBuzz');
    if(mbuzz){const bCount=hubLive*2;mbuzz.textContent=bCount+' / 4 ARMED';mbuzz.style.color=bCount<4?'var(--warn)':'var(--ok)';}
    const mlig=document.getElementById('meshLights');
    if(mlig){const lCount=jbLive*6;mlig.textContent=lCount+' / 24';mlig.style.color=lCount<24?'var(--warn)':'var(--ok)';}
    const mug=document.getElementById('meshUg');
    if(mug){mug.textContent='NONE (SURFACE MONITORED)';mug.style.color='var(--dim)';}
    const mh=document.getElementById('meshHealth');
    mh.textContent=Math.round((snLive/4)*100)+'%';
    mh.style.color=snLive<4?'var(--warn)':'var(--ok)';
    const mr=document.getElementById('meshRer');
    mr.textContent=cFault?'UPLINK LOST · LOCAL LOG ONLY'
      :sFault?('DEGRADED · '+sFault+' NODE'+(sFault>1?'S':'')+' OFF AIR')
      :lost?'SEGMENT ISOLATED':lost===0&&st!=='NOMINAL'?'ARMED':'IDLE';
    mr.style.color=(cFault||lost)?'var(--crit)':(sFault||st!=='NOMINAL')?'var(--warn)':'var(--dim)';
    const mg=document.getElementById('meshGasp');
    const gasps=[d.nodes.node_1.status==='CRITICAL'?'E5':'',d.nodes.node_2.status==='CRITICAL'?'E8':''].filter(Boolean).join(' + ');
    mg.textContent=gasps||'—';
    mg.style.color=gasps?'var(--crit)':'var(--dim)';
    document.getElementById('meshDot').style.background=(cFault||lost)?'var(--crit)':(sFault||st==='WARNING')?'var(--warn)':'var(--ok)';
    document.getElementById('meshDot').style.boxShadow='0 0 10px '+((cFault||lost)?'var(--crit)':(sFault||st==='WARNING')?'var(--warn)':'var(--ok)');
    const sdr=document.getElementById('stabDotR');
    if(sdr){const sc=(cFault||lost)?'var(--crit)':(sFault||st==='WARNING')?'var(--warn)':'var(--ok)';
      sdr.style.background=sc;sdr.style.boxShadow='0 0 7px '+sc;}
  }

  const zA=d.rfid_log.filter(r=>r.zone==='Tunnel A').length;
  const eA=d.rfid_log.filter(r=>r.zone==='Evacuating A').length;
  const zB=d.rfid_log.filter(r=>r.zone==='Tunnel B').length;
  const eB=d.rfid_log.filter(r=>r.zone==='Evacuating B').length;
  if(window.UG) UG.api.crew={a:{n:zA,e:eA},b:{n:zB,e:eB}};
  if(window.UG&&window.MINE){
    const EXIT3D={AW:[-4.35,0.25,-0.30],BW:[-4.50,0.25,-0.30],AS:[3.2,0.25,-0.30],SE:[4.35,0.25,-0.30]};
    const mkRoute=fromId=>{
      const pl=window.MINE.plan(fromId,rA,rB); if(!pl)return null;
      const pts=[];
      pl.path.forEach(id=>{
        const n=window.MINE.N[id];
        if(n.kind==='exit')pts.push(EXIT3D[id]||[n.x,0.25,-0.30]);
        else pts.push([n.x,(n.y>-1.6?-1.075:-2.195)+0.06,-0.62]);
      });
      return{pts,exit:pl.exit};
    };
    UG.api.routes3d={a:mkRoute('aJ2'),b:mkRoute('bJ2'),
                     aBlocked:rA==='BLOCKED',bBlocked:rB==='BLOCKED'};
  }
  const sr=window.UG_SAFE(rA,rB);
  if(window.UG) UG.api.siren=sr.lvl;
  LAST_DATA=d;

  pushCellHistory(d);
  paintCellStatus(d);
  if(CUR_VIEW==='node')renderCellView();

  const sirenOn=!!sr.lvl;
  const pill=document.getElementById('sirenPill');
  pill.className='siren-pill'+(sirenOn?' on':'');
  document.getElementById('sirenTxt').textContent=sirenOn?'RINGING':'READY';
  const lvlTxt=sr.lvl==='ALL'?'ALL LEVELS':sr.lvl?('LEVEL '+sr.lvl):'—';
  document.getElementById('sirenLvl').textContent=sirenOn?lvlTxt:'—';
  document.getElementById('sirenDot').style.background=sirenOn?'var(--crit)':'var(--faint)';
  document.getElementById('sirenDot').style.boxShadow=sirenOn?'0 0 10px var(--crit)':'none';
  const audioOn=document.getElementById('tgSiren').classList.contains('on');
  if(sirenOn&&audioOn&&!SURF_ALARM.hornOn())Siren.play(sr.lvl); else Siren.stop();

  const crewA=zA+eA, crewB=zB+eB;
  const air=(v,ph)=>v==='OPEN'?(3.4+0.9*Math.abs(Math.sin(Date.now()/4000+ph))).toFixed(1)+' m/s':'0.4 m/s · stagnant';
  const setCard=(sfx,v,crew,airst,exitText,exitBad)=>{
    document.getElementById('tc'+sfx+'st').textContent=v;
    document.getElementById('tc'+sfx+'st').className='tc-st'+(v==='BLOCKED'?' bad':'');
    document.getElementById('tc'+sfx+'dot').className='dot'+(v==='BLOCKED'?' bad':'');
    document.getElementById('tc'+sfx+'crew').textContent=crew;
    document.getElementById('tc'+sfx+'air').textContent=airst;
    const ex=document.getElementById('tc'+sfx+'exit');
    ex.textContent=exitText;
    ex.style.color=exitBad?'var(--crit)':'var(--ok)';
  };
  const exitA=rA==='BLOCKED'?'EAST AIR SHAFT':'NEAREST EXIT (ROUTED)';
  const exitB=rB==='BLOCKED'?'EAST AIR SHAFT':'NEAREST EXIT (ROUTED)';
  setCard('A',rA,crewA,air(rA,0),exitA,rA==='BLOCKED');
  setCard('B',rB,crewB,air(rB,2),exitB,rB==='BLOCKED');

  try{SURF_ALARM.update(d);}catch(e){console.warn('surface alarm',e);}

  setMapBanner(d);

  const key=JSON.stringify(d.rfid_log);
  if(key!==lastLog){ lastLog=key;
    document.getElementById('rfidBody').innerHTML=d.rfid_log.length?d.rfid_log.map(r=>
      `<tr><td style="color:var(--faint)">${esc(r.id)}</td>
       <td style="font-family:var(--sans);font-size:12px">${esc(r.name)}</td>
       <td>${esc(r.level)}</td>
       <td>${esc(r.tunnel)}</td>
       <td><span class="z ${zoneClass(r.zone)}">${esc(r.zone)}</span></td>
       <td style="text-align:right;color:var(--dim)">${esc(r.time)}</td></tr>`).join('')
      :'<tr><td colspan="6" style="color:var(--dim)">No tag scanned yet. A swipe at the level hub appears here.</td></tr>';
  }
  const readersEl=document.getElementById('rfidReaders');
  if(readersEl){
    const readers=Array.isArray(d.rfid_readers)?d.rfid_readers:[];
    readersEl.textContent=readers.length
      ?readers.map(r=>(r.present?'LIVE':'NOT RESPONDING')+' · '+r.reader
        +' · '+r.reads+' swipe'+(r.reads===1?'':'s')+' accepted on the hub').join('   ')
      :'No report from the level-hub reader yet.';
  }
  document.getElementById('tkAdc').textContent=Math.round(worstD/100*4095);

  const alarmed=st!=='NOMINAL';
  document.querySelectorAll('.ri').forEach(r=>{
    if(r.dataset.view==='overview') r.classList.toggle('alarmed', alarmed && !r.classList.contains('on'));
    if(r.dataset.view==='evacmap') r.classList.toggle('alarmed', alarmed);
  });

  if(document.getElementById('v-telemetry').classList.contains('on')){
    drawChart('cAcc','acceleration',{threshold:3.0});
    drawChart('cDisp','displacement_mm');
    drawChart('cTilt','tilt_x');
    drawChart('cInv','inv_v',{store:invSeries,max:MAXINV,dp:1,band:true});
  }
  updateAiPanel();
}

function renderExt(e){
  e=cleanExtended(e);
  GEO=e.geomech;                 // the schematic's labels read this every frame
  if(window.__NS)window.__NS.geo=GEO;
  paintPairs();
  document.getElementById('tkMode').textContent=e.mode;
  Object.entries(e.nodes).forEach(([n,v])=>{
    const el=document.getElementById('pk_'+n);
    if(el) el.textContent='PEAK '+v.peak_acceleration.toFixed(2);
    pushInv(n,v.velocity);
    if(aiState[n])aiState[n].v=v.velocity;
  });
  const mu=e.muster;
  document.getElementById('cTotal').textContent=mu.total;
  document.getElementById('cUnder').textContent=mu.underground;
  document.getElementById('cSafe').textContent=mu.safe;
  document.getElementById('ugCount').textContent=mu.underground+' / '+mu.total;
  document.getElementById('muHead').textContent=mu.underground+' UNDERGROUND';
  document.getElementById('eventLog').innerHTML=e.events.map(v=>
    `<div><span class="t">${esc(v.time)}</span><span class="${esc(v.level)}">${esc(v.text)}</span></div>`).join('');
}

let aiState={node_1:{d:0,v:0,a:0},node_2:{d:0,v:0,a:0},node_3:{d:0,v:0,a:0},node_4:{d:0,v:0,a:0}};
let aiModel={model:'—',status:'OFFLINE',horizon:'—',last_train:'—',confidence:'—',note:'Awaiting /api/ai'};
const aiFeed=[]; let lastTtfSig={};
function fmtDur(s){s=Math.max(0,Math.round(s));
  if(s<3600)return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');
  return Math.floor(s/3600)+'h '+Math.floor(s%3600/60)+'m';}
function aiBreachTime(d0,v0,a0){
  const R=100-d0; if(R<=0)return 0;
  if(a0>0.02){
    const t=(-v0+Math.sqrt(v0*v0+2*a0*R))/a0;
    return t>0?t:null;
  }
  if(v0>0.02)return R/v0;
  return null;
}
function aiFeedPush(n,ttf,risk){
  const sig=ttf===null?'na':Math.round(ttf/60);
  if(lastTtfSig[n]===sig)return;
  lastTtfSig[n]=sig;
  const t=new Date().toTimeString().slice(0,8);
  const name='NODE '+(NODES.indexOf(n)+1);
  const text=ttf===null?name+' stable — no failure convergence (risk '+Math.round(risk)+'%)'
    :name+' TTF '+fmtDur(ttf)+' (risk '+Math.round(risk)+'%)'+(ttf<3600?' — IMMINENT':' — monitoring');
  aiFeed.unshift({t,ttf:ttf===null?null:ttf<3600,text});
  if(aiFeed.length>30)aiFeed.pop();
  renderAiFeed();
}
function renderAiFeed(){
  const el=document.getElementById('aiLog'); if(!el)return;
  el.innerHTML=aiFeed.map(f=>
    `<div><span class="t">${esc(f.t)}</span><span style="color:${f.ttf?'var(--crit)':'var(--dim)'}">${esc(f.text)}</span></div>`).join('');
}
function drawForecast(n){
  const cv=document.getElementById('cFc'+(NODES.indexOf(n)+1)); if(!cv)return;
  const b=cv.getBoundingClientRect(); if(!b.width)return;
  if(NSD.paintOk)NSD.paintOk(cv.id);
  const {c,w,h}=fit(cv);
  const st=aiState[n];
  const pad={l:36,r:10,t:10,b:22};
  const Yv=v=>pad.t+(1-v/110)*(h-pad.t-pad.b);
  const Xv=t=>pad.l+(t/3600)*(w-pad.l-pad.r);
  c.clearRect(0,0,w,h);
  [0,25,50,75,100].forEach(v=>{
    const y=Yv(v);
    c.strokeStyle=v===100?'rgba(255,90,80,.8)':'rgba(255,255,255,.05)';
    c.lineWidth=v===100?1.5:1;
    if(v===100)c.setLineDash([6,4]);
    c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();c.setLineDash([]);
    c.fillStyle='#6c645e';c.font='9px ui-monospace,monospace';c.textAlign='right';c.textBaseline='middle';
    c.fillText(v,pad.l-5,y);
  });
  c.textAlign='center';c.textBaseline='top';c.fillStyle='#5f574f';
  [0,900,1800,2700,3600].forEach(t=>c.fillText(t===0?'now':(t/60)+' min',Xv(t),h-pad.b+5));
  const d=series[n].displacement_mm;
  if(d&&d.length>1){
    c.strokeStyle='#7cc6f0';c.lineWidth=1.8;c.beginPath();
    const dt=0.5; let started=false;
    d.forEach((v,i)=>{
      const t=(i-(d.length-1))*dt; if(t<0)return;
      const x=Xv(t),y=Yv(Math.min(110,Math.max(0,v)));
      started?c.lineTo(x,y):c.moveTo(x,y); started=true;
    });
    c.stroke();
  }
  const d0=Math.min(st.d,109), v0=Math.max(0,st.v), a0=Math.max(0,st.a);
  const pts=[]; let breach=null;
  for(let t=0;t<=3600;t+=30){
    const x=d0+v0*t+0.5*a0*t*t;
    if(x>=100&&breach===null)breach=t;
    pts.push([t,Math.min(110,x)]);
    if(x>=110)break;
  }
  c.strokeStyle=a0>0.25?'#ff9538':'rgba(255,196,116,.6)';
  c.lineWidth=1.6;c.setLineDash([7,5]);
  c.beginPath();
  pts.forEach(([t,x],i)=>{const px=Xv(t),py=Yv(x);i?c.lineTo(px,py):c.moveTo(px,py);});
  c.stroke();c.setLineDash([]);
  if(breach!==null){
    const bx=Xv(breach);
    c.fillStyle='#ff6b5e';
    c.beginPath();c.moveTo(bx,Yv(100));c.lineTo(bx-5,Yv(100)-9);c.lineTo(bx+5,Yv(100)-9);c.closePath();c.fill();
    c.font='8.5px ui-monospace,monospace';c.textAlign='center';c.textBaseline='bottom';
    c.fillText('T-'+fmtDur(breach),bx,Yv(100)-11);
  }
}
function updateAiPanel(){
  const sev=(aiModel&&aiModel.severity)||{};
  NODES.forEach((n,i)=>{
    const nd=lastTelemetry&&lastTelemetry.nodes[n]; if(!nd)return;
    const ttfEl=document.getElementById('aiTtf'+(i+1));
    const riskEl=document.getElementById('aiRisk'+(i+1));
    const brEl=document.getElementById('aiBreach'+(i+1));
    if(!ttfEl||!riskEl||!brEl)return;
    if(!aiState[n])aiState[n]={d:0,v:0,a:0};
    aiState[n].d=nd.displacement_mm; aiState[n].a=nd.acceleration;
    const s=aiState[n];
    const model=sev[n]||{};
    const modelTtf=(typeof model.ttf==='number')?model.ttf:null;
    let ttf=modelTtf;
    if(ttf===null&&s.a>0.25&&s.v>0.03)ttf=s.v/s.a;
    const band=Array.isArray(model.band)&&model.band.length===3?model.band:null;
    const bt=aiBreachTime(s.d,Math.max(0,s.v),Math.max(0,s.a));
    ttfEl.textContent=ttf===null?'N/A':(band
      ?('T−'+fmtDur(band[1])+'  ·  '+fmtDur(band[0])+'–'+fmtDur(band[2]))
      :('T−'+fmtDur(ttf)));
    ttfEl.style.color=ttf===null?'var(--ok)':ttf<300?'var(--crit)':ttf<3600?'var(--warn)':'var(--ok)';
    const risk=modelTtf!=null
      ?Math.max(0,Math.min(100,Math.round((1-Math.min(modelTtf,3600)/3600)*100)))
      :Math.max(0,Math.min(100,Math.round(Math.max(0,s.a)/3*100)));
    riskEl.textContent=risk+'%';
    riskEl.style.color=risk>66?'var(--crit)':risk>25?'var(--warn)':'var(--ok)';
    brEl.textContent=bt===null?'—':bt<=0?'BREACHED':'T−'+fmtDur(bt);
    brEl.style.color=bt===null?'var(--dim)':bt<=0?'var(--crit)':bt<3600?'var(--crit)':'var(--warn)';
    aiFeedPush(n,ttf,risk);
  });
  if(document.getElementById('v-ai').classList.contains('on')){
    NODES.forEach(drawForecast);
  }
}
/* ---------------------------------------------------------------------------
 * The AI Cascade panel (Phase 5, §5.9).
 *
 * Everything here is **absent-safe**, and that is the design constraint rather
 * than a nicety. `model_1` and `cascade` are additive blocks on `/api/ai`: they
 * are missing whenever the server predates Phase 5, whenever the classifier
 * artifact is absent (a supported configuration — `MANIFEST.json` sets
 * `model_1.required = false`), and for the first 60 s after boot while the
 * buffer fills. In every one of those cases the panel must read as "no verdict
 * yet" rather than blanking the card or showing a stale value as if it were
 * current. A card that vanishes is worse than one that says nothing is known:
 * the first looks like a bug, the second is the truth.
 *
 * The disagreement flag is deliberately NOT styled as an error. It is not one.
 * When Model 1 says environmental and Model 2 says fifteen seconds, both are
 * true readings of the same window, and the operator is the person who has to
 * weigh them. Rendering it as a fault would invite someone to "fix" the
 * contradiction in software, which is exactly the suppression the firewall
 * exists to prevent.
 * ------------------------------------------------------------------------- */
function renderCascade(ai){
  const m1=(ai&&ai.model_1)||{};
  const cs=(ai&&ai.cascade)||{};
  const set=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v;};

  const label=text(m1.label,'');
  const chip=document.getElementById('c1Label');
  if(chip){
    chip.textContent=label||text(m1.status,'UNAVAILABLE');
    chip.dataset.label=label||text(m1.status,'UNAVAILABLE');
  }

  const di=(typeof m1.disruption_index==='number')?m1.disruption_index:null;
  set('c1Index', di===null?'—':di.toFixed(2)+(m1.structural_prob!=null
      ? '   ·   P(structural) '+Number(m1.structural_prob).toFixed(2) : ''));
  const bar=document.getElementById('c1Bar');
  if(bar){
    bar.style.width=(di===null?0:Math.max(0,Math.min(1,di))*100)+'%';
    bar.classList.toggle('hot',di!==null&&di>0.6);
  }

  // Top features come from the model's global importance ranking, not a
  // per-window saliency: a saliency number for a bagged forest is not stably
  // defined, and a value that changes between identical windows is worse than
  // useless on a panel an operator is trying to trust.
  const feats=Array.isArray(m1.top_features)?m1.top_features:[];
  set('c1Feat', feats.length
      ? feats.slice(0,3).map(f=>Array.isArray(f)?(f[0]+' '+Number(f[1]).toFixed(2)):String(f)).join('  ·  ')
      : '—');

  const hz=(cs&&typeof cs.m2_hz==='number')?cs.m2_hz:null;
  const rate=document.getElementById('c1Rate');
  if(rate){
    rate.textContent=(hz===null)?'—':('M1 @ 2 Hz  ·  M2 @ '+hz+' Hz');
    // The cadence is the one number an M1 error can make *cheaper*, so it is
    // shown with the gate that decides it rather than as a bare rate.
    rate.style.color=(hz!==null&&hz<=0.2)?'var(--faint)':'var(--dim)';
  }
  const mode=document.getElementById('cMode');
  if(mode)mode.textContent=text(cs.mode,'—').toUpperCase()+' MODE';

  const ttf=(m1.ttf_s!==undefined&&m1.ttf_s!==null)?m1.ttf_s:null;
  set('c2Ttf', ttf===null?'—':(Math.round(ttf)+' s'));

  // The band. Rendered as a range of seconds, and when it is absent it says so
  // rather than drawing a zero-width interval — a band of width zero is a claim
  // of certainty, and this system does not have one.
  const band=m1.band;
  set('c2Band', (Array.isArray(band)&&band.length===3)
      ? (Math.round(band[0])+' s  →  '+Math.round(band[2])+' s')
      : 'not available');

  const tier=text(m1.tier,text(cs.reason,''));
  const te=document.getElementById('c2Tier');
  if(te){
    te.textContent=(tier&&['NOMINAL','ADVISORY','ELEVATED','IMMINENT'].includes(tier))?tier:'—';
    te.dataset.tier=te.textContent;
  }

  set('c2Src', text(ai&&ai.model_2_source,'—'));
  set('cReason', text(cs.reason,'Awaiting the first full 60 s window.'));

  const nodes=cs.nodes||{};
  const ids=Object.keys(nodes);
  let worstN=null;
  ids.forEach(id=>{
    const ix=((nodes[id]||{}).novelty||{}).index;
    if(typeof ix==='number'&&(worstN===null||ix>worstN.index))worstN={id:id,index:ix,seen:(nodes[id].novelty||{}).seen};
  });
  const nv=document.getElementById('cNovel');
  if(nv){
    nv.textContent=worstN===null?'—':(worstN.seen?'SEEN':'NOVEL')+' · '+worstN.index.toFixed(2)+' · '+worstN.id.replace('node_','N');
  }
  const box=document.getElementById('cNodes');
  if(box){
    box.textContent=ids.length?ids.map(id=>{
      const d=nodes[id]||{};
      const lab=text(d.label,'—');
      const ttf=(typeof d.ttf_s==='number')?Math.round(d.ttf_s)+' s':'—';
      const band=Array.isArray(d.band)&&d.band.length===3?(Math.round(d.band[0])+'–'+Math.round(d.band[2])+' s'):'';
      const di=(typeof d.disruption_index==='number')?d.disruption_index.toFixed(2):'—';
      const sp=(typeof d.structural_prob==='number')?Number(d.structural_prob).toFixed(2):'—';
      const nx=(d.novelty&&typeof d.novelty.index==='number')?d.novelty.index.toFixed(2):'—';
      return id.replace('node_','N')+'  '+lab+'  ·  M2 '+ttf+(band?(' ('+band+')'):'')+'  ·  disrupt '+di+'  ·  P(struct) '+sp+'  ·  novel '+nx;
    }).join('\n'):'Awaiting a full 60 s window on each node.';
  }

  const dg=document.getElementById('cDisagree');
  if(dg)dg.style.display=(cs.disagreement?'block':'none');
}

/* ---------------------------------------------------------------------------
 * Severity, stage and confidence — Phase 6 (§6.3, §6.5).
 *
 * The server publishes a `severity` block per node (tier, TTF, its 80 % band,
 * the creep stage, the curvature behind it, confidence and *why*) plus a
 * `kinematic` block naming which rung of the fallback chain is answering. This
 * function is the only place those reach the deck, and it obeys three rules:
 *
 *  1. **Never a bare number.** The pill's countdown is written with its band
 *     beside it whenever the server publishes one — "6 s (5–11 s)". The band is
 *     the honest part of an extrapolation, and a countdown without it reads as
 *     more certain than any model in this system is.
 *  2. **Absent-safe.** No `severity` block (pre-Phase-6 server, classifier
 *     artifact missing, or the first 60 s after boot) hides the chips entirely
 *     and leaves the pill's text alone. Nothing blanks; nothing invents.
 *  3. **Staleness is visible, not silent.** A stale node keeps its last tier —
 *     a hazard does not end because a radio did — so the chip says STALE. The
 *     alternative, quietly dropping the tier, is the failure mode where the
 *     panel looks calm during a link outage.
 * ------------------------------------------------------------------------- */
const SEV_RANK={NOMINAL:0,WATCH:1,ELEVATED:2,HIGH:3,CRITICAL:4};
const SEV_COL={NOMINAL:'var(--ok)',WATCH:'var(--dim)',ELEVATED:'var(--amber)',
               HIGH:'var(--amber)',CRITICAL:'var(--crit)'};

function renderSeverity(ai){
  const blk=(ai&&ai.severity)||null;
  const row=document.getElementById('sevPill');
  if(!row)return;
  const ids=Object.keys(blk||{});
  if(!ids.length){row.style.display='none';return;}

  // Worst node first: this is a single system-wide readout, and a summary that
  // reported the calmest node while another was about to fail would be the most
  // dangerous kind of correct.
  let worst=ids[0];
  ids.forEach(id=>{
    const a=SEV_RANK[(blk[id]||{}).tier]||0, b=SEV_RANK[(blk[worst]||{}).tier]||0;
    if(a>b)worst=id;
  });
  const n=blk[worst]||{};
  const name=worst.replace('node_','Node ');

  // 1. the band, beside the number
  const band=Array.isArray(n.band)&&n.band.length===3
             && n.band[0]!==null&&n.band[1]!==null&&n.band[2]!==null
             ? n.band:null;
  const pill=document.getElementById('ttfPill');
  if(pill&&band&&n.ttf!==null&&n.ttf!==undefined){
    pill.textContent = name+' · predicted failure in '+fmtTTF(band[1])+
                       ' (band '+fmtTTF(band[0])+'–'+fmtTTF(band[2])+')';
  }else if(pill&&n.tier==='WATCH'&&(n.ttf===null||n.ttf===undefined)){
    pill.textContent = name+' · no estimate yet — stage '+text(n.stage,'—');
  }

  const set=(id,txt,title,colour)=>{
    const e=document.getElementById(id);
    if(!e)return;
    e.textContent=txt;
    e.title=title||'';
    if(colour)e.style.color=colour;
  };

  const cur=Number(n.curvature)||0;
  set('stageChip','STAGE '+text(n.stage,'—'),
      'Creep stage from the velocity shape over the node\u2019s 60 s window'+
      (cur?' · curvature '+cur.toFixed(2):''),
      n.stage==='TERTIARY'||n.stage==='FAILURE'?'var(--crit)':'var(--dim)');

  set('sevChip', text(n.tier,'—'),
      'Severity from the time-to-failure bands (<30 s CRITICAL · 30–120 HIGH · '+
      '120–600 ELEVATED · above NOMINAL). WATCH means no estimate yet.'+
      (n.stale?' · last known tier, link stale':''),
      SEV_COL[n.tier]||'var(--dim)');

  // 2. the confidence chip carries §6.2.4's *reason* on hover — a tier with no
  //    stated cause is the decoration this phase replaced.
  set('confChip','CONF '+text(n.confidence,'—'),
      text(n.confidence_reason,'no reason published'),
      n.confidence==='LOW'?'var(--warn)':n.confidence==='HIGH'?'var(--ok)':'var(--dim)');

  // 3. which rung of the fallback chain is answering (§6.2's explicit chain)
  const kin=(ai&&ai.kinematic)||{};
  const src=text(kin.source,'none');
  const alpha=(kin.alpha===null||kin.alpha===undefined)?'':' \u03b1='+kin.alpha;
  set('kinChip','PHYSICS '+src,
      'Fallback chain: cascade \u2192 Model 2 (fresh) \u2192 Fukuzono 1/v \u2192 v/a \u2192 N/A. '+
      'This countdown came from '+src+alpha+
      (kin.calibration?(' ('+kin.calibration+' calibration)'):''),
      src==='fukuzono_1/v'?'var(--amber)':'var(--dim)');

  const hot=SEV_RANK[n.tier]>=SEV_RANK.HIGH;
  const sevEl=document.getElementById('sevChip');
  if(sevEl)sevEl.classList.toggle('hot',hot);
  if(n.stale){
    const s2=document.getElementById('confChip');
    if(s2)s2.textContent='CONF '+text(n.confidence,'—')+' · STALE';
  }
  row.style.display='flex';
}

async function pollAi(){
  try{
    if(!backendDue())throw new Error('offline');
    const j=await getJSON(apiUrl('/api/ai'),2000);
    if(j&&j.model)aiModel=j;
  }catch(e){
    aiModel={model:'—',status:'OFFLINE',horizon:'—',last_train:'—',confidence:'—',
             note:'Model service offline — kinematic fallback only.'};
  }
  document.getElementById('aiModel').textContent=text(aiModel.model,'—');
  const st=text(aiModel.status,'—');
  const se=document.getElementById('aiStatus');
  se.textContent=st;
  // Colour rule (relaxed for the edge server's real /api/ai statuses). ACTIVE is
  // the only "everything is fine" state; WARMING UP (the model's 60 s buffer is
  // still filling, kinematic drives the pill meanwhile) and FALLBACK (the model is
  // stale or contradicted by the physics) are both healthy, so they are amber.
  // Only a genuine fault -- or a dead model service -- is red.
  const stFault=st!=='ACTIVE'&&st!=='WARMING UP'&&st!=='FALLBACK'&&st!=='AWAITING_INTEGRATION';
  se.style.color=st==='ACTIVE'?'var(--ok)':stFault?'var(--crit)':'var(--amber)';
  document.getElementById('aiHorizon').textContent=text(aiModel.horizon,'—');
  document.getElementById('aiTrain').textContent=text(aiModel.last_train,'—');
  document.getElementById('aiConf').textContent=text(aiModel.confidence,'—');
  document.getElementById('aiNote').textContent=text(aiModel.note,'',180);
  renderCascade(aiModel);
  renderSeverity(aiModel);
  const painted=[];
  NODES.forEach((n,i)=>{
    const tag=document.getElementById('aiTag'+(i+1));
    if(!tag)return;
    const sev=(aiModel.severity||{})[n]||{};
    const nodeAi=((aiModel.cascade||{}).nodes||{})[n]||{};
    const modelTtf=typeof sev.ttf==='number';
    const imminent=aiModel.forecasts&&aiModel.forecasts[n]===true;
    const pattern=text(nodeAi.label,'');
    const base=!modelTtf?'KINEMATIC MODE':imminent?'AI FORECAST · UNDER 5 MIN':'MODEL FORECAST';
    tag.textContent=pattern?(pattern+' · '+base):base;
    painted.push({node:n,tag:tag.textContent,ttf:sev.ttf==null?null:sev.ttf,label:pattern||null,novel:(nodeAi.novelty||{}).index});
  });
  // #region agent log
  fetch('http://127.0.0.1:7737/ingest/5fc2ba1e-4d66-4628-a84e-f3d6dbbf8bcb',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'83ae1d'},body:JSON.stringify({sessionId:'83ae1d',hypothesisId:'AI',location:'06-app.js:pollAi',message:'four node forecasts painted',data:{status:aiModel.status,nodes:painted},timestamp:Date.now()})}).catch(()=>{});
  // #endregion
}

let MODE='surface', LVL='all', LINK=false, lastSwitch=performance.now();
let CUR_VIEW='overview';

function onActivate(el,fn){
  if(!el)return;
  el.addEventListener('click',fn);
  el.addEventListener('keydown',e=>{
    if(e.key==='Enter'||e.key===' '||e.key==='Spacebar'){e.preventDefault();fn(e);}
  });
}

let CUR_CELL='E5';
let SEL_CELL=null;
const cellSeries={}; MESH_CELLS.forEach(c=>cellSeries[c.id]=[]);

function cellReading(id,d){
  const meta=CELL_BY_ID[id];
  const owner=CELL_OF[id];
  if(owner&&d&&d.nodes&&d.nodes[owner]){
    const n=d.nodes[owner];
    const st=(n.status==='OFFLINE'||n.status==='DEGRADED')?n.status:n.status;
    return {d:n.displacement_mm,t:n.tilt_x,a:n.acceleration,
            status:st,instrumented:true,owner};
  }
  if(!d||!d.nodes)return{d:0,t:0,a:0,status:'DATA_UNAVAILABLE',instrumented:false,owner:null};
  let wsum=0,dd=0,tt=0,acc=0,nearAlert=false,validCount=0;
  NODES.forEach(n=>{
    const src=CELL_BY_ID[NODE_META[n].cell]; const nd=d.nodes[n]; if(!src||!nd)return;
    if(nd.status==='OFFLINE')return;
    validCount++;
    const dist=Math.abs(src.row-meta.row)+Math.abs(src.col-meta.col);
    const w=1/Math.pow(1+dist,2.1);
    wsum+=w; dd+=(nd.displacement_mm||0)*w; tt+=(nd.tilt_x||0)*w;
    acc=Math.max(acc,(nd.acceleration||0)/(1+Math.pow(dist,1.9)));
    if(dist<=1&&nd.status!=='NOMINAL')nearAlert=true;
  });
  if(!validCount||!wsum)return{d:0,t:0,a:0,status:'DATA_UNAVAILABLE',instrumented:false,owner:null};
  const st=(acc>0.8&&nearAlert)?'WARNING':'NOMINAL';
  return{d:dd/wsum,t:tt/wsum,a:acc,status:st,instrumented:false,owner:null};
}

function pushCellHistory(d){
  MESH_CELLS.forEach(c=>{
    const a=cellSeries[c.id], v=cellReading(c.id,d).a;
    if(a.length===0){for(let i=0;i<MAXPTS;i++)a.push(v);}
    else{a.push(v); if(a.length>MAXPTS)a.shift();}
  });
}

function paintCellStatus(d){
  MESH_CELLS.forEach(c=>{
    if(CELL_OF[c.id])return;
    const el=document.getElementById('cl_'+c.id); if(!el)return;
    const st=cellReading(c.id,d).status;
    el.classList.toggle('crit',st==='CRITICAL');
    el.classList.toggle('sel',st==='WARNING');
  });
}

function buildCellNav(){
  const nav=document.getElementById('ndNav'); if(!nav||nav.childElementCount)return;
  nav.innerHTML=MESH_CELLS.map(c=>{
    const live=!!CELL_OF[c.id];
    return `<button type="button" data-ndcell="${c.id}" class="${live?'inst':''}">${c.id}${live?' ·':''}</button>`;
  }).join('');
  nav.querySelectorAll('[data-ndcell]').forEach(b=>{
    b.addEventListener('click',()=>showCell(b.dataset.ndcell));
  });
}

function pointAtCell(id){
  const meta=CELL_BY_ID[id]; if(!meta)return;
  CUR_CELL=id; SEL_CELL=id;
  const i=meta.idx;
  if(window.SURF&&SURF.selectCell)SURF.selectCell(i);
  if(window.UG&&UG.selectCell)UG.selectCell(i,MODE==='underground');
  document.querySelectorAll('#cells .cell').forEach(el=>
    el.classList.toggle('act',el.dataset.cell===id));
  const nav=document.getElementById('ndNav');
  if(nav)nav.querySelectorAll('[data-ndcell]').forEach(b=>
    b.classList.toggle('on',b.dataset.ndcell===id));
  updateCellBar();
}

function clearCellPointer(){
  if(window.SURF&&SURF.selectCell)SURF.selectCell(null);
  if(window.UG&&UG.selectCell)UG.selectCell(null);
  SEL_CELL=null;
  document.querySelectorAll('#cells .cell').forEach(el=>el.classList.remove('act'));
  updateCellBar();
}

function updateCellBar(){
  const bar=document.getElementById('cellBar'); if(!bar)return;
  if(!SEL_CELL){bar.classList.remove('on');return;}
  bar.classList.add('on');
  const r=cellReading(SEL_CELL,LAST_DATA);
  document.getElementById('cbId').textContent=SEL_CELL;
  document.getElementById('cbWhat').textContent =
    r.instrumented?NODE_META[r.owner].name.replace(/ — .*/,'')+' · instrumented'
                  :'Relay cell · '+CELL_BY_ID[SEL_CELL].grid;
  document.getElementById('cbOpen').textContent='OPEN '+SEL_CELL+' DETAIL ▸';
}

(function(){
  const o=document.getElementById('cbOpen'), x=document.getElementById('cbClear');
  if(o)o.addEventListener('click',()=>{if(SEL_CELL)showCell(SEL_CELL);});
  if(x)x.addEventListener('click',clearCellPointer);
  const loc=document.getElementById('ndLocate');
  if(loc)loc.addEventListener('click',()=>{
    pointAtCell(CUR_CELL);
    if(MODE==='underground'&&window.UG&&UG.selectCell)
      UG.selectCell(CELL_BY_ID[CUR_CELL].idx,true);
  });
})();

function showCell(id){
  if(!CELL_BY_ID[id])return;
  SEL_CELL=id;
  pointAtCell(id);
  buildCellNav();
  show('node');
  renderCellView();
}

function renderCellView(){
  const id=CUR_CELL, meta=CELL_BY_ID[id]; if(!meta)return;
  buildCellNav();
  const r=cellReading(id,LAST_DATA);
  const st=r.status;
  const cls=st==='CRITICAL'?'crit':st==='WARNING'?'warn':r.instrumented?'':'quiet';
  const col=st==='CRITICAL'?'#ff4d4d':st==='WARNING'?'#ff9538':r.instrumented?'#4ade80':'#8b8178';

  document.getElementById('ndTitle').textContent='Mesh cell '+id;
  document.getElementById('ndLead').textContent=
    r.instrumented?'Instrumented monument · '+NODE_META[r.owner].name
                  :'Relay cell · derived from neighbouring monuments';
  document.getElementById('ndHero').className='nodehero '+cls;
  document.getElementById('ndBig').textContent=id;
  document.getElementById('ndName').textContent=
    r.instrumented?NODE_META[r.owner].name:'Cell '+id+' — mesh relay';
  document.getElementById('ndSub').textContent=meta.zone+' · '+meta.grid;
  const badge=document.getElementById('ndBadge');
  badge.textContent=r.instrumented?st:(st==='NOMINAL'?'RELAY OK':st);

  document.getElementById('ndD').textContent=r.d.toFixed(1);
  document.getElementById('ndT').textContent=r.t.toFixed(1);
  document.getElementById('ndA').textContent=r.a.toFixed(2);
  document.getElementById('ndDb').style.width=Math.max(1.5,Math.min(100,r.d))+'%';
  const tb=document.getElementById('ndTb'),tv=Math.max(-45,Math.min(45,r.t)),tw=Math.abs(tv)/45*50;
  tb.style.left=(tv>=0?50:50-tw)+'%'; tb.style.width=Math.max(0.6,tw)+'%';
  const ab=document.getElementById('ndAb');
  ab.style.width=Math.max(1,Math.min(100,(r.a+1)/6*100))+'%';
  ab.style.background=r.a>=3?'linear-gradient(90deg,#7a1c1c,#ff4d4d)':
    r.a>0.25?'linear-gradient(90deg,#8f5a1e,#ff9538)':'linear-gradient(90deg,#1c7a45,#4ade80)';

  document.getElementById('ndGrid').textContent=meta.grid+' · '+meta.zone;
  document.getElementById('ndInst').textContent=
    r.instrumented?'MPU-6050 + SLIDE POT (LIVE)':'ESP-NOW RELAY · DERIVED';
  document.getElementById('ndNbr').textContent=CELL_ADJ[meta.idx].join(' · ');
  document.getElementById('ndHop').textContent =
    st==='CRITICAL'?'DROP ISOLATED · LAST-GASP SENT':'DROP → JB → HEAD-END · 1 BUS HOP';
  document.getElementById('ndParent').textContent=
    'JUNCTION BOX JB-'+meta.jb+' → MINI HUB MH'+meta.lvl+' (LEVEL '+meta.lvl+') → M-01';
  document.getElementById('ndBatt').textContent=meta.batt+' V · '+meta.rssi+' dBm';
  document.getElementById('ndSeen').textContent =
    st==='CRITICAL'&&r.instrumented?'LAST-GASP PACKET':(LAST_DATA?LAST_DATA.timestamp||'live':'—');

  document.getElementById('ndLegDot').style.background=col;
  document.getElementById('ndLegTxt').textContent=id;
  document.getElementById('ndChartTitle').textContent='CELL '+id+' · ACCELERATION · 120-SECOND WINDOW';

  document.querySelectorAll('#ndNav [data-ndcell]').forEach(b=>{
    const cid=b.dataset.ndcell, cr=cellReading(cid,LAST_DATA);
    b.classList.toggle('on',cid===id);
    b.classList.toggle('warnst',cr.status==='WARNING');
    b.classList.toggle('critst',cr.status==='CRITICAL');
  });

  drawCellChart(id,col);
}

function drawCellChart(id,col){
  const cv=document.getElementById('cNode');
  if(!cv||!cv.getBoundingClientRect().width)return;
  const{c,w,h}=fit(cv);
  const pad={l:40,r:12,t:12,b:26},lo=-1,hi=5,N=MAXPTS;
  const X=i=>pad.l+(i/(N-1))*(w-pad.l-pad.r);
  const Y=v=>pad.t+(1-((Math.min(hi,Math.max(lo,v))-lo)/(hi-lo)))*(h-pad.t-pad.b);
  c.clearRect(0,0,w,h);
  c.font='9px ui-monospace,monospace';c.textAlign='right';c.textBaseline='middle';
  for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,y=Y(v);
    c.strokeStyle='rgba(255,255,255,.045)';c.lineWidth=1;
    c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();
    c.fillStyle='#6c645e';c.fillText(v.toFixed(1),pad.l-7,y);}
  c.textAlign='center';c.textBaseline='top';
  for(let i=0;i<=4;i++){const x=pad.l+(i/4)*(w-pad.l-pad.r);
    c.fillStyle='#5f574f';c.fillText(i===4?'NOW':'−'+Math.round(120*(1-i/4))+'s',x,h-pad.b+5);}
  const y0=Y(0);c.strokeStyle='rgba(255,255,255,.10)';c.setLineDash([3,3]);
  c.beginPath();c.moveTo(pad.l,y0);c.lineTo(w-pad.r,y0);c.stroke();c.setLineDash([]);
  const yt=Y(3);c.strokeStyle='rgba(255,77,77,.75)';c.lineWidth=1.3;c.setLineDash([6,4]);
  c.beginPath();c.moveTo(pad.l,yt);c.lineTo(w-pad.r,yt);c.stroke();c.setLineDash([]);
  c.textAlign='left';c.fillStyle='rgba(255,77,77,.9)';c.fillText('CRITICAL 3.0',pad.l+6,yt-14);
  const data=cellSeries[id]||[];
  if(data.length>1){
    const off=N-data.length;
    c.lineWidth=1.9;c.strokeStyle=col;c.lineJoin='round';
    c.beginPath();data.forEach((v,i)=>{const x=X(off+i),y=Y(v);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();
    const g=c.createLinearGradient(0,pad.t,0,h-pad.b);
    g.addColorStop(0,col+'33');g.addColorStop(1,col+'00');
    c.lineTo(X(off+data.length-1),h-pad.b);c.lineTo(X(off),h-pad.b);c.closePath();
    c.fillStyle=g;c.fill();
    const lx=X(off+data.length-1),ly=Y(data[data.length-1]);
    c.fillStyle=col;c.beginPath();c.arc(lx,ly,2.8,0,7);c.fill();
  }
}

function redrawView(view){
  if(view==='telemetry'){
    drawChart('cAcc','acceleration',{threshold:3.0});
    drawChart('cDisp','displacement_mm'); drawChart('cTilt','tilt_x');
    drawChart('cInv','inv_v',{store:invSeries,max:MAXINV,dp:1,band:true});
  }
  if(view==='ai')NODES.forEach(drawForecast);
  if(view==='evacmap')drawMap(performance.now());
  if(view==='node')renderCellView();
}
function show(view){
  CUR_VIEW=view;
  document.querySelectorAll('.ri[data-view]').forEach(r=>{
    const on=r.dataset.view===view;
    r.classList.toggle('on',on);
    r.setAttribute('aria-current',on?'page':'false');
  });
  document.querySelectorAll('.view').forEach(v=>{
    const on=v.id==='v-'+view;
    v.classList.toggle('on',on);
    v.setAttribute('aria-hidden',on?'false':'true');
  });
  setTimeout(()=>redrawView(view),30);
}
document.querySelectorAll('.ri[data-view]').forEach(r=>onActivate(r,()=>show(r.dataset.view)));
document.querySelectorAll('.vclose').forEach(b=>b.onclick=()=>show('overview'));
document.querySelectorAll('.tg').forEach(t=>onActivate(t,()=>{
  const on=t.classList.toggle('on');
  t.setAttribute('aria-checked',on?'true':'false');
}));

onActivate(document.getElementById('panelsBtn'),()=>document.body.classList.toggle('panels'));

(function(){
  const KEY='ns.slide.';
  [['L','left'],['R','right']].forEach(function(pair){
    const k=pair[0], side=pair[1], el=document.getElementById('stab'+k);
    if(!el)return;
    let parked=false;
    try{parked=localStorage.getItem(KEY+side)==='1';}catch(e){}
    const paint=function(){
      document.body.classList.toggle('hide'+k,parked);
      el.setAttribute('aria-pressed',parked?'true':'false');
      el.title=(k==='L'?'Sensor strip (S1–S4 · M-01)':'Metric panels (deformation · slope motion · mesh)')+
        (parked?' — slide back in':' — slide out of the way');
    };
    paint();
    onActivate(el,function(){
      parked=!parked;paint();
      try{localStorage.setItem(KEY+side,parked?'1':'0');}catch(e){}
    });
  });
})();

function setMode(m){
  MODE=m; lastSwitch=performance.now();
  document.body.classList.toggle('ug',m==='underground');
  document.body.classList.remove('rfid');
  const bs=document.getElementById('mbSurf'), bu=document.getElementById('mbUg');
  bs.classList.toggle('on',m==='surface'); bs.setAttribute('aria-pressed',m==='surface');
  bu.classList.toggle('on',m==='underground'); bu.setAttribute('aria-pressed',m==='underground');
  document.getElementById('modeLine').textContent=
    m==='surface'?'Surface 2D plan · north up · mesh + plant':
    'Underground · 3D evac network · live RFID + Dijkstra';
  if(m==='underground'&&window.UG){
    UG.scene.setLevel(LVL);
    if(SEL_CELL&&UG.selectCell)UG.selectCell(CELL_BY_ID[SEL_CELL].idx,true);
  }
}
document.getElementById('mbSurf').onclick=()=>setMode('surface');
document.getElementById('mbUg').onclick=()=>setMode('underground');
document.querySelectorAll('.lv').forEach(b=>b.onclick=()=>{
  LVL=b.dataset.lvl;
  document.querySelectorAll('.lv').forEach(x=>{
    const on=x===b; x.classList.toggle('on',on); x.setAttribute('aria-pressed',on);
  });
  if(window.UG)UG.scene.setLevel(LVL);
});

const VIEW_KEYS={'1':'overview','2':'telemetry','3':'control','4':'records','5':'evacmap','6':'ai'};
document.addEventListener('keydown',e=>{
  if(e.metaKey||e.ctrlKey||e.altKey)return;
  const t=e.target;
  if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable))return;
  if(e.key==='Escape'){
    if(document.body.classList.contains('panels')){document.body.classList.remove('panels');return;}
    if(CUR_VIEW!=='overview'){show('overview');return;}
    if(SEL_CELL){clearCellPointer();return;}
  }
  if(e.key==='['||e.key===']'){
    const cur=SEL_CELL?CELL_BY_ID[SEL_CELL].idx:(e.key===']'?-1:0);
    const nx=((e.key===']'?cur+1:cur-1)+CELL_COUNT)%CELL_COUNT;
    pointAtCell(MESH_CELLS[nx].id);
    if(CUR_VIEW==='node')showCell(MESH_CELLS[nx].id);
    return;
  }
  if(VIEW_KEYS[e.key]){show(VIEW_KEYS[e.key]);return;}
  const k=e.key.toLowerCase();
  if(k==='s'){setMode('surface');return;}
  if(k==='u'){setMode('underground');return;}
  if(k==='r'){rearm();return;}
  if(k==='i'){openScenarios();return;}
  if(k==='?'){show('control');return;}
});

let rzT=null;
window.addEventListener('resize',()=>{
  clearTimeout(rzT);
  rzT=setTimeout(()=>redrawView(CUR_VIEW),140);
});
document.getElementById('evacBtn').onclick=()=>{
  if(MODE!=='underground')setMode('underground');
  show('evacmap');
};
const post=(p,b)=>fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},
                          body:JSON.stringify(b||{})}).catch(()=>{});
function doTrigger(node,severity){
  if(LINK)post(apiUrl('/api/sim/trigger'),{node,severity});
  else SIM.trigger(node,severity);
}

/* Phase 5 (§5.9) — arm one of the seven simulator profiles.
 *
 * Posts `{"node": …, "profile": …}`. The server accepts both this shape and the
 * older `{"severity": "warning"|"critical"}`, which is why the four buttons above
 * did not need touching: the runbook's curl commands keep working verbatim.
 *
 * The profile list is NOT hard-coded here. It is filled from the server's own
 * rejection payload — `/api/sim/trigger` answers an unknown profile with the set
 * it does know — so a profile added to `ingestion/simulator.py` appears in the
 * deck without a frontend release. The fallback list exists only so the control
 * is usable if the server is old enough not to send one.
 */
async function armProfile(node,profile,btn){
  if(!profile){setScStatus('PICK A PROFILE FIRST','var(--amber)');return;}
  if(btn){btn.disabled=true;btn.textContent='arming…';}
  try{
    const res=await fetch(apiUrl('/api/sim/trigger'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({node,profile})});
    const r=await res.json().catch(()=>({}));
    const ok=res.ok&&r&&r.ok!==false;
    setScStatus(ok
      ? node.toUpperCase()+' · '+String(r.profile||profile).toUpperCase()+' ARMED · '+
        (r.duration_s!=null?r.duration_s+' s':'')+(r.verdict?' · '+r.verdict:'')
      : ('REFUSED — '+((r&&r.error)||'unknown profile')),
      ok?'var(--amber-2)':'var(--crit)');
  }catch(e){
    setScStatus('ARM FAILED — server unreachable','var(--crit)');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Arm profile';}
  }
}

function setScStatus(msg,colour){
  const el=document.getElementById('scStatus');
  if(!el)return;
  el.textContent=msg;
  if(colour)el.style.color=colour;
}
function rearm(){
  if(LINK)post(apiUrl('/api/reset'));
  else SIM.rearm();
  SURF_ALARM.rearm();
  NODES.forEach(n=>{series[n]={acceleration:[],displacement_mm:[],tilt_x:[]};invSeries[n]={inv_v:[]};});
  document.querySelectorAll('#sconsole .sc-b').forEach(x=>x.classList.remove('armed'));
  const ss=document.getElementById('scStatus');
  if(ss){ss.textContent='NO INJECTION ARMED';ss.style.color='';}
}
const scStatus=document.getElementById('scStatus');
function fireScenario(node,severity,btn){
  doTrigger(node,severity);
  if(scStatus){
    scStatus.textContent=(node==='node_1'?'N1':'N2')+' · '+severity.toUpperCase()+' INJECTED';
    scStatus.style.color=severity==='critical'?'var(--crit)':'var(--amber-2)';
  }
  document.querySelectorAll('#sconsole .sc-b').forEach(x=>x.classList.remove('armed'));
  if(btn)btn.classList.add('armed');
}
document.querySelectorAll('[data-trig]').forEach(b=>b.onclick=()=>{
  const[node,severity]=b.dataset.trig.split('|');
  fireScenario(node,severity,b);
});
/* Phase 5 (§5.9) — the profile picker's binding. Separate from the four buttons
   above because it posts a different body (`profile`, not `severity`), and the
   two paths must stay independently breakable: the stage demo depends on the
   four, and the acceptance table depends on the seven. */
(function(){
  const go=document.getElementById('scProfileGo');
  const sel=document.getElementById('scProfile');
  const node=document.getElementById('scProfileNode');
  if(go&&sel&&node)go.onclick=()=>armProfile(node.value,sel.value,go);
})();
function openScenarios(){
  show('control');
  const p=document.getElementById('scenarioPanel');
  if(p){
    if(p.scrollIntoView)p.scrollIntoView({block:'center',behavior:'smooth'});
    const f=p.querySelector('.sc-b'); if(f)setTimeout(()=>f.focus(),260);
  }
}
const scBtn=document.getElementById('scenarioBtn');
if(scBtn)scBtn.onclick=openScenarios;
document.getElementById('mainBtn').onclick=e=>{
  if(e.target.dataset.act==='rearm')rearm();
  else fireScenario('node_1','critical',
    document.querySelector('#sconsole [data-trig="node_1|critical"]'));
};
document.getElementById('rearmBtn').onclick=rearm;

function setLink(ok){
  document.getElementById('linkDot').style.background=ok?'var(--ok)':'#8b8178';
  document.getElementById('linkDot').style.boxShadow='0 0 9px '+(ok?'var(--ok)':'transparent');
  document.getElementById('linkTxt').textContent=ok?'Online':'Sim mode';
  document.getElementById('tkMode').textContent=ok?'LIVE':'SIM';
}
let retryAt=0, retryStep=1000, linkKnown=false;
const backendDue=()=>LINK||performance.now()>=retryAt;
function backendFailed(){
  if(LINK||!linkKnown){LINK=false;linkKnown=true;setLink(false);}
  retryStep=Math.min(15000,retryStep*1.8);
  retryAt=performance.now()+retryStep;
}
function backendOk(){
  if(!LINK||!linkKnown){LINK=true;linkKnown=true;setLink(true);}
  retryStep=1000; retryAt=0;
}
const apiUrl=p=>window.apiUrl(p);

async function getJSON(url,ms){
  const ac=('AbortController' in window)?new AbortController():null;
  const to=ac?setTimeout(()=>ac.abort(),ms||2000):null;
  try{
    const r=await fetch(url,{cache:'no-store',signal:ac?ac.signal:undefined});
    if(!r.ok)throw new Error('HTTP '+r.status);
    return await r.json();
  } finally { if(to)clearTimeout(to); }
}
async function poll(){
  if(!backendDue()){ render(SIM.tick()); return; }
  try{
    const d=await getJSON(apiUrl('/api/telemetry'),1500);
    backendOk();
    // #region agent log
    if(d&&((d.rfid_log&&d.rfid_log.length)||(d.rfid_readers&&d.rfid_readers.length))){
      fetch('http://127.0.0.1:7737/ingest/5fc2ba1e-4d66-4628-a84e-f3d6dbbf8bcb',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'83ae1d'},body:JSON.stringify({sessionId:'83ae1d',hypothesisId:'D',location:'06-app.js:poll',message:'dashboard received rfid payload',data:{rows:(d.rfid_log||[]).map(r=>({id:r.id,level:r.level,tunnel:r.tunnel,zone:r.zone})),readers:d.rfid_readers||[]},timestamp:Date.now()})}).catch(()=>{});
    }
    // #endregion
    try{ render(d); }
    catch(paintErr){ console.warn('telemetry paint', paintErr); }
  }catch(e){
    backendFailed();
    render(SIM.tick());
  }
}
async function pollExt(){
  if(!backendDue()){ renderExt(cleanExtended(SIM.extended())); return; }
  try{
    const raw=await getJSON(apiUrl('/api/extended'),2000);
    if(window.BBX&&BBX.ingest)try{BBX.ingest(raw);}catch(e){}
    renderExt(cleanExtended(raw));
  }
  catch(e){ renderExt(cleanExtended(SIM.extended())); }
}
async function backfill(){
  try{ const h=await getJSON(apiUrl('/api/history'),2500);
    if(!h||typeof h!=='object'||Array.isArray(h))return;
    NODES.forEach(n=>{
      const arr=Array.isArray(h[n])?h[n]:[];
      arr.slice(-MAXPTS).forEach(s=>push(n,cleanNode(s)));
    });
  }catch(e){ backendFailed(); }
}

setInterval(()=>document.getElementById('tkClock').textContent=
  new Date().toLocaleTimeString('en-GB'),1000);

document.addEventListener('visibilitychange',()=>{ if(document.hidden)Siren.stop(); });

let MAP_LEVEL=null;

const MINE=(function(){
  const N={};
  const A=-1.075, B=-2.195, S=0.10;
  const add=(id,x,y,kind,name)=>{N[id]={x,y,kind,name};};
  add('AS',3.2,S,'exit','EAST AIR SHAFT');
  add('AW',-4.7,S,'exit','WEST PORTAL');
  add('BW',-3.7,S,'exit','WEST AIR SHAFT');
  add('SE',4.35,S,'exit','EXIT 2 · EMERGENCY INCLINE');
  add('aWt',-3.6,A,'jt');
  add('aH1',-2.9,A,'hub','MINI HUB MH1');
  add('aJ1',-1.2,A,'junction','JUNCTION BOX JB-1');
  add('aJ2', 1.2,A,'junction','JUNCTION BOX JB-2');
  add('aE',3.2,A,'jt');
  add('aSE',3.9,A,'jt');
  add('aS2',-0.9,-0.70,'stope','VENTILATION DRIFT 1');
  add('aS3',0.55,A-0.40,'stope','DEVELOPMENT DRIFT 2');
  add('aS4',2.85,A+0.40,'stope','EXPLORATION DRIFT');
  add('bWt',-3.6,B,'jt');
  add('bH1',-2.8,B,'hub','MINI HUB MH2');
  add('bJ1',-1.0,B,'junction','JUNCTION BOX JB-3');
  add('bJ2', 1.2,B,'junction','JUNCTION BOX JB-4');
  add('bE',3.2,B,'jt');
  add('bSE',3.8,B,'jt');
  add('bS2',-0.6,B-0.40,'stope','EXPLORATION DRIFT');
  add('bS3',1.20,B+0.40,'stope','EXPLORATION DRIFT');
  add('bS4',2.8,B-0.36,'stope','EXPLORATION DRIFT')
  add('S1',-1.80,0.46,'hub','SURFACE NODE S1')
  add('S2',-0.55,0.34,'hub','SURFACE NODE S2')
  add('S3', 1.15,0.36,'hub','SURFACE NODE S3')
  add('S4', 2.30,0.48,'hub','SURFACE NODE S4')
  add('M01',0.30,0.78,'hub','MAIN HUB M-01');
  const E=[];
  const link=(a,b)=>E.push({a,b,w:Math.hypot(N[a].x-N[b].x,N[a].y-N[b].y)});
  link('AW','aWt'); link('BW','bWt'); link('AS','aE'); link('aE','bE');
  link('SE','aSE'); link('aSE','bSE'); link('aE','aSE'); link('bE','bSE');
  link('aJ2','bJ2');
  link('aWt','aH1');link('aH1','aJ1');link('aJ1','aJ2');link('aJ2','aE');
  link('aJ1','aS2');link('aJ1','aS3');link('aJ2','aS4');
  link('bWt','bH1');link('bH1','bJ1');link('bJ1','bJ2');link('bJ2','bE');
  link('bJ1','bS2');link('bJ1','bS3');link('bJ2','bS4');
  const key=(a,b)=>a<b?a+'|'+b:b+'|'+a;
  const blockedEdges=(rA,rB)=>{
    const set=new Set();
    if(rA==='BLOCKED')['AW|aWt','aH1|aWt','aH1|aJ1','aJ1|aS2'].forEach(k=>set.add(k));
    if(rB==='BLOCKED')['BW|bWt','bH1|bWt','bH1|bJ1','bJ1|bS2'].forEach(k=>set.add(k));
    return set;
  };
  const dijkstra=(start,blocked)=>{
    const dist={},prev={},done={};
    for(const id in N)dist[id]=1e9;
    dist[start]=0;
    for(;;){
      let u=null,ub=1e9;
      for(const id in N){if(!done[id]&&dist[id]<ub){ub=dist[id];u=id;}}
      if(u===null)break;
      done[u]=true;
      for(const e of E){
        if(blocked.has(key(e.a,e.b)))continue;
        let v=null;
        if(e.a===u)v=e.b; else if(e.b===u)v=e.a; else continue;
        if(dist[u]+e.w<dist[v]){dist[v]=dist[u]+e.w;prev[v]=u;}
      }
    }
    return{dist,prev};
  };
  const plan=(from,rA,rB)=>{
    const blocked=blockedEdges(rA,rB);
    const{dist,prev}=dijkstra(from,blocked);
    let best=null,bd=1e9;
    const exits=Object.keys(N).filter(id=>N[id].kind==='exit');
    for(const e of exits)if(dist[e]<bd){bd=dist[e];best=e;}
    if(!best||bd>=1e8)return null;
    const path=[]; let u=best;
    for(;;){
      path.push(u);
      if(u===from)break;
      u=prev[u];
      if(!u)break;
    }
    path.reverse();
    return{exit:best,path,dist:bd};
  };
  const CREW_POS={a:['aS2','aJ1','aH1','aS4'], b:['bS2','bJ1','bH1','bS4']};
  const M=window.NS_MESH;
  const cellsOf=f=>M.nums.filter(f).map(n=>'E'+n);
  const BUS=[
    {lvl:1,level:'l1',hub:'aH1',hubName:'MH1',eol:'aE',cells:cellsOf(n=>M.levelOf(n)===1),
     jb:[{id:'aJ1',name:'JB-1',cells:cellsOf(n=>M.jbOf(n)===1)},{id:'aJ2',name:'JB-2',cells:cellsOf(n=>M.jbOf(n)===2)}]},
    {lvl:2,level:'l2',hub:'bH1',hubName:'MH2',eol:'bE',cells:cellsOf(n=>M.levelOf(n)===2),
     jb:[{id:'bJ1',name:'JB-3',cells:cellsOf(n=>M.jbOf(n)===3)},{id:'bJ2',name:'JB-4',cells:cellsOf(n=>M.jbOf(n)===4)}]}
  ];
  return{N,E,key,blockedEdges,plan,CREW_POS,BUS};
})();
window.MINE=MINE;
function pointAt(path,t){
  const N=MINE.N;
  const pts=path.map(id=>[N[id].x,N[id].y]);
  const segs=[]; let total=0;
  for(let i=0;i<pts.length-1;i++){
    const l=Math.hypot(pts[i+1][0]-pts[i][0],pts[i+1][1]-pts[i][1]);
    segs.push(l); total+=l;
  }
  let d=t*total;
  for(let i=0;i<segs.length;i++){
    if(d<=segs[i]||i===segs.length-1){
      const u=segs[i]?Math.min(1,d/segs[i]):0;
      return[pts[i][0]+(pts[i+1][0]-pts[i][0])*u, pts[i][1]+(pts[i+1][1]-pts[i][1])*u];
    }
    d-=segs[i];
  }
  return pts[pts.length-1];
}

function setMapBanner(d){
  const mb=document.getElementById('mapBanner');
  const rA=d.underground.route_A_status, rB=d.underground.route_B_status;
  const zA=d.rfid_log.filter(r=>r.zone==='Tunnel A').length;
  const eA=d.rfid_log.filter(r=>r.zone==='Evacuating A').length;
  const zB=d.rfid_log.filter(r=>r.zone==='Tunnel B').length;
  const eB=d.rfid_log.filter(r=>r.zone==='Evacuating B').length;
  const crewA=zA+eA, crewB=zB+eB;
  const safeN=d.rfid_log.filter(r=>r.zone==='Safe Exit').length;
  const ms=document.getElementById('mapStats');
  if(ms)ms.textContent='Live Dijkstra routing from the backend · 4 surface entrances · L1: '+crewA+
    ' · L2: '+crewB+' · Total Mustered: '+safeN;
  const mc=document.getElementById('mapCalc');
  if(mc){
    const active=(crewA+crewB)>0;
    const danger=rA==='BLOCKED'||rB==='BLOCKED';
    mc.textContent=active?(danger?'PATH CALCULATION ACTIVE — PANIC ROUTES':'PATHS NOMINAL — ALL CREW ROUTED'):'PATHS NOMINAL';
    mc.className='calc'+(active&&danger?' act':'');
  }
  const both=rA==='BLOCKED'&&rB==='BLOCKED';
  if(both){
    mb.className='mapbanner crit';
    mb.textContent='⚠ BOTH WEST ENTRANCES BLOCKED — ALL CREW EXIT VIA EAST AIR SHAFT + EMERGENCY INCLINE (or cross level via CROSS SHAFT). SIREN: ALL LEVELS · L1 crew: '+crewA+' · L2 crew: '+crewB;
  } else if(rA==='BLOCKED'){
    mb.className='mapbanner warn';
    mb.textContent='⚠ LEVEL 1 — WEST PORTAL BLOCKED · L1 CREW EXIT VIA EAST AIR SHAFT / EMERGENCY INCLINE'+(crewB?' · L2 unaffected':'')+'. SIREN: LEVEL 1 · L1 crew: '+crewA+' · L2 crew: '+crewB;
  } else if(rB==='BLOCKED'){
    mb.className='mapbanner warn';
    mb.textContent='⚠ LEVEL 2 — WEST AIR SHAFT BLOCKED · L2 CREW EXIT VIA EAST AIR SHAFT / EMERGENCY INCLINE'+(crewA?' · L1 unaffected':'')+'. SIREN: LEVEL 2 · L1 crew: '+crewA+' · L2 crew: '+crewB;
  } else {
    mb.className='mapbanner';
    mb.textContent='✓ ALL ENTRANCES OPEN — crew routed to nearest exit (Dijkstra). L1 crew: '+crewA+' · L2 crew: '+crewB+' · Mustered: '+d.rfid_log.filter(r=>r.zone==='Safe Exit').length;
  }
  if(MAP_LEVEL){
    const isL1=MAP_LEVEL==='a';
    const own=isL1?rA:rB;
    mb.textContent+=' · VIEW: '+(isL1?'LEVEL 1':'LEVEL 2')+' NETWORK'+(own==='BLOCKED'?' — PANIC ROUTING ACTIVE':'')+' · click map to return';
  }
}
function mapProj(W,H,zoom,lv){
  const base=Math.min((W-100)/10.0,(H-150)/3.8)*(zoom||1);
  const cy=lv==='a'?-1.075:lv==='b'?-2.195:-1.15;
  return{base, X:x=>W/2+x*base, Y:y=>H/2+(cy-y)*base};
}
function mapHitLevel(mx,my,W,H){
  const{Y}=mapProj(W,H,1,null);
  if(Math.abs(my-Y(-1.075))<24)return'a';
  if(Math.abs(my-Y(-2.195))<24)return'b';
  return null;
}
function rr(c,x,y,w,h,r){
  c.beginPath();
  if(c.roundRect)c.roundRect(x,y,w,h,r); else c.rect(x,y,w,h);
}
let rockTex=null, rockTexKey='';
const coalGlints=(()=>{ let seed=42; const rnd=()=>{seed|=0;seed=seed+0x6D2B79F5|0;
  let t=Math.imul(seed^seed>>>15,1|seed);t=t+Math.imul(t^t>>>7,61|t)^t;
  return((t^t>>>14)>>>0)/4294967296;};
  const a=[]; for(let i=0;i<240;i++)a.push([rnd(),rnd()]); return a;})();
function getRockTex(W,H){
  const key=W+'x'+H;
  if(rockTex&&rockTexKey===key)return rockTex;
  rockTexKey=key;
  const off=document.createElement('canvas');
  off.width=Math.max(2,W); off.height=Math.max(2,H);
  const c=off.getContext('2d');
  c.fillStyle='#14110e';c.fillRect(0,0,W,H);
  let seed=1337;
  const rnd=()=>{seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);
    t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};
  for(let i=0;i<3200;i++){
    const x=rnd()*W,y=rnd()*H,v=rnd();
    c.fillStyle=v<0.55?`rgba(0,0,0,${0.10+rnd()*0.30})`:`rgba(255,235,200,${0.012+rnd()*0.05})`;
    const sz=0.6+rnd()*2.1;
    c.fillRect(x,y,sz,rnd()<0.85?sz:sz*0.5);
  }
  c.strokeStyle='rgba(0,0,0,.22)';c.lineWidth=1;
  for(let y=0;y<H;y+=6+rnd()*4){c.globalAlpha=0.4+rnd()*0.5;
    c.beginPath();c.moveTo(0,y);c.lineTo(W,y);c.stroke();}
  c.globalAlpha=1;
  rockTex=off;
  return off;
}
const STRATA=[
  [0.75,0.10,'#211c16'],
  [0.10,-0.50,'#251f19'],
  [-0.50,-0.78,'#0f0d0b'],
  [-0.78,-1.50,'#211b15'],
  [-1.50,-2.45,'#1e1813'],
  [-2.45,-2.70,'#0e0c0a'],
  [-2.70,-3.40,'#171310']
];
const SEAMS=[[-0.50,-0.78],[-2.45,-2.70]];

let _mapCtx=null,_mapW=0,_mapH=0;
function drawMap(now){
  const cv=document.getElementById('cMap'); if(!cv)return;
  const b=cv.getBoundingClientRect(); if(!b.width)return;
  if(NSD.paintOk)NSD.paintOk('cMap');
  const r=Math.min(window.devicePixelRatio||1,2);
  const cw=Math.round(b.width*r),ch=Math.round(b.height*r);
  if(cw!==_mapW||ch!==_mapH){cv.width=_mapW=cw;cv.height=_mapH=ch;_mapCtx=null;}
  const c=_mapCtx||(_mapCtx=cv.getContext('2d'));
  c.setTransform(r,0,0,r,0,0);
  const W=b.width,H=b.height;
  c.clearRect(0,0,W,H);
  const d=LAST_DATA;
  if(!d){c.fillStyle='#6c645e';c.font='12px sans-serif';c.fillText('Waiting for telemetry…',20,30);return;}
  const lv=MAP_LEVEL;
  const{base,X,Y}=mapProj(W,H,lv?1.55:1,lv);
  const A=-1.075, B=-2.195;
  const rA=d.underground.route_A_status, rB=d.underground.route_B_status;
  const both=rA==='BLOCKED'&&rB==='BLOCKED';
  const danger=rA==='BLOCKED'||rB==='BLOCKED';
  const M=MINE.N;
  const blocked=MINE.blockedEdges(rA,rB);
  const isBl=(a,b2)=>blocked.has(MINE.key(a,b2));
  const inView=y=>Y(y)>-60&&Y(y)<H+60;

  c.drawImage(getRockTex(W,H),0,0);
  STRATA.forEach(([y0,y1,col])=>{
    const ya=Y(y0),yb=Y(y1);
    c.fillStyle=col;c.globalAlpha=0.82;
    c.fillRect(0,Math.min(ya,yb),W,Math.abs(yb-ya));c.globalAlpha=1;
  });
  c.strokeStyle='rgba(0,0,0,.5)';c.lineWidth=1;
  STRATA.forEach(([y0])=>{const y=Y(y0);c.beginPath();c.moveTo(0,y);c.lineTo(W,y);c.stroke();});
  SEAMS.forEach(([y0,y1])=>{
    const ya=Y(y0),yb=Y(y1);
    c.fillStyle='#0b0908';c.fillRect(0,ya,W,yb-ya);
    c.strokeStyle='rgba(255,200,150,.10)';
    coalGlints.forEach(([fx,fy])=>{
      const x=fx*W,y=ya+fy*(yb-ya);
      c.fillStyle=`rgba(255,215,170,${0.04+((fx*7)%1)*0.20})`;
      c.fillRect(x,y,1.5,1);
    });
  });

  if(inView(0.10)){
    const ys=Y(0.10);
    c.strokeStyle='rgba(190,168,135,.75)';c.lineWidth=2;
    c.beginPath();c.moveTo(0,ys);c.lineTo(W,ys);c.stroke();
    c.strokeStyle='rgba(0,0,0,.55)';c.lineWidth=1;
    c.beginPath();c.moveTo(0,ys+2);c.lineTo(W,ys+2);c.stroke();
  }

  const tube=(p,q,w,core,glowCol,glowW)=>{
    c.lineCap='round';
    c.strokeStyle='#0c0a08';c.lineWidth=w+4;
    c.beginPath();c.moveTo(X(p.x),Y(p.y));c.lineTo(X(q.x),Y(q.y));c.stroke();
    c.strokeStyle=core;c.lineWidth=w;
    c.beginPath();c.moveTo(X(p.x),Y(p.y));c.lineTo(X(q.x),Y(q.y));c.stroke();
    if(glowCol){
      c.shadowColor='#39ff88';c.shadowBlur=5;
      c.strokeStyle=glowCol;c.lineWidth=glowW||1.5;
      c.beginPath();c.moveTo(X(p.x),Y(p.y));c.lineTo(X(q.x),Y(q.y));c.stroke();
      c.shadowBlur=0;
    }
  };
  MINE.E.forEach(e=>{
    const p=M[e.a],q=M[e.b];
    const bl=isBl(e.a,e.b);
    const vert=Math.abs(p.x-q.x)<0.05;
    const main=Math.abs(p.y-q.y)<0.01;
    const w=vert?7:(main?9:6);
    if(bl){
      c.lineCap='round';
      c.strokeStyle='rgba(255,70,55,.25)';c.lineWidth=w+6;
      c.beginPath();c.moveTo(X(p.x),Y(p.y));c.lineTo(X(q.x),Y(q.y));c.stroke();
      c.shadowColor='#ff4638';c.shadowBlur=7;
      c.strokeStyle='rgba(255,80,62,.9)';c.lineWidth=Math.max(2,w-3);
      c.beginPath();c.moveTo(X(p.x),Y(p.y));c.lineTo(X(q.x),Y(q.y));c.stroke();
      c.shadowBlur=0;
      const mx=(X(p.x)+X(q.x))/2,my=(Y(p.y)+Y(q.y))/2;
      c.strokeStyle='#ff6b5e';c.lineWidth=4;
      c.beginPath();c.moveTo(mx-9,my-9);c.lineTo(mx+9,my+9);
      c.moveTo(mx+9,my-9);c.lineTo(mx-9,my+9);c.stroke();
      return;
    }
    tube(p,q,w,'#221d17',main||vert?'rgba(125,255,176,.5)':'rgba(125,255,176,.35)');
  });
  Object.values(M).forEach(p=>{
    if(p.kind!=='jt')return;
    c.fillStyle='#7dffb0';
    c.fillRect(X(p.x)-3,Y(p.y)-3,6,6);
    c.strokeStyle='#0c0a08';c.lineWidth=1;c.strokeRect(X(p.x)-3,Y(p.y)-3,6,6);
  });

  Object.entries(M).forEach(([id,p])=>{
    if(p.kind!=='stope')return;
    const e=MINE.E.find(e2=>e2.a===id||e2.b===id);
    if(!e)return;
    const other=M[e.a===id?e.b:e.a];
    const dx=X(p.x)-X(other.x),dy=Y(p.y)-Y(other.y);
    const ang=Math.atan2(dy,dx);
    c.strokeStyle='rgba(210,190,160,.75)';c.lineWidth=1.5;
    for(let k=-1;k<=1;k++){
      const px=X(p.x)+Math.cos(ang)*4+Math.cos(ang+Math.PI/2)*k*5;
      const py=Y(p.y)+Math.sin(ang)*4+Math.sin(ang+Math.PI/2)*k*5;
      c.beginPath();c.moveTo(px,py);
      c.lineTo(px+Math.cos(ang)*6,py+Math.sin(ang)*6);c.stroke();
    }
    c.font='8.5px ui-monospace,Menlo,monospace';
    c.fillStyle='#948a7e';
    c.textAlign=dx>=0?'left':'right';
    c.fillText(p.name,X(p.x)+(dx>=0?10:-10),Y(p.y)+(dy>=0?16:-8));
    c.textAlign='left';
    if(p.name.indexOf('VENTILATION')===0){
      const vx=X(p.x),vy=Y(p.y);
      c.fillStyle='#26221e';c.strokeStyle='rgba(255,255,255,.25)';c.lineWidth=1;
      rr(c,vx+6,vy-14,26,16,3);c.fill();c.stroke();
      c.strokeStyle='rgba(160,220,255,.7)';c.lineWidth=1.2;
      c.beginPath();c.arc(vx+19,vy-6,4.5,0,7);c.stroke();
      const fa=now/400;
      c.beginPath();
      for(let k=0;k<3;k++){const a2=fa+k*2.09;
        c.moveTo(vx+19,vy-6);c.lineTo(vx+19+Math.cos(a2)*4,vy-6+Math.sin(a2)*4);}
      c.stroke();
    }
  });
  c.font='700 10px ui-monospace,Menlo,monospace';
  c.fillStyle='#d3cbc0';
  c.fillText('MAIN HAULAGE ROAD 1',X(-3.45),Y(A)-15);
  c.fillText('CONVEYOR DRIFT A',X(-3.45),Y(B)-15);
  c.font='8px ui-monospace,monospace';c.fillStyle='#948a7e';
  c.fillText('CROSS SHAFT',X(1.72)+9,(Y(A)+Y(B))/2);
  c.fillText('SECONDARY',X(4.02)+12,Y(-1.45));
  c.fillText('EMERGENCY INCLINE',X(4.02)+12,Y(-1.45)+10);

  const exitStruct=(id,kind)=>{
    const p=M[id]; if(!inView(p.y))return;
    const px=X(p.x),py=Y(p.y);
    if(kind==='portal'){
      c.fillStyle='#26221e';
      c.beginPath();c.arc(px,py,15,Math.PI,0);c.closePath();c.fill();
      c.strokeStyle='rgba(255,255,255,.3)';c.lineWidth=1.5;
      c.beginPath();c.arc(px,py,15,Math.PI,0);c.stroke();
      c.fillStyle='#0c0a08';
      c.beginPath();c.arc(px,py,9,Math.PI,0);c.closePath();c.fill();
    } else if(kind==='shaft'){
      c.fillStyle='#26221e';
      c.fillRect(px-14,py-22,28,18);
      c.strokeStyle='rgba(255,255,255,.3)';c.lineWidth=1;
      c.strokeRect(px-14,py-22,28,18);
      c.strokeStyle='#8d857e';c.lineWidth=1.4;
      c.beginPath();c.arc(px,py-13,5.5,0,7);c.stroke();
      c.beginPath();c.moveTo(px-5.5,py-13);c.lineTo(px+5.5,py-13);
      c.moveTo(px,py-18.5);c.lineTo(px,py-7.5);c.stroke();
    } else {
      c.fillStyle='#26221e';c.strokeStyle='rgba(255,255,255,.3)';c.lineWidth=1;
      c.beginPath();c.moveTo(px,py-11);c.lineTo(px+9,py);c.lineTo(px,py+11);c.lineTo(px-9,py);
      c.closePath();c.fill();c.stroke();
      c.fillStyle='#ffb84d';
      c.beginPath();c.moveTo(px,py-5);c.lineTo(px+4,py+3);c.lineTo(px-4,py+3);c.closePath();c.fill();
    }
  };
  exitStruct('AW','portal');
  exitStruct('BW','shaft');
  exitStruct('AS','shaft');
  exitStruct('SE','incline');
  const exitLabel=(id,name,sub,d1,d2)=>{
    const p=M[id]; const px=X(p.x),py=Y(p.y);
    const bl=(id==='AW'&&rA==='BLOCKED')||(id==='BW'&&rB==='BLOCKED');
    c.textAlign='center';
    c.font='700 10.5px ui-monospace,Menlo,monospace';
    c.fillStyle=bl?'#ff9d9d':'#a7e5bf';
    c.fillText(name,px,py+d1);
    c.font='8.5px ui-monospace,monospace';c.fillStyle='#8d857e';
    c.fillText(sub,px,py+d2);
    if(bl){c.font='700 9px ui-monospace,monospace';c.fillStyle='#ff6b5e';
      c.fillText('BLOCKED',px,py+d1-12);}
    c.textAlign='left';
  };
  exitLabel('AW','EXIT · WEST PORTAL','L1 · INCLINED',-38,-27);
  exitLabel('BW','EXIT · WEST AIR SHAFT','L2 · VERTICAL',-56,-45);
  exitLabel('AS','EXIT · EAST AIR SHAFT','SURFACE · L1 · L2',-38,-27);
  exitLabel('SE','SECONDARY INCLINE','EMERGENCY · L1 · L2',-38,-27);

  const chamber=(p,danger)=>{
    const px=X(p.x),py=Y(p.y);
    const w=54,h=34;
    c.fillStyle='#0d0b09';
    rr(c,px-w/2,py-h/2,w,h,5);c.fill();
    c.strokeStyle=danger?'rgba(255,90,70,.85)':'rgba(125,255,176,.5)';
    c.lineWidth=1.5;
    if(danger){c.shadowColor='#ff5a46';c.shadowBlur=9;}
    rr(c,px-w/2,py-h/2,w,h,5);c.stroke();c.shadowBlur=0;
    c.fillStyle='rgba(255,255,255,.09)';
    c.fillRect(px-w/2+5,py+h/2-9,w*0.42,5);
    c.fillRect(px+w/2-16,py-h/2+6,11,7);
    c.fillStyle='rgba(125,255,176,.55)';
    c.fillRect(px+w/2-15,py-h/2+7,9,2.5);
    c.fillStyle='#0d0b09';
    c.fillRect(px+w/2-1.5,py-6,3,12);
    c.strokeStyle='rgba(255,255,255,.2)';c.lineWidth=1;
    c.strokeRect(px+w/2-1.5,py-6,3,12);
    if(danger){
      c.fillStyle='#ff5a46';
      c.beginPath();c.moveTo(px-w/2+11,py-7);c.lineTo(px-w/2+19,py+7);c.lineTo(px-w/2+3,py+7);c.closePath();c.fill();
      c.fillStyle='#ffc061';
      c.beginPath();c.arc(px-w/2+11,py+3,2.2+0.6*Math.sin(now/120),0,7);c.fill();
    }
    for(let i=0;i<2;i++){
      c.fillStyle='#ffb84d';
      c.beginPath();c.arc(px-9+i*9,py+3,2.8,0,7);c.fill();
      c.strokeStyle='#0d0b09';c.lineWidth=1;c.stroke();
    }
    c.font='600 9px ui-monospace,monospace';c.textAlign='center';
    c.fillStyle=danger?'#ff9d9d':'#a7e5bf';
    c.fillText(p.name,px,py+h/2+13);
    c.textAlign='left';
  };
  Object.values(M).forEach(p=>{
    if(p.kind!=='hub')return;
    chamber(p,p.name.indexOf('L1')===0?rA==='BLOCKED':rB==='BLOCKED');
  });

  const crewCount=tag=>{
    const zones=tag==='a'?['Tunnel A','Evacuating A']:['Tunnel B','Evacuating B'];
    return d.rfid_log.filter(q=>zones.includes(q.zone)).length;
  };
  const evacCount=tag=>{
    const zones=tag==='a'?['Evacuating A']:['Evacuating B'];
    return d.rfid_log.filter(q=>zones.includes(q.zone)).length;
  };
  [['a',rA],['b',rB]].forEach(([tag,ownSt])=>{
    const n=Math.min(crewCount(tag),4);
    const evac=evacCount(tag)>0;
    const panic=ownSt==='BLOCKED';
    for(let i=0;i<n;i++){
      const fromId=MINE.CREW_POS[tag][i%MINE.CREW_POS[tag].length];
      const from=M[fromId];
      const pl=MINE.plan(fromId,rA,rB);
      if(pl){
        c.lineCap='round';c.lineJoin='round';
        c.shadowColor='#39ff88';c.shadowBlur=panic?9:4;
        c.strokeStyle=panic?`rgba(125,255,176,${0.7+0.3*Math.sin(now/120+i)})`:'rgba(125,255,176,.5)';
        c.lineWidth=panic?3.4:2.2;
        c.setLineDash([16,9]);c.lineDashOffset=-((now/26)%25);
        c.beginPath();
        pl.path.forEach((id,k)=>{const p=M[id];k?c.lineTo(X(p.x),Y(p.y)):c.moveTo(X(p.x),Y(p.y));});
        c.stroke();c.setLineDash([]);c.shadowBlur=0;
        for(let k=0;k<3;k++){
          const u=((now/2400)+k/3+i*0.13)%1;
          const p1=pointAt(pl.path,u),p2=pointAt(pl.path,Math.min(1,u+0.012));
          const ang=Math.atan2(Y(p2[1])-Y(p1[1]),X(p2[0])-X(p1[0]));
          c.save();c.translate(X(p1[0]),Y(p1[1]));c.rotate(ang);
          c.fillStyle=panic?'#7dffb0':'rgba(125,255,176,.8)';
          c.beginPath();c.moveTo(5,0);c.lineTo(-4,-4.5);c.lineTo(-4,4.5);c.closePath();c.fill();
          c.restore();
        }
        const ex=M[pl.exit];
        const pr=8+4*Math.abs(Math.sin(now/200));
        c.strokeStyle='rgba(125,255,176,.85)';c.lineWidth=1.6;
        c.beginPath();c.arc(X(ex.x),Y(ex.y)-14,pr,0,7);c.stroke();
        c.font='700 9px ui-monospace,monospace';c.fillStyle='#7dffb0';c.textAlign='center';
        c.fillText('EXIT',X(ex.x),Y(ex.y)-14-pr-5);
        c.textAlign='left';
      } else {
        let hb=null,bd=1e9;
        const hubs=Object.values(M).filter(p2=>p2.kind==='hub'&&p2.name.indexOf(tag==='a'?'L1':'L2')===0);
        hubs.forEach(p2=>{const dd=Math.hypot(p2.x-from.x,p2.y-from.y);if(dd<bd){bd=dd;hb=p2;}});
        if(hb){
          c.strokeStyle='rgba(255,192,97,.8)';c.lineWidth=1.8;c.setLineDash([5,4]);
          c.beginPath();c.moveTo(X(from.x),Y(from.y));c.lineTo(X(hb.x),Y(hb.y));c.stroke();c.setLineDash([]);
          c.font='700 9px ui-monospace,monospace';c.fillStyle='#ffc061';c.textAlign='center';
          c.fillText('SHELTER',X((from.x+hb.x)/2),Y((from.y+hb.y)/2)-10);
          c.textAlign='left';
        }
      }
      let cx=from.x,cy=from.y;
      if(evac&&pl){const p2=pointAt(pl.path,(now/9000+i*0.33)%1);cx=p2[0];cy=p2[1];}
      c.fillStyle=evac?'#ff7849':'#ffb84d';
      c.shadowColor=evac?'#ff7849':'#ffb84d';c.shadowBlur=6;
      c.beginPath();c.arc(X(cx),Y(cy),4.5,0,7);c.fill();
      c.shadowBlur=0;
      c.strokeStyle='#0c0a08';c.lineWidth=1.2;c.stroke();
    }
  });

  c.font='700 11px ui-monospace,monospace';c.textAlign='right';c.textBaseline='middle';
  /* Phase 4 — the level labels carry their zones' risk.
   *
   * A healthy level keeps the schematic's neutral grey rather than turning
   * green: the label is a *position* marker, and a schematic that is coloured in
   * when nothing is wrong stops meaning anything when something is. Amber and
   * red are the signal, and they are the server's words — `ZONE_COLOR` mirrors
   * the status palette and nothing here re-derives a risk. */
  const lr1=levelZoneRisk(1), lr2=levelZoneRisk(2);
  c.fillStyle=(lr1==='NOMINAL')?'#d8d1c8':(ZONE_COLOR[lr1]||'#d8d1c8');
  c.fillText('LEVEL 1  ·  RL+200',W-14,Y(A));
  c.fillStyle=(lr2==='NOMINAL')?'#d8d1c8':(ZONE_COLOR[lr2]||'#d8d1c8');
  c.fillText('LEVEL 2  ·  RL+186',W-14,Y(B));
  c.fillStyle='#d8d1c8';
  c.fillStyle='#9b938c';c.font='10px ui-monospace,monospace';
  c.fillText('SURFACE  ·  RL+212',W-14,Y(0.10)-14);
  c.textAlign='left';c.textBaseline='alphabetic';

  const cx=W-118,cy=96;
  c.strokeStyle='rgba(210,200,190,.4)';c.lineWidth=1.2;
  c.beginPath();c.arc(cx,cy,24,0,7);c.stroke();
  c.beginPath();c.moveTo(cx,cy-24);c.lineTo(cx,cy+24);
  c.moveTo(cx-24,cy);c.lineTo(cx+24,cy);c.stroke();
  c.fillStyle='#e8e2da';
  c.beginPath();c.moveTo(cx,cy-19);c.lineTo(cx-6,cy);c.lineTo(cx+6,cy);c.closePath();c.fill();
  c.fillStyle='rgba(210,200,190,.3)';
  c.beginPath();c.moveTo(cx,cy+19);c.lineTo(cx-6,cy);c.lineTo(cx+6,cy);c.closePath();c.fill();
  c.font='700 9px ui-monospace,monospace';c.fillStyle='#cfc7c0';c.textAlign='center';c.textBaseline='middle';
  c.fillText('N',cx,cy-33);c.fillText('S',cx,cy+33);
  c.fillText('E',cx+33,cy);c.fillText('W',cx-33,cy);
  c.textAlign='left';c.textBaseline='alphabetic';

  const bx=22,by=H-22;
  c.strokeStyle='rgba(210,200,190,.55)';c.lineWidth=1.5;
  c.beginPath();c.moveTo(bx,by);c.lineTo(bx+2*base,by);c.stroke();
  c.beginPath();c.moveTo(bx,by-4);c.lineTo(bx,by+4);
  c.moveTo(bx+2*base,by-4);c.lineTo(bx+2*base,by+4);c.stroke();
  c.font='9px ui-monospace,monospace';c.fillStyle='rgba(210,200,190,.6)';c.textAlign='center';c.textBaseline='bottom';
  c.fillText('20 m',bx+base,by-5);
  c.textAlign='left';c.textBaseline='alphabetic';
}
(function(){
  const cv=document.getElementById('cMap');
  if(!cv)return;
  cv.style.cursor='pointer';
  cv.addEventListener('click',e=>{
    if(MAP_LEVEL){ MAP_LEVEL=null; }
    else{
      const b=cv.getBoundingClientRect();
      const hit=mapHitLevel(e.clientX-b.left,e.clientY-b.top,b.width,b.height);
      if(hit)MAP_LEVEL=hit;
    }
    if(LAST_DATA)setMapBanner(LAST_DATA);
    drawMap(performance.now());
  });
})();

let _evacView=null;
function loop(){
  const now=performance.now();
  const covered=(CUR_VIEW!=='overview')||document.hidden;
  const fade=(now-lastSwitch<700);
  if(window.SURF){
    SURF.scene.wantRender=!covered&&((MODE==='surface')||fade);
    SURF.scene.tick();
  }
  if(window.UG){
    UG.scene.wantRender=!covered&&((MODE==='underground')||fade);
    UG.scene.tick();
  }
  if(window.UG3D) UG3D.setActive(!covered&&((MODE==='underground')||fade));
  const ev=_evacView||(_evacView=document.getElementById('v-evacmap'));
  if(ev&&ev.classList.contains('on')&&!document.hidden)drawMap(now);
  requestAnimationFrame(loop);
}

show('overview');
backfill().then(()=>{poll();pollExt();pollAi();loop();});
setInterval(poll,200);
setInterval(pollExt,1500);
setInterval(pollAi,5000);

window.__NS={show,rearm,doTrigger,fireScenario,openScenarios,Siren,Horn,surfaceAlarm:SURF_ALARM,drawMap,showCell,
             pointAtCell,clearCellPointer,setMode,
             get cell(){return CUR_CELL;},get selCell(){return SEL_CELL;},
             cellReading:(id)=>cellReading(id,LAST_DATA),
             mapClick:(x,y)=>{const b=document.getElementById('cMap').getBoundingClientRect();
               if(MAP_LEVEL)MAP_LEVEL=null;
               else{const hit=mapHitLevel(x,y,b.width,b.height); if(hit)MAP_LEVEL=hit;}
               if(LAST_DATA)setMapBanner(LAST_DATA); drawMap(performance.now());},
             get mapLevel(){return MAP_LEVEL;},
             get data(){return LAST_DATA;}};

/* ── CR-13: the PWA link ────────────────────────────────────────────────────
 * `manifest.webmanifest` is linked in the document head; this registers the
 * service worker that goes with it. Both files already shipped beside
 * `index.html` — nothing was generated for this rule.
 *
 * It lives here rather than in an inline block because the build inlines
 * `src/js/*.js` and nothing else: an inline `<script>` in `src/index.html` is
 * rejected by `tools/check.js`, and it would also be the one piece of script
 * that could not be reviewed in a file of its own.
 *
 * Registered on `load` so a cold cache can never block first paint, and guarded
 * because private modes and `file://` reject registration outright — a failure
 * here must never surface as an error on a dashboard that is otherwise fine.
 */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('service-worker.js').catch(function () {});
  });
}
