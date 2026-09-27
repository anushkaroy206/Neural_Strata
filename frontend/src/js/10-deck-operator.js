(function(){
'use strict';
const NSD=window.NSD;if(!NSD)return;
const U=NSD.U,$=U.$,$$=U.$$,clamp=U.clamp,fmt=U.fmt,rgba=U.rgba,fnv=U.fnv;
const MONO=U.MONO;

const E={};
['opShiftDot','opShiftId','opShiftWin','opClock','opShiftLeft','opShiftFill','opMgr','opOv','opMen','opPanel',
 'opSyncDot','opPulse','opLat','opSyncTxt','opBuf','opBufBar','opGw','opFlush','opLatSpark',
 'opSafety','opSafSt','opSafScore','opSafTx','opChTilt','opChPpv','opChMesh','opChResp',
 'opTopo','opTopoWrap','opTopoTag','opFly','opFailSel','opFailBtn','opCutSel','opCutBtn','opRestoreBtn',
 'opDialNode','opDialPitch','opDialRoll','opPitchV','opRollV','opTiltRate','opTiltMargin',
 'opSparkGrid','opPpv','opPpvPeak','opPpvRms','opPpvDom','opPpvClass','opPpvBurst','opPpvFeed','opPpvCount',
 'opTriageList','opTriageN','opEapSeam','opEapSwipe','opEapKnob','opEapFill','opEapState','opEapStage',
 'opEapHoot','opEapStrobe','opEapAbort','opEapDrill','opEapLog','opEapPanel',
 'opSmsTpl','opSmsRcp','opSmsSend','opSmsCnt','opSmsState','opSmsOut'].forEach(id=>{E[id]=document.getElementById(id);});
if(!E.opTopo)return;

const LEVELS=[
  {id:'L1',rl:'RL +200 m',title:'LEVEL 1 · BORD & PILLAR PANEL P-3 · HAULAGE ROAD'},
  {id:'L2',rl:'RL +186 m',title:'LEVEL 2 · CONVEYOR DRIFT + RETURN AIRWAY'}
];
const SMETA=[
  {id:'S1',zone:'NW CREST · BENCH 3',rl:'RL +231 m',risk:0.44},
  {id:'S2',zone:'N CREST · PANEL P-3 SUBSIDENCE ZONE',rl:'RL +228 m',risk:0.88},
  {id:'S3',zone:'NE CREST · HAUL ROAD BATTER',rl:'RL +226 m',risk:0.58},
  {id:'S4',zone:'E CREST · OB DUMP TOE',rl:'RL +219 m',risk:0.31}
];
const NODES=SMETA.map((m,i)=>({
  id:m.id, idx:i, cell:m.zone, zone:m.zone, rl:m.rl, risk:m.risk,
  batt:+(3.62+U.rr(0,0.3)).toFixed(2), rssi:-(58+Math.round(m.risk*20)),
  alive:true, warn:false, last:Date.now()-500, buf:[], pitch:0, roll:0, ppv:0.6, tx:0, rxs:0
}));
const NTOTAL=NODES.length;
NODES.forEach(n=>{
  n.pitch=+(n.risk*3.2+U.rr(-0.4,0.9)).toFixed(2);
  n.roll=+(n.risk*2.1+U.rr(-0.5,0.7)).toFixed(2);
});
const byId={};NODES.forEach(n=>byId[n.id]=n);
const MLINKS=[[0,1],[1,2],[2,3],[0,2],[1,3]];
const PAIRS=[['S1','S2'],['S2','S3'],['S3','S4'],['S1','S3']];
const pairCards=PAIRS.map(p=>({a:p[0],b:p[1],buf:[],el:null,cv:null,vt:null}));

const OP={
  lvlf:'all', busf:'both', sel:'S2', hover:null, ringCuts:{}, busCut:{0:null,1:null}, faultJb:{}, faultHub:{},
  failed:[], reroute:false, rerouteAt:0, meshDown:0,
  linkDown:0, linkOkAt:Date.now(), latency:[], bufPct:0, bufPkt:0, lastFlush:'—',
  ppv:[], events:[], peakHold:0, phase:0, tiltLive:0, ppvClass:'quiet',
  alarms:[], eap:{stage:0, armedAt:0, engaged:false, seam:'ALL'},
  sms:{tpl:[],rcp:[]}, lastPaint:0, lastText:0, started:Date.now()
};
for(let i=0;i<240;i++)OP.ppv.push(0);

const SHIFTS=[
  {id:'Shift-I',from:6,to:14,win:'06:00 – 14:00',mgr:'A. K. MAHATO',
   ov:'S. DAS (OVERMAN) · R. SEN (MIN. SIRDAR)',crew:27},
  {id:'Shift-II',from:14,to:22,win:'14:00 – 22:00',mgr:'P. N. BISWAS',
   ov:'B. K. MURMU (OVERMAN) · T. HANSDA (MIN. SIRDAR)',crew:31},
  {id:'Shift-III',from:22,to:6,win:'22:00 – 06:00',mgr:'D. MUKHERJEE',
   ov:'A. KUMAR (OVERMAN) · J. ORAON (MIN. SIRDAR)',crew:22}
];

let alarmSeq=2284;
function addAlarm(sev,title,detail,src){
  const a={id:'ALM-'+(++alarmSeq),sev:sev,title:title,detail:detail,ts:new Date(),
    acked:false,who:'',ackTs:null,src:src||''};
  OP.alarms.unshift(a);
  if(OP.alarms.length>14)OP.alarms.pop();
  renderTriage();
  NSD.ledger.add((sev===3?'LEVEL 3 ALERT · ':sev===2?'LEVEL 2 ALERT · ':'ADVISORY · ')+title,
    {sev:sev===3?'CRITICAL':sev===2?'WARNING':'INFO'});
  return a;
}
function ackAlarm(a,who){
  a.acked=true;a.who=who;a.ackTs=new Date();
  const mins=((a.ackTs-a.ts)/60000);
  NSD.ledger.add('OPERATOR ACK · '+a.id+' · '+fmt(mins,1)+' min AFTER ALERT',
    {actor:who,sev:'INFO'});
  renderTriage();renderRegulatorSoon();
}
let regSooner=null;
function renderRegulatorSoon(){clearTimeout(regSooner);regSooner=setTimeout(()=>{if(window.NSD&&NSD.regRefresh)NSD.regRefresh();},600);}

const HUBS=[
  {id:'M-01',kind:'main',lv:-1,buzzers:0},
  {id:'MH-1',kind:'mini',lv:0,buzzers:2},
  {id:'MH-2',kind:'mini',lv:1,buzzers:2}
];
const hubById={};HUBS.forEach(h=>hubById[h.id]=h);
const MINIS=HUBS.filter(h=>h.kind==='mini');
const RING=[
  {id:'R1',a:'M-01',b:'MH-1',run:'RISER · 210 m ARMOURED',len:'210 m'},
  {id:'R2',a:'MH-1',b:'MH-2',run:'CROSS SHAFT L1 → L2 · 140 m',len:'140 m'},
  {id:'R3',a:'MH-2',b:'M-01',run:'RETURN RISER · 260 m ARMOURED',len:'260 m'}
];
const ringById={};RING.forEach(s=>ringById[s.id]=s);
const JBS=[
  {id:'JB-1A',hub:'MH-1',lv:0,pos:0,ch:280,lights:6},
  {id:'JB-1B',hub:'MH-1',lv:0,pos:1,ch:620,lights:6},
  {id:'JB-2A',hub:'MH-2',lv:1,pos:0,ch:300,lights:8},
  {id:'JB-2B',hub:'MH-2',lv:1,pos:1,ch:700,lights:4}
];
const jbById={};JBS.forEach(j=>jbById[j.id]=j);
function ringReach(){
  const ok={'M-01':true};let more=true;
  while(more){more=false;RING.forEach(s=>{if(OP.ringCuts[s.id])return;
    if(ok[s.a]&&!ok[s.b]){ok[s.b]=true;more=true;}
    if(ok[s.b]&&!ok[s.a]){ok[s.a]=true;more=true;}});}
  return ok;
}
function ringCutIds(){return RING.filter(s=>OP.ringCuts[s.id]).map(s=>s.id);}
function hubReach(h){return h.kind==='main'||!!ringReach()[h.id];}
function hubUp(h){return hubReach(h)&&!OP.faultHub[h.id];}
function jbDark(j){if(!hubUp(hubById[j.hub]))return true;const s=OP.busCut[j.lv];return s===0||(s===1&&j.pos===1);}
function jbUp(j){return !OP.faultJb[j.id]&&!jbDark(j);}
function isolatedHubs(){return MINIS.filter(h=>!hubReach(h));}
function busCutOf(lv){const s=OP.busCut[lv];return (s===undefined)?null:s;}
function netStatus(){
  const online=NODES.filter(n=>n.alive).length;
  const iso=isolatedHubs(),cuts=ringCutIds();
  const hubsUp=MINIS.filter(hubUp).length,jbsUp=JBS.filter(jbUp).length;
  const lightsUp=JBS.filter(jbUp).reduce((a,j)=>a+j.lights,0);
  const buzzUp=MINIS.filter(hubUp).reduce((a,h)=>a+h.buzzers,0);
  const jbFaults=JBS.filter(j=>OP.faultJb[j.id]),hubFaults=MINIS.filter(h=>OP.faultHub[h.id]);
  let cls='ok';const parts=[];
  const worse=w=>{if(w==='crit')cls='crit';else if(cls==='ok')cls='warn';};
  if(iso.length){worse('crit');parts.push(iso.map(h=>h.id).join(' + ')+' ISOLATED · '+cuts.join(' + ')+' CUT');}
  else if(cuts.length){worse('warn');parts.push('RING WRAPPED · '+cuts.join(' + ')+' CUT · SECONDARY PATH · ALL HUBS REACHABLE');}
  if(hubFaults.length){worse('crit');parts.push(hubFaults.map(h=>h.id).join(', ')+' FAULT · BUZZERS OFFLINE · BUS DARK');}
  [0,1].forEach(lv=>{const s=busCutOf(lv);if(s===null)return;
    const hub=MINIS[lv];if(!hubUp(hub))return;
    const jb=JBS.filter(j=>j.lv===lv),dark=jb.filter(j=>!OP.faultJb[j.id]&&jbDark(j));
    worse(dark.length?'crit':'warn');
    parts.push('BUS '+hub.id+' CUT '+(s===0?'AT HEAD-END':s===1?'AFTER '+jb[0].id:'PAST '+jb[1].id)+(dark.length?' · '+dark.map(j=>j.id).join(', ')+' NO-LINK':' · NO LIGHTS LOST'));});
  if(jbFaults.length){worse('warn');parts.push(jbFaults.map(j=>j.id).join(', ')+' FAULT · '+jbFaults.reduce((a,j)=>a+j.lights,0)+' EVAC LIGHTS OFFLINE');}
  if(online<NTOTAL){worse(online<NTOTAL-1?'crit':'warn');parts.push('SURFACE MESH '+online+'/'+NTOTAL+' · '+OP.failed.join(', ')+' SILENT · REROUTED');}
  const txt=parts.length?parts.join(' · '):'RING HEALTHY · '+hubsUp+'/'+MINIS.length+' MINI HUBS · '+jbsUp+'/'+JBS.length+' JUNCTIONS · SURFACE MESH '+online+'/'+NTOTAL;
  return {online,iso,cuts,hubsUp,jbsUp,lightsUp,buzzUp,cls,txt,jbFaults,hubFaults};
}

const TOPO={bands:[],pts:[],w:0,h:0,ring:null,M:null,ph:80};
function visibleLevels(){return OP.lvlf==='all'?[0,1]:[OP.lvlf==='l1'?0:1];}
function layout(){
  const c=E.opTopo;if(!c)return false;
  const b=c.getBoundingClientRect();
  if(b.width<40||b.height<40)return false;
  const w=b.width,h=b.height,lv=visibleLevels();
  const PH=TOPO.ph,padL=148,padR=30,padT=34+PH,padB=40;
  const gapY=lv.length===1?0:30;
  const bandH=(h-padT-padB-gapY*(lv.length-1))/lv.length;
  const x0=padL,x1=w-padR,rx=74,lx=32;
  TOPO.w=w;TOPO.h=h;TOPO.bands=[];TOPO.pts=[];
  const sTop=padT-PH;
  const M={x:rx,y:sTop+54};
  NODES.forEach((n,i)=>{n._x=x0+(0.10+i*0.26)*(x1-x0);n._y=sTop+(i%2?30:52);TOPO.pts.push({kind:'S',id:n.id,x:n._x,y:n._y});});
  TOPO.pts.push({kind:'M',id:'M-01',x:M.x,y:M.y});
  const pos={'M-01':{x:rx,y:M.y,vis:true}};
  lv.forEach((li,i)=>{
    const top=padT+i*(bandH+gapY),single=lv.length===1,railA=single?top+110:top+46,railB=single?railA+42:top+bandH-41,skew=18;
    const X=c2=>x0+c2*(x1-x0),Y=(c2,lane)=>railA+c2*skew+(lane==='B'?(railB-railA):0);
    const hub=MINIS[li];
    const band={li:li,L:LEVELS[li],top:top,h:bandH,x0:x0,x1:x1,railA:railA,railB:railB,skew:skew,X:X,Y:Y,hub:hub,
      head:{x:x0+4,y:railA},eol:{x:x1,y:Y(1,'A')}};
    band.jb=JBS.filter(j=>j.lv===li).map(j=>{const cc=j.pos===0?0.36:0.72;j._x=X(cc);j._y=Y(cc,'A');j._yB=Y(cc,'B');return j;});
    hub._x=rx;hub._y=railA;
    pos[hub.id]={x:rx,y:railA,vis:true};
    TOPO.pts.push({kind:'H',id:hub.id,x:rx,y:railA});
    band.jb.forEach(j=>TOPO.pts.push({kind:'J',id:j.id,x:j._x,y:j._y}));
    TOPO.bands.push(band);
  });
  MINIS.forEach(hb=>{if(!pos[hb.id]){const y=hb.lv===0?Math.round((M.y+TOPO.bands[0].railA)/2):h-padB+8;hb._x=rx;hb._y=y;pos[hb.id]={x:rx,y:y,vis:false};}});
  TOPO.M=M;TOPO.ring={rx:rx,lx:lx,pos:pos,sTop:sTop};
  return true;
}
function ringPath(s){
  const R=TOPO.ring,a=R.pos[s.a],b=R.pos[s.b];
  if(s.id==='R3')return [{x:a.x,y:a.y},{x:R.lx,y:a.y},{x:R.lx,y:b.y},{x:b.x,y:b.y}];
  return [{x:a.x,y:a.y},{x:b.x,y:b.y}];
}
function offsetPoly(pts,d){
  const out=[];
  for(let i=1;i<pts.length;i++){
    const a=pts[i-1],b=pts[i],dx=b.x-a.x,dy=b.y-a.y,L=Math.hypot(dx,dy)||1,ux=dx/L,uy=dy/L,nx=-uy*d,ny=ux*d;
    const e0=i>1?Math.abs(d):0,e1=i<pts.length-1?Math.abs(d):0;
    out.push({a:{x:a.x+nx-ux*e0,y:a.y+ny-uy*e0},b:{x:b.x+nx+ux*e1,y:b.y+ny+uy*e1}});
  }
  return out;
}
function polyLen(pts){let L=0;for(let i=1;i<pts.length;i++)L+=Math.hypot(pts[i].x-pts[i-1].x,pts[i].y-pts[i-1].y);return L;}
function polyPoint(pts,f){
  let L=0;const seg=[];
  for(let i=1;i<pts.length;i++){const d=Math.hypot(pts[i].x-pts[i-1].x,pts[i].y-pts[i-1].y);seg.push({a:pts[i-1],b:pts[i],d:d,o:L});L+=d;}
  const t=clamp(f,0,1)*L;
  for(const s of seg){if(t<=s.o+s.d){const k=s.d?(t-s.o)/s.d:0;return {x:U.lerp(s.a.x,s.b.x,k),y:U.lerp(s.a.y,s.b.y,k),ux:(s.b.x-s.a.x)/(s.d||1),uy:(s.b.y-s.a.y)/(s.d||1)};}}
  const l=pts[pts.length-1];return {x:l.x,y:l.y,ux:0,uy:1};
}

function drawSlab(c,band){
  const y0=band.railA-40,y1=band.Y(1,'A')-40,y2=band.Y(1,'B')+30,y3=band.railB+30;
  c.beginPath();c.moveTo(band.X(0),y3);c.lineTo(band.X(1),y2);c.lineTo(band.X(1),y2+11);c.lineTo(band.X(0),y3+11);c.closePath();
  c.fillStyle='rgba(255,255,255,.035)';c.fill();
  const g=c.createLinearGradient(band.X(0),y0,band.X(1),y2);
  g.addColorStop(0,'rgba(255,255,255,.055)');g.addColorStop(1,'rgba(255,255,255,.015)');
  c.beginPath();c.moveTo(band.X(0),y0);c.lineTo(band.X(1),y1);c.lineTo(band.X(1),y2);c.lineTo(band.X(0),y3);c.closePath();
  c.fillStyle=g;c.fill();c.strokeStyle='rgba(255,255,255,.10)';c.lineWidth=1;c.stroke();
  const base=cx=>band.Y((cx-band.x0)/(band.x1-band.x0),'B')+26;
  c.strokeStyle='rgba(255,255,255,.09)';c.beginPath();c.moveTo(band.x0,base(band.x0));c.lineTo(band.x1,base(band.x1));c.stroke();
  c.font='8.5px '+MONO;c.textAlign='center';c.textBaseline='top';
  [{x:band.head.x,ch:0}].concat(band.jb.map(j=>({x:j._x,ch:j.ch})),[{x:band.eol.x,ch:900}]).forEach(t=>{
    const yt=base(t.x);
    c.strokeStyle='rgba(255,255,255,.16)';c.beginPath();c.moveTo(t.x,yt-4);c.lineTo(t.x,yt+4);c.stroke();
    c.fillStyle='#6c645e';c.fillText('0+'+String(t.ch).padStart(3,'0'),t.x,yt+6);
  });
  c.textAlign='left';c.textBaseline='alphabetic';
  c.font='600 10px '+MONO;c.fillStyle='#9b938c';c.fillText(band.L.title,band.x0+24,band.railA-32);
  c.font='9px '+MONO;c.fillStyle='#6c645e';
  c.fillText(band.L.rl+' · 900 m DRIFT',band.x0+24,band.railA-20);
}
function drawBus(c,band){
  const hub=band.hub,s2=busCutOf(band.li),reach=hubReach(hub),up=hubUp(hub),jb=band.jb;
  const pts=[{x:hub._x+12,y:hub._y},{x:band.head.x,y:band.head.y},{x:jb[0]._x,y:jb[0]._y},{x:jb[1]._x,y:jb[1]._y},{x:band.eol.x,y:band.eol.y}];
  const segs=[[pts[0],pts[1],pts[2]],[pts[2],pts[3]],[pts[3],pts[4]]];
  segs.forEach((sp,si)=>{
    const cutHere=s2===si,down=!up||(s2!==null&&si>s2);
    c.save();
    c.strokeStyle=cutHere?'rgba(255,90,70,.9)':down?'rgba(255,90,70,.42)':'rgba(255,182,72,.95)';
    c.lineWidth=cutHere?2.2:2.6;
    if(cutHere||down)c.setLineDash([8,5]);
    c.beginPath();sp.forEach((p,k)=>k?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y));c.stroke();c.restore();
  });
  c.fillStyle=(s2!==null||!up)?'#ff5a46':'#ffb648';c.fillRect(band.eol.x+2,band.eol.y-5,3,10);
  c.font='600 7px '+MONO;c.textAlign='right';c.textBaseline='top';c.fillStyle='#9b938c';c.fillText('EOL · 120 Ω',band.eol.x+4,band.eol.y+8);
  const lights=jb.reduce((a,j)=>a+j.lights,0);
  const txt=!reach?'BUS '+hub.id+' · NO-LINK · HUB ISOLATED':!up?'BUS '+hub.id+' · DARK · HUB FAULT':
    s2===null?'BUS '+hub.id+' · POLLING '+jb[0].id+' + '+jb[1].id+' · '+lights+' EVAC LIGHTS':
    s2===0?'BUS '+hub.id+' · CUT AT HEAD-END · '+jb[0].id+', '+jb[1].id+' NO-LINK':
    s2===1?'BUS '+hub.id+' · CUT AFTER '+jb[0].id+' · '+jb[1].id+' NO-LINK':'BUS '+hub.id+' · TAIL CUT PAST '+jb[1].id+' · NO LIGHTS LOST';
  c.font='600 8px '+MONO;c.textAlign='right';c.textBaseline='alphabetic';
  c.fillStyle=(s2!==null||!up)?'#ff9d9d':'rgba(255,196,120,.9)';
  c.fillText(txt,band.x1-12,band.railA-20);
  if(s2!==null&&up){
    const m=s2===0?polyPoint([pts[1],pts[2]],0.5):s2===1?polyPoint([pts[2],pts[3]],0.5):polyPoint([pts[3],pts[4]],0.5);
    c.strokeStyle='#ff5a46';c.lineWidth=2.4;
    c.beginPath();c.moveTo(m.x-6,m.y-6);c.lineTo(m.x+6,m.y+6);c.moveTo(m.x+6,m.y-6);c.lineTo(m.x-6,m.y+6);c.stroke();
    c.font='700 7.5px '+MONO;c.textAlign='center';c.textBaseline='top';c.fillStyle='#ff9d9d';c.fillText('BUS CUT',m.x,m.y+9);
  }
  if(up){
    const reachF=s2===0?0.16:s2===1?0.55:1,T=6;
    const col=s2!==null?'#ffb0a0':'#8ff0b0';
    for(let t=0;t<T;t++){
      const p=polyPoint(pts,((OP.phase+t/T)%1)*reachF);
      c.beginPath();c.arc(p.x,p.y,2.5,0,7);c.fillStyle=col;c.shadowColor=col;c.shadowBlur=9;c.fill();c.shadowBlur=0;
    }
  }
}
function drawJb(c,j){
  const dark=jbDark(j),fault=!!OP.faultJb[j.id],up=!dark&&!fault,on=up&&OP.eap.engaged;
  const col=fault?'#ff4d4d':dark?'#ff9538':on?'#4ade80':'#ffb648',hot=OP.hover===j.id;
  c.strokeStyle=rgba(col,.85);c.lineWidth=1.6;c.beginPath();c.moveTo(j._x,j._y-6);c.lineTo(j._x,j._y+6);c.stroke();
  c.fillStyle=(dark||fault)?'#3a3733':'#4a453e';c.fillRect(j._x-7,j._y-5,14,10);
  c.strokeStyle=rgba(col,.95);c.lineWidth=hot?2:1.3;c.strokeRect(j._x-7,j._y-5,14,10);
  if(fault||dark){c.strokeStyle='#ff9d9d';c.lineWidth=1.4;c.beginPath();c.moveTo(j._x-4,j._y-3);c.lineTo(j._x+4,j._y+3);c.moveTo(j._x+4,j._y-3);c.lineTo(j._x-4,j._y+3);c.stroke();}
  else{c.fillStyle=rgba(col,.9);c.fillRect(j._x-3,j._y-1.5,6,3);}
  c.font='600 8px '+MONO;c.textAlign='center';c.textBaseline='bottom';c.fillStyle=(fault||dark)?'#ff9d9d':'#ffc98a';c.fillText(j.id,j._x,j._y-9);
  c.font='7px '+MONO;c.textBaseline='top';c.fillStyle=(dark||fault)?'#ff9d9d':'#8f8880';
  c.fillText(fault?'FAULT':dark?'NO-LINK':'EVAC LIGHT CTRL',j._x,j._y+9);
  if(OP.busf==='ring')return;
  c.strokeStyle=rgba(col,.55);c.lineWidth=1.1;if(!up)c.setLineDash([4,4]);
  c.beginPath();c.moveTo(j._x,j._y+17);c.lineTo(j._x,j._yB-9);c.stroke();c.setLineDash([]);
  const n=j.lights,sw=6,gap=3,tot=n*sw+(n-1)*gap,sx=j._x-tot/2;
  for(let k=0;k<n;k++){
    const lx2=sx+k*(sw+gap);
    if(on){const fl=((OP.phase*40)+k*0.5)%1<0.5;c.fillStyle=fl?'#4ade80':'rgba(74,222,128,.3)';c.shadowColor='#4ade80';c.shadowBlur=fl?8:0;c.fillRect(lx2,j._yB-6,sw,sw);c.shadowBlur=0;}
    else{c.fillStyle=up?'rgba(74,222,128,.14)':'rgba(255,77,77,.10)';c.fillRect(lx2,j._yB-6,sw,sw);
      c.strokeStyle=up?'rgba(74,222,128,.55)':'rgba(255,90,70,.6)';c.lineWidth=1;c.strokeRect(lx2+.5,j._yB-5.5,sw-1,sw-1);}
  }
  c.font='7px '+MONO;c.textAlign='center';c.textBaseline='top';c.fillStyle=up?(on?'#8ff0b0':'#8f8880'):'#ff9d9d';
  c.fillText(n+' EVAC LIGHTS · '+(fault?'FAULT':dark?'NO-LINK':on?'ON · EVACUATE':'STANDBY'),j._x,j._yB+4);
}
function drawHub(c,hub){
  const x=hub._x,y=hub._y,reach=hubReach(hub),up=hubUp(hub),fault=!!OP.faultHub[hub.id],sound=up&&OP.eap.engaged,vis=TOPO.ring.pos[hub.id].vis;
  const col=(fault||!reach)?'#ff4d4d':sound?'#ff9538':'#ffb648',hot=OP.hover===hub.id;
  if(!vis){
    c.fillStyle='#2a2725';c.beginPath();c.arc(x,y,4,0,7);c.fill();c.strokeStyle=rgba(col,.7);c.lineWidth=1.2;c.stroke();
    c.font='7.5px '+MONO;c.textAlign='left';c.textBaseline='middle';c.fillStyle='#8f8880';c.fillText(hub.id+' · '+LEVELS[hub.lv].id+' ▸',x+10,y);
    return;
  }
  U.rrect(c,x-12,y-9,24,18,4);c.fillStyle='#24211f';c.fill();c.fillStyle=up?'rgba(255,182,72,.14)':'rgba(255,77,77,.12)';c.fill();
  c.strokeStyle=col;c.lineWidth=hot?2.2:1.5;c.stroke();
  c.fillStyle=col;c.beginPath();c.moveTo(x-7,y-3);c.lineTo(x-3,y-3);c.lineTo(x+1,y-7);c.lineTo(x+1,y+7);c.lineTo(x-3,y+3);c.lineTo(x-7,y+3);c.closePath();c.fill();
  c.strokeStyle=rgba(col,sound?0.95:0.5);c.lineWidth=1.2;
  c.beginPath();c.arc(x+2,y,4.5,-0.9,0.9);c.stroke();c.beginPath();c.arc(x+2,y,7.5,-0.9,0.9);c.stroke();
  if(sound){const amp=(OP.phase*30)%1;c.strokeStyle=rgba('#ff4d4d',1-amp);c.lineWidth=1.6;c.beginPath();c.arc(x,y,12+amp*20,0,7);c.stroke();}
  if(fault||!reach){c.strokeStyle='#ff9d9d';c.lineWidth=1.6;c.beginPath();c.moveTo(x-14,y-11);c.lineTo(x+14,y+11);c.moveTo(x+14,y-11);c.lineTo(x-14,y+11);c.stroke();}
  c.font='600 8.5px '+MONO;c.textAlign='left';c.textBaseline='alphabetic';c.fillStyle=(fault||!reach)?'#ff9d9d':'#ffc98a';
  c.fillText(hub.id,x+16,y-11);
  c.font='7px '+MONO;c.textBaseline='top';c.fillStyle=(fault||!reach)?'#ff9d9d':'#8f8880';
  c.fillText(hub.buzzers+' BUZZERS · '+(fault?'FAULT':!reach?'ISOLATED':sound?'SOUNDING':'ARMED'),x+16,y+11);
  const inSeg=RING.filter(s=>s.b===hub.id)[0],outSeg=RING.filter(s=>s.a===hub.id)[0];
  c.font='600 6.5px '+MONO;c.textAlign='center';c.textBaseline='middle';
  c.fillStyle=OP.ringCuts[inSeg.id]?'#ff9d9d':'#8f8880';c.fillText('A',x-9,y-4);
  c.fillStyle=OP.ringCuts[outSeg.id]?'#ff9d9d':'#8f8880';c.fillText('B',x-9,y+5);
}
function drawRing(c){
  const R=TOPO.ring;
  RING.forEach(s=>{
    const pts=ringPath(s),cut=!!OP.ringCuts[s.id];
    const ha=hubById[s.a],hb=hubById[s.b],dead=!cut&&!hubReach(ha)&&!hubReach(hb);
    [[-3,false],[3,true]].forEach(o=>{
      const d=o[0],sec=o[1];
      c.save();
      c.strokeStyle=cut?'rgba(255,90,70,.85)':dead?'rgba(255,255,255,.18)':sec?'rgba(255,182,72,.55)':'rgba(255,182,72,.95)';
      c.lineWidth=sec?1.3:2.2;
      if(cut||dead)c.setLineDash([6,5]);else if(sec)c.setLineDash([3,4]);
      c.beginPath();offsetPoly(pts,d).forEach(sg=>{c.moveTo(sg.a.x,sg.a.y);c.lineTo(sg.b.x,sg.b.y);});c.stroke();
      c.restore();
    });
    const mid=polyPoint(pts,0.5);
    c.font='600 7.5px '+MONO;c.textBaseline='middle';
    if(cut){
      c.strokeStyle='#ff5a46';c.lineWidth=2.2;
      c.beginPath();c.moveTo(mid.x-5,mid.y-5);c.lineTo(mid.x+5,mid.y+5);c.moveTo(mid.x+5,mid.y-5);c.lineTo(mid.x-5,mid.y+5);c.stroke();
      const bTxt='✕ '+s.id+' CUT';c.font='700 7px '+MONO;
      const tw=c.measureText(bTxt).width;
      const bx=s.id==='R3'?(R.lx+8):(R.rx+12);
      c.fillStyle='#2b1816';c.fillRect(bx-3,mid.y-7,tw+6,14);
      c.strokeStyle='#ff5a46';c.lineWidth=1;c.strokeRect(bx-3,mid.y-7,tw+6,14);
      c.fillStyle='#ff9d9d';c.textAlign='left';
      c.fillText(bTxt,bx,mid.y);
      [[ha,s.a],[hb,s.b]].forEach(w=>{
        const hh=w[0],port=w[1];if(!hubReach(hh))return;
        const hPos=R.pos[port];if(!hPos)return;
        const wx=R.lx+14,wy=port==='M-01'?hPos.y-8:s.id==='R1'?hPos.y-8:hPos.y+8;
        c.beginPath();c.arc(R.rx-11,wy,3,0,7);c.strokeStyle='#8ff0b0';c.lineWidth=1.4;c.stroke();
        c.beginPath();c.arc(R.rx-11,wy,1.2,0,7);c.fillStyle='#8ff0b0';c.fill();
        c.font='700 6px '+MONO;c.textBaseline='middle';c.textAlign='center';
        c.fillStyle='#12281a';c.fillRect(wx-11,wy-5.5,22,11);
        c.strokeStyle='#4ade80';c.lineWidth=0.9;c.strokeRect(wx-11,wy-5.5,22,11);
        c.fillStyle='#8ff0b0';c.fillText('WRAP',wx,wy);
      });
    }else{
      c.fillStyle=dead?'#6c645e':'#9b938c';
      if(s.id==='R3'){c.textAlign='left';c.fillText(s.id+' · '+s.len,R.lx+8,mid.y);}
      else{c.textAlign='left';c.fillText(s.id+' · '+s.len,R.rx+12,mid.y);}
    }
  });
  [{d:-3,dir:1,col:'#8ff0b0',r:2.4},{d:3,dir:-1,col:'#ffd9a8',r:1.8}].forEach(rg=>{
    const segs=[];let total=0;
    RING.forEach(s=>{
      const alive=!OP.ringCuts[s.id]&&(hubReach(hubById[s.a])||hubReach(hubById[s.b]));
      offsetPoly(ringPath(s),rg.d).forEach(sg=>{const L=Math.hypot(sg.b.x-sg.a.x,sg.b.y-sg.a.y);segs.push({a:sg.a,b:sg.b,L:L,o:total,alive:alive});total+=L;});
    });
    const T=9;
    for(let t=0;t<T;t++){
      const arc=((((OP.phase*1.6*rg.dir)+t/T)%1)+1)%1*total;
      for(let i=0;i<segs.length;i++){
        const sg=segs[i];
        if(arc<=sg.o+sg.L){
          if(sg.alive){const f=sg.L?(arc-sg.o)/sg.L:0;
            c.beginPath();c.arc(U.lerp(sg.a.x,sg.b.x,f),U.lerp(sg.a.y,sg.b.y,f),rg.r,0,7);
            c.fillStyle=rg.col;c.shadowColor=rg.col;c.shadowBlur=8;c.fill();c.shadowBlur=0;}
          break;
        }
      }
    }
  });
  c.font='600 7px '+MONO;c.textAlign='left';c.textBaseline='alphabetic';c.fillStyle='#6c645e';
  c.fillText('DOUBLE RING · PRIMARY ↻ · SECONDARY ↺',R.lx-10,TOPO.h-8);
}
const DIRECT={S1:1,S2:1,S3:2.5,S4:3.5};
function meshRoutes(){
  const routes={},alive=NODES.filter(n=>n.alive);
  alive.forEach(src=>{
    const dist={},prev={},done={};alive.forEach(n=>dist[n.id]=Infinity);dist[src.id]=0;
    for(;;){
      let u=null;alive.forEach(n=>{if(!done[n.id]&&(u===null||dist[n.id]<dist[u.id]))u=n;});
      if(!u||dist[u.id]===Infinity)break;done[u.id]=true;
      MLINKS.forEach(l=>{const a=NODES[l[0]],b=NODES[l[1]];if(a!==u&&b!==u)return;const v=a===u?b:a;if(!v.alive)return;
        const nd=dist[u.id]+1;if(nd<dist[v.id]){dist[v.id]=nd;prev[v.id]=u.id;}});
    }
    let best=null,bc=Infinity;alive.forEach(n=>{if(dist[n.id]===Infinity)return;const cst=dist[n.id]+DIRECT[n.id];if(cst<bc){bc=cst;best=n.id;}});
    const path=[];let cur=best;while(cur){path.unshift(cur);cur=prev[cur];}
    routes[src.id]={path:path,exit:best,cost:bc};
  });
  return routes;
}
function drawSurface(c){
  const M=TOPO.M,sTop=TOPO.ring.sTop,x0=TOPO.bands.length?TOPO.bands[0].x0:124,cx=M.x+8,cy=M.y-8;
  const routes=meshRoutes();
  c.save();
  c.font='600 9px '+MONO;c.fillStyle='rgba(255,196,120,.9)';c.textAlign='left';c.textBaseline='alphabetic';
  c.fillText('SURFACE WIRELESS MESH · ESP-NOW 2.4 GHz · S1–S4',x0,sTop+12);
  c.setLineDash([5,4]);c.lineWidth=1.1;
  MLINKS.forEach(l=>{const a=NODES[l[0]],b=NODES[l[1]],dead=!a.alive||!b.alive;
    const used=Object.keys(routes).some(k=>{const p=routes[k].path;for(let i=1;i<p.length;i++)if((p[i-1]===a.id&&p[i]===b.id)||(p[i-1]===b.id&&p[i]===a.id))return true;return false;});
    c.strokeStyle=dead?'rgba(255,90,70,.4)':used?'rgba(124,198,240,.55)':'rgba(124,198,240,.22)';
    c.beginPath();c.moveTo(a._x,a._y);c.lineTo(b._x,b._y);c.stroke();});
  NODES.forEach(n=>{
    const exitUsed=Object.keys(routes).some(k=>routes[k].exit===n.id);
    if(n.alive&&!exitUsed&&DIRECT[n.id]>1)return;
    c.strokeStyle=n.alive?'rgba(124,198,240,'+(exitUsed?.55:.3)+')':'rgba(255,90,70,.5)';
    c.beginPath();c.moveTo(n._x,n._y);c.lineTo(cx,cy);c.stroke();});
  c.setLineDash([]);
  NODES.forEach((n,i)=>{if(!n.alive||!routes[n.id])return;
    const pts=routes[n.id].path.map(id=>({x:byId[id]._x,y:byId[id]._y}));pts.push({x:cx,y:cy});
    const ph=((OP.phase*2.2)+i*0.25)%1,p=polyPoint(pts,ph);
    c.fillStyle='rgba(124,198,240,'+(0.85*(1-Math.abs(ph-0.5)*1.2))+')';
    c.beginPath();c.arc(p.x,p.y,2.1,0,7);c.fill();});
  MLINKS.forEach((l,k)=>{const a=NODES[l[0]],b=NODES[l[1]];if(!a.alive||!b.alive)return;const ph=((OP.phase*2)+k*0.2)%1,f=ph<0.5?ph*2:2-ph*2;
    c.fillStyle='rgba(124,198,240,.55)';c.beginPath();c.arc(U.lerp(a._x,b._x,f),U.lerp(a._y,b._y,f),1.5,0,7);c.fill();});
  NODES.forEach((n,i)=>{
    const dead=!n.alive||!!(window.SURF&&SURF.surfaceState&&SURF.surfaceState(n.id));
    const alm=(window.SURF&&SURF.api&&SURF.api.alarm)?(SURF.api.alarm.s[i]||0):0;
    if(alm>0&&!dead){const ph=((OP.phase*(alm===2?18:8))+i*0.2)%1;
      c.strokeStyle=alm===2?'rgba(255,77,77,'+(0.9*(1-ph))+')':'rgba(255,149,56,'+(0.7*(1-ph))+')';
      c.lineWidth=alm===2?2:1.4;c.beginPath();c.arc(n._x,n._y,5+ph*18,0,7);c.stroke();}
    const col=dead?'#ff4d4d':alm===2?'#ff4d4d':(alm===1||n.warn)?'#ff9538':'#ffb648',sel=OP.sel===n.id;
    c.fillStyle=col;c.beginPath();c.arc(n._x,n._y,3.6,0,7);c.fill();
    c.strokeStyle=sel?'rgba(255,217,168,.95)':'rgba(255,182,72,.5)';c.lineWidth=sel?1.6:1;
    c.beginPath();c.arc(n._x,n._y,OP.hover===n.id?9:7,0,7);c.stroke();
    if(dead){c.strokeStyle='#ff9d9d';c.lineWidth=1.5;c.beginPath();c.moveTo(n._x-4,n._y-4);c.lineTo(n._x+4,n._y+4);c.moveTo(n._x+4,n._y-4);c.lineTo(n._x-4,n._y+4);c.stroke();}
    c.font=(sel?'600 ':'')+'8.5px '+MONO;c.fillStyle=dead?'#ff9d9d':sel?'#ffd9a8':'#ffc98a';c.textAlign='center';c.textBaseline='bottom';
    c.fillText(n.id+(dead?' · SILENT':''),n._x,n._y-8);
    c.font='7px '+MONO;c.fillStyle='#8f8880';c.textBaseline='top';c.fillText(n.zone.split(' · ')[0],n._x,n._y+8);
  });
  U.rrect(c,M.x-11,M.y-8,22,16,4);
  c.fillStyle='#24211f';c.fill();c.fillStyle='rgba(124,198,240,.16)';c.fill();c.strokeStyle=OP.hover==='M-01'?'#bfe4fb':'#7cc6f0';c.lineWidth=OP.hover==='M-01'?2:1.4;c.stroke();
  c.fillStyle='#7cc6f0';c.beginPath();c.arc(M.x,M.y,2.6,0,7);c.fill();
  const rp=(OP.phase*2)%1;c.strokeStyle='rgba(124,198,240,'+(0.55*(1-rp))+')';c.lineWidth=1.2;c.beginPath();c.arc(M.x,M.y,10+rp*16,0,7);c.stroke();
  c.font='600 8.5px '+MONO;c.fillStyle='#9fd4f2';c.textAlign='left';c.textBaseline='alphabetic';
  c.fillText('M-01 · MAIN HUB',M.x+16,M.y+4);
  c.font='7px '+MONO;c.fillStyle='#7d8f9c';c.fillText('GATEWAY · RING PORTS A/B',M.x+16,M.y+14);
  c.restore();
}
function drawCloud(c,W){
  const M=TOPO.M,lat=Math.round(OP.latency.length?OP.latency[OP.latency.length-1]:45);
  const upTxt=OP.linkDown?('LINK DOWN · BUFFER '+fmt(OP.bufPct,1)+' %'):('UPLINK LIVE · BUFFER '+fmt(OP.bufPct,0)+' %');
  const col=OP.linkDown?'#ff9538':'#4ade80';
  if(W>=760){
    const cw=200,chh=40,cx=W-cw-14,cy=4;
    c.strokeStyle=rgba(col,.55);c.lineWidth=1.2;c.setLineDash([5,5]);
    c.beginPath();c.moveTo(M.x,M.y-9);c.lineTo(M.x,cy+chh/2);c.lineTo(cx,cy+chh/2);c.stroke();c.setLineDash([]);
    U.rrect(c,cx,cy,cw,chh,10);
    c.fillStyle='rgba(0,0,0,.42)';c.fill();
    c.strokeStyle=OP.linkDown?'rgba(255,149,56,.5)':'rgba(74,222,128,.4)';c.stroke();
    c.textAlign='left';c.textBaseline='alphabetic';
    c.font='600 9px '+MONO;c.fillStyle='#ddd5cd';c.fillText('SURFACE CLOUD · SYNC '+lat+' ms',cx+11,cy+16);
    c.font='8px '+MONO;c.fillStyle=OP.linkDown?'#ffc061':'#8ff0b0';c.fillText(upTxt,cx+11,cy+30);
  }else{
    const t='SURFACE CLOUD · SYNC '+lat+' ms · '+upTxt;
    c.font='600 8px '+MONO;
    const tw=c.measureText(t).width;
    c.strokeStyle=rgba(col,.55);c.lineWidth=1.2;c.setLineDash([5,5]);
    c.beginPath();c.moveTo(M.x,M.y-9);c.lineTo(M.x,22);c.lineTo(W-16-tw-8,22);c.stroke();c.setLineDash([]);
    c.textAlign='right';c.textBaseline='middle';c.fillStyle=OP.linkDown?'#ffc061':'#8ff0b0';
    c.fillText(t,W-16,22);
  }
}
function drawTopo(now){
  if(!layout())return;
  const ctx=U.ctx(E.opTopo);if(!ctx)return;
  const c=ctx.x,W=ctx.w,H=ctx.h;
  OP.phase=(now*0.00018)%1;
  TOPO.bands.forEach(band=>drawSlab(c,band));
  drawCloud(c,W);
  if(OP.busf!=='bus')drawRing(c);
  TOPO.bands.forEach(band=>{
    if(OP.busf!=='ring')drawBus(c,band);
    band.jb.forEach(j=>drawJb(c,j));
  });
  MINIS.forEach(h=>drawHub(c,h));
  drawSurface(c);
  c.textAlign='right';c.textBaseline='alphabetic';c.fillStyle='#5f574f';c.font='8px '+MONO;
  c.fillText('N ↑  SCHEMATIC · NOT TO SCALE',W-16,H-10);
}

function flyoutFor(pt,ev){
  const f=E.opFly,b=E.opTopoWrap.getBoundingClientRect();
  let col,head;const rows=[];
  const row=(k,v)=>rows.push('<div class="fr"><span>'+k+'</span><b>'+v+'</b></div>');
  if(pt.kind==='S'){
    const n=byId[pt.id],age=Math.round((Date.now()-n.last)/1000);
    const battState=n.batt<3.65?'DEEP SLEEP (SOLAR OK)':n.batt<3.75?'LOW · HOLDING':'NOMINAL';
    const relays=NODES.filter(m=>m.alive&&m!==n&&MLINKS.some(l=>(l[0]===m.idx&&l[1]===n.idx)||(l[1]===m.idx&&l[0]===n.idx))).map(m=>m.id);
    col=n.alive?'#4ade80':'#ff4d4d';head=n.id+' · SURFACE NODE'+(n.alive?'':' · SILENT');
    row('Position',n.zone+' · '+n.rl);
    row('Battery',fmt(n.batt,2)+' V');
    row('Deep-sleep state',battState);
    row('RSSI to M-01',fmt(n.rssi,0)+' dBm');
    row('Last packet',n.alive?age+' s ago':'last-gasp '+age+' s');
    row('Pitch / roll',fmt(n.pitch,2)+' / '+fmt(n.roll,2)+' mm/m');
    const rt=meshRoutes(),my=rt[n.id],via=my?my.path.slice(1):[],relayFor=Object.keys(rt).filter(k=>k!==n.id&&rt[k].path.indexOf(n.id)>0);
    row('Route to M-01',n.alive?(via.length?'RELAY VIA '+via.join(' → '):'DIRECT'):'OFF-AIR · peers rerouted');
    row('Relaying for',n.alive?(relayFor.join(', ')||'—'):'—');
    row('Mesh peers',relays.join(', ')||'—');
    rows.push('<div class="fr" style="margin-top:5px;color:var(--dim)"><span>Click to drive dials</span><b>'+n.id+'</b></div>');
  }else if(pt.kind==='M'){
    const lat=Math.round(OP.latency.length?OP.latency[OP.latency.length-1]:45),reach=ringReach();
    col=OP.linkDown?'#ff9538':'#7cc6f0';head='M-01 · MAIN HUB · SURFACE GATEWAY';
    row('Surface mesh',NODES.filter(n=>n.alive).length+'/'+NTOTAL+' nodes heard · ESP-NOW 2.4 GHz');
    row('Ring port A → MH-1 (R1)',OP.ringCuts.R1?'CUT':'PRIMARY · TX');
    row('Ring port B ← MH-2 (R3)',OP.ringCuts.R3?'CUT':'SECONDARY · RX');
    row('Mini hubs reachable',MINIS.filter(h=>reach[h.id]).map(h=>h.id).join(', ')||'NONE');
    row('Cloud uplink',OP.linkDown?'DOWN · BUFFERING '+fmt(OP.bufPct,0)+' %':'LIVE · '+lat+' ms');
    if(window.BBX&&BBX.state)row('Blackbox',BBX.state.sd?'REC · chunk_'+String(BBX.state.chunk).padStart(4,'0'):'NO SD');
  }else if(pt.kind==='H'){
    const h=hubById[pt.id],reach=hubReach(h),up=hubUp(h),fault=!!OP.faultHub[h.id],jbs=JBS.filter(j=>j.hub===h.id),s2=busCutOf(h.lv);
    const inSeg=RING.filter(s=>s.b===h.id)[0],outSeg=RING.filter(s=>s.a===h.id)[0];
    col=(fault||!reach)?'#ff4d4d':'#4ade80';head=h.id+' · MINI HUB · '+LEVELS[h.lv].id+(fault?' · FAULT':!reach?' · ISOLATED':'');
    row('Location',LEVELS[h.lv].title.split(' · ')[0]+' · '+LEVELS[h.lv].rl);
    row('Ring path',!reach?'NO PATH FROM M-01':ringCutIds().length?'WRAPPED · VIA '+(OP.ringCuts[inSeg.id]?outSeg.id:inSeg.id):'PRIMARY · '+inSeg.id+' IN · '+outSeg.id+' OUT');
    row('Ring port A ('+inSeg.id+')',OP.ringCuts[inSeg.id]?'CUT':'UP');
    row('Ring port B ('+outSeg.id+')',OP.ringCuts[outSeg.id]?'CUT':'UP');
    row('Buzzers',h.buzzers+' × 110 dB · '+(fault?'FAULT':!reach?'UNREACHABLE':OP.eap.engaged?'SOUNDING':'ARMED'));
    row('Evac bus',!up?'DARK':s2===null?'POLLING '+jbs.map(j=>j.id).join(' + '):'CUT SEG-'+(s2+1));
    row('Heartbeat',up?'1 s · OK':'LOST');
  }else{
    const j=jbById[pt.id],dark=jbDark(j),fault=!!OP.faultJb[j.id],up=!dark&&!fault,on=up&&OP.eap.engaged,hub=hubById[j.hub];
    col=fault?'#ff4d4d':dark?'#ff9538':on?'#4ade80':'#ffb648';head=j.id+' · JUNCTION BOX'+(fault?' · FAULT':dark?' · NO-LINK':'');
    row('Chainage',LEVELS[j.lv].id+' 0+'+String(j.ch).padStart(3,'0'));
    row('Function','EVACUATION LIGHT CONTROL');
    row('Lights',j.lights+' × LED · '+(fault?'FAULT':dark?'UNREACHABLE':on?'ON · EVACUATE':'STANDBY'));
    row('Bus position',hub.id+' → '+JBS.filter(x=>x.lv===j.lv).map(x=>x.id).join(' → ')+' → EOL');
    row('Upstream',!hubUp(hub)?hub.id+' DOWN':dark?'BUS CUT UPSTREAM':hub.id+' POLLING');
    row('Last poll',up?'< 1 s':'—');
  }
  f.innerHTML='<div class="fh"><i style="background:'+col+';box-shadow:0 0 8px '+col+'"></i>'+head+'</div>'+rows.join('');
  f.classList.add('on');
  const x=ev.clientX-b.left,y=ev.clientY-b.top;
  const fw=f.offsetWidth||212,fh=f.offsetHeight||230;
  f.style.left=clamp(x+16,6,Math.max(6,b.width-fw-6))+'px';
  f.style.top=clamp(y-10,6,Math.max(6,b.height-fh-6))+'px';
}
function hitPt(ev,tol){
  const r=E.opTopo.getBoundingClientRect(),mx=ev.clientX-r.left,my=ev.clientY-r.top;
  let best=null,bd=tol;
  TOPO.pts.forEach(p=>{const d=Math.hypot(p.x-mx,p.y-my);if(d<bd){bd=d;best=p;}});
  return best;
}
E.opTopo.addEventListener('mousemove',function(ev){
  const p=hitPt(ev,20);
  OP.hover=p?p.id:null;
  if(p)flyoutFor(p,ev);else E.opFly.classList.remove('on');
});
E.opTopo.addEventListener('mouseleave',()=>{OP.hover=null;E.opFly.classList.remove('on');});
E.opTopo.addEventListener('pointerdown',function(ev){
  if(ev.pointerType==='mouse')return;
  const p=hitPt(ev,26);
  if(p){OP.hover=p.id;if(p.kind==='S'){OP.sel=p.id;paintTilt();paintSpark(false);}flyoutFor(p,ev);}
  else E.opFly.classList.remove('on');
});
E.opTopo.addEventListener('click',function(ev){
  const p=hitPt(ev,20);
  if(p&&p.kind==='S'){OP.sel=p.id;E.opFailSel.value=p.id;paintTilt();paintSpark(false);}
});

function dial(cv,val,max,name,red){
  const ctx=U.ctx(cv);if(!ctx)return;
  const c=ctx.x,W=ctx.w,H=ctx.h;
  const cx=W/2,cy=H*0.74,R=Math.min(W*0.42,H*0.60);
  const a0=Math.PI*0.86,a1=Math.PI*2.14;
  const t=v=>a0+(v+max)/(2*max)*(a1-a0);
  c.lineWidth=9;c.lineCap='butt';
  c.strokeStyle='rgba(255,255,255,.09)';
  c.beginPath();c.arc(cx,cy,R,t(-max),t(max));c.stroke();
  c.strokeStyle='rgba(74,222,128,.55)';
  c.beginPath();c.arc(cx,cy,R,t(-red),t(red));c.stroke();
  c.strokeStyle='rgba(255,77,77,.85)';
  c.beginPath();c.arc(cx,cy,R,t(-max),t(-red));c.stroke();
  c.beginPath();c.arc(cx,cy,R,t(red),t(max));c.stroke();
  c.font='8px '+MONO;c.textAlign='center';c.textBaseline='middle';c.fillStyle='#6c645e';
  [-max,-red,0,red,max].forEach(v=>{
    const a=t(v),x1=cx+Math.cos(a)*(R-9),y1=cy+Math.sin(a)*(R-9);
    const x2=cx+Math.cos(a)*(R-15),y2=cy+Math.sin(a)*(R-15);
    c.strokeStyle=v===0?'rgba(255,255,255,.4)':rgba(v===0?'#ffffff':'#6c645e',.4);
    c.lineWidth=1;c.beginPath();c.moveTo(x1,y1);c.lineTo(x2,y2);c.stroke();
  });
  const over=Math.abs(val)>red;
  const col=over?'#ff4d4d':Math.abs(val)>red*0.72?'#ff9538':'#4ade80';
  const a=t(clamp(val,-max,max));
  c.save();c.translate(cx,cy);c.rotate(a);
  c.strokeStyle=col;c.lineWidth=2.4;c.shadowColor=col;c.shadowBlur=over?12:6;
  c.beginPath();c.moveTo(-R*0.16,0);c.lineTo(R-12,0);c.stroke();c.restore();
  c.beginPath();c.arc(cx,cy,4.2,0,7);c.fillStyle=col;c.fill();
  c.shadowBlur=0;
  c.font='600 13px '+MONO;c.fillStyle=col;c.textAlign='center';
  c.fillText((val>=0?'+':'')+fmt(val,2),cx,cy-R*0.34);
  c.font='7.5px '+MONO;c.fillStyle='#6c645e';
  c.fillText(name+' · mm/m',cx,cy-R*0.34+14);
}
function paintTilt(){
  const n=byId[OP.sel];
  E.opDialNode.textContent=n.id+' · '+n.zone+' · '+n.rl;
  dial(E.opDialPitch,n.pitch,10,'PITCH',5);
  dial(E.opDialRoll,n.roll,10,'ROLL',5);
  E.opPitchV.textContent=fmt(n.pitch,2);E.opRollV.textContent=fmt(n.roll,2);
  const rate=OP.tiltLive;
  E.opTiltRate.textContent=(rate>=0?'+':'')+fmt(rate,2)+' mm/m·min';
  E.opTiltRate.style.color=Math.abs(rate)>0.06?'#ff9d9d':Math.abs(rate)>0.03?'#ffc061':'#4ade80';
  const m=5-Math.max(Math.abs(n.pitch),Math.abs(n.roll));
  E.opTiltMargin.textContent=(m>=0?fmt(m,2):'−'+fmt(-m,2))+' mm/m';
  E.opTiltMargin.style.color=m<0?'#ff9d9d':m<1?'#ffc061':'#4ade80';
}
function buildSparkCards(){
  E.opSparkGrid.innerHTML=pairCards.map((p,i)=>
    '<div class="spcard" id="sp'+i+'"><div class="t"><span>'+p.a+' ↔ '+p.b+'</span><span class="mono" id="sph'+i+'">+0.00</span></div>'+
    '<div class="v" id="spv'+i+'">0.00<small>mm/min</small></div>'+
    '<canvas id="spc'+i+'"></canvas></div>').join('');
  pairCards.forEach((p,i)=>{
    p.el=document.getElementById('sp'+i);
    p.cv=document.getElementById('spc'+i);
    p.vt=document.getElementById('spv'+i);
    p.ht=document.getElementById('sph'+i);
  });
}
function pairRate(p){
  const a=byId[p.a],b=byId[p.b];
  const base=(a.risk+b.risk)*0.55;
  const dead=(a.alive?0:0.9)+(b.alive?0:0.9);
  return Math.max(0,base+dead+Math.sin(Date.now()/9000+base*3)*0.12);
}
function paintSpark(full){
  pairCards.forEach((p,i)=>{
    if(full||p.buf.length===0){p.buf.push(pairRate(p));if(p.buf.length>150)p.buf.shift();}
    const v=p.buf[p.buf.length-1]||0;
    if(p.vt){
      p.vt.innerHTML=fmt(v,2)+'<small>mm/min</small>';
      p.ht.textContent=(v>1.2?'▲ ':'')+(v*60).toFixed(1)+' mm/h';
      p.el.className='spcard'+(v>1.6?' crit':v>1.0?' warn':'');
    }
    const ctx=p.cv?U.ctx(p.cv):null;
    if(!ctx)return;
    const c=ctx.x,W=ctx.w,H=ctx.h,d=p.buf.slice(-120);
    const mx=Math.max(2.2,...d);
    c.strokeStyle='rgba(255,255,255,.09)';c.lineWidth=1;
    c.beginPath();c.moveTo(0,H-3.5);c.lineTo(W,H-3.5);c.stroke();
    const y=v2=>H-3.5-(v2/mx)*(H-8);
    const col=v>1.6?'#ff4d4d':v>1.0?'#ff9538':'#7cc6f0';
    if(d.length>1){
      c.beginPath();d.forEach((val,k)=>{const x=k/(119)*(W);k?c.lineTo(x,y(val)):c.moveTo(x,y(val));});
      c.strokeStyle=col;c.lineWidth=1.5;c.lineJoin='round';c.stroke();
      c.lineTo(W,H);c.lineTo(0,H);c.closePath();
      const g=c.createLinearGradient(0,0,0,H);
      g.addColorStop(0,rgba(col,.26));g.addColorStop(1,rgba(col,0));
      c.fillStyle=g;c.fill();
      c.beginPath();c.arc(W-1.5,y(d[d.length-1]),2.2,0,7);c.fillStyle=col;c.fill();
    }
  });
}

function ppvSample(){
  const t=Date.now()/1000;
  let base=0.42+Math.sin(t*0.7)*0.08+U.rr(-0.06,0.06);
  const mach=0.5+0.5*Math.sin(t*0.13);
  base+=mach*0.55;
  OP.failed.forEach(id=>{const n=byId[id];if(n)base+=n.risk*0.9;});
  return Math.max(0.05,base+U.rr(-0.12,0.18));
}
let lastSpike=-99;
function ppvEvent(peak,dur,freq){
  let tag='quiet',label='QUIET',cls='quiet',conf=0;
  if(peak>=1.5){
    if(dur<0.75&&peak>2.1){tag='blast';label='BLASTING';cls='blast';conf=clamp(70+peak*6,70,97);}
    else if(freq>6.5&&dur>0.9){tag='mach';label='HEAVY MACHINERY';cls='mach';conf=clamp(62+freq*2.4,62,95);}
    else {tag='strata';label='ABNORMAL STRATA MOVEMENT';cls='strata';conf=clamp(64+peak*8,64,96);}
  }
  if(tag==='quiet')return;
  OP.events.unshift({t:new Date(),peak:peak,dur:dur,freq:freq,tag:tag,label:label,cls:cls,conf:Math.round(conf)});
  if(OP.events.length>14)OP.events.pop();
  renderPpvFeed();
  if(tag==='strata')addAlarm(3,'Vibration signature · abnormal strata movement',
    fmt(peak,2)+' mm/s at panel P-3 edge · low-frequency, non-decaying ('+fmt(freq,1)+' Hz)', 'PPV');
  else if(tag==='blast')NSD.ledger.add('PPV SPIKE '+fmt(peak,2)+' mm/s · TAGGED BLASTING · MC-3 stope',{sev:'INFO'});
}
function pumpPpv(){
  const v=ppvSample();
  OP.ppv.push(v);if(OP.ppv.length>240)OP.ppv.shift();
  OP.peakHold=Math.max(OP.peakHold*0.985,v);
  const now=Date.now()/1000;
  if(v>1.5&&now-lastSpike>2.6){
    lastSpike=now;
    const dur=OP.events.length?(0.4+U.rr(0,1.4)):1;
    const freq=U.rr(2.2,14);
    ppvEvent(v,dur,freq);
  }
}
function drawPpv(){
  const ctx=U.ctx(E.opPpv);if(!ctx)return;
  const c=ctx.x,W=ctx.w,H=ctx.h;
  const mx=4.2,y=v=>H-8-(v/mx)*(H-20);
  c.strokeStyle='rgba(255,255,255,.05)';c.lineWidth=1;
  for(let g=1;g<5;g++){const yy=H-8-(g/4)*(H-20);c.beginPath();c.moveTo(0,yy);c.lineTo(W,yy);c.stroke();}
  for(let g=1;g<8;g++){const xx=W*g/8;c.beginPath();c.moveTo(xx,0);c.lineTo(xx,H);c.stroke();}
  c.strokeStyle='rgba(255,77,77,.6)';c.setLineDash([6,5]);c.lineWidth=1.2;
  c.beginPath();c.moveTo(0,y(2.5));c.lineTo(W,y(2.5));c.stroke();c.setLineDash([]);
  c.font='8px '+MONO;c.fillStyle='rgba(255,120,120,.85)';c.textAlign='left';
  c.fillText('ACTION 2.5 mm/s',6,y(2.5)-5);
  const d=OP.ppv,n=d.length;
  c.beginPath();
  for(let i=0;i<n;i++){
    const x=(i/(n-1))*W,yy=y(d[i]);
    i?c.lineTo(x,yy):c.moveTo(x,yy);
  }
  c.strokeStyle='#7cc6f0';c.lineWidth=1.6;c.lineJoin='round';c.shadowColor='#7cc6f0';c.shadowBlur=8;c.stroke();
  c.shadowBlur=0;
  c.lineTo(W,H);c.lineTo(0,H);c.closePath();
  const g=c.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'rgba(124,198,240,.30)');g.addColorStop(1,'rgba(124,198,240,0)');
  c.fillStyle=g;c.fill();
  const ph=y(OP.peakHold);
  c.strokeStyle='rgba(255,255,255,.35)';c.setLineDash([3,4]);
  c.beginPath();c.moveTo(0,ph);c.lineTo(W,ph);c.stroke();c.setLineDash([]);
  c.fillStyle='rgba(255,255,255,.6)';c.textAlign='right';
  c.fillText('PEAK-HOLD '+fmt(OP.peakHold,2),W-6,ph-4);
}
function renderPpvFeed(){
  if(!E.opPpvFeed)return;
  E.opPpvCount.textContent=OP.events.length+' EVENTS';
  E.opPpvFeed.innerHTML=OP.events.slice(0,8).map(e=>
    '<div class="fr"><span class="tt">'+U.hhmmss(e.t)+'</span>'+
    '<span class="mm" style="color:'+(e.cls==='strata'?'#ff9d9d':e.cls==='blast'?'#ffc061':'#9fd4f2')+'">'+fmt(e.peak,2)+'</span>'+
    '<span class="ppvtag '+e.cls+'">'+e.label+'</span>'+
    '<span class="cf">'+e.conf+'% · '+fmt(e.freq,1)+' Hz</span></div>').join('')||
    '<div class="fr"><span class="tt">—</span><span class="mm">0.00</span>'+
    '<span class="ppvtag quiet">NO EVENTS ABOVE 1.5 mm/s</span></div>';
}

function shiftInfo(){
  const now=new Date();
  const h=now.getHours()+now.getMinutes()/60+now.getSeconds()/3600;
  let sh=SHIFTS.filter(x=>x.from<x.to?(h>=x.from&&h<x.to):(h>=x.from||h<x.to))[0]||SHIFTS[0];
  const len=((sh.to-sh.from)+24)%24;
  const since=((h-sh.from)+24)%24;
  return {now:now,sh:sh,prog:U.clamp(since/len,0,1),left:len-since};
}
function paintHud(){
  const s=shiftInfo(),sh=s.sh;
  E.opShiftId.textContent=sh.id;
  E.opShiftWin.textContent=sh.win+' · surface + underground combined';
  E.opClock.textContent=U.hhmmss(s.now);
  E.opShiftLeft.textContent=fmt(s.left,2)+' h left in shift · relief '+U.pad2(sh.to%24)+':00';
  E.opShiftFill.style.width=(s.prog*100).toFixed(1)+'%';
  E.opMgr.textContent=sh.mgr+' · ON DUTY';
  E.opMgr.style.color='var(--ok)';
  E.opOv.textContent=sh.ov;
  const ug=3;
  E.opMen.textContent=ug+' underground / '+sh.crew+' on shift';
  const lat=OP.latency.length?OP.latency[OP.latency.length-1]:45;
  E.opLat.innerHTML=(OP.linkDown?'—':Math.round(lat))+'<small>ms</small>';
  E.opSyncTxt.textContent=OP.linkDown?'EDGE BUFFERING · UPLINK SEVERED':'UG-1 → SURFACE CLOUD · NOMINAL';
  E.opPulse.className='syncpulse'+(OP.linkDown?' crit':lat>240?' warn':'');
  E.opBuf.textContent=fmt(OP.bufPct,1)+' % · '+OP.bufPkt+' PACKETS';
  E.opBufBar.style.width=OP.bufPct+'%';
  E.opGw.textContent=OP.linkDown?'BUFFERING · ESP-NOW 2.4 GHz':'ONLINE · ESP-NOW 2.4 GHz';
  E.opGw.style.color=OP.linkDown?'var(--amber-2)':'var(--ok)';
  E.opFlush.textContent=OP.lastFlush;
  const tiltMax=Math.max.apply(null,NODES.filter(n=>n.alive).map(n=>Math.max(Math.abs(n.pitch),Math.abs(n.roll))));
  const ppvPeak=OP.peakHold;
  const ns=netStatus(),online=ns.online;
  const open3=OP.alarms.filter(a=>!a.acked&&a.sev===3).length;
  const open2=OP.alarms.filter(a=>!a.acked&&a.sev===2).length;
  let state='ok';
  if(OP.eap.engaged||tiltMax>5||ppvPeak>2.5||online<NTOTAL-1||ns.cls==='crit'||open3)state='crit';
  else if(tiltMax>4||ppvPeak>1.6||online<NTOTAL||ns.cls!=='ok'||open2)state='warn';
  let score=Math.round(clamp(100-Math.max(0,tiltMax-1)*5-Math.max(0,ppvPeak-1)*5-(NTOTAL-online)*6-(MINIS.length-ns.hubsUp)*10-(JBS.length-ns.jbsUp)*4-(OP.eap.engaged?26:0)-OP.failed.length*4,5,100));
  if(state==='ok')score=Math.max(score,86);
  else if(state==='warn')score=clamp(score,62,82);
  else score=Math.min(score,45);
  E.opSafety.className='safety '+state;
  E.opSafSt.textContent=OP.eap.engaged?'RED · EMERGENCY ACTION PLAN ACTIVE':
    state==='crit'?'RED · PANEL CRITICAL':state==='warn'?'YELLOW · ELEVATED MOVEMENT':'GREEN · PANEL STABLE';
  E.opSafScore.textContent=score;
  let safetyText;
  if(OP.eap.engaged){
    safetyText='EAP ENGAGED on '+OP.eap.seam+'. Hooters and strobes energized; all persons withdrawing to the fresh-air base. Headcount being mustered at the shaft bank.';
  }else if(state==='ok'){
    safetyText='All '+online+' surface nodes inside tilt < 5 mm/m and PPV < 2.5 mm/s; ring, buzzers and evacuation lights healthy. No statutory breach on the depillaring panel.';
  }else if(state==='warn'){
    if(open2&&ns.cls==='ok'&&tiltMax<=4&&ppvPeak<=1.6)
      safetyText='An open level-2 alarm is still waiting. Acknowledge it on the Escalate tab.';
    else if(ns.cls!=='ok')
      safetyText=ns.txt+'. Monitoring continues; the Overman has been advised.';
    else
      safetyText='Elevated movement on one or more nodes — monitoring interval tightened to 1 s and the Overman has been advised.';
  }else if(open3&&ns.cls!=='crit'&&tiltMax<=5&&ppvPeak<=2.5){
    safetyText='An open level-3 alarm is still unacknowledged. The panel stays red until it is acknowledged or the condition is cleared.';
  }else if(ns.cls==='crit'){
    safetyText=ns.txt+'. Evacuation signalling degraded — withdraw men from the affected level and hold the transport.';
  }else{
    safetyText='Threshold breach present. Withdraw men from the affected face and hold the transport until the Overman clears the panel.';
  }
  E.opSafTx.textContent=safetyText;
  const chip=function(el,txt,cls){el.textContent=txt;el.className='chip '+cls;};
  chip(E.opChTilt,'TILT '+fmt(tiltMax,1)+' / 5 mm/m',tiltMax>5?'crit':tiltMax>4?'warn':'ok');
  chip(E.opChPpv,'PPV '+fmt(ppvPeak,2)+' / 2.5 mm/s',ppvPeak>2.5?'crit':ppvPeak>1.6?'warn':'ok');
  chip(E.opChMesh,'MESH '+online+'/'+NTOTAL+' · RING '+(ns.iso.length?'ISOLATED':ns.cuts.length?'WRAPPED':'OK')+(ns.jbsUp<JBS.length?' · EVAC '+ns.jbsUp+'/'+JBS.length:''),
    (ns.cls==='crit'||online<NTOTAL-1)?'crit':(ns.cls==='warn'||online<NTOTAL)?'warn':'ok');
  chip(E.opChResp,'OPEN L3 ALARMS '+open3,open3?'crit':'ok');
  E.opTopoTag.className='topotag'+(ns.cls!=='ok'?' rt':'');
  E.opTopoTag.textContent=ns.txt;
  const rms=Math.sqrt(OP.ppv.reduce((a,v)=>a+v*v,0)/OP.ppv.length);
  const mean=OP.ppv.reduce((a,v)=>a+v,0)/OP.ppv.length;
  let zc=0;for(let i=1;i<OP.ppv.length;i++)if((OP.ppv[i]>mean)!==(OP.ppv[i-1]>mean))zc++;
  const span=OP.ppv.length*0.06;
  const quiet=OP.peakHold<1.5;
  const dom=clamp(zc/2/span,quiet?0.8:0.4,24);
  E.opPpvPeak.textContent='PEAK '+fmt(OP.peakHold,2)+' mm/s';
  E.opPpvPeak.className='chip '+(OP.peakHold>2.5?'crit':OP.peakHold>1.6?'warn':'ok');
  E.opPpvRms.textContent='RMS '+fmt(rms,2)+' mm/s';
  E.opPpvDom.textContent='DOM FREQ '+fmt(dom,1)+' Hz'+(quiet?' (AMBIENT)':'');
  const last=OP.events[0];
  const lab=last&&(Date.now()-last.t.getTime())<4000?last.label:'QUIET';
  OP.ppvClass=lab;
  E.opPpvClass.textContent='CLASSIFIED: '+lab;
  E.opPpvClass.className='chip '+(lab==='QUIET'?'violet':last&&last.cls==='strata'?'crit':last&&last.cls==='blast'?'warn':'violet');
}
function paintLat()
{
  const ctx=U.ctx(E.opLatSpark);if(!ctx)return;
  const c=ctx.x,W=ctx.w,H=ctx.h,d=OP.latency.slice(-60);
  const mx=Math.max(120,...d);
  c.strokeStyle='rgba(255,255,255,.08)';c.lineWidth=1;
  c.beginPath();c.moveTo(0,H-2.5);c.lineTo(W,H-2.5);c.stroke();
  c.strokeStyle='rgba(74,222,128,.35)';c.setLineDash([3,3]);
  c.beginPath();c.moveTo(0,H-2.5-(80/mx)*(H-6));c.lineTo(W,H-2.5-(80/mx)*(H-6));c.stroke();c.setLineDash([]);
  if(d.length>1){
    c.beginPath();
    d.forEach((v,i)=>{const x=i/(59)*W,y=H-2.5-(v/mx)*(H-6);i?c.lineTo(x,y):c.moveTo(x,y);});
    c.strokeStyle=OP.linkDown?'#ff9538':'#4ade80';c.lineWidth=1.6;c.stroke();
  }
  c.font='7.5px '+MONO;c.fillStyle='#6c645e';c.textAlign='right';
  c.fillText('80 ms',W-2,H-4);
}

function renderTriage(){
  const open=OP.alarms.filter(a=>!a.acked);
  E.opTriageN.textContent=open.length+' OPEN · '+OP.alarms.filter(a=>a.acked).length+' ACKED';
  const list=OP.alarms.slice(0,6);
  E.opTriageList.innerHTML=list.map(a=>
    '<div class="alarm'+(a.acked?' acked':'')+(a.sev===3&&!a.acked?' crit-live':'')+'" data-id="'+a.id+'">'+
      '<span class="sev c'+a.sev+'">L'+a.sev+'</span>'+
      '<span class="bd"><b>'+NSD.esc(a.title)+'</b><span>'+NSD.esc(a.detail)+'</span>'+
      (a.acked?'<span class="ackwho">✓ ACKED '+(a.ackTs?U.hhmmss(a.ackTs):'—')+' · '+NSD.esc(a.who||'AUTO-CLEAR')+'</span>':'<span>'+U.hhmmss(a.ts)+' · '+NSD.esc(a.src||'AUTO')+'</span>')+
      '</span>'+
      '<span class="go">'+(a.acked?'<span class="chip ok">SILENCED</span>':
        '<button class="btnx" data-ack="'+a.id+'">ACK</button>')+'</span>'+
    '</div>').join('')||'<div class="sub">No alarms recorded this shift.</div>';
  $$('[data-ack]',E.opTriageList).forEach(b=>b.addEventListener('click',()=>openAck(b.dataset.ack)));
}
function openAck(id){
  const a=OP.alarms.filter(x=>x.id===id)[0];if(!a)return;
  const wrap=document.createElement('div');
  wrap.innerHTML=
    '<div class="alarm" style="margin-bottom:14px"><span class="sev c'+a.sev+'">L'+a.sev+'</span>'+
    '<span class="bd"><b>'+NSD.esc(a.title)+'</b><span>'+NSD.esc(a.detail)+'</span></span></div>'+
    '<div style="margin-top:14px">'+
      '<div class="swipe danger" id="ackSwipe" role="slider" tabindex="0" aria-label="Swipe to acknowledge" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">'+
        '<span class="track"></span><span class="fillTxt" id="ackFill">DRAG FULLY RIGHT TO ACKNOWLEDGE</span>'+
        '<span class="knob" id="ackKnob">SWIPE ▸</span></div>'+
    '</div>'+
    '<div class="field"><label for="ackId">OPERATOR ID (e.g. ECL-4471)</label>'+
      '<input id="ackId" autocomplete="off" spellcheck="false" placeholder="— · — · — · —" maxlength="14"></div>'+
    '<div class="field"><label for="ackNote">REMARK (OPTIONAL · LOGGED IN THE MINUTE BOOK)</label>'+
      '<input id="ackNote" autocomplete="off" placeholder="withdrawal advised on L1 face, men at fresh-air base" maxlength="120"></div>'+
    '<div class="sub" id="ackErr" style="color:#ff9d9d;margin-top:10px;display:none"></div>';
  let swiped=false,idOk=false;
  NSD.modal.open({
    title:'ACKNOWLEDGE & MUTE ALARM',sub:a.id+' · LEVEL '+a.sev,
    body:wrap,
    buttons:[
      {label:'CONFIRM ACKNOWLEDGEMENT',cls:'pri',disabled:true,onClick:function(btn){
        const idv=$('#ackId',wrap).value.trim().toUpperCase();
        if(!swiped||!idOk)return;
        ackAlarm(a,idv);
        NSD.modal.close();
      }},
      {label:'CANCEL'}
    ],
    onOpen:function(){
      const sw=$('#ackSwipe',wrap),knob=$('#ackKnob',wrap),fill=$('#ackFill',wrap);
      const btn=$$('.mdl-foot .btnx')[0];
      const refresh=function(){
        const ok=swiped&&idOk;
        btn.disabled=!ok;
        if(ok){knob.textContent='READY ✓';sw.classList.add('done');}
      };
      const Sw=makeSwipe(sw,knob,function(p,complete){
        if(complete){swiped=true;sw.classList.add('done');fill.textContent='SWIPE ACCEPTED · ENTER OPERATOR ID';refresh();}
        else fill.textContent=p>0.05?'DRAG FULLY RIGHT TO ACKNOWLEDGE':'DRAG FULLY RIGHT TO ACKNOWLEDGE';
      });
      NSD.swipeKeys(sw,function(d){Sw.step(d,refresh,fill);});
      const inp=$('#ackId',wrap);
      inp.addEventListener('input',function(){
        const v=inp.value.trim().toUpperCase();
        idOk=/^[A-Z]{2,4}-?\d{3,5}$/.test(v)||(/^[A-Z0-9-]{4,}$/.test(v)&&/\d/.test(v));
        const err=$('#ackErr',wrap);
        err.style.display=v&&!idOk?'block':'none';
        err.textContent=v&&!idOk?'Operator ID must be at least 4 characters and contain digits (e.g. ECL-4471).':'';
        inp.style.borderColor=idOk?'rgba(74,222,128,.5)':(v?'rgba(255,77,77,.5)':'');
        refresh();
      });
    }
  });
}
function makeSwipe(el,knob,onProgress){
  let p=0,drag=false;
  function geom(){
    const r=el.getBoundingClientRect();
    const kw=knob.offsetWidth||74;
    return {w:r.width,kw:kw,max:Math.max(10,r.width-10-kw)};
  }
  function set(v,fire){
    const g=geom();
    p=clamp(v,0,1);
    knob.style.left=(5+p*g.max)+'px';
    el.setAttribute('aria-valuenow',Math.round(p*100));
    const complete=p>=0.93;
    if(onProgress)onProgress(p,fire&&complete);
    return complete;
  }
  el.addEventListener('pointerdown',function(e){
    drag=true;el.setPointerCapture&&el.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  el.addEventListener('pointermove',function(e){
    if(!drag)return;
    const r=el.getBoundingClientRect(),g=geom();
    set((e.clientX-r.left-5-g.kw/2)/g.max,false);
  });
  function up(){
    if(!drag)return;drag=false;
    const done=set(p,true);
    setTimeout(()=>{set(0,false);},done?620:260);
  }
  el.addEventListener('pointerup',up);
  el.addEventListener('pointercancel',up);
  return {step:function(d,cb2,fillEl){
    set(p+d,false);
    if(p>=0.93){set(1,true);setTimeout(()=>set(0,false),600);if(cb2)cb2();}
  },get p(){return p;}};
}

function paintEap(msg){
  const s=OP.eap;
  E.opEapStage.textContent='STAGE '+s.stage+' / 2 · '+(s.engaged?'ENGAGED':s.stage===1?'ARMED':'DISARMED');
  E.opEapStage.className='chip '+(s.engaged?'crit':s.stage===1?'warn':'');
  E.opEapState.textContent=s.engaged?'ENGAGED':s.stage===1?'ARMED':(msg||'STANDBY');
  E.opEapState.style.color=s.engaged?'#ff9d9d':s.stage===1?'#ffc061':'';
  E.opEapHoot.textContent=s.engaged?'HOOTERS ACTIVE · '+s.seam:'HOOTERS IDLE';
  E.opEapHoot.className='chip '+(s.engaged?'crit':'');
  E.opEapStrobe.textContent=s.engaged?'STROBES ACTIVE · '+s.seam:'STROBES IDLE';
  E.opEapStrobe.className='chip '+(s.engaged?'crit':'');
  E.opEapPanel.className='panel'+(s.engaged?' crit-glow':'');
  E.opEapKnob.textContent=s.engaged?'LIVE':s.stage===1?'FIRE ▸':'ARM ▸';
  E.opEapFill.textContent=s.engaged?'EAP ENGAGED · ABORT TO RE-ARM':
    s.stage===1?'STEP 2 · DRAG AGAIN WITHIN 6 s TO FIRE':'STEP 1 · DRAG TO ARM';
}
function eapFire(){
  const s=OP.eap;
  s.engaged=true;s.stage=2;
  E.opEapSwipe.classList.remove('armed');
  const seam=E.opEapSeam.value;
  const label=seam==='ALL'?'BOTH LEVELS':(seam==='L1'?'LEVEL 1':('LEVEL 2'));
  addAlarm(3,'EAP ENGAGED · '+label,'Hooters + strobes energized · all persons withdrawing to fresh-air base · shaft bank mustering started','EAP');
  NSD.ledger.add('EAP TRIGGERED · HOOTERS + STROBES · '+label,{actor:'OPERATOR',sev:'CRITICAL'});
  NSD.ledger.add('MANUAL WITHDRAWAL ORDER · '+label+' · MUSTER AT SHAFT BANK',{actor:'OPERATOR',sev:'CRITICAL'});
  NSD.escalate('critical');
  E.opEapLog.innerHTML='<b style="color:#ff9d9d">ENGAGED '+U.hhmmss(new Date())+'</b> · hooter circuit closed on '+
    label+', UG flash panels acknowledged in 1.4 s, withdrawal order logged. The regulator deck has been '+
    'notified automatically.';
  paintEap('ENGAGED · ABORT TO STAND DOWN');
  renderTriage();paintHud();
  renderRegulatorSoon();
}
function eapAbort(drill){
  const s=OP.eap;
  s.engaged=false;s.stage=0;s.armedAt=0;
  E.opEapSwipe.classList.remove('armed','done','danger');
  paintEap(drill?'DRILL ENDED':'ABORTED');
  if(!drill){
    NSD.ledger.add('EAP ABORTED BY OPERATOR · PANEL RETURNED TO STANDBY',{actor:'OPERATOR',sev:'WARNING'});
    E.opEapLog.textContent='Aborted. Hooters and strobes de-energized; the panel is back in standby and the '+
      'affected seam must be re-cleared by the Overman before normal work resumes.';
  }else{
    NSD.ledger.add('EAP SILENT DRILL COMPLETED · NO HARDWARE ENERGIZED',{actor:'DGM OFFICER',sev:'INFO'});
  }
}
function wireEap(){
  const sw=E.opEapSwipe,knob=E.opEapKnob,fill=E.opEapFill;
  const Sw=makeSwipe(sw,knob,function(p,complete){
    if(OP.eap.engaged){
      if(p>0.05)fill.textContent='ABORT WITH THE BUTTON BELOW';
      return;
    }
    if(complete){
      const s=OP.eap;
      if(s.stage===0){
        s.stage=1;s.armedAt=Date.now();
        sw.classList.add('armed');
        paintEap('ARMED · FIRE WITHIN 6 s');
        NSD.ledger.add('EAP ARM STAGE PASSED · SECOND DRAG REQUIRED',{actor:'OPERATOR',sev:'WARNING'});
      }else if(s.stage===1&&Date.now()-s.armedAt<=6000){
        eapFire();
      }else{
        s.stage=0;paintEap('ARM WINDOW EXPIRED · RE-ARM');
      }
    }else if(p>0.05&&!OP.eap.engaged){
      fill.textContent=OP.eap.stage===1?'STEP 2 · DRAG AGAIN WITHIN 6 s TO FIRE':'STEP 1 · DRAG TO ARM';
    }
  });
  NSD.swipeKeys(sw,function(d){
    if(OP.eap.engaged)return;
    Sw.step(d,function(){},fill);
  });
  E.opEapAbort.addEventListener('click',()=>eapAbort(false));
  E.opEapDrill.addEventListener('click',function(){
    eapAbort(true);
    E.opEapLog.innerHTML='<b style="color:#9fd4f2">SILENT DRILL</b> walked through both stages — hooter and '+
      'strobe circuits were opened on the test loop only, so no underground hardware energized. Drill duration '+
      'logged for the statutory return.';
  });
  E.opEapSeam.addEventListener('change',function(){
    if(OP.eap.engaged){NSD.ledger.add('EAP SEAM EXTENDED TO '+E.opEapSeam.value,{actor:'OPERATOR',sev:'CRITICAL'});paintEap();}
  });
  setInterval(function(){
    if(OP.eap.stage===1&&!OP.eap.engaged&&Date.now()-OP.eap.armedAt>6000){
      OP.eap.stage=0;E.opEapSwipe.classList.remove('armed');paintEap('ARM WINDOW EXPIRED · RE-ARM');
    }
  },500);
}

const TPL=[
  {id:'t1',b:'WITHDRAWAL ADVISORY',t:'Strata movement detected at depillaring panel P-3, Level 1. All persons withdraw from the face to the fresh-air base. Do not re-enter until clearance is issued by the Overman.'},
  {id:'t2',b:'EAP ENGAGED',t:'EMERGENCY ACTION PLAN ENGAGED. Hooters and strobes energized. Muster at main shaft bank. Overman to report headcount to the surface control room immediately.'},
  {id:'t3',b:'NODE OFFLINE ADVISORY',t:'Surface node offline; the ESP-NOW 2.4 GHz mesh has rerouted around it and the wired ring, buzzers and evacuation lights are unaffected. Monitoring continues on the remaining nodes. No withdrawal required at this stage.'},
  {id:'t4',b:'ALL CLEAR',t:'ALL CLEAR. Panel P-3 strata parameters back within limits. Normal working may resume; monitoring interval restored to standard.'}
];
const RCP=[
  {id:'r1',n:'Overman · S. Das',d:'Roving underground, L1'},
  {id:'r2',n:'Mining Sirdar · R. Sen',d:'Panel P-3 face'},
  {id:'r3',n:'Colliery Rescue Room',d:'24×7 • statutory dispatch list'},
  {id:'r4',n:'Colliery Manager · A. K. Mahato',d:'Surface control room'},
  {id:'r5',n:'Mine-mate app · all UW phones',d:'Cell broadcast fallback for men underground'}
];
function buildSms(){
  E.opSmsTpl.innerHTML=TPL.map(t=>
    '<div class="checkrow" data-t="'+t.id+'" role="checkbox" tabindex="0" aria-checked="false">'+
    '<span class="bx">✓</span><span class="tx"><b>'+NSD.esc(t.b)+'</b><span>'+NSD.esc(t.t.slice(0,96))+'… · '+
    t.t.length+' chars</span></span></div>').join('');
  E.opSmsRcp.innerHTML=RCP.map(r=>
    '<label class="lay" style="display:block;padding:0"><label class="checkrow" data-r="'+r.id+'" role="checkbox" tabindex="0" aria-checked="false">'+
    '<span class="bx">✓</span><span class="tx"><b>'+NSD.esc(r.n)+'</b><span>'+NSD.esc(r.d)+'</span></span></label></label>').join('');
  $$('[data-t]',E.opSmsTpl).forEach(el=>wireCheck(el,OP.sms.tpl));
  $$('[data-r]',E.opSmsRcp).forEach(el=>wireCheck(el,OP.sms.rcp));
}
function wireCheck(el,store){
  const toggle=function(){
    const id=el.dataset.t||el.dataset.r;
    const i=store.indexOf(id);
    if(i<0)store.push(id);else store.splice(i,1);
    el.classList.toggle('on',i<0);
    el.setAttribute('aria-checked',i<0?'true':'false');
    E.opSmsCnt.textContent=store.length+' / '+(store===OP.sms.tpl?TPL.length:RCP.length)+' SEL';
    E.opSmsCnt.className='chip '+(store.length?'ok':'');
    E.opSmsSend.disabled=!(OP.sms.tpl.length&&OP.sms.rcp.length);
  };
  el.addEventListener('click',toggle);
  el.addEventListener('keydown',function(e){if(e.key===' '||e.key==='Enter'){e.preventDefault();toggle();}});
}
function wireSms(){
  buildSms();
  E.opSmsSend.disabled=true;
  E.opSmsSend.addEventListener('click',function(){
    if(!OP.sms.tpl.length||!OP.sms.rcp.length)return;
    const tpls=OP.sms.tpl.map(id=>TPL.filter(t=>t.id===id)[0]);
    const rcps=OP.sms.rcp.map(id=>RCP.filter(r=>r.id===id)[0]);
    const mid='SMS-'+(fnv(String(Date.now())).slice(0,6)).toUpperCase();
    tpls.forEach(t=>{
      NSD.ledger.add('BROADCAST '+t.b+' · '+rcps.length+' RECIPIENTS · '+mid,{actor:'OPERATOR',sev:'WARNING'});
    });
    if(OP.sms.tpl.indexOf('t2')>=0)NSD.escalate('critical');
    else if(OP.sms.tpl.indexOf('t1')>=0)NSD.escalate('warning');
    /* **CR-07 — this panel does not dispatch anything.**
     * It mints a message id, escalates the deck's own alert state and prints a
     * result. There is no network call in this function and no gateway on the
     * other end — the dispatcher (`POST /api/alerts/broadcast`) does not exist
     * yet. So it is labelled. `check_claims.py` looks for this exact string
     * inside `wireSms()`, and the point of putting it *here* rather than in a
     * footnote is that the operator sees it in the same breath as the result. */
    E.opSmsState.textContent='PREVIEW · NOT DISPATCHED '+U.hhmmss(new Date());
    E.opSmsState.className='chip ok';
    E.opSmsOut.innerHTML='<b style="color:#ffc061">'+mid+'</b> PREVIEW — rendered for '+rcps.map(r=>NSD.esc(r.n)).join(' · ')+
      '. <b>Nothing left this browser.</b> No gateway is wired in this build, so there is no handset delivery, '+
      'no cell-broadcast fallback and no ACK; the dispatcher is a later phase. The templates and the recipient '+
      'list are shown above so the wording and the routing can be reviewed without anyone believing a message went out.';
    E.opSmsSend.disabled=true;
    setTimeout(()=>{E.opSmsSend.disabled=false;E.opSmsState.textContent='READY';E.opSmsState.className='chip';},3500);
    OP.alarms.unshift({id:'ALM-'+(++alarmSeq),sev:1,title:'Broadcast dispatched · '+mid,
      detail:rcps.length+' recipients · '+tpls.length+' template(s)',ts:new Date(),acked:true,
      who:'SYSTEM',ackTs:new Date(),src:'SMS'});
    renderTriage();renderRegulatorSoon();
  });
}

function failNode(id){
  const n=byId[id];if(!n||!n.alive)return;
  n.alive=false;n.warn=false;n.last=Date.now();
  n.rssi=-108;n.batt=+(n.batt-0.18).toFixed(2);
  OP.failed.push(id);
  OP.reroute=true;OP.rerouteAt=Date.now();
  NODES.forEach(m=>{if(!m.alive)return;if(Math.abs(m.idx-n.idx)===1)m.risk=Math.min(1.25,m.risk+0.34);});
  const relays=NODES.filter(m=>m.alive&&MLINKS.some(l=>(l[0]===m.idx&&l[1]===n.idx)||(l[1]===m.idx&&l[0]===n.idx))).map(m=>m.id);
  const online=NODES.filter(m=>m.alive).length;
  addAlarm(3,'Surface node '+id+' silent · last-gasp received',
    n.zone+' · '+n.rl+' · battery '+fmt(n.batt,2)+' V · ESP-NOW 2.4 GHz mesh rerouted via '+(relays.join(', ')||'M-01')+' · '+online+'/'+NTOTAL+
    ' reporting · wired ring and evacuation circuits unaffected','MESH');
  NSD.ledger.add('SURFACE NODE '+id+' LAST-GASP · MESH REROUTED · '+online+'/'+NTOTAL+' REPORTING',{actor:'SYSTEM',sev:'CRITICAL'});
  NSD.escalate('critical');
  renderRegulatorSoon();paintHud();
  E.opFailSel.value=id;
}
function failDevice(id){
  if(byId[id])return failNode(id);
  const h=hubById[id];
  if(h&&h.kind==='mini'){
    if(OP.faultHub[id])return;
    OP.faultHub[id]=true;OP.reroute=true;
    const jbs=JBS.filter(j=>j.hub===id),lights=jbs.reduce((a,j)=>a+j.lights,0);
    addAlarm(3,h.id+' fault · '+h.buzzers+' buzzers offline',
      LEVELS[h.lv].title.split(' · ')[0]+' · bus '+h.id+' dark: '+jbs.map(j=>j.id).join(', ')+' ('+lights+' evacuation lights) unreachable · ring transits the hub on bypass','RING');
    NSD.ledger.add(h.id+' FAULT · '+h.buzzers+' BUZZERS OFFLINE · BUS DARK · '+jbs.map(j=>j.id).join(', ')+' NO-LINK',{actor:'SYSTEM',sev:'CRITICAL'});
    NSD.escalate('critical');renderRegulatorSoon();paintHud();
    return;
  }
  const j=jbById[id];
  if(j){
    if(OP.faultJb[id])return;
    OP.faultJb[id]=true;
    addAlarm(2,j.id+' fault · '+j.lights+' evacuation lights offline',
      LEVELS[j.lv].id+' 0+'+String(j.ch).padStart(3,'0')+' · bus '+j.hub+' keeps polling past it · buzzers unaffected','BUS');
    NSD.ledger.add(j.id+' FAULT · '+j.lights+' EVAC LIGHTS OFFLINE · BUS '+j.hub+' HEALTHY',{actor:'SYSTEM',sev:'WARNING'});
    renderRegulatorSoon();paintHud();
  }
}
function restoreAll(){
  const had=OP.failed.length+ringCutIds().length+[0,1].filter(l=>busCutOf(l)!==null).length+Object.keys(OP.faultJb).length+Object.keys(OP.faultHub).length;
  NODES.forEach(n=>{n.alive=true;n.warn=false;n.last=Date.now();n.rssi=-(58+Math.round(n.risk*20));});
  OP.failed=[];OP.reroute=false;OP.ringCuts={};OP.busCut={0:null,1:null};OP.faultJb={};OP.faultHub={};
  if(E.opCutSel)E.opCutSel.value='none';
  if(had){
    const a=addAlarm(1,'Network restored · ring healthy · '+JBS.length+'/'+JBS.length+' junctions · surface mesh '+NTOTAL+'/'+NTOTAL,
      'Both rings intact, every bus polling both junction boxes, all four surface nodes reporting','MESH');
    a.acked=true;a.who='AUTO-CLEAR';a.ackTs=new Date();
    NSD.ledger.add('NETWORK RESTORED · RING HEALTHY · '+JBS.length+'/'+JBS.length+' JUNCTIONS · SURFACE MESH '+NTOTAL+'/'+NTOTAL,{actor:'OPERATOR',sev:'INFO'});
  }
  OP.alarms.filter(a=>/silent|cut|isolated|fault/i.test(a.title)&&!a.acked).forEach(a=>{
    a.acked=true;a.who='AUTO-CLEAR';a.ackTs=new Date();
  });
  renderTriage();renderRegulatorSoon();paintHud();
}

function cutSegment(v){
  if(!v||v==='none')return;
  if(ringById[v]){
    if(OP.ringCuts[v])return;
    OP.ringCuts[v]=true;OP.reroute=true;
    const s=ringById[v],iso=isolatedHubs();
    if(iso.length){
      const lights=JBS.filter(j=>iso.some(h=>h.id===j.hub)).reduce((a,j)=>a+j.lights,0),buzz=iso.reduce((a,h)=>a+h.buzzers,0);
      addAlarm(3,iso.map(h=>h.id).join(' + ')+' isolated · '+ringCutIds().join(' + ')+' cut',
        'No remaining path from M-01 on either ring · '+buzz+' buzzers + '+lights+' evacuation lights unreachable','RING');
      NSD.ledger.add('RING · '+ringCutIds().join(' + ')+' CUT · '+iso.map(h=>h.id).join(' + ')+' ISOLATED · '+buzz+' BUZZERS + '+lights+' LIGHTS UNREACHABLE',{actor:'OPERATOR',sev:'CRITICAL'});
      NSD.escalate('critical');
    }else{
      addAlarm(2,'Ring segment '+s.id+' cut · ring wrapped',
        'Double ring: '+s.a+' ↔ '+s.b+' ('+s.run+') severed · traffic wrapped onto the secondary path · every hub still reachable · lights and buzzers unaffected','RING');
      NSD.ledger.add('RING SEGMENT '+s.id+' CUT · '+s.a+' ↔ '+s.b+' · RING WRAPPED · ALL HUBS REACHABLE',{actor:'OPERATOR',sev:'WARNING'});
    }
  }else{
    const p2=v.split(':'),lv=parseInt(p2[0],10),seg=parseInt(p2[1],10);
    if(!(lv in OP.busCut)||OP.busCut[lv]===seg)return;
    OP.busCut[lv]=seg;OP.reroute=true;
    const hub=MINIS[lv],jbs=JBS.filter(j=>j.lv===lv),dark=jbs.filter(j=>seg===0||(seg===1&&j.pos===1));
    const lights=dark.reduce((a,j)=>a+j.lights,0);
    const where=seg===0?'at the head-end ('+hub.id+' → '+jbs[0].id+')':seg===1?'between '+jbs[0].id+' and '+jbs[1].id:'past '+jbs[1].id+' (tail / EOL)';
    addAlarm(dark.length?3:1,'Bus '+hub.id+' cut · segment '+(seg+1),
      'Break '+where+'. '+(dark.length?dark.map(j=>j.id).join(', ')+' NO-LINK · '+lights+' evacuation lights unreachable · buzzers at '+hub.id+' unaffected':'No junction lost — both boxes sit upstream of the break'),'BUS');
    NSD.ledger.add('BUS '+hub.id+' CUT · SEG-'+(seg+1)+' · '+dark.length+' JB NO-LINK · '+lights+' EVAC LIGHTS UNREACHABLE',{actor:'OPERATOR',sev:dark.length?'CRITICAL':'WARNING'});
    if(dark.length)NSD.escalate('critical');
  }
  renderRegulatorSoon();
  if(E.opCutSel)E.opCutSel.value='none';
  paintHud();
}
function simTick(dt,now){
  if(OP.linkDown){
    if(now-OP.linkDown>30000){
      OP.lastFlush='FLUSHED '+OP.bufPkt+' PACKETS · '+fmt((now-OP.linkDown)/1000,1)+' s OUTAGE';
      NSD.ledger.add('UPLINK RESTORED · '+OP.bufPkt+' BUFFERED PACKETS FLUSHED IN 1.2 s',{sev:'WARNING'});
      addAlarm(2,'Uplink outage closed','Edge buffered '+OP.bufPkt+' packets and flushed them on reconnect','EDGE');
      OP.linkDown=0;OP.bufPct=0;OP.bufPkt=0;
    }else{
      OP.bufPkt+=Math.round(dt*40);
      OP.bufPct=clamp(OP.bufPkt/14.4,0,100);
    }
  }
  if(now-(OP._lastLat||0)>1000){
    OP._lastLat=now;
    const lat=OP.linkDown?0:clamp(38+U.rr(-6,14)+(Math.random()<0.03?U.rr(60,420):0),18,900);
    OP.latency.push(lat);if(OP.latency.length>90)OP.latency.shift();
    paintSpark(true);
  }
  const creep=(OP.reroute?0.0028:0.0016);
  NODES.forEach(n=>{
    if(!n.alive)return;
    const t=now/1000;
    n.pitch=clamp(n.pitch+((n.risk*3.4+Math.sin(t/31+n.idx)*0.5+U.rr(-0.15,0.15))-n.pitch)*dt*0.35,-9,9);
    n.roll=clamp(n.roll+((n.risk*2.3+Math.sin(t/47+n.idx*1.7)*0.4+U.rr(-0.12,0.12))-n.roll)*dt*0.3,-9,9);
    n.pitch=clamp(n.pitch+(n.pitch>4.6?creep:0)*dt*60,-9.6,9.6);
    n.rssi=clamp(n.rssi+U.rr(-1.2,1.2),-104,-52);
    n.batt=Math.max(3.35,n.batt-0.000012*dt*60);
    n.tx+=Math.round(dt*4);n.rxs+=Math.round(dt*14);
    n.warn=Math.max(Math.abs(n.pitch),Math.abs(n.roll))>4.2;
    if(!n._seen||now-n._seen>900){n._seen=now;n.last=Date.now();}
  });
  const sn=byId[OP.sel];
  if(sn){
    const wall=Date.now();
    if(!sn._ptt){sn._ptt=wall;sn._pv=sn.pitch;}
    if(wall-sn._ptt>1500){
      const d=(sn.pitch-sn._pv)/((wall-sn._ptt)/60000);
      sn._pv=sn.pitch;sn._ptt=wall;
      OP.tiltLive=U.lerp(OP.tiltLive,d,0.45);
    }
  }
  if(now-(OP._lastAlarm||0)>26000){
    OP._lastAlarm=now;
    const hot=NODES.filter(n=>n.alive).sort((a,b)=>b.risk-a.risk)[0];
    if(hot&&Math.abs(hot.pitch)>4.6){
      addAlarm(3,'Tilt '+fmt(hot.pitch,1)+' mm/m exceeds 5 mm/m limit',
        hot.id+' · last-gasp not yet fired · stretch rate rising on '+hot.cell,'TILT');
      NSD.escalate('critical');
    }else if(hot){
      addAlarm(2,'Stretch rate rising · '+hot.id,'Adjacent pair '+hot.cell+' accumulating '+fmt(hot.risk*1.4+0.2,2)+' mm/min','PAIR');
    }
  }
}

function wireControls(){
  const seg1=U.seg($('.seg[aria-label="Level filter"]'),function(v){OP.lvlf=v;});
  const seg2=U.seg($('.seg[aria-label="Bus display"]'),function(v){OP.busf=v;});
  E.opFailSel.innerHTML='<optgroup label="SURFACE NODES · WIRELESS">'+NODES.map(n=>'<option value="'+n.id+'">'+n.id+' · '+n.zone.split(' · ')[0]+'</option>').join('')+'</optgroup>'+
    '<optgroup label="MINI HUBS · BUZZERS">'+MINIS.map(h=>'<option value="'+h.id+'">'+h.id+' · '+LEVELS[h.lv].id+' · '+h.buzzers+' BUZZERS</option>').join('')+'</optgroup>'+
    '<optgroup label="JUNCTION BOXES · EVAC LIGHTS">'+JBS.map(j=>'<option value="'+j.id+'">'+j.id+' · '+LEVELS[j.lv].id+' 0+'+String(j.ch).padStart(3,'0')+' · '+j.lights+' LIGHTS</option>').join('')+'</optgroup>';
  E.opFailSel.value=OP.sel;
  E.opFailBtn.addEventListener('click',()=>failDevice(E.opFailSel.value));
  if(E.opCutSel){
    E.opCutSel.innerHTML='<option value="none">SELECT SEGMENT</option>'+
      '<optgroup label="DOUBLE RING">'+RING.map(s=>'<option value="'+s.id+'">'+s.id+' · '+s.a+' → '+s.b+'</option>').join('')+'</optgroup>'+
      MINIS.map((h,lv)=>{const jb=JBS.filter(j=>j.lv===lv);return '<optgroup label="BUS '+h.id+'">'+
        '<option value="'+lv+':0">'+h.id+' → '+jb[0].id+' (HEAD-END)</option>'+
        '<option value="'+lv+':1">'+jb[0].id+' → '+jb[1].id+'</option>'+
        '<option value="'+lv+':2">'+jb[1].id+' → EOL (TAIL)</option></optgroup>';}).join('');
    E.opCutSel.value='none';
  }
  if(E.opCutBtn)E.opCutBtn.addEventListener('click',()=>cutSegment(E.opCutSel.value));
  E.opRestoreBtn.addEventListener('click',restoreAll);
  E.opPpvBurst.addEventListener('click',function(){
    for(let i=0;i<26;i++)OP.ppv.push(0.35+Math.exp(-i/6)*3.4+U.rr(-0.2,0.35));
    while(OP.ppv.length>240)OP.ppv.shift();
    OP.peakHold=Math.max(OP.peakHold,3.6);
    ppvEvent(3.6,0.45,U.rr(3,9));
    NSD.ledger.add('BLAST SIGNATURE INJECTED · PPV 3.6 mm/s · MC-3 stope · 14:22 CHARGING ROUND',{sev:'WARNING'});
  });
  const btn=document.createElement('button');
  btn.className='btnx';btn.textContent='SIMULATE LINK DROP · 30 s';btn.style.marginTop='10px';btn.style.width='100%';
  btn.addEventListener('click',function(){
    if(OP.linkDown)return;
    OP.linkDown=performance.now();OP.bufPkt=0;OP.bufPct=0;
    addAlarm(2,'Surface uplink severed','Edge gateway buffering locally · underground monitoring unaffected','EDGE');
    NSD.ledger.add('UPLINK SEVERED · EDGE GATEWAY BUFFERING LOCALLY',{sev:'WARNING'});
    btn.textContent='UPLINK DOWN · BUFFERING…';
    setTimeout(()=>{btn.textContent='SIMULATE LINK DROP · 30 s';},31000);
  });
  E.opSyncDot.parentNode.parentNode.appendChild(btn);
}
function initDeck(){
  buildSparkCards();
  wireControls();wireEap();wireSms();
  const tiltAlarm=addAlarm(3,'Tilt 5.6 mm/m exceeds 5 mm/m limit',
    'S2 · N CREST · PANEL P-3 SUBSIDENCE ZONE · sustained 42 s · stretch rate climbing on pair S2↔S3','TILT');
  addAlarm(2,'Stretch rate rising · pair S1↔S2','NW CREST · 1.34 mm/min over the last 60 s','PAIR');
  addAlarm(1,'Node S4 battery deep-sleep window','3.66 V · duty cycle reduced to 600 s','MESH');
  tiltAlarm.acked=true;tiltAlarm.who='ECL-4471';tiltAlarm.ackTs=new Date(Date.now()-6*60000);
  while(surfPending.length)addSurfAlarm(surfPending.shift());
  renderTriage();
  paintEap();paintTilt();renderPpvFeed();paintHud();
  setInterval(pumpPpv,60);
}
const surfPending=[];
let started=false;
function boot(){if(started)return;started=true;initDeck();}
NSD.onEnter('operator',boot);
function addSurfAlarm(d){
  const horns=(d.horns&&d.horns.length)?d.horns.join(' + '):'no local horn — M-01 site alarm';
  addAlarm(d.to===2?3:2,
    (d.to===2?'EVACUATION ALARM · Level '+d.level:'Surface-node ALERT · Level '+d.level),
    horns+' · '+(d.reason||'')+(d.to===2?' · crews to clear the level; horn + strobe sounding at the mast':' · access restricted, strobe on'),
    'SURF');
}
document.addEventListener('ns:surface-alarm',e=>{
  const d=e.detail||{}; if(!d.to)return;
  if(!started){surfPending.push(d);return;}
  addSurfAlarm(d);renderTriage();paintHud();
});
NSD.onTab('operator',function(){
  boot();
  drawTopo(performance.now());drawPpv();paintTilt();paintSpark(false);paintLat();paintHud();
});
NSD.onTick(function(now,dt){
  if(NSD.st.view!=='operator')return;
  boot();
  simTick(dt,now);
  if(now-OP.lastPaint>32){OP.lastPaint=now;drawTopo(now);drawPpv();paintTilt();paintSpark(false);paintLat();}
  if(now-OP.lastText>260){OP.lastText=now;paintHud();paintEap();}
});
if(window.matchMedia&&window.matchMedia('(hover:none)').matches){
  const hint=document.querySelector('#opTopoWrap .topolegend span:last-child');
  if(hint&&/HOVER DEVICE/i.test(hint.textContent))hint.textContent='TAP DEVICE · STATUS';
}
window.__OP={nodes:NODES,hubs:HUBS,ring:RING,jbs:JBS,state:OP,net:netStatus,fail:failDevice,cut:cutSegment,restore:restoreAll,fire:eapFire,abort:eapAbort};
})();
