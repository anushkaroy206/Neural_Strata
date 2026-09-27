    (function(){
      function pad(n){return (n<10?'0':'')+n;}
      function clock(){
        var el=document.getElementById('lgClock');
        if(!el)return;
        try{
          var t=new Date().toLocaleTimeString('en-GB',{timeZone:'Asia/Kolkata',hour12:false});
          el.textContent=t;
        }catch(e){
          var d=new Date();el.textContent=pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());
        }
      }
      clock();setInterval(clock,1000);
      function ready(){return document.body.getAttribute('data-auth')==='ready';}
      function later(){
        if(ready())return;
        var f=document.getElementById('lgFallback');
        if(!f)return;
        f.classList.add('on');
        var s=document.getElementById('lgSkip');
        if(s&&!s.__w){s.__w=1;s.addEventListener('click',function(){
          document.body.classList.remove('nsLocked');
          document.body.setAttribute('data-auth','bypassed');
          var n=document.createElement('div');
          n.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:99999;'+
            'padding:10px 15px;border-radius:12px;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;'+
            'background:rgba(10,14,20,.96);color:#ffd9a3;border:1px solid rgba(255,149,56,.5);max-width:92vw';
          n.textContent='READ-ONLY PREVIEW · the sign-in layer is unavailable in this browser, so no role is signed in.';
          document.body.appendChild(n);
        });}
      }
      setTimeout(later,3500);
      document.addEventListener('DOMContentLoaded',function(){setTimeout(later,3000);});
    })();
    
