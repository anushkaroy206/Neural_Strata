window.BBX=(function(){
'use strict';
const $=id=>document.getElementById(id);
if(!$('bbxCard'))return null;
const CFG={rateHz:8,rowsPerChunk:1000,uploadEvery:30,batchRows:100,cardMB:7580,
           rowBytes:26,ramRingMax:2000,fifoPct:5,brownoutS:4,replayRate:400};
const pad4=n=>String(n).padStart(4,'0');
const cname=n=>'chunk_'+pad4(n);
const num=n=>Math.round(n).toLocaleString('en-US');
const p2=n=>String(n).padStart(2,'0');
const hms=d=>{d=d||new Date();return p2(d.getHours())+':'+p2(d.getMinutes())+':'+p2(d.getSeconds());};
const kb=rows=>{const b=rows*CFG.rowBytes;if(!b)return '0 KB';return b<1024*1024?(b/1024).toFixed(b<10240?1:0)+' KB':(b/1048576).toFixed(2)+' MB';};
const ledger=(ev,sev,actor)=>{try{if(window.NSD&&NSD.ledger)NSD.ledger.add(ev,{sev:sev||'INFO',actor:actor||'BLACKBOX'});}catch(e){}};

const S={
  live:false,liveAt:0,
  sd:true,usb:true,internet:true,fifo:false,brownout:0,
  reasons:{},releasable:0,retainedTotal:0,deleteBlocked:'',
  /* §5.7's recovery plane, as the surface reports it: the handshake, whatever
     REPLAY is in flight, and what it has put back. `seen` holds the last values
     logged so a transition is announced once instead of twice a second. */
  recovery:null,recSeen:{asks:-1,facts:-1,rows:-1,chunk:0,state:'',exhausted:0},
  chunk:42,row:450,onCard:null,archive:'',
  chunks:{},order:[],
  freeMB:3712,ram:0,ramDropped:0,
  outbox:[],pending:0,sent:41354,
  lastUpload:null,lastAck:null,nextUp:12,
  syncing:null,replay:0,phase:0,ackFlash:0,
  log:[]
};
function newChunk(id,rows,opts){
  opts=opts||{};
  S.chunks[id]={rows:rows||0,sd:opts.sd===undefined?(rows||0):opts.sd,laptop:opts.laptop===undefined?(rows||0):opts.laptop,
                sentRows:opts.sentRows||0,closed:!!opts.closed};
  S.order.push(id);
}
newChunk(42,450,{sentRows:450-96});
S.outbox.push({chunk:42,n:96});S.pending=96;
S.lastUpload={t:new Date(Date.now()-12000),rows:240,batches:3,ok:true};
S.lastAck={chunk:41,t:new Date(Date.now()-12000)};

function log(txt,cls,led){
  S.log.unshift({t:hms(),txt,cls:cls||'info'});
  if(S.log.length>6)S.log.length=6;
  if(led)ledger(led.ev||txt.toUpperCase(),led.sev||(cls==='crit'?'CRITICAL':cls==='warn'?'WARNING':'INFO'),led.actor);
}
log('chunk_0041 · ACK_DELETE:0041 → SD.remove() 1 ms · after cloud 200 OK','ok');
log('upload · 240 rows · 3 batches ≤100 · GZIP −85 % · HMAC-SHA256 · 200 OK','ok');
log('Core 1 master stamp · chunk_0042 open · row 450 / 1000','info');

function laptopReceive(chunkId,k){
  const c=S.chunks[chunkId]; if(!c||k<=0)return;
  c.laptop+=k;
  const last=S.outbox[S.outbox.length-1];
  if(last&&last.chunk===chunkId)last.n+=k; else S.outbox.push({chunk:chunkId,n:k});
  S.pending+=k;
}
function addRows(k){
  const c=S.chunks[S.chunk];
  S.row+=k;c.rows+=k;
  if(S.sd){c.sd+=k;}
  else{
    S.ram+=k;
    if(S.ram>CFG.ramRingMax){S.ramDropped+=S.ram-CFG.ramRingMax;S.ram=CFG.ramRingMax;}
  }
  if(S.usb)laptopReceive(S.chunk,k);
}
function rollChunk(reason){
  const old=S.chunk,c=S.chunks[old];
  c.closed=true;
  S.freeMB=Math.max(0,S.freeMB-(c.sd*CFG.rowBytes)/1048576);
  S.chunk++;S.row=0;newChunk(S.chunk,0);
  log(cname(old)+' closed · '+num(c.rows)+' rows · FAT flushed · '+cname(S.chunk)+' open'+(reason?' · '+reason:''),'info',
      {ev:'BLACKBOX · '+cname(old).toUpperCase()+' CLOSED ('+num(c.rows)+' ROWS) · '+cname(S.chunk).toUpperCase()+' OPEN · CORE 1 STAMP',sev:'INFO'});
  if(S.fifo&&S.sd&&freePct()<=CFG.fifoPct)fifoUnlink();
}
function freePct(){return S.freeMB/CFG.cardMB*100;}
function ingestRows(n){
  let left=n;
  while(left>0){
    const space=CFG.rowsPerChunk-S.row;
    const take=Math.min(left,space);
    addRows(take);left-=take;
    if(S.row>=CFG.rowsPerChunk)rollChunk();
  }
}

function auditAndAck(){
  if(!S.internet)return 0;
  const acked=[];
  S.order.slice().forEach(id=>{
    const c=S.chunks[id];
    if(!c.closed||id===S.chunk)return;
    const pendingRows=S.outbox.filter(o=>o.chunk===id).reduce((a,o)=>a+o.n,0);
    if(pendingRows>0)return;
    if(c.laptop<c.rows)return;
    ackDelete(id);acked.push(id);
  });
  R.acked=acked.slice();
  if(acked.length>2)
    ledger('BLACKBOX · ACK_DELETE:'+pad4(acked[0])+' … '+pad4(acked[acked.length-1])+' · '+acked.length+' SD CHUNKS UNLINKED AFTER CLOUD 200 OK','INFO');
  else acked.forEach(id=>ledger('BLACKBOX · ACK_DELETE:'+pad4(id)+' · SD CHUNK UNLINKED AFTER CLOUD 200 OK','INFO'));
  return acked.length;
}
function ackDelete(id){
  const c=S.chunks[id];
  S.order=S.order.filter(x=>x!==id);
  S.freeMB=Math.min(CFG.cardMB,S.freeMB+(c.sd*CFG.rowBytes)/1048576);
  delete S.chunks[id];
  S.lastAck={chunk:id,t:new Date()};S.ackFlash=3;
  log('ACK_DELETE:'+pad4(id)+' → SD.remove(/'+cname(id)+'.csv) · 1 ms · zero flash wear','ok');
}
function fifoUnlink(){
  const victim=S.order.find(id=>id!==S.chunk&&S.chunks[id].closed);
  if(victim===undefined)return;
  const c=S.chunks[victim];
  S.order=S.order.filter(x=>x!==victim);
  S.freeMB=Math.min(CFG.cardMB,S.freeMB+(c.sd*CFG.rowBytes)/1048576);
  delete S.chunks[victim];
  log('EMERGENCY FIFO · '+cname(victim)+' unlinked (oldest) · SD '+(100-freePct()).toFixed(0)+' % full · freshest data kept','crit',
      {ev:'BLACKBOX · EMERGENCY CIRCULAR FIFO · '+cname(victim).toUpperCase()+' UNLINKED (OLDEST) · SD 95 % FULL',sev:'WARNING'});
}

function runUpload(force,done){
  if(S.syncing){if(done)done(false,'busy');return;}
  if(!S.internet){
    S.lastUpload={t:new Date(),rows:0,batches:0,ok:false};
    R.s3={t:S.outbox.length?cname(S.outbox[0].chunk):'—',c:'warn'};R.s4={t:'HTTPS TIMEOUT',c:'crit'};R.s5={t:'SUSPENDED · '+num(S.pending)+' PENDING',c:'warn'};
    log('HTTPS timeout · Central Mining Cloud unreachable · outbox '+num(S.pending)+' PENDING · deletes suspended','warn',
        force?{ev:'FORCE SYNC REFUSED · CLOUD UNREACHABLE · OUTBOX '+num(S.pending)+' PENDING · SD CHUNKS RETAINED',sev:'WARNING',actor:'SAFETY OFFICER'}:null);
    if(done)done(false,'offline');return;
  }
  if(S.pending<=0){
    const acked=auditAndAck();
    if(force||acked){R.s3={t:'QUEUE EMPTY',c:'ok'};R.s4={t:'NOTHING TO SEND',c:'ok'};R.s5={t:acked?R.acked.map(pad4).join(', '):'NONE DUE',c:acked?'ok':''};}
    if(force)log('force sync · outbox empty · 100 % synced'+(acked?' · '+acked+' chunk(s) unlinked':''),'ok',
                 {ev:'FORCE SYNC · OUTBOX EMPTY · CLOUD 100 % SYNCED',sev:'INFO',actor:'SAFETY OFFICER'});
    if(done)done(true,'empty');return;
  }
  const total=S.pending,batches=Math.ceil(total/CFG.batchRows);
  S.syncing={i:0,batches,rows:0,force:!!force};
  R.s3={t:cname(S.outbox[0].chunk)+' · '+num(total)+' rows',c:'on'};R.s4={t:'BATCH 0/'+batches,c:'on'};R.s5={t:'—',c:''};
  const step=Math.max(18,Math.min(140,3200/batches));
  const tm=setInterval(()=>{
    if(!S.internet){
      clearInterval(tm);
      log('upload aborted · link lost after batch '+S.syncing.i+'/'+batches+' · '+num(S.pending)+' rows remain PENDING','warn');
      if(R.s3)R.s3.c='warn';R.s4={t:'LINK LOST · BATCH '+S.syncing.i+'/'+batches,c:'crit'};R.s5={t:'SUSPENDED',c:'warn'};
      S.lastUpload={t:new Date(),rows:S.syncing.rows,batches:S.syncing.i,ok:false};
      S.syncing=null;render();if(done)done(false,'offline');return;
    }
    let take=CFG.batchRows;
    while(take>0&&S.outbox.length){
      const o=S.outbox[0],k=Math.min(take,o.n);
      o.n-=k;take-=k;S.pending-=k;S.sent+=k;S.syncing.rows+=k;
      const c=S.chunks[o.chunk];if(c)c.sentRows+=k;
      if(o.n<=0)S.outbox.shift();
    }
    S.syncing.i++;
    R.s4={t:'BATCH '+S.syncing.i+'/'+batches+' · GZIP + HMAC',c:'on'};
    if(S.syncing.i>=batches||S.pending<=0){
      clearInterval(tm);
      const r=S.syncing;S.syncing=null;
      S.lastUpload={t:new Date(),rows:r.rows,batches:r.i,ok:true};
      const acked=auditAndAck();
      if(R.s3)R.s3.c='ok';R.s4={t:'200 OK · '+num(r.rows)+' ROWS · '+r.i+' BATCH'+(r.i>1?'ES':''),c:'ok'};R.s5={t:acked?R.acked.map(pad4).join(', '):'NONE DUE',c:acked?'ok':''};
      log((r.force?'FORCE SYNC · ':'upload · ')+num(r.rows)+' rows · '+r.i+' batch'+(r.i>1?'es':'')+' ≤100 · GZIP −85 % · HMAC-SHA256 · 200 OK'+
          (acked?' · '+acked+' chunk'+(acked>1?'s':'')+' unlinked':''),'ok',
          r.force?{ev:'FORCE SYNC · '+num(r.rows)+' ROWS PUSHED · CLOUD 200 OK'+(acked?' · '+acked+' SD CHUNK(S) UNLINKED LIVE':''),sev:'INFO',actor:'SAFETY OFFICER'}:null);
      render();if(done)done(true,acked);
    }else render();
  },step);
}

function drilling(){return armed.internet||armed.usb||armed.sd||armed.power||armed.fifo;}
function tick(){
  if(S.live&&!drilling()){ if(Date.now()-S.liveAt>8000)S.live=false; else {render();return;} }
  S.phase=(S.phase+1)%7;
  if(S.ackFlash>0)S.ackFlash--;
  if(S.brownout>0){
    S.brownout--;
    if(S.brownout===0){
      const c=S.chunks[S.chunk],lost=Math.min(c.sd,Math.round(CFG.rateHz*4));
      c.sd-=lost;
      rollChunk('hub reboot');
      log('hub rebooted · closed chunks intact · '+lost+' uncommitted rows (<5 s) affected · '+cname(S.chunk)+' open','ok',
          {ev:'BLACKBOX · HUB REBOOTED AFTER BROWNOUT · CLOSED CHUNKS INTACT · '+lost+' ROWS (<5 s) UNCOMMITTED · '+cname(S.chunk).toUpperCase()+' OPEN',sev:'INFO'});
    }
    S.nextUp--;
    if(S.nextUp<=0){S.nextUp=CFG.uploadEvery;runUpload(false);}
    render();return;
  }
  ingestRows(CFG.rateHz);
  if(S.replay>0&&S.usb){
    let k=Math.min(CFG.replayRate,S.replay);S.replay-=k;
    S.order.forEach(id=>{const c=S.chunks[id];const gap=c.sd-c.laptop;if(gap>0&&k>0){const g=Math.min(gap,k);laptopReceive(id,g);k-=g;}});
    if(S.replay<=0)log('SD replay complete · every retained row is in the outbox · uploads resume','ok');
  }
  S.nextUp--;
  if(S.nextUp<=0){S.nextUp=CFG.uploadEvery;runUpload(false);}
  render();
}

const DRILL={
  internet:{on(){S.internet=false;log('mine internet down · fibre cut / 4G outage · outbox holds rows · laptop refuses ACK_DELETE','warn',
                 {ev:'CLOUD LINK DOWN · OUTBOX HOLDING · SD DELETES SUSPENDED (ZERO-LOSS RULE)',sev:'WARNING',actor:'DRILL'});},
            off(){S.internet=true;S.nextUp=Math.min(S.nextUp,3);log('internet restored · outbox '+num(S.pending)+' PENDING will drain · ACK_DELETE resumes after 200 OK','ok',
                 {ev:'CLOUD LINK RESTORED · '+num(S.pending)+' PENDING ROWS QUEUED FOR UPLOAD',sev:'INFO',actor:'DRILL'});}},
  usb:{on(){S.usb=false;log('rockfall cut the USB cable · surface blind · Core 0 keeps archiving 2 Hz rows to MicroSD','crit',
            {ev:'USB SERIAL SEVERED (ROCKFALL) · HUB ARCHIVING TO SD BLACKBOX · SURFACE BLIND',sev:'CRITICAL',actor:'DRILL'});},
       off(){S.usb=true;S.replay=S.order.reduce((a,id)=>a+Math.max(0,S.chunks[id].sd-S.chunks[id].laptop),0);
             log('cable restored · SD replay → outbox · '+num(S.replay)+' rows retained underground · zero loss','ok',
             {ev:'USB SERIAL RESTORED · '+num(S.replay)+' RETAINED ROWS REPLAYED FROM SD · ZERO LOSS',sev:'INFO',actor:'DRILL'});}},
  sd:{on(){S.sd=false;S.ram=0;S.ramDropped=0;log('MicroSD removed · BLACKBOX: FAULT / NO SD · Core 0 buffering to RAM ring ('+num(CFG.ramRingMax)+' rows)','crit',
           {ev:'BLACKBOX FAULT · NO SD · CORE 0 RAM RING BUFFER ENGAGED',sev:'CRITICAL',actor:'DRILL'});},
      off(){S.sd=true;const c=S.chunks[S.chunk];const f=S.ram;c.sd+=f;S.ram=0;
            log('MicroSD re-inserted · RAM ring '+num(f)+' rows flushed → '+cname(S.chunk)+'.csv'+(S.ramDropped?' · '+num(S.ramDropped)+' rows lost (ring full)':''),S.ramDropped?'warn':'ok',
            {ev:'BLACKBOX RESTORED · SD RE-INSERTED · RAM RING '+num(f)+' ROWS FLUSHED',sev:S.ramDropped?'WARNING':'INFO',actor:'DRILL'});S.ramDropped=0;}},
  power:{on(){S.brownout=CFG.brownoutS;
              log('substation trip · brownout ISR fired · last open sector committed before the caps drained · hub dark','crit',
              {ev:'HUB POWER LOST · BROWNOUT ISR COMMITTED LAST OPEN SECTOR · ≤5 s ROWS AT RISK',sev:'CRITICAL',actor:'DRILL'});},
         off(){S.brownout=0;}},
  fifo:{on(){
          const chunks={};
          S.order.forEach(id=>{const c=S.chunks[id];chunks[id]={rows:c.rows,sd:c.sd,laptop:c.laptop,sentRows:c.sentRows,closed:c.closed};});
          S._fifoSnap={internet:S.internet,armedInternet:armed.internet,pending:S.pending,
            outbox:S.outbox.map(o=>({chunk:o.chunk,n:o.n})),chunks:chunks,order:S.order.slice(),
            freeMB:S.freeMB,row:S.row,chunk:S.chunk};
          S.fifo=true;S.internet=false;
          const start=Math.max(1,S.chunk-24);
          const ids=[];for(let id=start;id<S.chunk;id++)if(!S.chunks[id])ids.push(id);
          const cur=S.order.filter(id=>id>=S.chunk);
          S.order=ids.concat(S.order.filter(id=>id<S.chunk),cur);
          ids.forEach(id=>{S.chunks[id]={rows:CFG.rowsPerChunk,sd:CFG.rowsPerChunk,laptop:CFG.rowsPerChunk,sentRows:0,closed:true};
                           S.outbox.unshift({chunk:id,n:CFG.rowsPerChunk});S.pending+=CFG.rowsPerChunk;});
          S.outbox.sort((a,b)=>a.chunk-b.chunk);
          S.freeMB=Math.round(CFG.cardMB*0.05);
          if(S.row<CFG.rowsPerChunk-40)S.row=CFG.rowsPerChunk-40,S.chunks[S.chunk].rows=S.row,S.chunks[S.chunk].sd=S.row,S.chunks[S.chunk].laptop=S.row;
          log('month-long isolation · '+ids.length+' chunks never ACKed · SD 95 % full · emergency circular FIFO armed','warn',
              {ev:'EXTENDED BLACKOUT · SD 95 % FULL · EMERGENCY CIRCULAR FIFO ARMED · OLDEST CHUNK ONLY',sev:'WARNING',actor:'DRILL'});},
        off(){
          const snap=S._fifoSnap;S._fifoSnap=null;S.fifo=false;
          if(snap){
            S.chunks=snap.chunks;S.order=snap.order;S.outbox=snap.outbox;S.pending=snap.pending;
            S.freeMB=snap.freeMB;S.row=snap.row;S.chunk=snap.chunk;S.internet=snap.internet;
            armed.internet=!!snap.armedInternet;
          }else S.freeMB=3712;
          log('FIFO drill cleared · card headroom restored · retained chunks stay until cloud ACK','ok');}}
};
const armed={internet:false,usb:false,sd:false,power:false,fifo:false};
function drill(kind,on){
  if(!DRILL[kind])return;
  const want=on===undefined?!armed[kind]:!!on;
  if(want===armed[kind]&&kind!=='power')return;
  armed[kind]=want;
  if(want)DRILL[kind].on(); else DRILL[kind].off();
  if(kind==='fifo'&&want)armed.internet=true;
  if(kind==='power'){armed.power=true;setTimeout(()=>{armed.power=false;paintDrills();},CFG.brownoutS*1000+200);}
  paintDrills();render();
}
function restoreAll(){
  if(armed.fifo)drill('fifo',false);
  ['sd','usb','internet','power'].forEach(k=>{if(armed[k])drill(k,false);});
  S.brownout=0;armed.power=false;
  paintDrills();render();
  if(S.live&&typeof pollExt==='function')pollExt();
}
function toast(m){try{if(window.NSD&&NSD.toast)NSD.toast(m);}catch(e){}}

let syncT=0;
function forceSync(){
  const b=$('bbxSync');
  const set=(cls,txt)=>{if(!b)return;b.className='btn sync'+(cls?' '+cls:'');b.innerHTML=txt;};
  const reset=()=>set('','⟳ &nbsp;FORCE CLOUD SYNC');
  clearTimeout(syncT);
  const finish=(ok,info)=>{
    if(ok===false&&info==='busy'){set('busy','⟳ &nbsp;DAEMON ALREADY UPLOADING · WAIT');syncT=setTimeout(reset,1600);return;}
    if(!ok){set('fail','✕ &nbsp;CLOUD UNREACHABLE · OUTBOX PROTECTED · SD RETAINED');}
    else if(info==='empty')set('done','✓ &nbsp;NOTHING PENDING · 100 % SYNCED');
    else set('done','✓ &nbsp;SYNCED · '+num(S.lastUpload?S.lastUpload.rows:0)+' ROWS · 200 OK'+(info?' · '+info+' CHUNK'+(info>1?'S':'')+' UNLINKED':''));
    syncT=setTimeout(reset,4200);
  };
  const linked=(typeof LINK!=='undefined')&&LINK&&(typeof apiUrl==='function');
  if(linked){
    set('busy','⟳ &nbsp;SYNCING · POST /api/cloud/force_sync');
    // Deliberately not the shared post() helper: it ends in .catch(()=>{}) so it
    // always resolves, and this console used to print "✓ SYNCED" even when the
    // gateway never accepted the sync or answered 404/503. A safety-officer
    // control has to report what actually came back, so check the status here.
    const ac=('AbortController' in window)?new AbortController():null;
    const to=ac?setTimeout(()=>ac.abort(),8000):null;
    fetch(apiUrl('/api/cloud/force_sync'),{method:'POST',headers:{'Content-Type':'application/json'},
          body:'{}',cache:'no-store',signal:ac?ac.signal:undefined})
      .then(r=>{ if(to)clearTimeout(to);
                 if(!r.ok)throw new Error('HTTP '+r.status);
                 return r.json().catch(()=>({})); })
      .then(j=>{
                 if(j&&j.ok===false)throw new Error(j.error||'refused');
                 const batchesFailed = j && typeof j.batches_failed === 'number' ? j.batches_failed : 0;
                 const uploaded = j && typeof j.uploaded === 'number' ? j.uploaded : 0;
                 const ackedN = j && Array.isArray(j.acked) ? j.acked.length : 0;
                 const follow=(typeof pollExt==='function')?pollExt():null;
                 const done=()=>{
                   if(batchesFailed>0)finish(false,'net');
                   else if(uploaded<=0&&ackedN<=0)finish(true,'empty');
                   else finish(true,ackedN);
                 };
                 if(follow&&typeof follow.then==='function')return follow.then(done,done);
                 done();
               })
      .catch(e=>{ if(to)clearTimeout(to); finish(false,'net'); });
    return;
  }
  set('busy','⟳ &nbsp;SYNCING · CLAIMING PENDING ROWS');
  runUpload(true,finish);
}

function ingest(raw){
  if(!raw||typeof raw!=='object')return;
  const b=raw.blackbox,c=raw.cloud;
  if(!b&&!c)return;
  const wasLive=S.live;S.live=true;S.liveAt=Date.now();
  if(drilling()){
    if(!wasLive)log('live gateway feed · blackbox + cloud state now from the Flask surface gateway','info');
    render();
    return;
  }
  if(b){
    if(b.sd_ok!==undefined)S.sd=!!b.sd_ok;
    if(b.usb_ok!==undefined)S.usb=!!b.usb_ok;
    if(b.power_ok!==undefined)S.brownout=b.power_ok?0:1;
    if(isFinite(b.chunk_id))S.chunk=+b.chunk_id;
    if(isFinite(b.row))S.row=+b.row;
    if(isFinite(b.on_card))S.onCard=+b.on_card;
    if(typeof b.archive==='string')S.archive=b.archive;
    if(isFinite(b.rows_per_chunk))CFG.rowsPerChunk=+b.rows_per_chunk;
    if(isFinite(b.free_mb))S.freeMB=+b.free_mb;
    if(isFinite(b.card_mb))CFG.cardMB=+b.card_mb;
    if(isFinite(b.ram_ring))S.ram=+b.ram_ring;
    if(Array.isArray(b.retained)){
      S.chunks={};S.order=[];S.reasons={};
      b.retained.forEach(r=>{const id=+r.chunk_id;if(!isFinite(id))return;
        S.chunks[id]={rows:+r.rows||0,sd:+r.rows||0,laptop:+r.rows||0,sentRows:0,closed:id!==S.chunk};S.order.push(id);
        /* The gate's refusal, as the surface computed it for *this* file a moment
           ago. It rides in the same payload as the row counts on purpose: a reason
           fetched separately can be seconds out of step with the numbers it is
           supposed to explain, and a panel that argues with itself is worse than
           one that says nothing. */
        if(r.reason)S.reasons[id]={reason:String(r.reason),releasable:!!r.releasable};});
      if(!S.chunks[S.chunk])newChunk(S.chunk,S.row);
    }
    if(isFinite(b.retained_total))S.retainedTotal=+b.retained_total;
    if(isFinite(b.retained_releasable))S.releasable=+b.retained_releasable;
    /* Set only when the *hub* is the reason — no card, or no answer. It outranks
       any per-chunk reason: when the card is out, every file is held for the same
       reason, and "350 rows not yet confirmed" would send an operator to the wrong
       subsystem. */
    S.deleteBlocked=typeof b.delete_blocked==='string'?b.delete_blocked:'';
    if(b.recovery&&typeof b.recovery==='object')recoveryIngest(b.recovery);
  }
  if(c){
    if(c.online!==undefined)S.internet=!!c.online;
    if(isFinite(c.pending))S.pending=+c.pending;
    if(isFinite(c.sent))S.sent=+c.sent;
    if(isFinite(c.next_upload_s))S.nextUp=+c.next_upload_s;
    if(c.last_upload)S.lastUpload={t:c.last_upload.ts||hms(),rows:+c.last_upload.rows||0,batches:+c.last_upload.batches||0,ok:c.last_upload.ok!==false};
    if(c.last_ack)S.lastAck={chunk:+c.last_ack.chunk_id||0,t:c.last_ack.ts||hms()};
  }
  if(!wasLive)log('live gateway feed · blackbox + cloud state now from the Flask surface gateway','info');
  render();
}

/* ── §5.7 recovery, on the deck ──────────────────────────────────────────────
   Four things are worth a line in the console log, and nothing else is:
   a handshake that actually told us something new, a REPLAY that has started,
   rows that came back, and a recovery that gave up. The rest of the block is
   rendered, not narrated — a log that repeats itself every 2 s is a log nobody
   reads, and this one has to be readable during an incident. */
function recoveryIngest(r){
  const hs=r.handshake||{}, rec=r.recovered||{}, act=(r.active||[])[0]||null, seen=S.recSeen;
  S.recovery={asks:+hs.asks||0,facts:+hs.new_facts||0,missingRows:+hs.missing_rows||0,
              inFlight:+r.in_flight||0,awaiting:+r.awaiting||0,
              recoveredRows:+rec.rows||0,recoveredChunks:+rec.chunks||0,
              exhausted:(r.exhausted||[]).slice(0,3),active:act,handshake:hs};

  if(seen.asks>=0&&S.recovery.facts>seen.facts){
    log('STATE? → reconciled with the hub · '+S.recovery.facts+' reply(ies) told us something new',
        'info',{ev:'BLACKBOX RECONCILED',sev:'INFO',actor:'BLACKBOX'});
  }
  if(act&&(act.chunk_id!==seen.chunk||act.state!==seen.state)){
    const why=act.state==='IN_FLIGHT'?' · '+num(act.rows_recovered)+' row(s) back so far'
             :act.state==='EXHAUSTED'?' · GIVING UP':'';
    log('REPLAY '+act.line+' · '+cname(act.chunk_id)+' · '+num(act.missing)+' row(s) missing'
        +(act.attempts>1?' · attempt '+act.attempts:'')+why,
        act.state==='EXHAUSTED'?'crit':'warn',
        {ev:'REPLAY '+(act.state==='EXHAUSTED'?'EXHAUSTED':'REQUESTED'),
         sev:act.state==='EXHAUSTED'?'CRITICAL':'WARNING',actor:'BLACKBOX'});
  }
  if(seen.rows>=0&&S.recovery.recoveredRows>seen.rows){
    const back=S.recovery.recoveredRows-seen.rows;
    log('card re-read · '+num(back)+' row(s) back-filled · '+num(S.recovery.missingRows)+' still missing',
        'ok',{ev:'ROWS BACK-FILLED',sev:'INFO',actor:'BLACKBOX'});
  }
  if(seen.asks>=0&&S.recovery.exhausted.length>(seen.exhausted||0)){
    log('recovery EXHAUSTED on '+S.recovery.exhausted.map(cname).join(' ')+
        ' · POST /api/blackbox/recover retries by hand','crit',
        {ev:'RECOVERY EXHAUSTED',sev:'CRITICAL',actor:'BLACKBOX'});
  }
  seen.asks=S.recovery.asks;seen.facts=S.recovery.facts;seen.rows=S.recovery.recoveredRows;
  seen.chunk=act?act.chunk_id:0;seen.state=act?act.state:'';
  seen.exhausted=S.recovery.exhausted.length;
}

const tsTxt=t=>(t instanceof Date)?hms(t):String(t||'—');
const R={s3:null,s4:null,s5:null,acked:[]};
const put=(id,t,col)=>{const el=$(id);if(!el)return;el.textContent=t;if(col!==undefined)el.style.color=col;};
function retainedClosed(){return S.order.filter(id=>id!==S.chunk&&S.chunks[id]&&S.chunks[id].closed);}
/* The one refusal worth putting on a caption line: the newest file the gate is
   still holding, and the gate's own sentence about it. A *long* reason is
   truncated on the line and complete in the tooltip — the number of characters a
   caption can hold is a layout fact, and the sentence itself is evidence. */
const whyShort=r=>r.length>72?r.slice(0,69)+'…':r;
function heldWhy(){
  if(S.deleteBlocked)return {id:null,reason:S.deleteBlocked};
  for(let i=S.order.length-1;i>=0;i--){
    const id=S.order[i];
    if(id===S.chunk)continue;
    const r=S.reasons[id];
    if(r&&!r.releasable)return {id:id,reason:r.reason};
  }
  return null;
}
function setChip(el,tx,cls,txt){if(!el)return;el.className='bbx-chip '+cls;if(tx)tx.textContent=txt;}
function render(){
  const closed=retainedClosed();
  const retainedRows=closed.reduce((a,id)=>a+S.chunks[id].sd,0);
  const synced=(S.sent+S.pending)>0?S.sent/(S.sent+S.pending)*100:100;
  const off=S.brownout>0;
  let bcls='ok',btxt;
  if(off){bcls='warn';btxt='BLACKBOX: BROWNOUT · LAST SECTOR COMMITTED · REBOOT IN '+S.brownout+' s';}
  else if(!S.sd){bcls='crit';btxt='BLACKBOX: FAULT / NO SD · RAM RING '+num(S.ram)+' ROWS';}
  else if(freePct()<=CFG.fifoPct){bcls='warn';btxt='BLACKBOX: ACTIVE ('+cname(S.chunk)+' · '+num(S.freeMB)+' MB free · FIFO ARMED)';}
  else if(!S.usb){bcls='warn';btxt='BLACKBOX: ACTIVE · ARCHIVING OFFLINE ('+cname(S.chunk)+' · '+closed.length+' file'+(closed.length===1?'':'s')+' held)';}
  else btxt='BLACKBOX: ACTIVE ('+cname(S.chunk)+' · '+num(S.freeMB)+' MB free)';
  setChip($('bbxChip'),$('bbxChipTx'),bcls,btxt);
  const dot=$('bbxChip')&&$('bbxChip').querySelector('i');if(dot)dot.className=bcls==='ok'?'pulse':'';
  let ccls='ok',ctxt;
  if(S.syncing){ccls='ok';ctxt='CLOUD: SYNCING · BATCH '+S.syncing.i+'/'+S.syncing.batches+' · HMAC-SHA256';}
  else if(!S.internet){ccls=S.pending>=1000?'crit':'warn';ctxt='CLOUD: '+num(S.pending)+' PENDING · OFFLINE';}
  else if(S.pending>=1000){ccls='warn';ctxt='CLOUD: '+num(S.pending)+' PENDING · DRAINING';}
  else if(!S.usb&&!S.pending){ccls='warn';ctxt='CLOUD: SYNCED · SURFACE BLIND · SERIAL CUT';}
  else ctxt='CLOUD: 100% SYNCED'+(S.pending?' · next push '+S.nextUp+' s':'');
  setChip($('cldChip'),$('cldChipTx'),ccls,ctxt);
  const pipe=$('bbxPipe');
  if(pipe){
    const st=[off?'off':'done',off?'off':'done',(!S.sd||off)?'off':'done',(!S.usb||off)?'off':'done',
              S.syncing?'on':(!S.internet?'hold':'done'),!S.internet?'off':(S.syncing?'on':'done'),
              !S.internet?'hold':(S.ackFlash?'on':(closed.length?'hold':'done'))];
    Array.prototype.forEach.call(pipe.children,(el,i)=>{
      let c=st[i];
      if(c==='done'&&S.phase===i)c='on';
      el.className=c;
    });
  }
  const T=(id,t,col)=>{const el=$(id);if(!el)return;el.textContent=t;if(col!==undefined)el.style.color=col;};
  T('bbxChunk',off?cname(S.chunk)+' · hub dark':cname(S.chunk)+' · row '+num(S.row)+' / '+num(CFG.rowsPerChunk)+(S.live?'':(!S.sd?' · RAM':'')));
  const hw=heldWhy(),retEl=$('bbxRetained');
  T('bbxRetained',closed.length+' of '+num(S.retainedTotal||closed.length)+' file'+((S.retainedTotal||closed.length)===1?'':'s')+' · '+kb(retainedRows)
      +(closed.length?' · '+(closed.length===1?cname(closed[0]):cname(closed[0])+'…'+cname(closed[closed.length-1])):'')
      +(hw?' · '+(hw.id?cname(hw.id)+' ':'')+'held: '+whyShort(hw.reason):''),
    hw||closed.length>3?'var(--amber-2)':'');
  if(retEl)retEl.title=hw?('Gate refuses the delete — '+hw.reason):'Every file on the card has been released by the gate';
  T('bbxOutbox',num(S.pending)+' PENDING · '+num(S.sent)+' SENT'+(S.replay>0?' · replay '+num(S.replay):''),(!S.internet&&S.pending)?'var(--amber-2)':'');
  const lu=S.lastUpload;
  T('bbxUpload',!lu?'—':lu.ok?(tsTxt(lu.t)+' · '+num(lu.rows)+' rows · '+lu.batches+' × ≤100 · gz −85 % · HMAC ✓ · 200 OK'):(tsTxt(lu.t)+' · HTTPS TIMEOUT · retry in '+S.nextUp+' s'),
    !lu?'':lu.ok?'':'#ff9d9d');
  T('bbxAck',S.lastAck?(cname(S.lastAck.chunk)+' · '+tsTxt(S.lastAck.t)+' · SD.remove 1 ms'):'—',(!S.internet&&closed.length)?'var(--amber-2)':'');
  T('bbxUsb',off?'HUB OFF · NO FRAMES':S.usb?'LINKED · 115200 · chunk_id JSON':'SEVERED · HUB ARCHIVING TO SD',off?'var(--amber-2)':S.usb?'var(--ok)':'#ff9d9d');
  const lg=$('bbxLog');
  if(lg)lg.innerHTML=S.log.slice(0,4).map(e=>'<div><span class="t">'+e.t+'</span><span class="'+e.cls+'">'+esc(e.txt)+'</span></div>').join('');
  const worst=(bcls==='crit'||ccls==='crit')?'crit':(bcls==='warn'||ccls==='warn')?'warn':'ok';
  const col=worst==='crit'?'var(--crit)':worst==='warn'?'var(--amber)':'var(--ok)';
  const bd=$('bbxDot');if(bd){bd.style.background=col;bd.style.boxShadow='0 0 10px '+col;}
  const fault=off?'hub dark · brownout':!S.sd?'NO SD · RAM ring buffer':!S.usb?'USB serial severed · hub archiving offline'
              :!S.internet?'cloud unreachable · '+num(S.pending)+' rows pending · deletes suspended'
              :freePct()<=CFG.fifoPct?'SD 95 % full · emergency FIFO':'';
  const ledEl=$('bbxLed');
  if(ledEl){
    ledEl.className='bbxled'+(fault?' crit':'');
    ledEl.title='Underground blackbox & cloud sync · '+(fault?'FAULT: '+fault:'ACTIVE · '+cname(S.chunk)+' · '+num(S.pending)+' rows pending')+' — click to open the operator deck';
    ledEl.setAttribute('aria-label','Blackbox and cloud sync '+(fault?'fault: '+fault:'active'));
  }
  T('bbxDotTx',fault?'PIPELINE FAULT':'PIPELINE ACTIVE');
  T('opBbx',off?'BROWNOUT · REBOOTING':!S.sd?'FAULT / NO SD':'ACTIVE · '+cname(S.chunk)+' · '+num(S.freeMB)+' MB',bcls==='ok'?'var(--ok)':bcls==='warn'?'var(--amber-2)':'#ff9d9d');
  T('opCloud',S.syncing?'SYNCING '+S.syncing.i+'/'+S.syncing.batches:!S.internet?num(S.pending)+' PENDING · OFFLINE':synced>=99.9&&S.pending<1000?'100% SYNCED':synced.toFixed(1)+'% · '+num(S.pending)+' PENDING',
    ccls==='ok'?'var(--ok)':ccls==='warn'?'var(--amber-2)':'#ff9d9d');
  T('lgStatBbx',!S.sd?'FAULT / NO SD · RAM ring':cname(S.chunk)+' · '+num(S.freeMB)+' MB free');
  T('lgStatCld',(!S.internet?'OFFLINE · ':'')+(S.pending>=1000||!S.internet?num(S.pending)+' rows pending':'100 % synced · outbox '+num(S.pending)+' pending'));
  const l1=$('lgLedBbx'),l2=$('lgLedCld');
  if(l1)l1.className='lg-led '+(bcls==='ok'?'g blink':'a');
  if(l2)l2.className='lg-led '+(ccls==='ok'?'g':'a');
  const ds=$('bbxDrillStatus');
  if(ds){
    const on=Object.keys(armed).filter(k=>armed[k]);
    const recLine=recoveryLine();
    const cardOut=!S.sd&&!armed.sd;
    ds.textContent=(on.length?on.map(k=>({internet:'NET DOWN',usb:'USB CUT',sd:'NO SD',power:'BROWNOUT',fifo:'FIFO 95 %'})[k]).join(' · ')+' · '+num(S.pending)+' PENDING · '+closed.length+' CHUNKS HELD':
      (cardOut?'SD CARD NOT CONNECTED · ':'ALL LINKS HEALTHY · ')+num(S.pending)+' PENDING · SD HOLDS '+closed.length+' CHUNK'+(closed.length===1?'':'S'))
      +(recLine?' · '+recLine:'');
    ds.style.color=(on.length||recLine||cardOut)?'var(--amber-2)':'';
  }
  renderConsole(closed,off,bcls);
}
/* The recovery clause on the status line: what the surface is doing about the
   rows it knows it is missing. `EXHAUSTED` is the one that must be loud — it
   means an automatic repair has given up and a human has to press the button. */
function recoveryLine(){
  const r=S.recovery;
  if(!r||!r.asks)return '';
  if(r.exhausted.length)return 'RECOVERY EXHAUSTED ON '+r.exhausted.map(cname).join(' ');
  if(r.active)return 'RECOVERING '+r.active.line+' · '+num(r.active.missing)+' ROWS MISSING'
    +(r.active.rows_recovered?' · '+num(r.active.rows_recovered)+' BACK':'');
  if(r.missingRows>0)return num(r.missingRows)+' ROWS MISSING · '+r.awaiting+' CHUNK(S) QUEUED';
  if(r.recoveredRows>0)return num(r.recoveredRows)+' ROWS BACK-FILLED FROM THE CARD';
  return '';
}

/* POST /api/blackbox/recover — the manual half of §5.7 (the automatic half is the
   handshake on reconnect and the upkeep sweep). Exposed on `BBX` so it can be
   called from the console and from a future button; the pump's own controls are
   the ones already in the DOM. */
function recover(){
  const linked=(typeof LINK!=='undefined')&&LINK&&(typeof apiUrl==='function');
  if(!linked){log('recover needs the live gateway (LINK) — this panel is in demo mode','warn');render();return;}
  log('POST /api/blackbox/recover · asking the hub what it holds','info',{ev:'RECONCILE REQUESTED',actor:'OPERATOR'});
  fetch(apiUrl('/api/blackbox/recover'),{method:'POST',headers:{'Content-Type':'application/json'},
        body:'{}',cache:'no-store'})
    .then(r=>{ if(!r.ok)throw new Error('HTTP '+r.status); return r.json(); })
    .then(j=>{ log((j.replay_lines&&j.replay_lines.length?'REPLAY burst: '+j.replay_lines.join(' '):'no gaps to recover')
                 +' · '+num(j.missing_rows||0)+' row(s) missing',
                 j.ok===false?'crit':'ok',{ev:'RECONCILE',sev:j.ok===false?'WARNING':'INFO',actor:'OPERATOR'});
               if(typeof pollExt==='function')return pollExt(); })
    .catch(e=>log('recover failed · '+e.message,'crit',{ev:'RECONCILE FAILED',sev:'CRITICAL'}));
  render();
}

function renderConsole(closed,off,bcls){
  const hw=heldWhy();
  const chip=$('bbxChunksChip');
  if(chip){chip.textContent=closed.length+' CHUNK'+(closed.length===1?'':'S')+' PENDING';chip.className='chip '+(closed.length?(S.internet?'warn':'crit'):'ok');}
  put('bbxQueueN',num(S.pending));
  const bar=$('bbxQueueBar');
  if(bar){bar.style.width=Math.min(100,S.pending/CFG.rowsPerChunk*100).toFixed(1)+'%';bar.className=S.syncing?'on':!S.internet?(S.pending>=CFG.rowsPerChunk?'crit':'warn'):S.pending>=CFG.rowsPerChunk?'warn':'';}
  put('bbxQueueCap',
    S.syncing?'Uploading · batch '+S.syncing.i+'/'+S.syncing.batches+' · GZIP + HMAC-SHA256':
    !S.internet?'Cloud unreachable · '+num(S.pending)+' rows held · ACK_DELETE suspended':
    !S.sd?'SD card fault · RAM ring '+num(S.ram)+' rows · outbox '+num(S.pending)+' pending':
    !S.usb?'Serial cut · surface blind · hub archiving to SD':
    S.pending<=0?(hw?('Sync complete · files held · '+whyShort(hw.reason)):'All chunks synced · SD card nominal'):
    num(S.pending)+' rows queued · next push in '+S.nextUp+' s');
  const sdc=off?'var(--amber-2)':!S.sd?'#ff9d9d':bcls==='warn'?'var(--amber-2)':'var(--ok)';
  put('bbxSdStatus',off?'BROWNOUT · REBOOT IN '+S.brownout+' s':!S.sd?'NO SD · RAM RING '+num(S.ram):freePct()<=CFG.fifoPct?'RECORDING · FIFO ARMED':'RECORDING',sdc);
  put('bbxSdChunk',cname(S.chunk));
  put('bbxSdRows',num(S.row)+' / '+num(CFG.rowsPerChunk));
  const sealed=S.onCard!=null?Math.max(0,S.onCard-1):Math.max(0,S.chunk-1);
  put('bbxSdSealed',num(sealed));
  const place=$('bbxSdPlace');
  if(place){
    const vol=S.archive?S.archive.split('/').filter(Boolean).pop():'';
    place.textContent=vol?('LAPTOP SD · '+vol):'MICROSD BLACKBOX · VSPI BUS';
  }
  const used=Math.max(0,CFG.cardMB-S.freeMB);
  put('bbxSdUsage',(used>=1024?(used/1024).toFixed(2)+' GB':Math.round(used)+' MB')+' / '+(CFG.cardMB/1024).toFixed(1)+' GB',freePct()<=CFG.fifoPct?'var(--amber-2)':'');
  put('bbxSdQueue',off?'HALTED · HUB DARK':!S.sd?'BACKPRESSURE · RAM RING':'HEALTHY · 24-byte records',off?'#ff9d9d':!S.sd?'var(--amber-2)':'var(--ok)');
  const step=(id,t,cls)=>{const li=$(id);if(!li)return;li.className=cls||'';li.querySelector('b').textContent=t;};
  step('bbxRs1',S.internet?(S.lastAck?pad4(S.lastAck.chunk)+' ✓':'— ✓'):'UNREACHABLE ✗',S.internet?'ok':'crit');
  const ids=S.order.slice(0,4).map(pad4).join(', ')+(S.order.length>4?' … +'+(S.order.length-4):'');
  step('bbxRs2',S.sd?(ids||'NONE')+' ✓':'NO SD ✗',S.sd?'ok':'crit');
  if(S.live){
    const lu=S.lastUpload;
    step('bbxRs3',closed.length?cname(closed[0]):'—',closed.length?'on':'');
    step('bbxRs4',lu?(lu.ok?'200 OK · '+num(lu.rows)+' ROWS ✓':'HTTPS TIMEOUT ✗'):'—',lu?(lu.ok?'ok':'crit'):'');
    step('bbxRs5',S.lastAck?pad4(S.lastAck.chunk)+' · '+tsTxt(S.lastAck.t)+' ✓':'—',S.lastAck?'ok':'');
  }else{
    step('bbxRs3',R.s3?R.s3.t+(R.s3.c==='ok'?' ✓':''):'—',R.s3?R.s3.c:'');
    step('bbxRs4',R.s4?R.s4.t+(R.s4.c==='ok'?' ✓':R.s4.c==='crit'?' ✗':''):'—',R.s4?R.s4.c:'');
    step('bbxRs5',R.s5?R.s5.t+(R.s5.c==='ok'?' ✓':''):'—',R.s5?R.s5.c:'');
  }
  const box=$('bbxSsot');
  if(box){
    const st=!S.sd?['crit','STATELESS · NO SD · CLOUD = SSOT']:!S.internet?['warn','STATELESS · CLOUD UNREACHABLE · SD = SSOT']:
             !S.usb?['warn','STATELESS · SERIAL CUT · SD = SSOT']:['','STATELESS · CLOUD + SD = SSOT'+(S.live?' · LIVE GATEWAY':'')];
    box.className='bbx-ssot'+(st[0]?' '+st[0]:'');put('bbxSsotTx',st[1]);
  }
}
function download(name,text,type){
  const b=new Blob([text],{type:type||'text/csv;charset=utf-8'});
  const u=URL.createObjectURL(b),a=document.createElement('a');
  a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(u),2000);
}
function dumpCsv(){
  const lines=[],ser=(typeof series!=='undefined')?series:null,now=Date.now();
  let count=0;
  if(ser&&ser.node_1&&ser.node_1.acceleration&&ser.node_1.acceleration.length){
    lines.push('chunk_id,row,ts_ms,node,tilt_x,displacement_mm,acceleration,flag');
    const n=ser.node_1.acceleration.length,nodes=['node_1','node_2'].filter(k=>ser[k]),total=n*nodes.length;
    const head=S.chunk*CFG.rowsPerChunk+S.row;
    let k=0;
    for(let i=0;i<n;i++)nodes.forEach((nd,j)=>{
      const s=ser[nd],abs=head-(total-1-k);k++;
      const a=+s.acceleration[i]||0;
      lines.push([Math.floor(abs/CFG.rowsPerChunk),abs%CFG.rowsPerChunk,now-(n-1-i)*500,j+1,(+s.tilt_x[i]||0).toFixed(2),(+s.displacement_mm[i]||0).toFixed(2),a.toFixed(3),a>=3?2:a>=0.25?1:0].join(','));
    });
    count=total;
  }else{
    lines.push('chunk_id,file,rows,rows_on_sd,rows_at_laptop,rows_sent,status');
    S.order.forEach(id=>{const c=S.chunks[id];lines.push([id,cname(id)+'.csv',c.rows,c.sd,c.laptop,c.sentRows,id===S.chunk?'OPEN':'SEALED'].join(','));count++;});
  }
  const name='blackbox_'+cname(S.chunk)+'_'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.csv';
  download(name,lines.join('\n')+'\n');
  log('blackbox log dumped · '+name+' · '+num(count)+' rows','info',{ev:'BLACKBOX LOG DUMPED · '+name.toUpperCase()+' · '+num(count)+' ROWS',sev:'INFO',actor:'SAFETY OFFICER'});
  toast('<b>'+esc(name)+'</b> · '+num(count)+' rows saved to your downloads folder.');
  render();
}
function rearm(){
  restoreAll();
  const flushed=S.ram;
  if(flushed&&S.sd&&S.chunks[S.chunk])S.chunks[S.chunk].sd+=flushed;
  S.ram=0;S.ramDropped=0;R.s3=R.s4=R.s5=null;R.acked=[];
  if(S.brownout>1)S.brownout=1;
  clearTimeout(syncT);const b=$('bbxSync');if(b){b.className='btn sync';b.innerHTML='⟳ &nbsp;FORCE CLOUD SYNC';}
  log('system re-armed · drills cleared · RAM ring flushed · outbox '+num(S.pending)+' rows retained (zero-loss)','ok',
      {ev:'BLACKBOX CONSOLE · SYSTEM RE-ARMED · BUFFERS CLEARED · OUTBOX '+num(S.pending)+' ROWS RETAINED',sev:'INFO',actor:'SAFETY OFFICER'});
  render();
  if(!rearm._busy&&window.__NS&&typeof window.__NS.rearm==='function'){
    rearm._busy=true;
    try{window.__NS.rearm();}finally{rearm._busy=false;}
  }
}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
function paintDrills(){
  document.querySelectorAll('#bbxDrills [data-drill]').forEach(b=>{
    const on=!!armed[b.dataset.drill];
    b.classList.toggle('armed',on);b.setAttribute('aria-pressed',on?'true':'false');
  });
}
function summary(){
  const closed=retainedClosed();
  return cname(S.chunk)+' open (row '+num(S.row)+') · '+num(S.pending)+' rows pending in outbox · '+
    closed.length+' SD chunk'+(closed.length===1?'':'s')+' awaiting cloud ACK · last ACK_DELETE '+
    (S.lastAck?cname(S.lastAck.chunk)+' '+tsTxt(S.lastAck.t):'—')+' · '+(S.internet?'cloud link up':'CLOUD LINK DOWN');
}

document.querySelectorAll('#bbxDrills [data-drill]').forEach(b=>b.addEventListener('click',()=>drill(b.dataset.drill)));
const rb=$('bbxRestoreBtn');if(rb)rb.addEventListener('click',restoreAll);
const sb=$('bbxSync');if(sb)sb.addEventListener('click',forceSync);
const db=$('bbxDumpBtn');if(db)db.addEventListener('click',dumpCsv);
const ab=$('bbxRearmBtn');if(ab)ab.addEventListener('click',rearm);
const led=$('bbxLed');
if(led)led.addEventListener('click',()=>{
  try{window.show('operator');}catch(e){}
  setTimeout(()=>{try{if(window.NSD&&NSD.setTab)NSD.setTab('operator','blackbox');}catch(e){}},60);
});
render();paintDrills();
setInterval(tick,1000);
return {state:S,cfg:CFG,ingest,forceSync,recover,drill,restoreAll,dumpCsv,rearm,summary,render};
})();
