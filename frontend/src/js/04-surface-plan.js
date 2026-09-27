window.SURF=(function(){
  const cv=document.getElementById('heroSurf'); if(!cv) return null;
  const NC=window.NS_MESH?window.NS_MESH.count:10;
  const api={nodes:{node_1:{d:0,t:0,s:'NOMINAL'},node_2:{d:0,t:0,s:'NOMINAL'}},
             routes:{route_A_status:'OPEN',route_B_status:'OPEN'},
             mesh:{stat:Array(NC).fill('NOMINAL'),ids:window.NS_MESH?window.NS_MESH.ids.slice():[]},
             hub:{s:'NOMINAL',online:NC,total:NC,reroute:false},
             surface:[{id:'S1',hub:0},{id:'S2',hub:0},{id:'S3',hub:1},{id:'S4',hub:1}],
             mini:[{id:'MH1',lvl:1,cells:NS_MESH.ofLevel(1)},{id:'MH2',lvl:2,cells:NS_MESH.ofLevel(2)}],
             jb:[{id:'JB-1',lvl:1,hub:0,cells:NS_MESH.ofJb(1)},{id:'JB-2',lvl:1,hub:0,cells:NS_MESH.ofJb(2)},
                 {id:'JB-3',lvl:2,hub:1,cells:NS_MESH.ofJb(3)},{id:'JB-4',lvl:2,hub:1,cells:NS_MESH.ofJb(4)}],
             central:{id:'M-01',s:'NOMINAL'},
             fault:{s:[false,false,false,false],central:false},
             alarm:{s:[0,0,0,0],central:0,lvl:[0,0],txt:'',ttf:null,since:0},
             sel:null, selT:0,
             panic:false,running:true};
  const scene={wantRender:true,
    tick(){ T+=1/60; if(scene.wantRender)draw(performance.now()); }};
  const COL={NOMINAL:[74,222,128],WARNING:[255,149,56],CRITICAL:[255,77,77]};
  const MESH={x0:-5.30,z0:-3.30,dx:1.05,dz:0.62};
  const cellPos=i=>{const g=(window.NS_MESH?NS_MESH.nums[i]:i+3)-1;
    return [MESH.x0+(g%4)*MESH.dx, MESH.z0+Math.floor(g/4)*MESH.dz];};
  const HUB={x:MESH.x0+1.5*MESH.dx, z:MESH.z0+2*MESH.dz+1.02};
  const SURF_NODE_POS=[[MESH.x0-0.10,MESH.z0+1.55],[MESH.x0+1.00,MESH.z0+0.35],
                       [MESH.x0+2.40,MESH.z0+0.25],[MESH.x0+3.55,MESH.z0+1.20]];
  const DRUMS=[[4.15,-2.5],[4.7,-2.5],[5.25,-2.5],[4.15,-1.6],[4.7,-1.6],[5.25,-1.6],[4.15,-0.7],[5.25,-0.7]];
  const FLOOD=[[-1.05,-3.45],[4.9,-2.6],[-4.6,2.6],[0.2,3.2],[5.4,-0.2]];
  const smoke=[]; let T=0;
  const disp={node_1:{d:0,t:0},node_2:{d:0,t:0}};

  function rr(c,x,y,w,h,r){c.beginPath();if(c.roundRect)c.roundRect(x,y,w,h,r);else c.rect(x,y,w,h);}
  function shed(c,X,Y,s,x0,z0,x1,z1,fill){
    c.fillStyle=fill;c.fillRect(X(x0),Y(z0),(x1-x0)*s,(z1-z0)*s);
    c.strokeStyle='rgba(255,255,255,.12)';c.lineWidth=1;c.strokeRect(X(x0),Y(z0),(x1-x0)*s,(z1-z0)*s);
  }
  function lab(c,X,Y,ax,az,tx,tz,txt,align){
    c.font='9px ui-monospace,Menlo,Consolas,monospace';
    c.textAlign=align||'center';c.textBaseline='middle';
    c.fillStyle='#a29a92';
    c.fillText(txt,X(tx),Y(tz));
    c.fillStyle='rgba(162,154,146,.7)';
    c.beginPath();c.arc(X(ax),Y(az),1.6,0,7);c.fill();
  }

  let _ctx=null,_cw=0,_ch=0;
  function draw(){
    const r=Math.min(window.devicePixelRatio||1,2),b=cv.getBoundingClientRect();
    if(!b.width)return;
    if(window.NSD&&NSD.paintOk)NSD.paintOk('heroSurf');
    const cw=Math.round(b.width*r),ch=Math.round(b.height*r);
    if(cw!==_cw||ch!==_ch){cv.width=_cw=cw;cv.height=_ch=ch;_ctx=null;}
    const c=_ctx||(_ctx=cv.getContext('2d',{alpha:true}));
    c.setTransform(r,0,0,r,0,0);
    const W=b.width,H=b.height;
    c.clearRect(0,0,W,H);
    const s=Math.min((W-170)/11.9,(H-170)/8.3);
    const X=x=>W/2+x*s, Y=z=>H/2+z*s;
    const run=api.running;

    if(run){
      if(Math.random()<0.5)smoke.push({x:3.71+(Math.random()-.5)*.1,z:-0.75+(Math.random()-.5)*.1,
        vx:-0.004-Math.random()*0.003,vy:-0.002-Math.random()*0.002,r:2.5+Math.random()*2,life:1});
      if(Math.random()<0.14)smoke.push({x:3.30,z:-2.0,vx:-0.003-Math.random()*0.002,vy:-0.002,
        r:2+Math.random()*1.6,life:1});
      if(Math.random()<0.22)smoke.push({x:4.55,z:0.8,vx:-0.002,vy:-0.0015,r:1.6+Math.random()*1.2,life:1});
    }

    c.fillStyle='#26221e';
    rr(c,X(-5.7),Y(-3.7),11.4*s,7.4*s,10);c.fill();
    c.strokeStyle='rgba(255,255,255,.10)';c.lineWidth=1.5;c.stroke();
    c.strokeStyle='rgba(255,255,255,.03)';c.lineWidth=1;
    for(let gx=-5.5;gx<=5.5;gx+=1){c.beginPath();c.moveTo(X(gx),Y(-3.7));c.lineTo(X(gx),Y(3.7));c.stroke();}
    for(let gz=-3.5;gz<=3.5;gz+=1){c.beginPath();c.moveTo(X(-5.7),Y(gz));c.lineTo(X(5.7),Y(gz));c.stroke();}

    c.strokeStyle='rgba(160,155,148,.22)';c.lineWidth=2;c.setLineDash([6,5]);
    c.beginPath();c.moveTo(X(5.0),Y(3.3));c.lineTo(X(4.6),Y(1.2));c.stroke();c.setLineDash([]);

    shed(c,X,Y,s,2.1,-0.25,4.0,1.45,'#4a453e');
    shed(c,X,Y,s,2.55,0.1,3.55,1.1,'#3c3833');
    for(let i=0;i<5;i++){c.fillStyle=`rgba(255,196,116,${0.5+0.25*Math.sin(T*2+i)})`;
      c.fillRect(X(2.28+i*0.33),Y(0.48),0.16*s,0.22*s);}
    shed(c,X,Y,s,-0.3,-3.4,1.1,-2.6,'#454038');
    for(let i=0;i<3;i++){c.fillStyle='rgba(255,196,116,.6)';
      c.fillRect(X(-0.12+i*0.42),Y(-3.1),0.14*s,0.2*s);}
    DRUMS.forEach(([x,z])=>{
      c.fillStyle='#5a544c';c.beginPath();c.arc(X(x),Y(z),0.26*s,0,7);c.fill();
      c.strokeStyle='rgba(255,255,255,.15)';c.lineWidth=1;c.stroke();
      c.fillStyle='#6e675e';c.beginPath();c.arc(X(x),Y(z),0.1*s,0,7);c.fill();
    });
    c.strokeStyle='rgba(140,136,130,.55)';c.lineWidth=1.6;
    [3.80,4.15].forEach(x=>{c.beginPath();c.moveTo(X(x),Y(-2.5));c.lineTo(X(x),Y(0.3));c.stroke();});
    shed(c,X,Y,s,3.85,-1.62,4.35,-1.42,'#5a4632');
    shed(c,X,Y,s,4.33,0.25,5.17,1.11,'#5a4a3c');
    const fg=c.createRadialGradient(X(4.75),Y(0.68),0,X(4.75),Y(0.68),0.55*s);
    fg.addColorStop(0,`rgba(255,130,40,${0.35+0.2*Math.sin(T*9)})`);
    fg.addColorStop(1,'rgba(255,130,40,0)');
    c.fillStyle=fg;c.beginPath();c.arc(X(4.75),Y(0.68),0.55*s,0,7);c.fill();
    [[3.71,-0.75,0.17],[3.3,-2.0,0.10],[4.55,0.8,0.055]].forEach(([x,z,rw])=>{
      c.fillStyle='#57504a';c.beginPath();c.arc(X(x),Y(z),rw*s,0,7);c.fill();
      c.strokeStyle='rgba(255,255,255,.2)';c.lineWidth=1;c.stroke();
      c.fillStyle='#3a3531';c.beginPath();c.arc(X(x),Y(z),rw*s*0.45,0,7);c.fill();
    });
    c.fillStyle='#4e4941';c.beginPath();c.arc(X(1.7),Y(-3.0),0.42*s,0,7);c.fill();
    c.strokeStyle='rgba(255,255,255,.15)';c.lineWidth=1;c.stroke();
    shed(c,X,Y,s,-2.9,3.42,-2.5,3.72,'#4a453e');
    shed(c,X,Y,s,-2.3,3.42,-1.9,3.72,'#4a453e');
    shed(c,X,Y,s,5.0,3.3,5.45,3.7,'#4a453e');
    FLOOD.forEach(([x,z])=>{
      c.fillStyle='#8a8378';c.beginPath();c.arc(X(x),Y(z),0.05*s,0,7);c.fill();
      const g=c.createRadialGradient(X(x),Y(z),0,X(x),Y(z),0.5*s);
      g.addColorStop(0,'rgba(255,188,112,.16)');g.addColorStop(1,'rgba(255,188,112,0)');
      c.fillStyle=g;c.beginPath();c.arc(X(x),Y(z),0.5*s,0,7);c.fill();
    });

    const UGX0=MESH.x0-0.55,UGX1=MESH.x0+3*MESH.dx+0.55;
    const UGZ0=MESH.z0-0.45,UGZ1=MESH.z0+2*MESH.dz+0.55;
    c.fillStyle='rgba(0,0,0,.24)';
    rr(c,X(UGX0),Y(UGZ0),(UGX1-UGX0)*s,(UGZ1-UGZ0)*s,8);c.fill();
    c.setLineDash([5,5]);c.strokeStyle='rgba(180,172,164,.26)';c.lineWidth=1.2;c.stroke();c.setLineDash([]);
    c.font='600 8.5px ui-monospace,Menlo,Consolas,monospace';c.textAlign='left';c.textBaseline='top';
    c.fillStyle='rgba(190,182,174,.68)';
    c.fillText('PANEL P-3 FOOTPRINT · WORKINGS BELOW · SEE UNDERGROUND VIEW',X(UGX0)+2,Y(UGZ1)+7);

    const ms=api.mesh.stat;
    const stOf=a2=>a2.indexOf('CRITICAL')>=0?'CRITICAL':a2.indexOf('WARNING')>=0?'WARNING':'NOMINAL';
    const miniState=api.mini.map(mh=>stOf(mh.cells.map(ci=>ms[ci]||'NOMINAL')));
    api.surface.forEach(sn=>{sn.s=miniState[sn.hub];});
    api.central.s=(miniState.indexOf('CRITICAL')>=0)?'CRITICAL'
      :(miniState.indexOf('WARNING')>=0)?'WARNING':'NOMINAL';
    let hubS=COL[api.central.s]?api.central.s:'NOMINAL';
    if(api.fault.central)hubS='CRITICAL';
    else if(hubS==='NOMINAL'&&api.fault.s.some(Boolean))hubS='WARNING';
    const hubCol=COL[hubS];

    const AL=api.alarm||{s:[0,0,0,0],central:0,lvl:[0,0]};
    const worstAlarm=Math.max(AL.central||0,AL.s[0]||0,AL.s[1]||0,AL.s[2]||0,AL.s[3]||0);
    if(worstAlarm>0){
      const red=worstAlarm===2, ph=Math.abs(Math.sin(T*(red?4.2:2.2)));
      const zc=red?[255,60,60]:[255,149,56];
      c.save();
      rr(c,X(UGX0),Y(UGZ0),(UGX1-UGX0)*s,(UGZ1-UGZ0)*s,8);c.clip();
      c.fillStyle=`rgba(${zc[0]},${zc[1]},${zc[2]},${red?0.10+0.10*ph:0.06+0.05*ph})`;
      c.fillRect(X(UGX0),Y(UGZ0),(UGX1-UGX0)*s,(UGZ1-UGZ0)*s);
      c.strokeStyle=`rgba(${zc[0]},${zc[1]},${zc[2]},${red?0.22:0.14})`;c.lineWidth=red?2:1.4;
      const step=14, off=(T*18)%step;
      for(let hx=X(UGX0)-((Y(UGZ1)-Y(UGZ0)))-step+off;hx<X(UGX1)+step;hx+=step){
        c.beginPath();c.moveTo(hx,Y(UGZ1));c.lineTo(hx+(Y(UGZ1)-Y(UGZ0)),Y(UGZ0));c.stroke();
      }
      c.restore();
      c.strokeStyle=`rgba(${zc[0]},${zc[1]},${zc[2]},${red?0.55+0.35*ph:0.45})`;c.lineWidth=red?2.2:1.6;
      c.setLineDash(red?[10,6]:[6,6]);
      rr(c,X(UGX0),Y(UGZ0),(UGX1-UGX0)*s,(UGZ1-UGZ0)*s,8);c.stroke();c.setLineDash([]);
      c.font='700 10px ui-monospace,Menlo,Consolas,monospace';c.textAlign='left';c.textBaseline='top';
      const lv=(AL.lvl[0]>=worstAlarm&&AL.lvl[1]>=worstAlarm)?'LEVELS 1 + 2'
              :(AL.lvl[0]>=worstAlarm?'LEVEL 1':'LEVEL 2');
      const head=red?'◀ EVACUATION ZONE · '+lv+' · GROUND COLLAPSE PREDICTED':'◀ MOVEMENT ALERT · '+lv+' · RESTRICT ACCESS';
      const sub=red?'ALL PERSONS OUT · EXIT VIA '+(AL.exit||'EAST AIR SHAFT')+(AL.ttf?' · '+AL.ttf:''):'';
      const tx=X(UGX1)+10, ty=Y(UGZ1)+4;
      const tw=Math.max(c.measureText(head).width,sub?c.measureText(sub).width*0.8:0);
      c.fillStyle='rgba(12,6,6,.74)';
      rr(c,tx-7,ty-5,tw+14,red?32:20,6);c.fill();
      c.fillStyle=`rgba(${zc[0]},${zc[1]},${zc[2]},${red?0.75+0.25*ph:0.9})`;
      c.fillText(head,tx,ty);
      if(red){
        c.font='600 8px ui-monospace,Menlo,Consolas,monospace';
        c.fillStyle=`rgba(255,200,200,${0.6+0.4*ph})`;
        c.fillText(sub,tx,ty+14);
      }
    }

    const CN=[HUB.x,HUB.z];
    [[0,1],[1,2],[2,3],[0,4],[1,4],[2,4],[3,4]].forEach(([a2,b2])=>{
      const fa=a2===4?api.fault.central:api.fault.s[a2];
      const fb=b2===4?api.fault.central:api.fault.s[b2];
      const dead=fa||fb;
      const pa=a2===4?CN:SURF_NODE_POS[a2], pb=b2===4?CN:SURF_NODE_POS[b2];
      c.strokeStyle=dead?`rgba(255,90,70,${0.34+0.26*Math.abs(Math.sin(T*5))})`:'rgba(124,198,240,.34)';
      c.lineWidth=dead?1.7:1.3;
      c.setLineDash(dead?[3,4]:[6,4]);
      c.beginPath();c.moveTo(X(pa[0]),Y(pa[1]));c.lineTo(X(pb[0]),Y(pb[1]));c.stroke();c.setLineDash([]);
      if(dead){
        const mx=(X(pa[0])+X(pb[0]))/2, my=(Y(pa[1])+Y(pb[1]))/2;
        c.strokeStyle='rgba(255,90,70,.95)';c.lineWidth=1.6;
        c.beginPath();c.moveTo(mx-4,my-4);c.lineTo(mx+4,my+4);
        c.moveTo(mx+4,my-4);c.lineTo(mx-4,my+4);c.stroke();
      }
    });

    c.save();
    const lx=616, ly=786;
    c.fillStyle='rgba(12,13,14,.72)';
    c.strokeStyle='rgba(156,148,140,.30)';c.lineWidth=1;
    rr(c,lx-14,ly-22,392,50,10);c.fill();c.stroke();
    c.font='700 9px ui-monospace,Menlo,Consolas,monospace';c.textAlign='left';c.textBaseline='middle';
    c.fillStyle='rgba(206,198,190,.80)';
    c.fillText('LINK TYPE · SURFACE RADIO ONLY',lx,ly-8);
    c.strokeStyle='rgba(124,198,240,.95)';c.lineWidth=1.6;c.setLineDash([6,4]);
    c.beginPath();c.moveTo(lx,ly+10);c.lineTo(lx+24,ly+10);c.stroke();c.setLineDash([]);
    c.fillStyle='rgba(160,215,255,.95)';
    c.fillText('WIRELESS ESP-NOW · S1–S4 → MAIN HUB M-01',lx+31,ly+10);
    c.restore();
    SURF_NODE_POS.forEach((p,i)=>{
      const sn=api.surface[i], dead=!!api.fault.s[i];
      const al=dead?0:(AL.s[i]||0);
      const col=dead?COL.CRITICAL:al===2?COL.CRITICAL:al===1?COL.WARNING:(COL[sn.s]||COL.NOMINAL), px=X(p[0]),py=Y(p[1]);
      const g2=c.createRadialGradient(px,py,0,px,py,0.52*s);
      g2.addColorStop(0,`rgba(${col[0]},${col[1]},${col[2]},${0.26+0.14*Math.abs(Math.sin(T*2+i))})`);
      g2.addColorStop(1,`rgba(${col[0]},${col[1]},${col[2]},0)`);
      c.fillStyle=g2;c.beginPath();c.arc(px,py,0.52*s,0,7);c.fill();
      if(al>0){
        const speed=al===2?1.6:0.7, rings=al===2?3:2;
        for(let k=0;k<rings;k++){
          const ph=((T*speed)+k/rings)%1;
          c.strokeStyle=`rgba(${col[0]},${col[1]},${col[2]},${(al===2?0.9:0.6)*(1-ph)})`;
          c.lineWidth=al===2?2.2:1.4;
          c.beginPath();c.arc(px,py-0.20*s,(0.2+ph*1.15)*s,0,7);c.stroke();
        }
        if(al===2){
          const blink=Math.sin(T*9)>0;
          c.strokeStyle=`rgba(255,120,120,${blink?0.95:0.35})`;c.lineWidth=1.6;
          [1,-1].forEach(dir=>{for(let k=1;k<=3;k++){
            c.beginPath();c.arc(px+dir*0.18*s,py-0.62*s,k*0.08*s,dir>0?-0.7:Math.PI-0.7,dir>0?0.7:Math.PI+0.7);c.stroke();}});
          if(blink){c.fillStyle='rgba(255,90,90,.55)';c.beginPath();c.arc(px,py-0.62*s,0.12*s,0,7);c.fill();}
        }
      }
      c.strokeStyle='rgba(226,220,212,.75)';c.lineWidth=1.3;
      c.beginPath();c.moveTo(px-0.10*s,py+0.13*s);c.lineTo(px,py-0.16*s);c.lineTo(px+0.10*s,py+0.13*s);c.stroke();
      c.beginPath();c.moveTo(px,py-0.16*s);c.lineTo(px,py-0.30*s);c.stroke();
      c.fillStyle='#6d665e';rr(c,px-0.12*s,py-0.40*s,0.24*s,0.10*s,2);c.fill();
      c.fillStyle='#2e4472';c.fillRect(px-0.15*s,py-0.55*s,0.30*s,0.08*s);
      c.fillStyle=`rgb(${col[0]},${col[1]},${col[2]})`;
      c.globalAlpha=0.7+0.3*Math.abs(Math.sin(T*3+i));
      c.beginPath();c.arc(px,py-0.62*s,0.045*s,0,7);c.fill();c.globalAlpha=1;
      c.font='8px ui-monospace,Menlo,Consolas,monospace';c.textAlign='center';c.textBaseline='top';
      c.fillStyle=`rgb(${col[0]},${col[1]},${col[2]})`;
      c.fillText('S'+(i+1),px,py+0.16*s);
      c.fillStyle=dead?'#ff9d9d':al===2?'#ff9d9d':al===1?'#ffc061':'#7d746c';c.font='6.5px ui-monospace,Menlo,Consolas,monospace';
      c.fillText(dead?'OFFLINE · NO ESP-NOW LINK':al===2?'EVACUATION ALARM · L'+(sn.hub+1):al===1?'ALERT · MOVEMENT L'+(sn.hub+1):'WIRELESS · ESP-NOW 2.4 GHz',px,py+0.30*s);
      if(al===2){
        c.font='700 7px ui-monospace,Menlo,Consolas,monospace';
        c.fillStyle=`rgba(255,120,120,${0.55+0.45*Math.abs(Math.sin(T*6))})`;
        c.fillText('HORN + STROBE ON',px,py+0.42*s);
      }
      if(dead){
        c.strokeStyle='rgba(255,90,70,.95)';c.lineWidth=2;
        c.beginPath();c.moveTo(px-0.16*s,py-0.70*s);c.lineTo(px+0.16*s,py-0.54*s);
        c.moveTo(px+0.16*s,py-0.70*s);c.lineTo(px-0.16*s,py-0.54*s);c.stroke();
        c.strokeStyle=`rgba(255,90,70,${0.35+0.3*Math.abs(Math.sin(T*5))})`;c.lineWidth=1.2;
        c.beginPath();c.arc(px,py-0.20*s,0.34*s,0,7);c.stroke();
      }
    });
    for(let k=0;k<2;k++){
      const ph=((T*0.5)+k*0.5)%1;
      c.strokeStyle=`rgba(124,198,240,${0.3*(1-ph)})`;c.lineWidth=1.2;
      c.beginPath();c.arc(X(CN[0]),Y(CN[1]),ph*0.9*s,0,7);c.stroke();
    }

    {
      const hx=X(HUB.x), hy=Y(HUB.z);
      const hg=c.createRadialGradient(hx,hy,0,hx,hy,0.72*s);
      const ha=hubS==='CRITICAL'?0.34+0.20*Math.abs(Math.sin(T*6))
              :hubS==='WARNING'?0.24+0.12*Math.abs(Math.sin(T*3.4)):0.16;
      hg.addColorStop(0,`rgba(${hubCol[0]},${hubCol[1]},${hubCol[2]},${ha})`);
      hg.addColorStop(1,`rgba(${hubCol[0]},${hubCol[1]},${hubCol[2]},0)`);
      c.fillStyle=hg;c.beginPath();c.arc(hx,hy,0.72*s,0,7);c.fill();
      c.fillStyle='#3b3630';
      rr(c,hx-0.44*s,hy-0.30*s,0.88*s,0.60*s,5);c.fill();
      c.strokeStyle=`rgba(${hubCol[0]},${hubCol[1]},${hubCol[2]},.55)`;c.lineWidth=1.4;c.stroke();
      c.fillStyle='#585049';
      rr(c,hx-0.26*s,hy-0.17*s,0.52*s,0.34*s,3);c.fill();
      c.strokeStyle='rgba(255,255,255,.16)';c.lineWidth=1;c.stroke();
      for(let i=0;i<3;i++){
        c.fillStyle=`rgba(255,205,132,${0.45+0.20*Math.sin(T*2+i)})`;
        c.fillRect(hx-0.19*s+i*0.14*s,hy-0.02*s,0.08*s,0.10*s);
      }
      c.strokeStyle='rgba(215,208,200,.65)';c.lineWidth=1.4;
      c.beginPath();c.moveTo(hx,hy-0.17*s);c.lineTo(hx,hy-0.52*s);c.stroke();
      c.beginPath();c.moveTo(hx-0.07*s,hy-0.17*s);c.lineTo(hx+0.07*s,hy-0.17*s);c.stroke();
      const sweep=T*1.5;
      c.save();c.beginPath();c.moveTo(hx,hy-0.52*s);
      c.arc(hx,hy-0.52*s,0.34*s,sweep,sweep+0.55);c.closePath();
      c.fillStyle=`rgba(${hubCol[0]},${hubCol[1]},${hubCol[2]},.20)`;c.fill();c.restore();
      for(let k=0;k<2;k++){
        const ph=((T*0.75)+k*0.5)%1;
        c.strokeStyle=`rgba(${hubCol[0]},${hubCol[1]},${hubCol[2]},${0.34*(1-ph)})`;
        c.lineWidth=1.2;
        c.beginPath();c.arc(hx,hy-0.52*s,ph*0.42*s,0,7);c.stroke();
      }
      c.fillStyle=`rgb(${hubCol[0]},${hubCol[1]},${hubCol[2]})`;
      c.globalAlpha=hubS==='CRITICAL'?0.55+0.45*Math.abs(Math.sin(T*8))
                   :hubS==='WARNING'?0.70+0.30*Math.abs(Math.sin(T*4)):0.85+0.15*Math.sin(T*1.5);
      c.beginPath();c.arc(hx,hy-0.545*s,0.045*s,0,7);c.fill();
      c.globalAlpha=1;
      c.fillStyle='#6a625a';
      c.beginPath();
      if(c.ellipse)c.ellipse(hx+0.33*s,hy-0.05*s,0.075*s,0.055*s,0,0,7);
      else c.arc(hx+0.33*s,hy-0.05*s,0.065*s,0,7);
      c.fill();
      c.strokeStyle='rgba(255,255,255,.18)';c.lineWidth=1;c.stroke();
      c.fillStyle='#4c463f';c.fillRect(hx-0.40*s,hy+0.06*s,0.12*s,0.09*s);
      c.font='8.5px ui-monospace,Menlo,Consolas,monospace';
      c.textAlign='center';c.textBaseline='top';
      c.fillStyle=`rgb(${hubCol[0]},${hubCol[1]},${hubCol[2]})`;
      c.fillText('MAIN HUB · M-01',hx,hy+0.34*s);
      if(AL.central===2){
        const ph=Math.abs(Math.sin(T*6));
        for(let k=0;k<3;k++){
          const rp=((T*1.4)+k/3)%1;
          c.strokeStyle=`rgba(255,70,70,${0.85*(1-rp)})`;c.lineWidth=2;
          c.beginPath();c.arc(hx,hy-0.52*s,(0.25+rp*1.3)*s,0,7);c.stroke();
        }
        c.font='700 7.5px ui-monospace,Menlo,Consolas,monospace';c.textBaseline='top';
        c.fillStyle=`rgba(255,120,120,${0.6+0.4*ph})`;
        c.fillText('SITE ALARM RELAYED · '+(AL.txt||'EVACUATE'),hx+12,hy+0.62*s);
      }else if(AL.central===1){
        c.font='7.5px ui-monospace,Menlo,Consolas,monospace';c.textBaseline='top';
        c.fillStyle=`rgba(255,192,97,${0.65+0.35*Math.abs(Math.sin(T*3))})`;
        c.fillText('ALERT RELAYED · STAND BY',hx+12,hy+0.62*s);
      }
      if(api.hub.reroute){
        c.font='7.5px ui-monospace,Menlo,Consolas,monospace';c.textBaseline='top';
        c.fillStyle=`rgba(120,255,160,${0.65+0.35*Math.abs(Math.sin(T*4))})`;
        c.fillText('BUS TAP ISOLATED · RE-POLLING',hx+12,hy+(AL.central?0.74:0.62)*s);
      }
    }

    for(let i=smoke.length-1;i>=0;i--){
      const p=smoke[i];
      p.x+=p.vx; p.z+=p.vy; p.r+=0.09; p.life-=0.006;
      if(p.life<=0){smoke.splice(i,1);continue;}
      const g=c.createRadialGradient(X(p.x),Y(p.z),0,X(p.x),Y(p.z),p.r);
      g.addColorStop(0,`rgba(108,100,92,${p.life*0.30})`);
      g.addColorStop(1,'rgba(108,100,92,0)');
      c.fillStyle=g;c.beginPath();c.arc(X(p.x),Y(p.z),p.r,0,7);c.fill();
    }

    lab(c,X,Y,4.7,-1.6,5.52,-1.6,'TANK FARM','left');
    lab(c,X,Y,3.05,0.6,3.05,1.75,'WASHING PLANT');
    lab(c,X,Y,4.75,0.68,4.75,1.42,'BOILER');
    lab(c,X,Y,0.4,-3.0,0.4,-3.62,'AUX SHED');
    lab(c,X,Y,1.7,-3.0,1.7,-2.42,'WATER TOWER');
    lab(c,X,Y,5.22,3.5,5.22,3.95,'GATE');

    c.save();
    c.translate(W-64,52);
    c.strokeStyle='rgba(210,200,190,.6)';c.fillStyle='rgba(210,200,190,.75)';c.lineWidth=1.4;
    c.beginPath();c.moveTo(0,16);c.lineTo(0,-16);c.stroke();
    c.beginPath();c.moveTo(0,-16);c.lineTo(-5,-6);c.lineTo(5,-6);c.closePath();c.fill();
    c.font='10px ui-monospace,monospace';c.textAlign='center';c.textBaseline='top';
    c.fillText('N',0,-36);
    c.restore();
    const bx=30,by=H-34;
    c.strokeStyle='rgba(210,200,190,.5)';c.lineWidth=1.5;
    c.beginPath();c.moveTo(bx,by);c.lineTo(bx+2*s,by);c.stroke();
    c.beginPath();c.moveTo(bx,by-4);c.lineTo(bx,by+4);c.moveTo(bx+2*s,by-4);c.lineTo(bx+2*s,by+4);c.stroke();
    c.font='9px ui-monospace,monospace';c.fillStyle='rgba(210,200,190,.6)';
    c.textAlign='center';c.textBaseline='bottom';
    c.fillText('20 m',bx+s,by-5);

    if(api.panic){
      const a=0.14+0.10*Math.abs(Math.sin(T*3.4));
      const g=c.createRadialGradient(W/2,H/2,Math.min(W,H)*0.3,W/2,H/2,Math.max(W,H)*0.62);
      g.addColorStop(0,'rgba(255,30,30,0)');g.addColorStop(1,`rgba(255,28,28,${a})`);
      c.fillStyle=g;c.fillRect(0,0,W,H);
      c.fillStyle=`rgba(255,30,30,${0.05+0.04*Math.abs(Math.sin(T*3.4))})`;c.fillRect(0,0,W,H);
    }
  }
  function selectCell(i){
    if(i===null||i===undefined){api.sel=null;scene.wantRender=true;return null;}
    if(i<0||i>=api.mesh.stat.length)return null;
    if(api.sel!==i){api.sel=i;api.selT=T;}
    scene.wantRender=true;
    const[x,z]=cellPos(i);
    return{x,z,hub:{x:HUB.x,z:HUB.z}};
  }
  function toggleSurface(id){
    const key=String(id||'').toUpperCase().replace('M-01','M01');
    if(key==='M01'){
      api.fault.central=!api.fault.central;
    }else{
      const m=/^S([1-4])$/.exec(key); if(!m)return null;
      const i=parseInt(m[1],10)-1; api.fault.s[i]=!api.fault.s[i];
    }
    scene.wantRender=true;
    return surfaceState(id)?'failed':'ok';
  }
  function surfaceState(id){
    const key=String(id||'').toUpperCase().replace('M-01','M01');
    if(key==='M01')return api.fault.central;
    const m=/^S([1-4])$/.exec(key);
    return m?api.fault.s[parseInt(m[1],10)-1]:null;
  }
  return{scene,api,selectCell,cellPos,toggleSurface,surfaceState};
})();
