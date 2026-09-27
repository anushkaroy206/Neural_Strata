(function(){
'use strict';
const NSD=window.NSD;if(!NSD)return;
const U=NSD.U,$=U.$,$$=U.$$,clamp=U.clamp,fmt=U.fmt,rgba=U.rgba;
const MONO=U.MONO;

const E={};
['plK1','plK1d','plK2','plK2d','plK3','plK3d','plK4','plK4d','plK5','plK5d','plK6','plK6d',
 'plMap','plMapWrap','plGrid','plReadout','plHeat0','plHeat1','plHeat2','plHeat3','plHeat4','plMapEpoch','plMapZoom',
 'plLayers','plLayN','plLayAll','plLayUg','plStrataPanel','plModelTag','plRmr','plRmrV','plRmrClass','plFailTh',
 'plCritV','plTwf','plSmax','plMethod','plOb','plEh','plStrata','plBhCol','plSf','plDraw','plInfl',
 'plTtf','plTtfCi','plTtfP','plTtfChart','plClN','plClShare','plClSil','plScatter','plClusterBtn','plClusterState','plClusterIter',
 'plVelChart','plVelSeg','plVelPeak','plVelTrend','plScrub','plScrubDate','plScrubPlay','plScrubState','plScrubDepth','plScrubTag','plProf',
 'plSmpOpts','plSmpBtn','plSmpPreview','plSmpOut','plMapReset','plMapZoomIn','plMapZoomOut','plAlarm'].forEach(id=>{E[id]=document.getElementById(id);});
if(!E.plMap)return;

const WORLD={w:1000,h:700};
const P3={x:300,y:250,w:340,h:220,cx:470,cy:360};
const P2={x:660,y:280,w:260,h:180,cx:790,cy:370};
const BARRIER={x:640,y:230,w:20,h:260};
const VILLAGES=[
  {n:'BARWADIH',x:196,y:176,r:52,p:null},
  {n:'CHHOTA BASTI',x:566,y:148,r:38,p:null},
  {n:'RAMPUR',x:782,y:566,r:56,p:null}
];
const BORES=[
  {id:'BH-1',x:252,y:330,d:118},{id:'BH-2',x:470,y:206,d:142},
  {id:'BH-3',x:700,y:392,d:96},{id:'BH-4',x:842,y:524,d:168}
];
const LAYERS=[
  {id:'contours',n:'Surface topography contours',k:'5 m interval',on:true},
  {id:'villages',n:'Villages & habitation',k:'3 settlements',on:true},
  {id:'ht',n:'High-tension lines',k:'132 kV double circuit',on:true},
  {id:'forest',n:'Forest boundary',k:'Reserved forest block',on:true},
  {id:'road',n:'Haul road & rail siding',k:'2 corridors',on:false},
  {id:'pillars',n:'Underground pillars',k:'Bord & pillar layout',on:true},
  {id:'barrier',n:'Barrier pillar & goaf edge',k:'Statutory barrier',on:true},
  {id:'heat',n:'Subsidence trough heatmap',k:'AI mesh interpolation',on:true},
  {id:'cracks',n:'Tension crack vectors',k:'Predicted tensile zones',on:true},
  {id:'bh',n:'Borehole collars',k:'4 logged holes',on:false}
];
const PL={
  rmr:44, method:'caving', ob:120, eh:3.0, strata:'sandstone',
  win:12, scrub:0, playing:false, vel:'d', view:{k:1,tx:0,ty:0}, drag:null,
  cursor:null, field:null, cracks:[], off:null, ov:null, cont:[], offDirty:true, lastScrub:0,
  cl:{pts:[],cent:[],iter:0,share:0,sil:0,running:false}, d:null
};
PL.layers={};LAYERS.forEach(l=>PL.layers[l.id]=l.on);

const TOPO_LINES=[],FOREST=[];
(function seedFeatures(){
  let s=4242;
  const rnd=()=>{s=(Math.imul(s,1664525)+1013904223)>>>0;return s/4294967296;};
  for(let l=0;l<5;l++){
    const cx=170+rnd()*660,cy=120+rnd()*440,rr=70+rnd()*90,pts=[];
    for(let a=0;a<=44;a++){
      const t=a/44*Math.PI*2,k=1+0.16*Math.sin(t*3+l)+0.09*Math.sin(t*5-l*2);
      pts.push([cx+Math.cos(t)*rr*1.25*k,cy+Math.sin(t)*rr*0.8*k]);
    }
    TOPO_LINES.push({el:100+l*5-10,pts:pts});
  }
  const fp=[[40,60],[210,36],[330,74],[300,168],[150,196],[52,150]];
  fp.forEach(p=>FOREST.push(p));
  VILLAGES.forEach(v=>{
    const pts=[];
    for(let a=0;a<=26;a++){
      const t=a/26*Math.PI*2,k=1+0.2*Math.sin(t*4+v.x)+0.12*Math.cos(t*3);
      pts.push([v.x+Math.cos(t)*v.r*1.3*k,v.y+Math.sin(t)*v.r*0.9*k]);
    }
    v.p=pts;
  });
})();
const HT=[[70,600],[240,500],[410,420],[580,320],[760,236],[930,150]];
const ROAD=[[60,150],[260,250],[430,330],[600,430],[790,540],[950,640]];

/** Four surface nodes, far apart on the colliery, not one panel per level.
 *  Each footprint follows only that node's own warning or critical. */
const NODE_GROUNDS=[
  {node:'node_1',name:'NODE 1',where:'NORTH-WEST CREST · ABOVE TUNNEL A',panel:{x:48,y:58,w:168,h:118}},
  {node:'node_2',name:'NODE 2',where:'NORTH-EAST CREST · ABOVE TUNNEL A',panel:{x:790,y:48,w:168,h:118}},
  {node:'node_3',name:'NODE 3',where:'SOUTH-WEST SLOPE · ABOVE TUNNEL B',panel:{x:48,y:528,w:168,h:118}},
  {node:'node_4',name:'NODE 4',where:'SOUTH-EAST SLOPE · ABOVE TUNNEL B',panel:{x:790,y:528,w:168,h:118}}
];
function liveAlarmAreas(){
  const live=window.__NS&&window.__NS.data;
  return NODE_GROUNDS.map(s=>{
    const status=String(live&&live.nodes&&live.nodes[s.node]&&live.nodes[s.node].status||'NOMINAL').toUpperCase();
    if(status!=='WARNING'&&status!=='CRITICAL')return null;
    return Object.assign({status:status,nodes:[s.node]},s);
  }).filter(Boolean);
}
function paintNodeGrounds(c,tf){
  NODE_GROUNDS.forEach(s=>{
    const p=s.panel;
    c.save();
    c.setLineDash([5,4]);
    c.strokeStyle='rgba(180,210,255,.55)';
    c.lineWidth=1.2;
    c.strokeRect(tf.X(p.x),tf.Y(p.y),p.w*PL.view.k,p.h*PL.view.k);
    c.setLineDash([]);
    c.fillStyle='rgba(200,220,255,.85)';
    c.font='700 10px '+MONO;
    c.textAlign='left';
    c.fillText(s.name,tf.X(p.x)+6,tf.Y(p.y)+14);
    c.font='8px '+MONO;
    c.fillStyle='rgba(180,200,230,.7)';
    c.fillText(s.where,tf.X(p.x)+6,tf.Y(p.y)+26);
    c.restore();
  });
}
function alarmHits(x,y,area){
  const p=area.panel,pad=80;
  return x>=p.x-pad&&x<=p.x+p.w+pad&&y>=p.y-pad&&y<=p.y+p.h+pad;
}

const STRATA_NOTE={sandstone:'Massive sandstone roof — strong bridging, wide slow trough, low immediate convergence.',
  shale:'Laminated shale roof — weak bedding planes, faster immediate convergence and steeper trough edge.',
  inter:'Interbedded sandstone / shale — mixed caving behaviour; two-stage trough development expected.'};
function derive(){
  const strataF={sandstone:1.0,shale:1.16,inter:1.07}[PL.strata];
  const base=PL.method==='caving'?152:46;
  const Smax=Math.round(clamp(base*(1+(50-PL.rmr)/120)*strataF*Math.pow(PL.eh/3,0.9)*Math.pow(PL.ob/120,0.35),18,290));
  const a=+( (PL.method==='caving'?0.62:0.24)*(1+(50-PL.rmr)/150) ).toFixed(3);
  const draw=+clamp(26+PL.ob*0.045+(PL.rmr-40)*0.10,12,55).toFixed(1);
  const infl=Math.max(40,Math.round(PL.ob/Math.tan(draw*Math.PI/180)));
  const failTh=Math.round(clamp(92-PL.rmr*1.05+(PL.method==='caving'?8:0)+(PL.ob-120)*0.02,28,92));
  const critV=+(clamp(24-PL.rmr*0.22+(PL.ob-120)*0.02,3.5,24)).toFixed(1);
  const twf=+(0.62+(PL.ob/240)*0.22+(PL.method==='caving'?0.04:0.10)).toFixed(2);
  const prog=PL.method==='caving'?0.62:0.34;
  const now=Smax*prog;
  const vel=critV*0.45*(PL.method==='caving'?1:0.22)*(1+(50-PL.rmr)/200);
  const ttf=clamp((Smax-now)/Math.max(0.4,vel),2,120);
  const P=Math.round(clamp(failTh*(0.72+0.28*(vel/(critV*0.45)))*(PL.method==='caving'?1:0.55),4,97));
  const foot=+(Math.PI*Math.pow(infl+120,2)/10000*0.38).toFixed(1);
  const cracks=Math.round(clamp(6+ (60-PL.rmr)*0.35 + (PL.method==='caving'?3:0),4,26));
  PL.d={Smax:Smax,a:a,draw:draw,infl:infl,failTh:failTh,critV:critV,twf:twf,now:now,vel:vel,ttf:ttf,P:P,foot:foot,cracks:cracks,prog:prog};
  return PL.d;
}
function smaxAt(daysBack){
  const d=PL.d;
  const p=clamp(d.prog-(daysBack/360)*0.34,0.06,1);
  const winF=PL.win/12;
  return d.Smax*p*(0.62+0.38*winF);
}
function fieldAt(x,y){
  const d=PL.d;
  const sm=smaxAt(PL.scrub);
  const dx=Math.max(P3.x-x,0,x-(P3.x+P3.w));
  const dy=Math.max(P3.y-y,0,y-(P3.y+P3.h));
  const dist=Math.hypot(dx,dy);
  const w=Math.max(1,d.infl*1.15);
  let s=sm*Math.exp(-Math.pow(dist/w,1.7));
  const dx2=Math.max(P2.x-x,0,x-(P2.x+P2.w));
  const dy2=Math.max(P2.y-y,0,y-(P2.y+P2.h));
  const d2=Math.hypot(dx2,dy2);
  s+=sm*0.82*Math.exp(-Math.pow(d2/(w*0.95),1.7));
  s*=1+0.05*Math.sin(x/140)*Math.cos(y/170);
  return s;
}
function overburdenAt(x,y){return Math.round(PL.ob+22*Math.sin(x/230)+14*Math.cos(y/190));}

const GW=176,GH=124;
function recompute(){
  const d=PL.d;
  const field=new Float32Array(GW*GH);
  let mx=0.001;
  for(let j=0;j<GH;j++)for(let i=0;i<GW;i++){
    const x=i/(GW-1)*WORLD.w,y=j/(GH-1)*WORLD.h;
    let v=fieldAt(x,y);
    if(!Number.isFinite(v))v=0;
    field[j*GW+i]=v;
    if(v>mx)mx=v;
  }
  PL.field={f:field,max:mx};
  if(!PL.off){PL.off=document.createElement('canvas');}
  PL.off.width=GW;PL.off.height=GH;
  const oc=PL.off.getContext('2d');
  const img=oc.createImageData(GW,GH);
  const ramp=[[27,58,77],[45,127,143],[126,201,143],[232,215,106],[255,154,60],[255,77,77]];
  for(let p=0;p<GW*GH;p++){
    const t=clamp(field[p]/mx,0,1);
    const seg=clamp(t*(ramp.length-1),0,ramp.length-1),i0=Math.min(ramp.length-2,Math.floor(seg)),f=seg-i0;
    const c0=ramp[i0],c1=ramp[i0+1];
    img.data[p*4]=Math.round(U.lerp(c0[0],c1[0],f));
    img.data[p*4+1]=Math.round(U.lerp(c0[1],c1[1],f));
    img.data[p*4+2]=Math.round(U.lerp(c0[2],c1[2],f));
    img.data[p*4+3]=Math.round(clamp(t*1.25,0,1)*205);
  }
  oc.putImageData(img,0,0);
  const levels=[];
  const step=mx>160?50:mx>80?25:10;
  for(let L=step;L<mx*0.98;L+=step)levels.push(L);
  PL.cont=levels.map(L=>({L:L,segs:isoSegs(field,L)}));
  PL.contMax=mx;
  const cracks=[];
  for(let a=0;a<Math.PI*2;a+=Math.PI/2/Math.max(1,Math.round(d.cracks/4))){
    for(const pan of [P3,P2]){
      const rr=[];
      for(let r=40;r<d.infl*1.5;r+=14){
        const x=pan.cx+Math.cos(a)*r,y=pan.cy+Math.sin(a)*r;
        rr.push({x:x,y:y,v:fieldAt(x,y)});
      }
      let best=null;
      for(let i=1;i<rr.length-1;i++){
        const k=rr[i-1].v-2*rr[i].v+rr[i+1].v;
        if(best===null||k<best.k)best={k:k,i:i};
      }
      if(best&&rr[best.i].v>8&&rr[best.i].v<d.Smax*0.75){
        const p0=rr[best.i],p1=rr[Math.min(best.i+1,rr.length-1)];
        const gx=fieldAt(p0.x+10,p0.y)-fieldAt(p0.x-10,p0.y);
        const gy=fieldAt(p0.x,p0.y+10)-fieldAt(p0.x,p0.y-10);
        const gl=Math.hypot(gx,gy)||1;
        const tx=-gy/gl,ty=gx/gl;
        const len=34+Math.abs(best.k)*1.2;
        cracks.push({x:p0.x,y:p0.y,tx:tx,ty:ty,len:clamp(len,22,78),s:p0.v});
      }
    }
  }
  PL.cracks=cracks.slice(0,Math.max(4,d.cracks));
  buildOverlay();
  const round5=v=>Math.round(v/5)*5;
  E.plHeat0.textContent='0';E.plHeat1.textContent=round5(mx*0.25);
  E.plHeat2.textContent=round5(mx*0.5);E.plHeat3.textContent=round5(mx*0.75);
  E.plHeat4.textContent=Math.round(mx)+'+';
  E.plK6.textContent=PL.cracks.length;
  E.plK6d.textContent='predicted surface tensile zones';
  E.plK5.innerHTML=(Math.PI*Math.pow(d.infl+120,2)/10000*0.38).toFixed(1)+'<small>ha</small>';
  PL.offDirty=false;
}
function buildOverlay(){
  if(!PL.ov){PL.ov=document.createElement('canvas');PL.ov.width=WORLD.w;PL.ov.height=WORLD.h;}
  const c=PL.ov.getContext('2d');
  c.clearRect(0,0,WORLD.w,WORLD.h);
  c.imageSmoothingEnabled=true;
  c.globalAlpha=0.82;
  c.drawImage(PL.off,0,0,WORLD.w,WORLD.h);
  c.globalAlpha=1;
  (PL.cont||[]).forEach((cl,idx)=>{
    const last=idx===(PL.cont.length-1);
    c.strokeStyle=last?'rgba(255,255,255,.5)':'rgba(255,255,255,.20)';
    c.lineWidth=last?1.2:0.9;
    c.beginPath();
    cl.segs.forEach(s=>{
      c.moveTo(GX(s[0][0]),GY(s[0][1]));
      c.lineTo(GX(s[1][0]),GY(s[1][1]));
    });
    c.stroke();
    if(last&&cl.segs.length){
      const s=cl.segs[Math.floor(cl.segs.length*0.22)];
      c.fillStyle='rgba(255,255,255,.7)';
      c.font='600 9px '+MONO;
      c.fillText(cl.L+' mm',GX(s[0][0])+4,GY(s[0][1])-3);
    }
  });
}
function isoSegs(f,level){
  const segs=[];
  const at=(i,j)=>f[j*GW+i];
  for(let j=0;j<GH-1;j++)for(let i=0;i<GW-1;i++){
    const a=at(i,j),b=at(i+1,j),c=at(i+1,j+1),d=at(i,j+1);
    const pts=[];
    const ip=(v1,v2,x1,y1,x2,y2)=>{
      const t=(level-v1)/((v2-v1)||1e-6);
      return [x1+(x2-x1)*t,y1+(y2-y1)*t];
    };
    if((a<level)!==(b<level))pts.push(ip(a,b,i,j,i+1,j));
    if((b<level)!==(c<level))pts.push(ip(b,c,i+1,j,i+1,j+1));
    if((c<level)!==(d<level))pts.push(ip(c,d,i+1,j+1,i,j+1));
    if((d<level)!==(a<level))pts.push(ip(d,a,i,j+1,i,j));
    if(pts.length===2)segs.push([pts[0],pts[1]]);
    else if(pts.length===4){segs.push([pts[0],pts[1]]);segs.push([pts[2],pts[3]]);}
  }
  return segs;
}
const GX=i=>i/(GW-1)*WORLD.w, GY=j=>j/(GH-1)*WORLD.h;

function viewTf(W,H){
  const v=PL.view;
  return {X:x=>((x-WORLD.w/2)*v.k)+W/2+v.tx,Y:y=>((y-WORLD.h/2)*v.k)+H/2+v.ty};
}
function invTf(W,H){
  const v=PL.view;
  return {X:sx=>((sx-W/2-v.tx)/v.k)+WORLD.w/2,Y:sy=>((sy-H/2-v.ty)/v.k)+WORLD.h/2};
}
function drawMap(){
  const ctx=U.ctx(E.plMap);if(!ctx)return;
  PL.alarms=liveAlarmAreas();
  const c=ctx.x,W=ctx.w,H=ctx.h,tf=viewTf(W,H);
  c.fillStyle='#0f0e0d';c.fillRect(0,0,W,H);
  c.strokeStyle='rgba(255,255,255,.045)';c.lineWidth=1;
  for(let x=0;x<=WORLD.w;x+=100){c.beginPath();c.moveTo(tf.X(x),tf.Y(0));c.lineTo(tf.X(x),tf.Y(WORLD.h));c.stroke();}
  for(let y=0;y<=WORLD.h;y+=100){c.beginPath();c.moveTo(tf.X(0),tf.Y(y));c.lineTo(tf.X(WORLD.w),tf.Y(y));c.stroke();}
  c.font='8px '+MONO;c.fillStyle='rgba(255,255,255,.16)';c.textAlign='left';
  for(let x=0;x<=WORLD.w;x+=200)c.fillText('E '+(86.0+x/1000*0.05).toFixed(3)+'°',tf.X(x)+4,14);
  for(let y=200;y<=WORLD.h;y+=200)c.fillText('N '+(23.8-y/1000*0.04).toFixed(3)+'°',6,tf.Y(y)-4);
  const path=(pts,close)=>{
    c.beginPath();
    pts.forEach((p,i)=>{const x=tf.X(p[0]),y=tf.Y(p[1]);i?c.lineTo(x,y):c.moveTo(x,y);});
    if(close)c.closePath();
  };
  if(PL.layers.contours){
    c.save();c.strokeStyle='rgba(255,255,255,.14)';c.lineWidth=1;
    TOPO_LINES.forEach((l,i)=>{
      path(l.pts,true);c.stroke();
      const p=l.pts[6];
      c.fillStyle='rgba(255,255,255,.28)';c.font='8px '+MONO;
      c.fillText('RL '+(l.el+120)+' m',tf.X(p[0])+3,tf.Y(p[1]));
    });
    c.restore();
  }
  if(PL.layers.forest){
    c.save();path(FOREST,true);
    c.fillStyle='rgba(74,222,128,.09)';c.fill();
    c.strokeStyle='rgba(74,222,128,.45)';c.lineWidth=1.2;c.setLineDash([6,4]);c.stroke();
    c.setLineDash([]);
    c.fillStyle='rgba(126,222,160,.8)';c.font='600 9px '+MONO;
    c.fillText('RESERVED FOREST BLOCK — 214 ha',tf.X(70),tf.Y(120));
    c.restore();
  }
  if(PL.layers.villages){
    VILLAGES.forEach(v=>{
      path(v.p,true);
      c.fillStyle='rgba(232,215,106,.10)';c.fill();
      c.strokeStyle='rgba(232,215,106,.5)';c.lineWidth=1.2;c.stroke();
      c.fillStyle='rgba(240,228,160,.9)';c.font='600 9.5px '+MONO;
      c.fillText(v.n,tf.X(v.x)-34,tf.Y(v.y));
      const sub=Math.round(fieldAt(v.x,v.y));
      const hit=(PL.alarms||[]).find(a=>alarmHits(v.x,v.y,a));
      c.font='8.5px '+MONO;c.fillStyle=hit?'#ffb4b4':'#9b938c';
      c.fillText((hit?hit.status+' AFFECTED · ':'')+'predicted '+sub+' mm · '+Math.round(overburdenAt(v.x,v.y))+' m cover',tf.X(v.x)-34,tf.Y(v.y)+11);
    });
  }
  if(PL.layers.ht){
    c.save();c.strokeStyle='rgba(255,149,56,.7)';c.lineWidth=1.4;c.setLineDash([10,6]);
    path(HT,false);c.stroke();c.setLineDash([]);
    HT.forEach((p,i)=>{
      c.beginPath();c.moveTo(tf.X(p[0]),tf.Y(p[1])-5);c.lineTo(tf.X(p[0])+4,tf.Y(p[1])+4);
      c.lineTo(tf.X(p[0])-4,tf.Y(p[1])+4);c.closePath();
      c.fillStyle='rgba(255,149,56,.85)';c.fill();
    });
    c.fillStyle='rgba(255,180,110,.85)';c.font='600 8.5px '+MONO;
    c.fillText('132 kV D/C — TOWER 41–46',tf.X(410)+6,tf.Y(420)-6);
    c.restore();
  }
  if(PL.layers.road){
    c.save();c.strokeStyle='rgba(200,190,180,.4)';c.lineWidth=3;path(ROAD,false);c.stroke();
    c.strokeStyle='rgba(15,14,13,.85)';c.lineWidth=1.2;c.setLineDash([7,7]);path(ROAD,false);c.stroke();
    c.setLineDash([]);c.restore();
  }
  if(PL.layers.heat&&PL.ov){
    c.save();
    c.imageSmoothingEnabled=true;
    c.drawImage(PL.ov,tf.X(0),tf.Y(0),WORLD.w*PL.view.k,WORLD.h*PL.view.k);
    c.restore();
  }
  if(PL.layers.pillars){
    c.save();
    const cols=13,rows=8,pw=17,gap=9;
    for(let r=0;r<rows;r++)for(let k=0;k<cols;k++){
      const x=P3.x+6+k*(pw+gap),y=P3.y+12+r*(pw+gap);
      c.fillStyle='rgba(74,222,128,.16)';
      c.strokeStyle='rgba(74,222,128,.42)';c.lineWidth=.9;
      c.fillRect(tf.X(x),tf.Y(y),pw*PL.view.k,pw*PL.view.k);
      c.strokeRect(tf.X(x),tf.Y(y),pw*PL.view.k,pw*PL.view.k);
    }
    const front=P3.x+P3.w*clamp(PL.d.prog+(1-PL.scrub/360)*0.08,0.05,1);
    c.fillStyle='rgba(255,77,77,.10)';
    c.fillRect(tf.X(P3.x),tf.Y(P3.y),Math.max(0,(front-P3.x))*PL.view.k,P3.h*PL.view.k);
    c.strokeStyle='rgba(255,77,77,.85)';c.lineWidth=2;c.setLineDash([9,6]);
    c.beginPath();c.moveTo(tf.X(front),tf.Y(P3.y));c.lineTo(tf.X(front),tf.Y(P3.y+P3.h));c.stroke();
    c.setLineDash([]);
    c.fillStyle='rgba(255,150,150,.9)';c.font='600 8.5px '+MONO;
    c.fillText('GOAF EDGE · EXTRACTION FRONT',tf.X(front)+7,tf.Y(P3.y)-6);
    c.strokeStyle='rgba(255,255,255,.12)';c.lineWidth=.8;
    for(let r=0;r<6;r++)for(let k=0;k<10;k++){
      const x=P2.x+10+k*24,y=P2.y+14+r*26;
      c.strokeRect(tf.X(x),tf.Y(y),10*PL.view.k,10*PL.view.k);
    }
    c.fillStyle='rgba(255,255,255,.4)';c.font='8px '+MONO;
    c.fillText('P-2 WORKED OUT (14 MONTHS AGO)',tf.X(P2.x),tf.Y(P2.y)-6);
    c.fillStyle='rgba(200,255,215,.8)';c.font='600 9px '+MONO;
    c.fillText('P-3 ACTIVE DEPILLARING',tf.X(P3.x),tf.Y(P3.y+P3.h)+14);
    c.restore();
  }
  if(PL.layers.barrier){
    c.save();
    c.fillStyle='rgba(255,255,255,.16)';
    c.fillRect(tf.X(BARRIER.x),tf.Y(BARRIER.y),BARRIER.w*PL.view.k,BARRIER.h*PL.view.k);
    c.strokeStyle='rgba(255,255,255,.6)';c.lineWidth=1.4;
    c.strokeRect(tf.X(BARRIER.x),tf.Y(BARRIER.y),BARRIER.w*PL.view.k,BARRIER.h*PL.view.k);
    c.save();
    c.translate(tf.X(BARRIER.x+BARRIER.w/2),tf.Y(BARRIER.y+BARRIER.h/2));
    c.rotate(-Math.PI/2);c.textAlign='center';
    c.fillStyle='rgba(255,255,255,.75)';c.font='600 8px '+MONO;
    c.fillText('BARRIER PILLAR · 60 m',0,0);c.restore();
    c.restore();
  }
  if(PL.layers.cracks){
    c.save();
    PL.cracks.forEach(k=>{
      const x1=k.x-k.tx*k.len/2,y1=k.y-k.ty*k.len/2,x2=k.x+k.tx*k.len/2,y2=k.y+k.ty*k.len/2;
      c.strokeStyle='rgba(196,166,255,.95)';c.lineWidth=2;c.lineCap='round';
      c.beginPath();c.moveTo(tf.X(x1),tf.Y(y1));c.lineTo(tf.X(x2),tf.Y(y2));c.stroke();
      c.strokeStyle='rgba(196,166,255,.5)';c.lineWidth=1;
      c.beginPath();c.moveTo(tf.X(x2),tf.Y(y2));
      c.lineTo(tf.X(x2+(-k.ty)*9*Math.sign(k.tx||1)),tf.Y(y2+(k.tx)*9*Math.sign(k.tx||1)));
      c.stroke();
    });
    c.fillStyle='rgba(214,196,255,.95)';c.font='600 8.5px '+MONO;
    c.fillText('TENSION CRACK VECTORS · '+PL.cracks.length+' PREDICTED ZONES',tf.X(360),tf.Y(500));
    c.restore();
  }
  if(PL.layers.bh){
    c.save();
    BORES.forEach(b=>{
      const x=tf.X(b.x),y=tf.Y(b.y);
      c.beginPath();c.moveTo(x,y-7);c.lineTo(x+6,y+5);c.lineTo(x-6,y+5);c.closePath();
      c.fillStyle='rgba(124,198,240,.85)';c.fill();
      c.strokeStyle='#bfe4fb';c.lineWidth=1;c.stroke();
      c.fillStyle='#bfe4fb';c.font='600 8.5px '+MONO;c.textAlign='left';
      c.fillText(b.id+' · '+b.d+' m',x+9,y+3);
      c.textAlign='left';
    });
    c.restore();
  }
  paintNodeGrounds(c,tf);
  paintAffected(c,tf);
  if(PL.cursor){
    c.save();
    c.strokeStyle='rgba(255,255,255,.35)';c.lineWidth=1;c.setLineDash([3,3]);
    c.beginPath();c.moveTo(tf.X(PL.cursor.x),0);c.lineTo(tf.X(PL.cursor.x),H);
    c.moveTo(0,tf.Y(PL.cursor.y));c.lineTo(W,tf.Y(PL.cursor.y));c.stroke();c.setLineDash([]);
    c.restore();
  }
  c.save();
  const sb=Math.round(200*PL.view.k);
  c.strokeStyle='rgba(255,255,255,.5)';c.lineWidth=1.5;
  c.beginPath();c.moveTo(W-40-sb,H-26);c.lineTo(W-40,H-26);c.stroke();
  c.beginPath();c.moveTo(W-40-sb,H-30);c.lineTo(W-40-sb,H-22);c.moveTo(W-40,H-30);c.lineTo(W-40,H-22);c.stroke();
  c.fillStyle='rgba(255,255,255,.6)';c.font='8.5px '+MONO;c.textAlign='center';
  c.fillText('200 m',W-40-sb/2,H-32);
  c.textAlign='left';
  c.beginPath();c.moveTo(W-24,30);c.lineTo(W-30,64);c.lineTo(W-24,56);c.lineTo(W-18,64);c.closePath();
  c.fillStyle='rgba(255,255,255,.7)';c.fill();
  c.font='600 9px '+MONO;c.textAlign='center';c.fillText('N',W-24,74);
  c.restore();
}
function paintAffected(c,tf){
  const areas=PL.alarms||[];
  if(E.plAlarm){
    E.plAlarm.textContent=areas.length
      ?areas.map(a=>a.status+' · '+a.where).join('   ·   ')
      :'NO WARNING · NO AFFECTED AREA';
    E.plAlarm.style.color=areas.some(a=>a.status==='CRITICAL')?'#ff8d8d'
      :areas.length?'#ffc56b':'';
  }
  areas.forEach(a=>{
    const p=a.panel,pad=80;
    const x=p.x-pad,y=p.y-pad,w=p.w+pad*2,h=p.h+pad*2;
    const pulse=0.45+0.55*Math.abs(Math.sin(Date.now()/320));
    const crit=a.status==='CRITICAL';
    c.save();
    c.fillStyle=crit?'rgba(255,60,60,'+(0.16+0.12*pulse)+')':'rgba(255,176,40,'+(0.14+0.1*pulse)+')';
    c.fillRect(tf.X(x),tf.Y(y),w*PL.view.k,h*PL.view.k);
    c.setLineDash([10,6]);
    c.strokeStyle=crit?'#ff4d4d':'#ffb020';
    c.lineWidth=crit?3:2;
    c.strokeRect(tf.X(x),tf.Y(y),w*PL.view.k,h*PL.view.k);
    c.setLineDash([]);
    c.fillStyle=crit?'#ffd6d6':'#ffe3b0';
    c.font='700 12px '+MONO;
    c.textAlign='left';
    c.fillText(a.status+' · AFFECTED AREA',tf.X(x)+8,tf.Y(y)+18);
    c.font='600 10px '+MONO;
    c.fillText(a.name+' · '+a.where,tf.X(x)+8,tf.Y(y)+34);
    c.restore();
  });
}
function mapPointer(ev){
  const r=E.plMap.getBoundingClientRect();
  const tf=invTf(r.width,r.height);
  const x=tf.X(ev.clientX-r.left),y=tf.Y(ev.clientY-r.top);
  return {x:x,y:y};
}
function mapReadout(p){
  const s=Math.round(fieldAt(p.x,p.y));
  const ob=overburdenAt(p.x,p.y);
  const inP3=p.x>P3.x&&p.x<P3.x+P3.w&&p.y>P3.y&&p.y<P3.y+P3.h;
  const inP2=p.x>P2.x&&p.x<P2.x+P2.w&&p.y>P2.y&&p.y<P2.y+P2.h;
  const inB=p.x>BARRIER.x&&p.x<BARRIER.x+BARRIER.w&&p.y>BARRIER.y&&p.y<BARRIER.y+BARRIER.h;
  const near=VILLAGES.reduce((a,v)=>{const d=Math.hypot(v.x-p.x,v.y-p.y);return d<a.d?{v:v,d:d}:a;},{d:1e9});
  const zone=inB?'BARRIER PILLAR':inP3?'PANEL P-3 · ACTIVE':inP2?'PANEL P-2 · WORKED OUT':'SURFACE ONLY';
  E.plReadout.innerHTML='GRID E '+(86.0+p.x/20000).toFixed(4)+'° · N '+(23.8-p.y/25000).toFixed(4)+'°<br>'+
    'DEPRESSION <b style="color:'+(s>100?'#ff9d9d':s>40?'#ffc061':'#8ff0b0')+'">'+s+' mm</b> · OVERBURDEN '+ob+' m<br>'+
    NSD.esc(zone)+'<br>NEAREST SETTLEMENT '+NSD.esc(near.v.n)+' · '+Math.round(near.d*1.5)+' m';
  E.plGrid.textContent='GRID 23°41\u2032N · 86°12\u2032E · 1:'+(5000/PL.view.k).toFixed(0);
  E.plMapZoom.textContent='ZOOM '+PL.view.k.toFixed(2)+'× · DRAG / PINCH TO PAN · WHEEL OR ± TO ZOOM';
}
function zoomAt(cx,cy,scale,quiet){
  const r=E.plMap.getBoundingClientRect();
  const before=invTf(r.width,r.height);
  const wx=before.X(cx-r.left),wy=before.Y(cy-r.top);
  const v=PL.view;
  v.k=clamp(v.k*scale,0.6,4);
  v.tx=(cx-r.left)-((wx-WORLD.w/2)*v.k)-r.width/2;
  v.ty=(cy-r.top)-((wy-WORLD.h/2)*v.k)-r.height/2;
  if(!quiet)labelZoom();
}
const TOUCHY=!!(window.matchMedia&&window.matchMedia('(hover:none)').matches);
function labelZoom(){
  E.plMapZoom.textContent='ZOOM '+PL.view.k.toFixed(2)+'× · '+(TOUCHY?'DRAG / PINCH TO PAN · ± TO ZOOM':'DRAG TO PAN · WHEEL OR ± TO ZOOM');
}
if(TOUCHY&&E.plReadout&&/MOVE POINTER/i.test(E.plReadout.textContent))
  E.plReadout.textContent='TAP OR DRAG THE MAP FOR THE BOREHOLE READOUT';
const PTRS=new Map();
let PINCH={d:0,x:null,y:null};
function ptrList(){return Array.from(PTRS.values());}
E.plMap.addEventListener('pointerdown',function(ev){
  try{E.plMap.setPointerCapture(ev.pointerId);}catch(e){}
  PTRS.set(ev.pointerId,{x:ev.clientX,y:ev.clientY,moved:0});
  if(PTRS.size===1)PL.drag={x:ev.clientX,y:ev.clientY,tx:PL.view.tx,ty:PL.view.ty};
  else PL.drag=null;
  E.plMap.style.cursor='grabbing';
  if(ev.pointerType!=='mouse')ev.preventDefault();
});
E.plMap.addEventListener('pointermove',function(ev){
  const me=PTRS.get(ev.pointerId);
  if(me){me.moved+=Math.abs(ev.clientX-me.x)+Math.abs(ev.clientY-me.y);me.x=ev.clientX;me.y=ev.clientY;}
  if(PTRS.size>=2){
    const a=ptrList();
    const d=Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y);
    const mx=(a[0].x+a[1].x)/2,my=(a[0].y+a[1].y)/2;
    if(PINCH.d)zoomAt(mx,my,d/PINCH.d,true);
    if(PINCH.x!==null){PL.view.tx+=mx-PINCH.x;PL.view.ty+=my-PINCH.y;}
    PINCH={d:d,x:mx,y:my};
    labelZoom();
    return;
  }
  if(PL.drag){
    PL.view.tx=PL.drag.tx+(ev.clientX-PL.drag.x);
    PL.view.ty=PL.drag.ty+(ev.clientY-PL.drag.y);
    return;
  }
  if(ev.pointerType==='mouse'){const p=mapPointer(ev);PL.cursor=p;mapReadout(p);}
});
function endPtr(ev){
  const me=PTRS.get(ev.pointerId);
  PTRS.delete(ev.pointerId);
  if(PTRS.size<2)PINCH={d:0,x:null,y:null};
  if(PTRS.size===0){
    PL.drag=null;E.plMap.style.cursor='crosshair';
    if(me&&me.moved<7&&ev.pointerType!=='mouse'){const p=mapPointer(ev);PL.cursor=p;mapReadout(p);}
  }else{
    const a=ptrList();
    PL.drag={x:a[0].x,y:a[0].y,tx:PL.view.tx,ty:PL.view.ty};
  }
}
E.plMap.addEventListener('pointerup',endPtr);
E.plMap.addEventListener('pointercancel',endPtr);
E.plMap.addEventListener('pointerleave',function(ev){if(ev.pointerType==='mouse')PL.cursor=null;});
E.plMap.addEventListener('wheel',function(ev){
  ev.preventDefault();
  zoomAt(ev.clientX,ev.clientY,ev.deltaY<0?1.12:0.9);
},{passive:false});
function centreZoom(k){
  const r=E.plMap.getBoundingClientRect();
  zoomAt(r.left+r.width/2,r.top+r.height/2,k);
}
if(E.plMapZoomIn)E.plMapZoomIn.addEventListener('click',()=>centreZoom(1.25));
if(E.plMapZoomOut)E.plMapZoomOut.addEventListener('click',()=>centreZoom(0.8));
E.plMapReset.addEventListener('click',()=>{PL.view={k:1,tx:0,ty:0};PL.cursor=null;labelZoom();});
labelZoom();

function buildLayers(){
  E.plLayers.innerHTML=LAYERS.map(l=>
    '<label class="'+(PL.layers[l.id]?'on':'')+'" data-l="'+l.id+'" tabindex="0" role="switch" aria-checked="'+(PL.layers[l.id]?'true':'false')+'">'+
    '<span class="sw"></span>'+NSD.esc(l.n)+'<span class="kw">'+NSD.esc(l.k)+'</span></label>').join('');
  $$('[data-l]',E.plLayers).forEach(el=>{
    const tog=function(){
      const id=el.dataset.l;PL.layers[id]=!PL.layers[id];
      el.classList.toggle('on',PL.layers[id]);
      el.setAttribute('aria-checked',PL.layers[id]?'true':'false');
      layerCount();
    };
    el.addEventListener('click',tog);
    el.addEventListener('keydown',e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();tog();}});
  });
  layerCount();
}
function layerCount(){
  const on=LAYERS.filter(l=>PL.layers[l.id]).length;
  E.plLayN.textContent=on+' / '+LAYERS.length+' ON';
}
E.plLayAll.addEventListener('click',()=>{
  LAYERS.forEach(l=>PL.layers[l.id]=true);buildLayers();
});
E.plLayUg.addEventListener('click',()=>{
  LAYERS.forEach(l=>PL.layers[l.id]=['pillars','barrier','heat','cracks'].indexOf(l.id)>=0);buildLayers();
});

function bhColumn(){
  const ob=PL.ob,eh=PL.eh;
  const top={sandstone:0.34,shale:0.22,inter:0.28}[PL.strata];
  const layers=[
    {n:'Alluvium / soil',t:Math.max(6,ob*0.08)},
    {n:'Weathered sandstone',t:Math.max(9,ob*0.12)},
    {n:(PL.strata==='shale'?'Laminated shale':PL.strata==='inter'?'Interbedded sst/shale':'Massive sandstone')+' (main roof)',t:ob*top},
    {n:'Immediate roof — shale',t:ob*0.14},
    {n:'COAL SEAM',t:eh,seam:true},
    {n:'Floor — sandstone',t:Math.max(8,ob*0.1)}
  ];
  let d=0;
  E.plBhCol.innerHTML=layers.map(l=>{
    const depth=Math.round(d);
    d+=l.t;
    return '<div class="layr'+(l.seam?' seam':'')+'"><span class="dp">'+depth+' m</span><span class="nm">'+
      NSD.esc(l.t<1?l.n:l.n)+'</span><span class="th">'+(l.t<1?l.t.toFixed(2):l.t.toFixed(1))+' m</span></div>';
  }).join('')+'<div class="layr"><span class="dp">'+Math.round(ob+eh)+' m</span><span class="nm">TOTAL DEPTH TO FLOOR</span>'+
  '<span class="th">'+fmt(ob+eh,1)+' m</span></div>';
}
function plText(){
  const d=PL.d;
  E.plK1.innerHTML=d.Smax+'<small>mm</small>';
  E.plK1d.textContent='trough centre · '+fmt(d.a,2)+' subsidence factor';
  E.plK2.innerHTML=fmt(d.ttf,1)+'<small>days</small>';
  E.plK2d.textContent='±'+fmt(d.ttf*0.18,1)+' d @ 80 % CI · '+fmt(d.vel,2)+' mm/day now';
  E.plK3.textContent=PL.rmr;
  E.plK3d.textContent=rmrClass(PL.rmr).label+' · '+PL.strata;
  E.plK4.textContent=PL.method==='caving'?'DEPILLARING WITH CAVING':'SAND STOWING';
  E.plK4d.textContent='model '+modelTag();
  E.plK5.innerHTML=d.foot+'<small>ha</small>';
  E.plK5d.textContent='> 10 mm depression contour';
  E.plK6.textContent=d.cracks;
  E.plK6d.textContent='predicted surface tensile zones';
  E.plRmrV.textContent=PL.rmr;
  E.plRmrClass.textContent=rmrClass(PL.rmr).label;
  E.plRmrClass.className='chip '+(PL.rmr<40?'crit':PL.rmr<50?'warn':'ok');
  E.plFailTh.textContent=d.failTh+' %';
  E.plCritV.textContent=fmt(d.critV,1)+' mm/day';
  E.plTwf.textContent=fmt(d.twf,2);
  E.plSmax.textContent=d.Smax+' mm';
  E.plSf.textContent=fmt(d.a,2);
  E.plDraw.textContent=fmt(d.draw,1)+'°';
  E.plInfl.textContent=d.infl+' m';
  E.plModelTag.textContent=modelTag();
  E.plTtf.innerHTML=fmt(d.ttf,1)+'<span class="u">d</span>';
  E.plTtfCi.textContent='±'+fmt(d.ttf*0.18,1)+' d';
  E.plTtfP.textContent=d.P+' %';
  E.plVelPeak.textContent='PEAK '+fmt(d.vel*1.42,2)+' mm/day';
  E.plVelTrend.textContent='TREND '+(PL.method==='caving'?'ACCELERATING':'STABLE');
  const eta=new Date(Date.now()-PL.scrub*86400000);
  E.plScrubDate.textContent=U.iso(eta);
  E.plScrubDepth.textContent='TROUGH DEPTH '+Math.round(smaxAt(PL.scrub))+' mm';
  E.plMapEpoch.textContent='EPOCH '+U.iso(eta)+(PL.scrub?' · T−'+PL.scrub+' d':' · LIVE');
  E.plScrubState.textContent=(PL.playing?'PLAYBACK':'PAUSED')+' · T+'+PL.scrub+' d';
  E.plScrubTag.textContent=PL.scrub?'HISTORICAL EPOCH':'LIVE EPOCH';
}
function rmrClass(v){
  if(v<30)return {label:'VERY POOR · CLASS V',cls:'crit'};
  if(v<40)return {label:'POOR · CLASS IV',cls:'crit'};
  if(v<50)return {label:'FAIR · CLASS III',cls:'warn'};
  if(v<60)return {label:'GOOD · CLASS II',cls:'ok'};
  return {label:'VERY GOOD · CLASS I',cls:'ok'};
}
function modelTag(){return PL.method==='caving'?'CAV-ENSEMBLE v4.4':'STOW-RESIDUAL v3.1';}

function drawTtf(){
  const f=U.frame(E.plTtfChart,{l:40,b:24,t:14,r:12});if(!f)return;
  const c=f.c,W=f.w,H=f.h,p=f.p,d=PL.d;
  const t0=-30,t1=d.ttf*1.25+3,mx=Math.max(6,d.critV*1.5);
  const X=t=>p.l+(t-t0)/(t1-t0)*(W-p.l-p.r);
  const Y=v=>H-p.b-(v/mx)*(H-p.t-p.b);
  c.font='8.5px '+MONO;c.fillStyle='#6c645e';c.textAlign='right';c.textBaseline='middle';
  for(let i=0;i<=4;i++){const v=mx*i/4;c.fillText(fmt(v,1),p.l-6,Y(v));}
  c.textAlign='center';c.textBaseline='top';
  for(let t=Math.ceil(t0/10)*10;t<=t1;t+=10){
    c.fillStyle='#5f574f';c.fillText(t===0?'NOW':(t>0?'+'+Math.round(t):t),X(t),H-p.b+5);
    c.strokeStyle=t===0?'rgba(255,255,255,.28)':'rgba(255,255,255,.05)';
    c.beginPath();c.moveTo(X(t),p.t);c.lineTo(X(t),H-p.b);c.stroke();
  }
  const meas=[];
  for(let t=-30;t<=0;t+=1){
    const v=d.vel*(0.42+0.58*Math.pow((t+30)/30,1.5))+Math.sin(t/3.1)*d.vel*0.06;
    meas.push([t,Math.max(0.05,v)]);
  }
  c.beginPath();meas.forEach((q,i)=>{i?c.lineTo(X(q[0]),Y(q[1])):c.moveTo(X(q[0]),Y(q[1]));});
  c.strokeStyle='#7cc6f0';c.lineWidth=2;c.stroke();
  const ai=[],hi=[],lo=[];
  for(let t=0;t<=d.ttf;t+=d.ttf/80){
    const v=d.vel*Math.pow(d.ttf/Math.max(0.4,d.ttf-t),0.85);
    const spreadA=0.18+0.5*Math.pow(t/d.ttf,2.2);
    ai.push([t,Math.min(mx*1.05,v)]);
    hi.push([t,Math.min(mx*1.05,v*(1+spreadA))]);
    lo.push([t,Math.max(0,v*(1-spreadA*0.75))]);
  }
  c.beginPath();
  hi.forEach((q,i)=>{i?c.lineTo(X(q[0]),Y(q[1])):c.moveTo(X(q[0]),Y(q[1]));});
  for(let i=lo.length-1;i>=0;i--)c.lineTo(X(lo[i][0]),Y(lo[i][1]));
  c.closePath();c.fillStyle='rgba(255,154,60,.13)';c.fill();
  c.beginPath();ai.forEach((q,i)=>{i?c.lineTo(X(q[0]),Y(q[1])):c.moveTo(X(q[0]),Y(q[1]));});
  c.strokeStyle='#ff9a3c';c.lineWidth=2;c.setLineDash([7,5]);c.stroke();c.setLineDash([]);
  const xt=X(d.ttf);
  c.strokeStyle='rgba(255,77,77,.85)';c.lineWidth=1.6;
  c.beginPath();c.moveTo(xt,p.t);c.lineTo(xt,H-p.b);c.stroke();
  c.fillStyle='rgba(255,77,77,.95)';c.beginPath();c.arc(xt,Y(mx*0.94),3.4,0,7);c.fill();
  c.textAlign='right';c.font='600 9px '+MONO;
  c.fillText('TTF '+fmt(d.ttf,1)+' d  ±'+fmt(d.ttf*0.18,1),xt-6,p.t+9);
  c.fillStyle='rgba(255,180,120,.9)';c.textAlign='left';
  c.fillText('AI FAILURE ASYMPTOTE',p.l+6,H-p.b-46);
}
function drawScatter(){
  const f=U.frame(E.plScatter,{l:40,b:26,t:12,r:12});if(!f)return;
  const c=f.c,W=f.w,H=f.h,p=f.p;
  const X=v=>p.l+(v/1)*(W-p.l-p.r), Y=v=>H-p.b-v*(H-p.t-p.b);
  c.strokeStyle='rgba(255,255,255,.06)';
  for(let i=1;i<5;i++){
    c.beginPath();c.moveTo(p.l+(W-p.l-p.r)*i/5,p.t);c.lineTo(p.l+(W-p.l-p.r)*i/5,H-p.b);c.stroke();
    c.beginPath();c.moveTo(p.l,p.t+(H-p.t-p.b)*i/5);c.lineTo(W-p.r,p.t+(H-p.t-p.b)*i/5);c.stroke();
  }
  c.font='8.5px '+MONO;c.fillStyle='#6c645e';c.textAlign='left';c.textBaseline='top';
  c.fillText('MICRO-VIBRATION ENERGY (mm/s · 10⁻²)',p.l,H-p.b+6);
  c.save();c.translate(12,p.t+2);c.fillText('TILT RATE (mm/m per day)',0,0);c.restore();
  const clusters=[[],[]];
  PL.cl.pts.forEach(q=>clusters[q.k].push(q));
  clusters.forEach((arr,k)=>{
    if(arr.length<3)return;
    const pts=arr.map(q=>[X(q.x),Y(q.y)]);
    const cx=pts.reduce((a,q)=>a+q[0],0)/pts.length, cy=pts.reduce((a,q)=>a+q[1],0)/pts.length;
    pts.sort((a,b)=>Math.atan2(a[1]-cy,a[0]-cx)-Math.atan2(b[1]-cy,b[0]-cx));
    c.beginPath();pts.forEach((q,i)=>{i?c.lineTo(q[0],q[1]):c.moveTo(q[0],q[1]);});c.closePath();
    c.fillStyle=k?'rgba(255,77,77,.10)':'rgba(124,198,240,.09)';c.fill();
    c.strokeStyle=k?'rgba(255,77,77,.35)':'rgba(124,198,240,.28)';c.setLineDash([5,4]);c.stroke();c.setLineDash([]);
  });
  PL.cl.pts.forEach(q=>{
    c.beginPath();c.arc(X(q.x),Y(q.y),q.hot?3.6:2.6,0,7);
    c.fillStyle=q.k?'rgba(255,110,110,.92)':'rgba(124,198,240,.6)';c.fill();
  });
  PL.cl.cent.forEach((ce,i)=>{
    if(!ce)return;
    const x=X(ce[0]),y=Y(ce[1]);
    c.strokeStyle=i?'#ff8a8a':'#9fd4f2';c.lineWidth=1.6;
    c.beginPath();c.moveTo(x-7,y);c.lineTo(x+7,y);c.moveTo(x,y-7);c.lineTo(x,y+7);c.stroke();
    c.beginPath();c.arc(x,y,10,0,7);c.stroke();
    c.fillStyle=i?'#ff9d9d':'#bfe4fb';c.font='600 8.5px '+MONO;c.textAlign='left';
    c.fillText(i?'PRECURSOR':'SETTLING',x+13,y+3);
  });
}
let kmeansTimer=null;
function seedClusters(){
  const share=clamp(0.18+(50-PL.rmr)/160+(PL.method==='caving'?0.06:0),0.08,0.55);
  const pts=[];
  const n=150;
  for(let i=0;i<n;i++){
    const hot=U.rnd()<share;
    const x=hot?U.rr(0.55,0.98):U.rr(0.04,0.42);
    const y=hot?U.rr(0.5,0.95):U.rr(0.05,0.44);
    pts.push({x:clamp(x+(PL.rmr<40?0.06:0),0,1),y:clamp(y+(PL.rmr<40?0.05:0),0,1),k:hot?1:0,hot:hot});
  }
  PL.cl.pts=pts;
  PL.cl.share=Math.round(share*100);
}
function runKmeans(animate){
  seedClusters();
  if(kmeansTimer){clearInterval(kmeansTimer);kmeansTimer=null;}
  let iter=0;
  const c0=[[0.2,0.2],[0.8,0.8]];
  PL.cl.cent=c0.map(c=>c.slice());
  const step=function(){
    iter++;
    for(let k=0;k<2;k++)for(let j=0;j<2;j++)PL.cl.cent[k][j]=c0[k][j];
    PL.cl.pts.forEach(q=>{
      const d0=Math.hypot(q.x-PL.cl.cent[0][0],q.y-PL.cl.cent[0][1]);
      const d1=Math.hypot(q.x-PL.cl.cent[1][0],q.y-PL.cl.cent[1][1]);
      q.k=d1<d0?1:0;
    });
    for(let k=0;k<2;k++){
      const pts=PL.cl.pts.filter(q=>q.k===k);
      if(!pts.length)continue;
      const mx=pts.reduce((a,q)=>a+q.x,0)/pts.length,my=pts.reduce((a,q)=>a+q.y,0)/pts.length;
      c0[k][0]+= (mx-c0[k][0])*1;c0[k][1]+=(my-c0[k][1])*1;
      PL.cl.cent[k]=[mx,my];
    }
    const sep=Math.hypot(PL.cl.cent[0][0]-PL.cl.cent[1][0],PL.cl.cent[0][1]-PL.cl.cent[1][1]);
    let intra=0;
    PL.cl.pts.forEach(q=>{intra+=Math.hypot(q.x-PL.cl.cent[q.k][0],q.y-PL.cl.cent[q.k][1]);});
    intra/=PL.cl.pts.length;
    const sil=clamp(sep/(sep+intra*2.2),0,1);
    PL.cl.iter=iter;PL.cl.sil=sil;
    const pc=PL.cl.pts.filter(q=>q.k===1).length;
    PL.cl.share=Math.round(pc/PL.cl.pts.length*100);
    E.plClN.textContent='2';
    E.plClShare.textContent=PL.cl.share+' % precursor';
    E.plClSil.textContent=fmt(sil,2);
    E.plClusterIter.textContent='ITER '+iter;
    E.plClusterState.textContent=iter<8?'FITTING…':'CONVERGED';
    E.plClusterState.className='chip '+(iter<8?'warn':'ok');
    drawScatter();
    if(iter>=8){
      clearInterval(kmeansTimer);kmeansTimer=null;
      NSD.ledger.add('ANOMALY CLUSTERING RE-RUN · PRECURSOR SHARE '+PL.cl.share+' % · SILHOUETTE '+fmt(sil,2),
        {actor:'GEOLOGIST',sev:PL.cl.share>30?'WARNING':'INFO'});
    }
  };
  if(animate){kmeansTimer=setInterval(step,110);}
  else {for(let i=0;i<8;i++)step();}
}
function drawVel(){
  const f=U.frame(E.plVelChart,{l:44,b:28,t:14,r:12});if(!f)return;
  const c=f.c,W=f.w,H=f.h,p=f.p,d=PL.d;
  const cfg={d:{n:14,step:1,label:'DAY'},w:{n:12,step:7,label:'WEEK'},m:{n:6,step:30,label:'MONTH'}}[PL.vel];
  const mx=Math.max(4,d.critV*1.35);
  const bw=(W-p.l-p.r)/cfg.n;
  c.font='8.5px '+MONO;
  for(let i=0;i<=4;i++){
    const v=mx*i/4,y=H-p.b-(v/mx)*(H-p.t-p.b);
    c.strokeStyle='rgba(255,255,255,.05)';
    c.beginPath();c.moveTo(p.l,y);c.lineTo(W-p.r,y);c.stroke();
    c.fillStyle='#6c645e';c.textAlign='right';c.textBaseline='middle';c.fillText(fmt(v,1),p.l-6,y);
  }
  const yc=H-p.b-(d.critV/mx)*(H-p.t-p.b);
  c.strokeStyle='rgba(255,77,77,.7)';c.setLineDash([6,4]);
  c.beginPath();c.moveTo(p.l,yc);c.lineTo(W-p.r,yc);c.stroke();c.setLineDash([]);
  c.fillStyle='rgba(255,120,120,.9)';c.textAlign='left';c.textBaseline='bottom';
  c.fillText('CRITICAL VELOCITY '+fmt(d.critV,1)+' mm/day',p.l+4,yc-3);
  for(let i=0;i<cfg.n;i++){
    const t=i*cfg.step;
    const decay=Math.exp(-Math.pow(t/ (PL.vel==='m'?3.2:8),1.4));
    const vv=d.vel*(0.45+0.55*decay)+(PL.method==='caving'?d.critV*0.12:0.2)*Math.sin(i/1.7);
    const vh=vv*0.42*(1+0.3*Math.sin(i*1.3));
    const x=p.l+i*bw+bw*0.18,w2=bw*0.28;
    const Y=v=>H-p.b-(clamp(v,0,mx)/mx)*(H-p.t-p.b);
    c.fillStyle='#7cc6f0';
    c.fillRect(x,Y(vv),w2,H-p.b-Y(vv));
    c.fillStyle='#c4a6ff';
    c.fillRect(x+w2+2,Y(vh),w2,H-p.b-Y(vh));
    c.fillStyle=vv>d.critV*0.75?'#ff9d9d':'#9b938c';
    c.font='8px '+MONO;c.textAlign='center';c.textBaseline='top';
    c.fillText(fmt(vv,1),x+w2,H-p.b+5);
    c.fillStyle='#5f574f';
    c.fillText(PL.vel==='d'?('D−'+t):(PL.vel==='w'?('W−'+t):('M−'+t)),x+w2,H-p.b+16);
  }
}
function drawProf(){
  const f=U.frame(E.plProf,{l:38,b:20,t:12,r:12});if(!f)return;
  const c=f.c,W=f.w,H=f.h,p=f.p,d=PL.d;
  const half=d.infl*1.6,mx=Math.max(20,smaxAt(PL.scrub)*1.15);
  const X=x=>p.l+((x+half)/(half*2))*(W-p.l-p.r);
  const Y=v=>p.t+(v/mx)*(H-p.t-p.b);
  c.strokeStyle='rgba(255,255,255,.06)';
  for(let i=1;i<5;i++){c.beginPath();c.moveTo(p.l+(W-p.l-p.r)*i/5,p.t);c.lineTo(p.l+(W-p.l-p.r)*i/5,H-p.b);c.stroke();}
  c.beginPath();
  for(let x=-half;x<=half;x+=half/60){
    const v=fieldAt(P3.cx+x,P3.cy);
    x===-half?c.moveTo(X(x),Y(v)):c.lineTo(X(x),Y(v));
  }
  c.strokeStyle='#ffb84d';c.lineWidth=1.8;c.stroke();
  c.lineTo(X(half),H-p.b);c.lineTo(X(-half),H-p.b);c.closePath();
  const g=c.createLinearGradient(0,p.t,0,H-p.b);
  g.addColorStop(0,'rgba(255,184,77,.28)');g.addColorStop(1,'rgba(255,184,77,0)');
  c.fillStyle=g;c.fill();
  VILLAGES.forEach(v=>{
    const dx=v.x-P3.cx;
    if(Math.abs(dx)>half)return;
    const x=X(dx),v2=fieldAt(v.x,v.y);
    c.strokeStyle='rgba(232,215,106,.8)';c.lineWidth=1;
    c.beginPath();c.moveTo(x,p.t+4);c.lineTo(x,H-p.b);c.stroke();
    c.fillStyle='rgba(232,215,106,.95)';c.font='8px '+MONO;c.textAlign='center';
    c.fillText(v.n+' · '+Math.round(v2)+' mm',x,p.t+2);
  });
  c.strokeStyle='rgba(255,255,255,.25)';c.setLineDash([4,4]);
  const x1=X(-P3.w/2),x2=X(P3.w/2);
  c.beginPath();c.moveTo(x1,p.t);c.lineTo(x1,H-p.b);c.moveTo(x2,p.t);c.lineTo(x2,H-p.b);c.stroke();c.setLineDash([]);
  c.fillStyle='rgba(255,255,255,.35)';c.font='8px '+MONO;c.textAlign='center';
  c.fillText('PANEL P-3 WIDTH '+P3.w+' m',(x1+x2)/2,H-p.b+4);
  c.textAlign='left';c.fillStyle='#6c645e';
  c.fillText('DEPTH (mm)',2,p.t+8);
}

E.plScrub.addEventListener('input',function(){
  PL.scrub=+E.plScrub.value;
  PL.offDirty=true;PL.lastScrub=performance.now();
  plText();recompute();drawProf();
});
E.plScrubPlay.addEventListener('click',function(){
  PL.playing=!PL.playing;
  E.plScrubPlay.textContent=PL.playing?'❙❙ PAUSE':'▶ PLAYBACK';
  plText();
});

const SMP_OPTS=[
  {id:'plan',n:'Annotated sub-surface plan',k:'GIS superimposition sheet',on:true},
  {id:'prof',n:'Trough cross-section',k:'Along panel short axis',on:true},
  {id:'ttf',n:'TTF projection & AI asymptote',k:'Inverse-velocity fit',on:true},
  {id:'scatter',n:'Anomaly cluster scatter',k:'k-means k=2',on:true},
  {id:'inv',n:'Instrumentation & network register',k:'4 surface nodes · double ring · 4 JBs',on:true},
  {id:'ledger',n:'Mesh availability log (30 d)',k:'From the uptime store',on:true}
];
PL.smp={};SMP_OPTS.forEach(o=>PL.smp[o.id]=o.on);
function buildSmp(){
  E.plSmpOpts.innerHTML=SMP_OPTS.map(o=>
    '<label class="'+(PL.smp[o.id]?'on':'')+'" data-s="'+o.id+'" tabindex="0" role="switch" aria-checked="'+(PL.smp[o.id]?'true':'false')+'">'+
    '<span class="sw"></span>'+NSD.esc(o.n)+'<span class="kw">'+NSD.esc(o.k)+'</span></label>').join('');
  $$('[data-s]',E.plSmpOpts).forEach(el=>{
    const tog=function(){
      const id=el.dataset.s;PL.smp[id]=!PL.smp[id];
      el.classList.toggle('on',PL.smp[id]);
      el.setAttribute('aria-checked',PL.smp[id]?'true':'false');
    };
    el.addEventListener('click',tog);
    el.addEventListener('keydown',e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();tog();}});
  });
}
function smpDoc(){
  const d=PL.d,eta=new Date(Date.now()-PL.scrub*86400000);
  const meta=[
    ['Colliery / panel','KUSUNDA (ECL) · Panel P-3 · Level 1, RL +200 m'],
    ['Extraction method',PL.method==='caving'?'Depillaring with caving':'Depillaring with sand stowing'],
    ['Seam / extraction height',fmt(PL.eh,1)+' m · overburden '+PL.ob+' m'],
    ['Dominant strata',PL.strata],
    ['Rock mass rating (calibrated)',PL.rmr+' — '+rmrClass(PL.rmr).label],
    ['Predicted maximum subsidence',d.Smax+' mm at trough centre'],
    ['Current depression (measured)',Math.round(smaxAt(PL.scrub))+' mm'],
    ['Time to failure (median)',fmt(d.ttf,1)+' days ±'+fmt(d.ttf*0.18,1)+' d'],
    ['Failure probability',d.P+' %'],
    ['Predicted crack zones',d.cracks+' vectors'],
    ['Model / baseline',modelTag()],
    ['Epoch of this return',U.iso(eta)+(PL.scrub?' (historical scrub T−'+PL.scrub+' d)':' (live)')]
  ];
  let html='<h2>1 · Panel &amp; modelling inputs</h2>'+
    NSD.report.table(['FIELD','VALUE'],meta)+
    '<div class="note">'+NSD.esc(STRATA_NOTE[PL.strata])+'</div>';
  if(PL.smp.plan)html+='<h2>2 · Annotated sub-surface plan — surface features over workings</h2>'+
    '<div class="figs">'+NSD.report.img(E.plMap,'Plan · pillars, barrier pillar, goaf edge, village footprints and predicted tension cracks')+'</div>';
  if(PL.smp.prof)html+='<h2>3 · Trough cross-section through P-3</h2>'+
    '<div class="figs">'+NSD.report.img(E.plProf,'Subsidence profile along the panel short axis, with settlement footprints')+'</div>';
  if(PL.smp.ttf)html+='<h2>4 · Time-to-failure projection</h2>'+
    '<div class="figs">'+NSD.report.img(E.plTtfChart,'Measured velocity against the fitted inverse-velocity asymptote')+'</div>';
  if(PL.smp.scatter)html+='<h2>5 · Micro-vibration / tilt anomaly clustering</h2>'+
    '<div class="figs">'+NSD.report.img(E.plScatter,'k-means separation of normal settling from failure precursors — precursor share '+PL.cl.share+' %')+'</div>';
  if(PL.smp.inv){
    const sRows=[
      ['S1 · NW CREST','RL +231 m · BENCH 3','3.82 V','3.4 V','-74 dBm','DIRECT → M-01 · ESP-NOW 2.4 GHz MESH PEER'],
      ['S2 · N CREST','RL +228 m · SUBSIDENCE ZONE','3.78 V','3.3 V','-69 dBm','DIRECT → M-01 · ESP-NOW 2.4 GHz MESH PEER'],
      ['S3 · NE CREST','RL +226 m · HAUL BATTER','3.80 V','3.5 V','-81 dBm','RELAY VIA S2 → M-01 · ESP-NOW 2.4 GHz'],
      ['S4 · E CREST','RL +219 m · OB DUMP TOE','3.74 V','3.2 V','-86 dBm','RELAY VIA S1/S2 → M-01 · ESP-NOW 2.4 GHz']
    ];
    const ugRows=[
      ['M-01 · MAIN HUB','SURFACE PITHEAD','GATEWAY','PRIMARY + SECONDARY RING PORTS A/B','RS-485 / OPTICAL RISER'],
      ['MH-1 · MINI HUB 1','LEVEL 1 · RL +200 m','2 × 110 dB BUZZERS','DOUBLE RING TRANSIT · BUS MASTER','BUS TO JB-1A + JB-1B'],
      ['MH-2 · LEVEL-2 HUB','LEVEL 2 · RL +186 m','2 × 110 dB BUZZERS','DOUBLE RING TRANSIT · BUS MASTER','BUS TO JB-2A + JB-2B'],
      ['JB-1A · JUNCTION BOX','LEVEL 1 · 0+280','6 EVAC LIGHTS','EVACUATION LIGHT CONTROL ONLY','STANDBY / PULSE ON EAP'],
      ['JB-1B · JUNCTION BOX','LEVEL 1 · 0+620','6 EVAC LIGHTS','EVACUATION LIGHT CONTROL · EOL 120 Ω','STANDBY / PULSE ON EAP'],
      ['JB-2A · JUNCTION BOX','LEVEL 2 · 0+300','8 EVAC LIGHTS','EVACUATION LIGHT CONTROL ONLY','STANDBY / PULSE ON EAP'],
      ['JB-2B · JUNCTION BOX','LEVEL 2 · 0+700','4 EVAC LIGHTS','EVACUATION LIGHT CONTROL · EOL 120 Ω','STANDBY / PULSE ON EAP']
    ];
    html+='<h2>6 · Strata instrumentation &amp; fail-safe evacuation register</h2>'+
      '<h3>6.1 · Surface Strata Wireless Mesh (ESP-NOW 2.4 GHz · Tilt &amp; Micro-vibration)</h3>'+
      NSD.report.table(['NODE / MONUMENT','LOCATION / RL','BATTERY','SOLAR','RSSI','TOPOLOGY ROLE'],sRows)+
      '<div class="note">Surface nodes operate on independent ESP-NOW 2.4 GHz wireless mesh with multi-hop relay fallback. Zero underground strata sensors deployed in accordance with CMR 1957.</div>'+
      '<h3>6.2 · Underground Fail-Safe Evacuation Circuits (Double Ring &amp; Bus Loops)</h3>'+
      NSD.report.table(['DEVICE','LEVEL / CHAINAGE','ACTUATORS / SIGNALS','CIRCUIT TOPOLOGY','OPERATIONAL STATE'],ugRows)+
      '<div class="note">Wired underground topology is a counter-rotating double ring (M-01 ↔ MH-1 ↔ MH-2 ↔ M-01) with auto-wrap on segment cut. Mini hubs host buzzers (sirens); junction boxes host evacuation LED strips only. No sensor nodes underground.</div>';
  }
  if(PL.smp.ledger){
    const rows=[];
    for(let i=0;i<8;i++){
      const day=new Date(Date.now()-i*86400000);
      const up=96.4+Math.sin(i*1.7)*2.1;
      rows.push([U.iso(day),fmt(up,1)+' %',(up<97?'OBSERVATION RAISED':'WITHIN LIMIT'),
        (i%3===0?'Secondary ring wrap test verified':'Double ring & surface mesh nominal')]);
    }
    html+='<h2>7 · Site network availability log (last 8 days)</h2>'+
      NSD.report.table(['DATE','UPTIME','STATUS','NOTE'],rows);
  }
  html+='<h2>8 · Statutory references</h2><div class="note">'+
    'Strata monitoring return compiled under Reg. 137(2) / CMR 1957 read with DGMS (Tech) circulars on '+
    'subsidence monitoring for depillaring operations. Digital signatures of the strata monitoring '+
    'instrumentation are held in the append-only audit ledger of this system.</div>';
  return NSD.report.doc({title:'STRATA MONITORING PLAN — FORM 1-A (PROVISIONAL)',
    sub:'Kusunda Colliery · Panel P-3 · issued '+U.iso(new Date()),
    stamp:'PROVISIONAL · AUTOGENERATED',meta:null,html:html,
    signL:'Colliery Manager',signR:'Strata Control Officer / DGMS'});
}
function openSmp(preview){
  const html=smpDoc();
  NSD.report.modal({title:'FORM 1-A',sub:'STRATA MONITORING PLAN · PROVISIONAL'+(preview?' · PREVIEW':' · READY TO ISSUE'),
    html:html,file:'SMP-Form-1A_Kusunda_'+U.iso(new Date())+'.html',
    note:preview?'':'Compiled and ready. <b>PRINT / SAVE AS PDF</b> routes to the browser print dialog; <b>DOWNLOAD .HTML</b> keeps every figure embedded.'});
  if(!preview)NSD.report.print(html);
  E.plSmpOut.innerHTML='<b style="color:#8ff0b0">Form 1-A compiled</b> from '+LAYERS.filter(l=>PL.layers[l.id]).length+
    ' active GIS layers, '+PL.cl.pts.length+' clustered anomaly points, 4 surface wireless nodes, double ring and 4 evacuation junction boxes at the last epoch. '+
    'Printing routes to the browser PDF engine; the .html copy keeps every figure embedded.';
  NSD.ledger.add('FORM 1-A STRATA MONITORING PLAN GENERATED · PANEL P-3',{actor:'GEOLOGIST',sev:'INFO'});
}
E.plSmpBtn.addEventListener('click',()=>openSmp(false));
E.plSmpPreview.addEventListener('click',()=>openSmp(true));

function wire(){
  derive();
  U.seg($('.seg[aria-label="Time window"]'),function(v){
    PL.win=+v;PL.offDirty=true;derive();
    const s=PL.d.Smax;
    E.plHeat0.textContent='0';E.plHeat1.textContent=Math.round(s*0.25);
    E.plHeat2.textContent=Math.round(s*0.5);E.plHeat3.textContent=Math.round(s*0.75);
    E.plHeat4.textContent=s+'+';
  });
  U.seg(E.plMethod,function(v){PL.method=v;PL.offDirty=true;derive();plText();});
  U.seg(E.plVelSeg,function(v){PL.vel=v;});
  E.plRmr.addEventListener('input',function(){
    PL.rmr=+E.plRmr.value;derive();PL.offDirty=true;plText();drawTtf();drawVel();
  });
  E.plOb.addEventListener('change',function(){PL.ob=clamp(+E.plOb.value||120,40,320);afterGeo();});
  E.plEh.addEventListener('change',function(){PL.eh=clamp(+E.plEh.value||3,1.5,6);afterGeo();});
  E.plStrata.addEventListener('change',function(){
    PL.strata=['sandstone','shale','inter'].indexOf(E.plStrata.value)<0?'sandstone':E.plStrata.value;afterGeo();});
  E.plClusterBtn.addEventListener('click',function(){
    E.plClusterState.textContent='RUNNING k-MEANS…';E.plClusterState.className='chip warn';
    runKmeans(true);
    NSD.ledger.add('ANOMALY CLUSTER PASS STARTED · '+(PL.method==='caving'?'CAVING':'STOWING')+' BASELINE',{actor:'GEOLOGIST',sev:'INFO'});
  });
  buildLayers();buildSmp();
}
function afterGeo(){
  derive();PL.offDirty=true;bhColumn();plText();drawTtf();drawVel();
  NSD.ledger.add('SEAM GEOMETRY UPDATED · OB '+PL.ob+' m · HEIGHT '+fmt(PL.eh,1)+' m · '+PL.strata.toUpperCase(),
    {actor:'GEOLOGIST',sev:'INFO'});
}
let started=false;
function initDeck(){
  derive();bhColumn();buildLayers();buildSmp();
  runKmeans(false);
  recompute();plText();drawTtf();drawVel();drawProf();drawScatter();drawMap();
  E.plRmr.value=PL.rmr;
}
function boot(){
  if(started)return;
  started=true;
  wire();initDeck();
}
NSD.onEnter('planner',function(){
  boot();
  derive();recompute();plText();
});
NSD.onTab('planner',function(){
  boot();
  recompute();plText();
  drawTtf();drawVel();drawProf();drawScatter();drawMap();
});
NSD.onTick(function(now,dt){
  if(NSD.st.view!=='planner')return;
  boot();
  if(!PL.d)return;
  if(PL.playing){
    PL.scrub=PL.scrub<=0?360:PL.scrub-3;
    E.plScrub.value=PL.scrub;
    PL.offDirty=true;plText();drawProf();
  }
  if(PL.offDirty){recompute();PL.offDirty=false;drawProf();drawVel();drawTtf();}
  drawMap();
});
window.__PL={state:PL,derive:derive,recompute:recompute,chart:{ttf:drawTtf,scatter:drawScatter},cluster:runKmeans,smp:smpDoc,drawMap:drawMap,alarmAreas:liveAlarmAreas};
})();
