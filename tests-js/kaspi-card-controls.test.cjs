const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function setup(){
  const copied=[];
  const amount={value:'9700',listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};
  const mode={value:'algorithm',listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};
  const buttons=['title','description','attributes','applicability','price','all'].map(key=>({dataset:{copy:key},addEventListener(name,fn){this.click=fn;}}));
  const payload={title:'Точная деталь',description:'Описание',brand:'WXQP',article:'00123',attributes:{Материал:'Сталь'},applicability:'Toyota',price:'9700',recommended:'9700'};
  vm.runInNewContext(fs.readFileSync('procurement/static/procurement/kaspi-cards-v1.js','utf8'),{
    document:{querySelectorAll:s=>s==='[data-copy]'?buttons:[],
      getElementById:id=>id==='kc-payload'?{textContent:JSON.stringify(payload)}:null,
      querySelector:s=>s==='[data-price-form]'?{querySelector:s=>s==='[name=amount]'?amount:mode}:null},
    navigator:{clipboard:{writeText:async text=>copied.push(text)}}, window:{isSecureContext:true}
  });
  return {amount,mode,buttons,copied};
}
test('editing calculated price enters manual mode; selecting algorithm restores recommendation',()=>{
  const {amount,mode}=setup();amount.value='9900';amount.listeners.input();assert.equal(mode.value,'manual');
  mode.value='algorithm';mode.listeners.change();assert.equal(amount.value,'9700');
});
test('each Kaspi field copies separately without mixing descriptions and attributes',async()=>{
  const {buttons,copied}=setup();await buttons[0].click();await buttons[1].click();await buttons[2].click();
  assert.deepEqual(copied,['Точная деталь','Описание','Материал: Сталь']);
});
