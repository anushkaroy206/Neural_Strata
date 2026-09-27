window.NS_MESH=(function(){
  const nums=[3,4,5,6,7,8,9,10,11,12];
  const levelOf=n=>n<=6?1:2;
  const jbOf=n=>n<=3?1:n<=6?2:n<=9?3:4;
  return {
    nums:nums,
    count:nums.length,
    ids:nums.map(n=>'E'+n),
    levelOf:levelOf,
    jbOf:jbOf,
    idxOf:n=>nums.indexOf(n),
    ofLevel:l=>nums.map((n,i)=>levelOf(n)===l?i:-1).filter(i=>i>=0),
    ofJb:j=>nums.map((n,i)=>jbOf(n)===j?i:-1).filter(i=>i>=0),
    label:'E3–E12'
  };
})();
