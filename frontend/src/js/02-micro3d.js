const V={sub:(a,b)=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]],
  cross:(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],
  dot:(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2],
  norm:a=>{const l=Math.hypot(a[0],a[1],a[2])||1;return[a[0]/l,a[1]/l,a[2]/l];}};

function xform(p,r,t){
  let[x,y,z]=p,c,s;
  if(r[2]){c=Math.cos(r[2]);s=Math.sin(r[2]);[x,y]=[x*c-y*s,x*s+y*c];}
  if(r[0]){c=Math.cos(r[0]);s=Math.sin(r[0]);[y,z]=[y*c-z*s,y*s+z*c];}
  if(r[1]){c=Math.cos(r[1]);s=Math.sin(r[1]);[x,z]=[x*c+z*s,-x*s+z*c];}
  return[x+t[0],y+t[1],z+t[2]];
}
const KEY=V.norm([-0.5,0.86,0.42]);
const RIM=V.norm([0.7,0.25,-0.6]);
const HALF=V.norm([KEY[0],KEY[1],KEY[2]+1]);

function Scene(cv,cam){
  const st={yaw:cam.yaw,pitch:cam.pitch,dist:cam.dist,fov:cam.fov,
            target:(cam.target||[0,0,0]).slice(),objs:[],drag:null,spin:cam.spin||0,
            minD:cam.minD||7,maxD:cam.maxD||22,labels:[],filter:null,goal:null,wantRender:true};
  cv.addEventListener('pointerdown',e=>{st.drag={x:e.clientX,y:e.clientY,yaw:st.yaw,pitch:st.pitch};
    st.goal=null; if(st.onUser)st.onUser(); cv.setPointerCapture(e.pointerId);});
  cv.addEventListener('pointermove',e=>{if(!st.drag)return;
    st.yaw=st.drag.yaw+(e.clientX-st.drag.x)*0.007;
    st.pitch=Math.max(0.10,Math.min(1.05,st.drag.pitch+(e.clientY-st.drag.y)*0.005));});
  const stop=()=>st.drag=null;
  cv.addEventListener('pointerup',stop); cv.addEventListener('pointercancel',stop);
  cv.addEventListener('wheel',e=>{e.preventDefault();st.goal=null;
    st.dist=Math.max(st.minD,Math.min(st.maxD,st.dist*(1+e.deltaY*0.0011)));},{passive:false});
  const home={yaw:st.yaw,pitch:st.pitch,dist:st.dist,target:st.target.slice()};
  cv.addEventListener('dblclick',()=>st.goTo(home));
  st.goTo=g=>{st.goal=Object.assign({},g);};

  st.add=o=>{st.objs.push(o);return o;};
  st.addAll=a=>{a.forEach(o=>st.objs.push(o));return a;};
  st.label=(p,t,s,col)=>{st.labels.push({p,t,s:undefined,col:undefined});
    const L=st.labels[st.labels.length-1]; if(s)L.s=s; if(col)L.col=col; return L;};

  function drawLabel(c,L,q,w){
    const x=q[0],y=q[1];
    if(x<-60||x>w+60||y<-30||y>4000)return;
    c.font='600 8.5px ui-monospace,Menlo,Consolas,monospace';
    const tw=c.measureText(L.t).width;
    const pw=tw+14, ph=L.s?22:15;
    let px=x+11,py=y-ph-9;
    if(px+pw>w-8)px=x-pw-11;
    if(py<6)py=y+11;
    c.strokeStyle='rgba(255,255,255,.28)';c.lineWidth=1;
    c.beginPath();c.moveTo(x,y);c.lineTo(px<x?px+pw:px,py+ph/2);c.stroke();
    c.fillStyle='rgba(15,13,12,.80)';
    c.beginPath();
    if(c.roundRect)c.roundRect(px,py,pw,ph,4);else c.rect(px,py,pw,ph);
    c.fill();c.strokeStyle='rgba(255,255,255,.15)';c.stroke();
    c.textAlign='left';c.textBaseline='middle';
    c.fillStyle=L.col||'#ddd5cd';
    c.fillText(L.t,px+7,L.s?py+6.5:py+ph/2+0.5);
    if(L.s){c.fillStyle='#7d746c';c.font='7.5px ui-monospace,monospace';c.fillText(L.s,px+7,py+15.5);}
    c.fillStyle=L.col||'#ddd5cd';c.beginPath();c.arc(x,y,2.1,0,7);c.fill();
  }

  let _ctx=null,_cw=0,_ch=0;
  st.render=()=>{
    const r=Math.min(window.devicePixelRatio||1,2),b=cv.getBoundingClientRect();
    if(!b.width)return null;
    const cw=Math.round(b.width*r),ch=Math.round(b.height*r);
    if(cw!==_cw||ch!==_ch){cv.width=_cw=cw;cv.height=_ch=ch;_ctx=null;}
    const c=_ctx||(_ctx=cv.getContext('2d',{alpha:true}));
    c.setTransform(r,0,0,r,0,0);
    const w=b.width,h=b.height; c.clearRect(0,0,w,h);
    if(st.goal&&!st.drag){
      const k=0.09;
      st.yaw+=(st.goal.yaw-st.yaw)*k; st.pitch+=(st.goal.pitch-st.pitch)*k;
      st.dist+=(st.goal.dist-st.dist)*k;
      st.target=st.target.map((v,i)=>v+(st.goal.target[i]-v)*k);
      if(Math.abs(st.goal.yaw-st.yaw)<0.003&&Math.abs(st.goal.dist-st.dist)<0.03)st.goal=null;
    }
    if(!st.drag&&!st.goal&&st.spin)st.yaw+=st.spin;
    const cy=Math.cos(st.yaw),sy=Math.sin(st.yaw),cp=Math.cos(st.pitch),sp=Math.sin(st.pitch);
    const camf=p=>{let x=p[0]-st.target[0],y=p[1]-st.target[1],z=p[2]-st.target[2];
      let X=x*cy+z*sy,Z=-x*sy+z*cy; let Y=y*cp-Z*sp; Z=y*sp+Z*cp; return[X,Y,Z+st.dist];};
    const tris=[];
    for(const o of st.objs){
      if(o.hidden||(st.filter&&!st.filter(o)))continue;
      const rot=o.rot||[0,0,0],pos=o.pos||[0,0,0];
      const cvv=o.verts.map(p=>camf(xform(p,rot,pos)));
      for(const f of o.faces){
        const P=f.i.map(i=>cvv[i]);
        if(P.some(p=>p[2]<0.5))continue;
        const n=V.norm(V.cross(V.sub(P[1],P[0]),V.sub(P[2],P[0])));
        if(n[2]>0)continue;
        const nn=[-n[0],-n[1],-n[2]];
        let lum=0.36+0.76*Math.max(0,V.dot(nn,KEY))+0.20*Math.max(0,V.dot(nn,RIM));
        if(o.spec)lum+=o.spec*Math.pow(Math.max(0,V.dot(nn,HALF)),30);
        const depth=P.reduce((a,p)=>a+p[2],0)/P.length;
        lum*=1-Math.min(.26,Math.max(0,(depth-st.dist+4)*0.026));
        tris.push({P,lum,f,o,depth});
      }
    }
    tris.sort((a,b)=>((a.o.layer??1)-(b.o.layer??1))||(b.depth-a.depth));
    for(const t of tris){
      const{P,lum,f,o}=t, col=f.c||o.c, e=(f.emis!==undefined?f.emis:o.emis)||0;
      const R=Math.min(255,col[0]*lum+15*lum+col[0]*e*2.4);
      const G=Math.min(255,col[1]*lum+10*lum+col[1]*e*2.4);
      const B=Math.min(255,col[2]*lum+ 7*lum+col[2]*e*2.4);
      c.beginPath();
      P.forEach((p,i)=>{const s=st.fov/p[2],x=w/2+p[0]*s,y=h/2-p[1]*s;i?c.lineTo(x,y):c.moveTo(x,y);});
      c.closePath();
      c.fillStyle=`rgb(${R|0},${G|0},${B|0})`; c.fill();
      if(f.edge!==false&&o.edge!==false){
        c.strokeStyle=f.glow||'rgba(0,0,0,.34)'; c.lineWidth=f.glow?1.2:0.55; c.stroke();
      }
    }
    for(const L of st.labels){
      if(L.hide)continue;
      const q=camf(L.p); if(q[2]<1.2)continue;
      const s=st.fov/q[2];
      drawLabel(c,L,[w/2+q[0]*s,h/2-q[1]*s],w);
    }
    return{w,h,ctx:c,project:p=>{const q=camf(p),s=st.fov/q[2];return[w/2+q[0]*s,h/2-q[1]*s,q[2]];}};
  };
  return st;
}

function box(x0,y0,z0,x1,y1,z1,c,opt={}){
  return Object.assign({verts:[[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],
                               [x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]],
    faces:[{i:[0,1,2,3]},{i:[5,4,7,6]},{i:[4,0,3,7]},{i:[1,5,6,2]},{i:[3,2,6,7]},{i:[4,5,1,0]}],
    c},opt);
}
function prismY(cx,cz,r,seg,y0,y1,c,opt={}){
  const v=[],f=[];
  for(let i=0;i<seg;i++){const a=i/seg*Math.PI*2;
    v.push([cx+Math.cos(a)*r,y0,cz+Math.sin(a)*r]); v.push([cx+Math.cos(a)*r,y1,cz+Math.sin(a)*r]);}
  for(let i=0;i<seg;i++){const j=(i+1)%seg; f.push({i:[i*2,j*2,j*2+1,i*2+1]});}
  f.push({i:Array.from({length:seg},(_,i)=>i*2+1)});
  f.push({i:Array.from({length:seg},(_,i)=>(seg-1-i)*2)});
  return Object.assign({verts:v,faces:f,c},opt);
}
function pyr(cx,cz,r,y0,hgt,c,opt={}){
  return Object.assign({verts:[[cx-r,y0,cz-r],[cx+r,y0,cz-r],[cx+r,y0,cz+r],[cx-r,y0,cz+r],[cx,y0+hgt,cz]],
    faces:[{i:[0,1,4]},{i:[1,2,4]},{i:[2,3,4]},{i:[3,0,4]}],c},opt);
}
