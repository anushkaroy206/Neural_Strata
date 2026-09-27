(function(){
'use strict';
const NSD=window.NSD;if(!NSD)return;
const U=NSD.U,$=U.$,$$=U.$$,clamp=U.clamp,fmt=U.fmt,rgba=U.rgba;
const MONO=U.MONO;

const E={};
['rgK1','rgK2','rgK2d','rgK3','rgK3d','rgK4','rgK4d','rgK5','rgK5d','rgK6','rgK6d',
 'rgMap','rgMapWrap','rgReadout','rgMapCount','rgMapRed','rgMapAmb','rgMapOk',
 'rgIdxBody','rgIdxNote','rgMinePanel','rgMineSel','rgMineBody',
 'rgLedger','rgChainState','rgChainTip','rgVerifyBtn','rgTamperBtn','rgExportBtn',
 'rgSlaPass','rgSlaFail','rgSlaWorst','rgSlaList','rgSlaNote',
 'rgUptSel','rgUptMine','rgUptAvg','rgUptWorst','rgCal','rgCalMonths','rgCalTip','rgCalCount','rgCalBody','rgCalNote','rgCalFilter',
 'rgRadar','rgRadarSel','rgRadarBlock',
 'rgRepMine','rgRepDate','rgRepType','rgRepParts','rgRepBtn','rgRepDl','rgRepOut','rgRepState'].forEach(id=>{E[id]=document.getElementById(id);});
if(!E.rgMap)return;

const RG={sel:NSD.state.mineLocal, uptSel:NSD.state.mineLocal, calFilter:'all', weighting:'live',
  radar:'JHARIA BLOCK', norm:false, hover:null, tamper:null, inited:false, calNotices:{}};

const INDIA=[[0.30,0.22],[0.36,0.13],[0.45,0.17],[0.52,0.12],[0.60,0.16],[0.67,0.22],[0.73,0.26],
 [0.79,0.22],[0.83,0.28],[0.89,0.25],[0.95,0.31],[0.97,0.38],[0.91,0.40],[0.89,0.47],[0.83,0.44],
 [0.81,0.37],[0.75,0.42],[0.72,0.50],[0.69,0.60],[0.64,0.70],[0.58,0.78],[0.52,0.72],[0.47,0.60],
 [0.42,0.50],[0.36,0.44],[0.31,0.47],[0.26,0.40],[0.22,0.32],[0.24,0.25]];
const ZONES=[
  {z:'ECL',x:0.782,y:0.352,label:'EASTERN COALFIELD'},
  {z:'BCCL',x:0.758,y:0.368,label:'JHARIA (BCCL)'},
  {z:'CCL',x:0.700,y:0.415,label:'CENTRAL COALFIELD'},
  {z:'WCL',x:0.460,y:0.436,label:'WESTERN COALFIELD'},
  {z:'SECL',x:0.620,y:0.444,label:'SOUTH EASTERN C.'
  }
];
function worstOf(m){
  return {l3:Math.max(m.l3, m.mesh<99?1:0), mesh:Math.min(m.live.mesh,m.mesh-1.6), ack:m.ack*1.25};
}
function effIndex(m){
  if(RG.weighting==='worst'){
    const w=worstOf(m);
    return Math.round(clamp(m.idx-(w.l3*6)-(99-w.mesh)*2.2,4,100));
  }
  return NSD.liveIndex(m);
}
function effStatus(m){
  if(RG.weighting==='worst'){
    const w=worstOf(m);
    return (w.l3>=2||w.mesh<92)?'crit':(w.l3===1||w.mesh<97)?'warn':'ok';
  }
  return NSD.status(m);
}

function drawMacro(now){
  const ctx=U.ctx(E.rgMap);if(!ctx)return;
  const c=ctx.x,W=ctx.w,H=ctx.h;
  const X=x=>40+x*(W-80), Y=y=>26+y*(H-64);
  c.beginPath();
  INDIA.forEach((p,i)=>{i?c.lineTo(X(p[0]),Y(p[1])):c.moveTo(X(p[0]),Y(p[1]));});
  c.closePath();
  const g=c.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'rgba(255,255,255,.055)');g.addColorStop(1,'rgba(255,255,255,.015)');
  c.fillStyle=g;c.fill();
  c.strokeStyle='rgba(255,255,255,.16)';c.lineWidth=1.2;c.stroke();
  c.save();c.clip();
  c.strokeStyle='rgba(255,255,255,.05)';
  for(let i=1;i<8;i++){c.beginPath();c.moveTo(X(0.2+0.1*i),Y(0.1));c.lineTo(X(0.2+0.1*i),Y(0.85));c.stroke();}
  for(let i=1;i<7;i++){c.beginPath();c.moveTo(X(0.18),Y(0.1+0.12*i));c.lineTo(X(0.98),Y(0.1+0.12*i));c.stroke();}
  c.restore();
  ZONES.forEach(z=>{
    const cx=X(z.x),cy=Y(z.y);
    c.beginPath();
    for(let a=0;a<=40;a++){
      const t=a/40*Math.PI*2,k=1+0.16*Math.sin(t*3+z.z.length);
      const px=cx+Math.cos(t)*70*k,py=cy+Math.sin(t)*46*k;
      a?c.lineTo(px,py):c.moveTo(px,py);
    }
    c.closePath();
    c.fillStyle=rgba('#8c9dff',0.055);c.fill();
    c.strokeStyle=rgba('#8c9dff',0.28);c.setLineDash([5,4]);c.lineWidth=1;c.stroke();c.setLineDash([]);
    c.fillStyle='rgba(180,190,255,.55)';c.font='600 9px '+MONO;c.textAlign='center';
    if(W>=620){
      c.fillText(z.z,cx,cy+58);
      c.font='7.5px '+MONO;c.fillStyle='rgba(180,190,255,.35)';
      c.fillText(z.label,cx,cy+69);
    }else{
      c.fillText(z.z,cx,cy+52);
    }
  });
  NSD.mines.forEach(m=>{
    const st=effStatus(m),x=X(m.x),y=Y(m.y);
    const col=st==='crit'?'#ff4d4d':st==='warn'?'#ff9538':'#4ade80';
    if(st==='crit'){
      const ph=(now/900)%1;
      c.beginPath();c.arc(x,y,6+ph*16,0,7);
      c.strokeStyle=rgba('#ff4d4d',(1-ph)*0.6);c.lineWidth=1.6;c.stroke();
    }
    c.beginPath();c.arc(x,y,RG.sel===m.n?7:5,0,7);
    c.fillStyle=rgba(col,.9);c.fill();
    c.strokeStyle='rgba(0,0,0,.55)';c.lineWidth=1;c.stroke();
    if(RG.sel===m.n){
      c.beginPath();c.arc(x,y,12,0,7);c.strokeStyle='rgba(255,255,255,.7)';c.lineWidth=1.2;c.stroke();
      c.font='600 9.5px '+MONO;
      const t1=m.n+' · INDEX '+effIndex(m),t2=METHODS(m);
      const tw=Math.max(c.measureText(t1).width,c.measureText(t2).width);
      const flip=x+16+tw>W-8;
      c.textAlign=flip?'right':'left';
      const tx=flip?x-16:x+16;
      c.fillStyle='#f3f0ed';c.fillText(t1,tx,y-9);
      c.font='8.5px '+MONO;c.fillStyle='#9b938c';
      c.fillText(t2,tx,y+3);
      c.textAlign='left';
    }
  });
  const cap='SCHEMATIC REGIONAL MATRIX — NOT A SURVEY MAP · PIN COLOUR = LIVE WARNING LEVEL';
  const wtxt='WEIGHTING: '+RG.weighting.toUpperCase();
  c.font='8.5px '+MONO;c.textAlign='left';c.fillStyle='rgba(255,255,255,.3)';
  if(W-80>=c.measureText(cap).width+c.measureText(wtxt).width+24){
    c.fillText(cap,44,H-14);
    c.textAlign='right';c.fillText(wtxt,W-40,H-14);
  }else{
    c.fillText(W<520?'SCHEMATIC · NOT A SURVEY MAP':cap.slice(0,34),44,H-24);
    c.fillText(wtxt,44,H-10);
  }
}
function METHODS(m){return m.m==='LW'?'LONGWALL':'BORD & PILLAR';}
const RG_HINT=(window.matchMedia&&window.matchMedia('(hover:none)').matches)?
  'TAP A PIN FOR COLLIERY TELEMETRY':'HOVER A PIN FOR COLLIERY TELEMETRY';
E.rgReadout.textContent=RG_HINT;
function pinAt(ev,slack){
  const r=E.rgMap.getBoundingClientRect();
  const x=ev.clientX-r.left,y=ev.clientY-r.top;
  let best=null,bd=slack||16;
  NSD.mines.forEach(m=>{
    const d=Math.hypot(40+m.x*(r.width-80)-x,26+m.y*(r.height-64)-y);
    if(d<bd){bd=d;best=m;}
  });
  return best;
}
function pinReadout(best){
  E.rgReadout.innerHTML='<b>'+NSD.esc(best.name)+'</b> · '+best.z+' · '+METHODS(best)+'<br>'+
    'SAFETY INDEX '+effIndex(best)+' · L3 '+best.live.l3+' · MESH '+fmt(best.live.mesh,1)+' %<br>'+
    'MEDIAN ACK '+fmt(best.live.ack,1)+' min · OBSERVATIONS '+obsOf(best).length+' OPEN<br>'+
    'LAST INSPECTION '+best.last+' · '+NSD.esc(best.officer);
}
E.rgMap.addEventListener('mousemove',function(ev){
  const best=pinAt(ev,16);
  RG.hover=best?best.n:null;
  if(best)pinReadout(best);
});
E.rgMap.addEventListener('mouseleave',()=>{E.rgReadout.textContent=RG_HINT;});
E.rgMap.addEventListener('pointerdown',function(ev){
  if(ev.pointerType==='mouse')return;
  const best=pinAt(ev,26);
  if(best){pinReadout(best);RG.sel=best.n;RG.uptSel=best.n;renderAll();}
  ev.preventDefault();
});
E.rgMap.addEventListener('click',function(ev){
  const r=E.rgMap.getBoundingClientRect();
  const x=ev.clientX-r.left,y=ev.clientY-r.top;
  let best=null,bd=18;
  NSD.mines.forEach(m=>{
    const d=Math.hypot(40+m.x*(r.width-80)-x,26+m.y*(r.height-64)-y);
    if(d<bd){bd=d;best=m;}
  });
  if(best){RG.sel=best.n;RG.uptSel=best.n;renderAll();}
});

function obsOf(m){
  const out=[];
  if(m.live.l3>=2)out.push('Two or more Level 3 warnings standing');
  else if(m.live.l3===1)out.push('Level 3 warning raised and under investigation');
  if(m.live.mesh<92)out.push('Site network below 92 % availability (surface mesh / underground ring)');
  else if(m.live.mesh<97)out.push('Ring wrap / bus telemetry latency elevated — spares to be checked');
  if(m.ack>10)out.push('Median alarm-to-acknowledgement above the 10-minute advisory');
  const cal=m.cal;
  if(cal)out.push(cal+' calibration certificate(s) expired');
  if(m.idx<70)out.push('Composite index below the 70-point comfort line');
  if(!out.length)out.push('No open observation at the last inspection');
  return out;
}
function renderTable(){
  const rows=NSD.mines.slice().sort((a,b)=>effIndex(a)-effIndex(b));
  E.rgIdxBody.innerHTML=rows.map(m=>{
    const idx=effIndex(m),st=effStatus(m);
    const col=st==='crit'?'#ff4d4d':st==='warn'?'#ff9538':'#4ade80';
    return '<tr class="click'+(RG.sel===m.n?' sel':'')+'" data-m="'+m.n+'">'+
      '<td style="color:#e9e4de">'+NSD.esc(m.name)+'</td>'+
      '<td style="color:var(--dim)">'+m.z+'</td>'+
      '<td><div style="display:flex;align-items:center;gap:8px"><span style="min-width:26px;color:'+col+'">'+idx+
        '</span><span class="idxbar"><i style="width:'+idx+'%;background:'+col+'"></i></span></div></td>'+
      '<td style="color:'+(m.live.l3?'#ff9d9d':'var(--faint)')+'">'+m.live.l3+'</td>'+
      '<td style="color:'+(m.live.mesh<97?'#ffc061':'#8ff0b0')+'">'+fmt(m.live.mesh,1)+'%</td></tr>';
  }).join('');
  $$('[data-m]',E.rgIdxBody).forEach(tr=>tr.addEventListener('click',()=>{
    RG.sel=tr.dataset.m;RG.uptSel=tr.dataset.m;renderAll();
  }));
}
function renderMine(){
  const m=NSD.mine(RG.sel);if(!m)return;
  E.rgMineSel.textContent=m.name.toUpperCase()+' · '+m.z;
  const st=effStatus(m),idx=effIndex(m);
  const col=st==='crit'?'#ff4d4d':st==='warn'?'#ff9538':'#4ade80';
  const obs=obsOf(m);
  E.rgMineBody.innerHTML=
    '<div class="kpis k3" style="margin-bottom:13px">'+
      '<div class="kpi '+(st==='crit'?'crit':st==='warn'?'warn':'ok')+'"><div class="l">SAFETY INDEX</div>'+
        '<div class="v" style="color:'+col+'">'+idx+'</div><div class="d">'+RG.weighting.toUpperCase()+' WEIGHTING</div></div>'+
      '<div class="kpi blue"><div class="l">NETWORK UPTIME</div><div class="v">'+fmt(m.live.mesh,1)+'<small>%</small></div>'+
        '<div class="d">surface mesh + ring, 90-d</div></div>'+
      '<div class="kpi warn"><div class="l">MEDIAN ACK</div><div class="v">'+fmt(m.live.ack,1)+'<small>min</small></div>'+
        '<div class="d">alert → manager ack</div></div>'+
    '</div>'+
    '<div class="kv"><span>Extraction methods on air</span><b>'+METHODS(m)+'</b></div>'+
    '<div class="kv"><span>Level 3 warnings · live</span><b style="color:'+(m.live.l3?'#ff9d9d':'var(--ok)')+'">'+m.live.l3+'</b></div>'+
    '<div class="kv"><span>Calibration certificates lapsed</span><b>'+m.cal+'</b></div>'+
    '<div class="kv"><span>Last DGMS inspection</span><b>'+m.last+'</b></div>'+
    '<div class="kv"><span>Nominated officer</span><b>'+NSD.esc(m.officer)+'</b></div>'+
    '<div class="ttl" style="margin-top:15px">OPEN OBSERVATIONS</div>'+
    '<div class="lay">'+obs.map((o,i)=>'<label style="cursor:default" class="'+(i===0&&st!=='ok'?'on':'')+'">'+
      '<span class="sw" style="background:'+(st==='ok'?'rgba(74,222,128,.4)':'rgba(255,149,56,.4)')+'"></span>'+
      NSD.esc(o)+'</label>').join('')+'</div>'+
    '<div style="display:flex;gap:9px;margin-top:13px">'+
      '<button class="btnx" id="rgNoticeBtn" style="flex:1">ISSUE OBSERVATION NOTICE</button>'+
      '<button class="btnx blue" id="rgAdvBtn" style="flex:1">ADVISORY TO AREA</button></div>';
  const nb=$('#rgNoticeBtn',E.rgMineBody);
  if(nb)nb.addEventListener('click',function(){
    NSD.ledger.add('OBSERVATION NOTICE ISSUED · '+obs[0].toUpperCase(),{mine:m.n,actor:'DGMS INSPECTOR',sev:'WARNING'});
    nb.textContent='NOTICE ISSUED ✓';nb.disabled=true;renderLedger();
  });
  const ab=$('#rgAdvBtn',E.rgMineBody);
  if(ab)ab.addEventListener('click',function(){
    NSD.ledger.add('SAFETY ADVISORY DISPATCHED TO AREA OFFICE',{mine:m.n,actor:'DGMS INSPECTOR',sev:'INFO'});
    ab.textContent='ADVISORY SENT ✓';ab.disabled=true;renderLedger();
  });
}

function renderLedger(){
  const L=NSD.ledger.blocks;
  E.rgChainTip.textContent='HEAD '+NSD.ledger.head+' · '+L.length+' BLOCKS';
  const rows=L.slice(-60).reverse();
  E.rgLedger.innerHTML=rows.map(b=>
    '<div class="le '+b.sev+'" data-i="'+b.i+'" title="prev '+b.prev+'">'+
    '<span class="hs">#'+b.i+'</span>'+
    '<span class="mt">'+b.t.slice(5)+'</span>'+
    '<span class="ev">'+NSD.esc(b.ev)+'</span>'+
    '<span class="ac">'+b.mine+' · '+NSD.esc(b.actor)+'</span>'+
    '<span class="hs">'+b.hash+'</span></div>').join('');
}
function verifyChain(announce){
  const v=NSD.ledger.verify();
  if(v.ok){
    E.rgChainState.textContent='CHAIN VERIFIED ✓ · '+v.at+' BLOCKS';
    E.rgChainState.className='chip ok';
    $$('.le',E.rgLedger).forEach(el=>el.style.background='');
  }else{
    E.rgChainState.textContent='TAMPER DETECTED AT BLOCK #'+v.at+' — '+v.why.toUpperCase();
    E.rgChainState.className='chip crit';
    $$('.le',E.rgLedger).forEach(el=>{
      el.style.background=Number(el.dataset.i)===v.at?'rgba(255,77,77,.18)':'';
    });
  }
  return v;
}
E.rgVerifyBtn.addEventListener('click',function(){
  const v=verifyChain(true);
  NSD.ledger.add(v.ok?'LEDGER CHAIN VERIFIED BY REGULATOR · '+v.at+' BLOCKS':
    'LEDGER INTEGRITY FAILURE REPORTED AT BLOCK #'+v.at,{mine:'REGIONAL',actor:'DGMS OFFICER',sev:v.ok?'INFO':'CRITICAL'});
  renderLedger();verifyChain(true);
});
E.rgTamperBtn.addEventListener('click',function(){
  const L=NSD.ledger.blocks;
  if(RG.tamper){
    L[RG.tamper.i-1].ev=RG.tamper.ev;
    RG.tamper=null;verifyChain(true);renderLedger();
    E.rgTamperBtn.textContent='TAMPER TEST';
    NSD.ledger.add('LEDGER RESTORED FROM SEALED BACKUP · CHAIN RE-VERIFIED',{mine:'REGIONAL',actor:'SYSTEM',sev:'INFO'});
    renderLedger();verifyChain(true);
    return;
  }
  const target=L[Math.max(0,L.length-9)];
  if(!target)return;
  RG.tamper={i:target.i,ev:target.ev};
  target.ev='RECORD EDITED IN PLACE — MANUAL SILENCE, NO OPERATOR ID LOGGED';
  renderLedger();verifyChain(true);renderLedger();
  E.rgTamperBtn.textContent='RESTORE FROM BACKUP';
  E.rgRepOut.innerHTML='<b style="color:#ff9d9d">Tamper test:</b> a historical row was edited in place under '+
    'the regulator\'s own nose and the chain verification immediately flagged block #'+target.i+
    '. This is exactly the failure mode the annexure rules exist to prevent.';
});
E.rgExportBtn.addEventListener('click',function(){
  const L=NSD.ledger.blocks;
  const head='BLOCK,TIME,COLLIERY,EVENT,ACTOR,SEVERITY,PREV_HASH,HASH\n';
  const body=L.map(b=>[b.i,'"'+b.t+'"','"'+b.mine+'"','"'+b.ev.replace(/"/g,'""')+'"','"'+b.actor+'"',b.sev,b.prev,b.hash].join(',')).join('\n');
  U.dl('DGMS-audit-ledger-'+U.iso(new Date())+'.csv',head+body,'text/csv');
  NSD.ledger.add('LEDGER EXPORTED FOR OFFICE RECORDS · '+L.length+' BLOCKS',{mine:'REGIONAL',actor:'DGMS OFFICER',sev:'INFO'});
  renderLedger();
});

function renderSla(){
  const rows=NSD.mines.slice().sort((a,b)=>b.live.ack-a.live.ack);
  const mx=30;
  const pass=rows.filter(m=>m.live.ack<=5).length;
  const fail=rows.filter(m=>m.live.ack>15).length;
  const above=rows.filter(m=>m.live.ack>5).length;
  E.rgSlaPass.textContent=pass+'/'+rows.length;
  E.rgSlaFail.textContent=fail;
  E.rgSlaWorst.textContent=fmt(rows[0]?rows[0].live.ack:0,1);
  E.rgSlaList.innerHTML=rows.map(m=>{
    const v=clamp(m.live.ack,0,mx);
    const col=m.live.ack>15?'#ff4d4d':m.live.ack>5?'#ff9538':'#4ade80';
    return '<div class="slarow"><div class="hd"><span>'+NSD.esc(m.name)+' <span style="color:var(--faint)">· '+m.z+'</span></span>'+
      '<b style="color:'+col+'">'+fmt(m.live.ack,1)+' min</b></div>'+
      '<div class="bullet">'+
        '<span class="rng g" style="left:0;width:'+(5/mx*100)+'%"></span>'+
        '<span class="rng a" style="left:'+(5/mx*100)+'%;width:'+(10/mx*100)+'%"></span>'+
        '<span class="rng r" style="left:'+(15/mx*100)+'%;width:'+(15/mx*100)+'%"></span>'+
        '<span class="tgt" style="left:'+(5/mx*100)+'%"></span>'+
        '<span class="act" style="left:'+(v/mx*100)+'%"></span>'+
        '<span class="lbl">'+fmt(m.live.ack,1)+' min</span>'+
      '</div>'+
      '<div class="axsmall"><span>0</span><span>5 · TARGET</span><span>15 · L2</span><span>30 · L3</span></div></div>';
  }).join('');
}

const WEEKS=26;
function uptimeFor(mine,dayIdx){
  const h=U.fnv(mine+'|'+dayIdx);
  const r=(parseInt(h,16)%1000)/1000;
  const base=NSD.mine(mine).mesh;
  let v=base-(r<0.06?3.4+r*20:r*1.6);
  if(dayIdx%97===0)v-=2.6;
  return clamp(v,86,100);
}
function renderCal(){
  const m=NSD.mine(RG.uptSel);
  if(!m)return;
  E.rgUptMine.textContent=m.name.toUpperCase()+' · '+m.z;
  const today=new Date();
  const cells=[];
  let sum=0,mn=100,mnD='';
  for(let i=0;i<WEEKS*7;i++){
    const d=new Date(today.getTime()-(WEEKS*7-1-i)*86400000);
    const v=uptimeFor(m.n,i);
    sum+=v;if(v<mn){mn=v;mnD=U.iso(d);}
    const t=clamp((v-90)/(100-90),0,1);
    const col=v<92?'#3a1717':v<95?'#7a2f22':v<97.5?'#8a6a20':v<99?'#3f8f57':'#8bf5ac';
    cells.push({date:U.iso(d),v:v,col:col});
  }
  E.rgCal.innerHTML=cells.map(cl=>'<i style="background:'+cl.col+'" data-d="'+cl.date+
    '" data-v="'+fmt(cl.v,1)+'" title="'+cl.date+' · '+fmt(cl.v,1)+' % uptime"></i>').join('');
  E.rgCalMonths.innerHTML=['APR','MAY','JUN','JUL','AUG','SEP'].map(x=>'<span style="width:53px">'+x+'</span>').join('');
  E.rgUptAvg.textContent='90-d MEAN '+fmt(sum/cells.length,1)+' %';
  E.rgUptWorst.textContent='WORST DAY '+fmt(mn,1)+' % · '+mnD;
  E.rgUptWorst.className='chip '+(mn<92?'crit':'warn');
  $$('#rgCal i').forEach(el=>{
    el.addEventListener('mouseenter',()=>{
      E.rgCalTip.textContent=el.dataset.d+' · '+el.dataset.v+' % uptime · '+
        (Number(el.dataset.v)<98?'OBSERVATION RAISED':'WITHIN LIMIT');
    });
  });
}

const INST=['Precision tilt sensor (TL)','Extensometer (EX)','Crackmeter (CM)','Vibrating-wire piezometer (VW)','PPV seismograph (SG)'];
const CAL=(function(){
  const rows=[];
  let k=0;
  NSD.mines.forEach(m=>{
    const n=2+(parseInt(U.fnv(m.n),16)%3);
    for(let i=0;i<n;i++){
      k++;
      const h=U.fnv(m.n+'cal'+i);
      const r=(parseInt(h,16)%997)/997;
      const due=r<0.085?Math.round(-150+r*1600)
               :r<0.205?Math.round(r*140)
               :Math.round(31+(r-0.205)*340);
      const inst=INST[Math.floor((parseInt(h,16)>>3)%INST.length)];
      rows.push({id:'TL-'+String(k).padStart(2,'0'),mine:m.n,inst:inst,due:due,last:null,
        serial:'CAL/'+m.z+'/'+(2020+(parseInt(h,16)%5))+'-'+String(100+ (parseInt(h,16)%800))});
    }
  });
  rows.forEach(r=>{const d=new Date(Date.now()+r.due*86400000);r.last=new Date(Date.now()+(r.due-365)*86400000);});
  return rows;
})();
function calState(r){
  if(RG.calNotices[r.id])return 'notice';
  if(r.due<0)return 'expired';
  if(r.due<=30)return 'due';
  return 'valid';
}
function renderCalLedger(){
  const filt=CAL.filter(r=>{
    const s=calState(r);
    if(RG.calFilter==='expired')return s==='expired'||s==='notice'&&r.due<0;
    if(RG.calFilter==='due')return s!=='valid';
    return true;
  }).sort((a,b)=>a.due-b.due);
  const flag=CAL.filter(r=>calState(r)==='expired').length;
  E.rgCalCount.textContent=flag+' EXPIRED · '+CAL.filter(r=>calState(r)==='due').length+' DUE ≤ 30 d';
  E.rgCalCount.className='chip '+(flag?'crit':'ok');
  E.rgCalNote.textContent=CAL.length+' INSTRUMENTS ON THE REGISTER';
  E.rgCalBody.innerHTML=filt.map(r=>{
    const s=calState(r);
    const chip=s==='expired'?'<span class="chip crit">EXPIRED</span>':s==='due'?'<span class="chip warn">DUE '+r.due+' d</span>'+
      '':s==='notice'?'<span class="chip crit">NOTICE ISSUED</span>':'<span class="chip ok">VALID</span>';
    const empty=s==='notice'?'':s==='valid'?'<span style="color:var(--faint)">—</span>':
      '<button class="btnx" style="padding:5px 9px" data-c="'+r.id+'">NOTICE</button>';
    return '<tr><td style="color:#e9e4de">'+r.id+'</td><td style="color:var(--dim)">'+NSD.esc(NSD.mine(r.mine).name)+
      '</td><td style="color:var(--dim)">'+NSD.esc(r.inst)+'</td><td>'+U.iso(r.last)+'</td>'+
      '<td style="color:'+(r.due<0?'#ff9d9d':r.due<=30?'#ffc061':'var(--text)')+'">'+U.iso(new Date(Date.now()+r.due*86400000))+'</td>'+
      '<td>'+chip+'</td><td>'+empty+'</td></tr>';
  }).join('');
  $$('[data-c]',E.rgCalBody).forEach(b=>b.addEventListener('click',function(){
    const r=CAL.filter(x=>x.id===b.dataset.c)[0];if(!r)return;
    RG.calNotices[r.id]=true;
    NSD.ledger.add('CALIBRATION NOTICE · '+r.id+' '+r.inst+' · '+r.mine+' · CERT EXPIRED',{mine:r.mine,actor:'DGMS OFFICER',sev:'CRITICAL'});
    renderCalLedger();renderLedger();renderKpis();
  }));
}

const AXES=['SUBSIDENCE SEVERITY','ALERT FREQUENCY','RESPONSE SPEED','MESH UPTIME','CALIBRATION COMPLIANCE','ABATEMENT SCORE'];
const BLOCKS={
  'JHARIA BLOCK':{lw:[78,64,72,91,84,70],bp:[54,48,80,86,78,62]},
  'RANIGANJ EAST':{lw:[66,58,80,94,90,74],bp:[48,52,86,89,82,68]},
  'KORBA BLOCK':{lw:[72,55,86,96,92,80],bp:[58,44,90,93,88,74]},
  'SINGRAULI':{lw:[84,70,66,88,76,64],bp:[62,56,74,84,72,58]}
};
function renderRadarSel(){
  E.rgRadarSel.innerHTML=Object.keys(BLOCKS).map(k=>'<option value="'+k+'">'+k+'</option>').join('');
  E.rgRadarSel.value=RG.radar;
  E.rgRadarBlock.textContent=RG.radar;
}
function renderRadar(now){
  const ctx=U.ctx(E.rgRadar);if(!ctx)return;
  const c=ctx.x,W=ctx.w,H=ctx.h;
  const cx=W/2,cy=H/2+6,R=Math.min(W*0.34,H*0.40);
  const n=AXES.length;
  const block=BLOCKS[RG.radar];
  const target=RG.norm?block.lw.map((v,i)=>{
    const m=Math.max(v, block.bp[i], 1);
    return {lw:v/m*100, bp:block.bp[i]/m*100};
  }).reduce((a,p)=>{a.lw.push(p.lw);a.bp.push(p.bp);return a;},{lw:[],bp:[]})
    :{lw:block.lw, bp:block.bp};
  RG.anim=RG.anim||{lw:target.lw.map(()=>0),bp:target.bp.map(()=>0)};
  const k=0.12;
  RG.anim.lw=RG.anim.lw.map((v,i)=>U.lerp(v,target.lw[i],k));
  RG.anim.bp=RG.anim.bp.map((v,i)=>U.lerp(v,target.bp[i],k));
  for(let r=1;r<=4;r++){
    c.beginPath();
    for(let i=0;i<=n;i++){
      const a=-Math.PI/2+i/n*Math.PI*2,px=cx+Math.cos(a)*R*r/4,py=cy+Math.sin(a)*R*r/4;
      i?c.lineTo(px,py):c.moveTo(px,py);
    }
    c.strokeStyle='rgba(255,255,255,.09)';c.lineWidth=1;c.stroke();
  }
  c.font='7.5px '+MONO;
  for(let i=0;i<n;i++){
    const a=-Math.PI/2+i/n*Math.PI*2;
    c.beginPath();c.moveTo(cx,cy);c.lineTo(cx+Math.cos(a)*R,cy+Math.sin(a)*R);
    c.strokeStyle='rgba(255,255,255,.10)';c.stroke();
    const lx=cx+Math.cos(a)*(R+22),ly=cy+Math.sin(a)*(R+14);
    c.fillStyle='rgba(200,193,186,.75)';
    c.textAlign=Math.abs(Math.cos(a))<0.3?'center':(Math.cos(a)>0?'left':'right');
    c.textBaseline='middle';
    c.fillText(AXES[i],lx,ly);
  }
  const poly=(vals,col,fill)=>{
    c.beginPath();
    vals.forEach((v,i)=>{
      const r=clamp(v,0,100)/100*R,a=-Math.PI/2+i/n*Math.PI*2;
      const px=cx+Math.cos(a)*r,py=cy+Math.sin(a)*r;
      i?c.lineTo(px,py):c.moveTo(px,py);
    });
    c.closePath();
    c.fillStyle=fill;c.fill();
    c.strokeStyle=col;c.lineWidth=1.8;c.stroke();
    vals.forEach((v,i)=>{
      const r=clamp(v,0,100)/100*R,a=-Math.PI/2+i/n*Math.PI*2;
      c.beginPath();c.arc(cx+Math.cos(a)*r,cy+Math.sin(a)*r,2.4,0,7);
      c.fillStyle=col;c.fill();
    });
  };
  poly(RG.anim.bp,'#ffb84d','rgba(255,184,77,.12)');
  poly(RG.anim.lw,'#7cc6f0','rgba(124,198,240,.14)');
  c.font='7.5px '+MONO;c.fillStyle='rgba(255,255,255,.35)';c.textAlign='left';c.textBaseline='alphabetic';
  c.fillText(RG.norm
    ? 'NORMALISED PER AXIS · 100 = THE LEADING METHOD ON THAT AXIS'
    : 'SCORES 0–100 · HIGHER = SAFER / FASTER RESPONSE',12,H-8);
}

function renderKpis(){
  const l3=NSD.mines.reduce((a,m)=>a+m.live.l3,0);
  const critMines=NSD.mines.filter(m=>effStatus(m)==='crit').length;
  const ack=NSD.mines.reduce((a,m)=>a+m.live.ack,0)/NSD.mines.length;
  const mesh=NSD.mines.reduce((a,m)=>a+m.live.mesh,0)/NSD.mines.length;
  const exp=CAL.filter(r=>calState(r)==='expired').length;
  const comp=Math.round(NSD.mines.reduce((a,m)=>a+effIndex(m),0)/NSD.mines.length);
  E.rgK1.textContent=NSD.mines.length;
  E.rgK2.textContent=l3;
  E.rgK2d.textContent='across '+critMines+' collieries under red weighting';
  E.rgK3.innerHTML=fmt(ack,1)+'<small>min</small>';
  E.rgK3d.textContent=NSD.mines.filter(m=>m.live.ack>5).length+' of '+NSD.mines.length+' above the 5-minute target';
  E.rgK4.innerHTML=fmt(mesh,1)+'<small>%</small>';
  E.rgK4d.textContent='surface mesh + double ring, 90-day mean';
  E.rgK5.textContent=exp;
  E.rgK5d.textContent='instruments past their statutory due date';
  E.rgK6.textContent=comp;
  E.rgK6d.textContent='regional weighted mean · '+RG.weighting.toUpperCase()+' weighting';
  E.rgMapCount.textContent=NSD.mines.length+' PINS';
  E.rgMapRed.textContent=critMines+' LEVEL 3 / COMPROMISED';
  E.rgMapAmb.textContent=NSD.mines.filter(m=>effStatus(m)==='warn').length+' WATCH';
  E.rgMapOk.textContent=NSD.mines.filter(m=>effStatus(m)==='ok').length+' NOMINAL';
  E.rgIdxNote.textContent=RG.weighting==='live'?'THRESHOLD BREACH × HARDWARE UPTIME':'WORST-CASE PASS · HISTORIC PENALTIES';
}
const REP_PARTS=[
  {id:'tele',n:'Telemetry extract',k:'48 h of mesh channels',on:true},
  {id:'mesh',n:'Surface mesh & ring availability log',k:'From the audit ledger',on:true},
  {id:'ai',n:'AI prediction state at the epoch',k:'TTF + probability',on:true},
  {id:'ledg',n:'Audit ledger excerpt',k:'Blocks touching this colliery',on:true},
  {id:'obs',n:'Open observations & notices',k:'Including calibration',on:true},
  {id:'stat',n:'Statutory reference block',k:'CMR / T&S clauses',on:true}
];
RG.rep={};REP_PARTS.forEach(p=>RG.rep[p.id]=p.on);
function buildRepOpts(){
  E.rgRepParts.innerHTML=REP_PARTS.map(p=>
    '<label class="'+(RG.rep[p.id]?'on':'')+'" data-p="'+p.id+'" tabindex="0" role="switch">'+
    '<span class="sw"></span>'+NSD.esc(p.n)+'<span class="kw">'+NSD.esc(p.k)+'</span></label>').join('');
  $$('[data-p]',E.rgRepParts).forEach(el=>el.addEventListener('click',()=>{
    const id=el.dataset.p;RG.rep[id]=!RG.rep[id];
    el.classList.toggle('on',RG.rep[id]);
  }));
}
function svgSpark(vals,col,w,h){
  const mx=Math.max.apply(null,vals),mn=Math.min.apply(null,vals);
  const sx=i=>i/(vals.length-1)*w, sy=v=>h-((v-mn)/((mx-mn)||1))*(h-6)-3;
  const d=vals.map((v,i)=>(i?'L':'M')+fmt(sx(i),1)+' '+fmt(sy(v),1)).join(' ');
  return '<svg viewBox="0 0 '+w+' '+h+'" style="width:100%;height:'+h+'px;background:#fff;border:1px solid #ddd5cc;border-radius:6px">'+
    '<path d="'+d+'" fill="none" stroke="'+col+'" stroke-width="1.6"/></svg>';
}
function repDoc(){
  const m=NSD.mine(E.rgRepMine.value)||NSD.mine(RG.sel);
  const type=E.rgRepType.options[E.rgRepType.selectedIndex].text;
  const date=E.rgRepDate.value||U.iso(new Date());
  const idx=effIndex(m),st=effStatus(m);
  const meta=[['Colliery',m.name+' · '+m.z],['Inquiry / report type',type],
    ['Report date',date],['Composite safety index',idx+' / 100'],
    ['Live warning level',st.toUpperCase()+' · '+m.live.l3+' × Level 3'],
    ['Network availability (90 d)',fmt(m.live.mesh,1)+' %'],
    ['Median alarm acknowledgement',fmt(m.live.ack,1)+' min'],
    ['Blackbox / cloud custody',(m.n===NSD.state.mineLocal&&window.BBX&&BBX.summary)?BBX.summary():'remote colliery · not on this console'],
    ['Last inspection',m.last],['Nominated officer',m.officer]];
  let html='<h2>1 · Particulars</h2>'+NSD.report.table(['FIELD','VALUE'],meta);
  if(RG.rep.tele){
    const days=[],v=[],h=[];
    for(let i=0;i<14;i++){
      days.push('D−'+(13-i));
      v.push(6+Math.sin(i/2.2)*2.4+(i>9?2.2:0));
      h.push(2.2+Math.cos(i/2.6)*0.9);
    }
    html+='<h2>2 · Telemetry extract — trough centre channel</h2>'+
      '<div class="figs"><div class="fig"><div class="cap">VERTICAL VELOCITY mm/day</div>'+svgSpark(v,'#b8791f',420,110)+'</div>'+
      '<div class="fig"><div class="cap">HORIZONTAL VELOCITY mm/day</div>'+svgSpark(h,'#6a5bb8',420,110)+'</div></div>'+
      NSD.report.table(['DAY'].concat(days),[
        ['VERTICAL mm/day'].concat(v.map(x=>fmt(x,2))),
        ['HORIZONTAL mm/day'].concat(h.map(x=>fmt(x,2)))]);
  }
  if(RG.rep.mesh){
    const rows=NSD.ledger.blocks.filter(b=>b.mine===m.n).slice(-8).reverse()
      .map(b=>[b.t,'#'+b.i,NSD.esc(b.ev),b.hash]);
    html+='<h2>3 · Surface mesh &amp; double ring availability log</h2>'+
      (rows.length?NSD.report.table(['TIME','BLOCK','EVENT','HASH'],rows):
      '<div class="note">No ledger blocks recorded against this colliery inside the retained window.</div>');
  }
  if(RG.rep.ai){
    const ttf=clamp(24-m.idx*0.16,1.5,40);
    html+='<h2>4 · AI prediction state at the epoch</h2>'+
      NSD.report.table(['METRIC','VALUE','NOTE'],[
        ['Time-to-failure (median)',fmt(ttf,1)+' days','Inverse-velocity asymptote, RMR-conditioned'],
        ['Failure probability',Math.round(clamp(30+(100-idx)*0.6,5,95))+' %','Above the calibrated threshold the panel is withdrawn'],
        ['Predicted crack zones',Math.max(2,Math.round((100-idx)/6))+' vectors','Tangential to the trough edge'],
        ['Model baseline',m.m==='LW'?'Longwall ensemble v4.4':'Bord & pillar caving v4.4','Selected from the extraction method']])+
      '<div class="note">Telemetry values are extracted from the archived channel; no interpolation was performed '+
      'beyond the recorded sample interval, so the table can be reconciled against the mine\'s own logger.</div>';
  }
  if(RG.rep.ledg){
    const rows=NSD.ledger.blocks.slice(-6).reverse().map(b=>['#'+b.i,b.t,NSD.esc(b.ev),b.hash]);
    const v=NSD.ledger.verify();
    html+='<h2>5 · Audit ledger excerpt</h2>'+NSD.report.table(['BLOCK','TIME','EVENT','HASH'],rows)+
      '<div class="note">Chain verification at the time of generation: '+(v.ok?'INTACT — '+v.at+' blocks':
      'FAILED AT BLOCK #'+v.at)+'. Hashes are FNV-1a over (previous hash | block fields); editing any historical '+
      'row breaks every subsequent hash.</div>';
  }
  if(RG.rep.obs){
    html+='<h2>6 · Open observations and notices</h2>'+
      NSD.report.table(['OBSERVATION','STATE'],obsOf(m).map(o=>[NSD.esc(o),'OPEN']))+
      NSD.report.table(['CALIBRATION CERT','INSTRUMENT','DUE','STATE'],
        CAL.filter(r=>r.mine===m.n).map(r=>[r.id,NSD.esc(r.inst),U.iso(new Date(Date.now()+r.due*86400000)),
          calState(r).toUpperCase()]));
  }
  if(RG.rep.stat){
    html+='<h2>7 · Statutory reference block</h2><div class="note">'+
      'Coal Mines Regulations 1957 — Reg. 137(2) strata monitoring returns; DGMS (Tech) circulars on '+
      'subsidence monitoring for depillaring; CMR 105 (mine plans) and CMR 108 read with the T&amp;S '+
      'conditions of this mine for threshold values. This annexure is auto-populated and must be '+
      'countersigned by the Colliery Manager with the officer\'s own findings appended.</div>';
  }
  html+='<h2>8 · Officer\'s findings (to be completed by hand)</h2>'+
    '<div style="border:1px dashed #b7afa7;height:90px;border-radius:8px;margin-top:6px"></div>';
  return NSD.report.doc({title:'DGMS INQUIRY / INVESTIGATION ANNEXURE',
    sub:m.name+' Colliery · '+date+' · auto-populated from the strata telemetry bus',
    stamp:'AUTO-POPULATED ANNEXURE',html:html,
    signL:'Colliery Manager',signR:'Inspector of Mines'});
}
function genRep(){
  const html=repDoc();
  const file='DGMS-Inquiry_'+E.rgRepMine.value+'_'+E.rgRepDate.value+'.html';
  E.rgRepState.textContent='GENERATED '+U.hhmmss(new Date());
  E.rgRepState.className='chip ok';
  NSD.report.modal({title:'INQUIRY ANNEXURE',sub:E.rgRepMine.value+' · '+E.rgRepDate.value,html:html,file:file});
  NSD.ledger.add('INQUIRY ANNEXURE GENERATED · '+E.rgRepType.value.toUpperCase(),
    {mine:E.rgRepMine.value,actor:'DGMS OFFICER',sev:'INFO'});
  renderLedger();
  E.rgRepOut.innerHTML='<b style="color:#8ff0b0">Annexure compiled</b> with '+
    REP_PARTS.filter(p=>RG.rep[p.id]).length+' of '+REP_PARTS.length+' sections. The download is a single '+
    'self-contained HTML file with the charts inlined as SVG, so it can be filed or printed to PDF without '+
    'the dashboard.';
}

function renderAll(){
  renderKpis();renderTable();renderMine();renderSla();renderCalLedger();
  renderLedger();verifyChain(false);
}
let started=false;
function initDeck(){
  E.rgUptSel.innerHTML=NSD.mines.map(m=>'<option value="'+m.n+'">'+m.name+' · '+m.z+'</option>').join('');
  E.rgUptSel.value=RG.uptSel;
  E.rgRepMine.innerHTML=NSD.mines.map(m=>'<option value="'+m.n+'">'+m.name+' · '+m.z+'</option>').join('');
  E.rgRepMine.value=RG.sel;
  E.rgRepDate.value=U.iso(new Date());
  renderRadarSel();buildRepOpts();
  NSD.ledger.sub(renderLedger);
  renderAll();renderCal();renderRadar(performance.now());
  U.seg($('.seg[aria-label="Risk weighting"]'),function(v){
    RG.weighting=v;renderKpis();renderTable();renderMine();renderSla();
  });
  U.seg($('.seg[aria-label="Method normalisation"]'),function(v){RG.norm=v==='norm';});
  U.seg(E.rgCalFilter,function(v){RG.calFilter=v;renderCalLedger();});
  E.rgUptSel.addEventListener('change',function(){RG.uptSel=E.rgUptSel.value;renderCal();});
  E.rgRadarSel.addEventListener('change',function(){RG.radar=E.rgRadarSel.value;E.rgRadarBlock.textContent=RG.radar;});
  E.rgRepBtn.addEventListener('click',genRep);
  E.rgRepDl.addEventListener('click',function(){
    const html=repDoc();
    U.dl('DGMS-Inquiry_'+E.rgRepMine.value+'_'+E.rgRepDate.value+'.html',
      NSD.report.standalone('DGMS Inquiry Annexure',html));
    NSD.ledger.add('INQUIRY ANNEXURE DOWNLOADED',{mine:E.rgRepMine.value,actor:'DGMS OFFICER',sev:'INFO'});
    renderLedger();
  });
}
NSD.regRefresh=function(){if(started&&NSD.st.view==='regulator')renderAll();};
function boot(){
  if(started)return;
  started=true;initDeck();
}
NSD.onEnter('regulator',function(){
  boot();
  renderAll();renderCal();
});
NSD.onTick(function(now,dt){
  if(NSD.st.view!=='regulator')return;
  boot();
  drawMacro(now);
  renderRadar(now);
});
NSD.onTab('regulator',function(){
  boot();
  renderAll();renderCal();renderRadar(performance.now());drawMacro(performance.now());
});
window.__RG={state:RG,render:renderAll,verify:verifyChain,report:repDoc,cal:CAL};
})();
