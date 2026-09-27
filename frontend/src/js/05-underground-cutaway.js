window.UG=(function(){
  const api={
    routes:{route_A_status:'OPEN',route_B_status:'OPEN'},
    crew:null, siren:null, panic:false, running:true,
    sel:null, selT:0, meshStat:Array(window.NS_MESH?NS_MESH.count:10).fill('NOMINAL'), routes3d:null
  };
  const scene={
    wantRender:true, levelMode:'all',
    tick(){},
    setLevel(l){ scene.levelMode=l; if(window.UG3D) window.UG3D.setLevel(l); },
    goTo(){}
  };
  function selectCell(i,focus){ return window.UG3D ? window.UG3D.selectCell(i,focus) : null; }
  return {scene, api, selectCell, MON:[]};
})();
window.UG_SAFE=function(rA,rB){
  const aB=rA==='BLOCKED', bB=rB==='BLOCKED';
  if(aB&&bB)return{shelter:true,lvl:'ALL',a:null,b:null};
  return{shelter:false,lvl:aB?'1':bB?'2':null,a:null,b:null};
};
