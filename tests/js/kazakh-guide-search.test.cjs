const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const navigation=fs.readFileSync('static/interface/navigation.js','utf8');
const translations=JSON.parse(navigation.match(/const translations = (\{.*?\});/)[1]);
const guide=fs.readFileSync('static/js/zpt-guide-v2.js','utf8');
const start=guide.indexOf('function searchFaq(query)');
const search=guide.slice(start,guide.indexOf("document.addEventListener('click'",start));
let found,home=false;
const question='Как купить запчасть на сайте?';
const context=vm.createContext({
 window:{ZPTI18n:{translateText:value=>translations[value]||value}},
 items:[{id:'retail-buy',question,answer:'Откройте карточку'}],
 normalize:value=>String(value||'').toLowerCase(),
 showHome:()=>{home=true;},
 showResults:(title,ids)=>{found=Array.from(ids);}
});
vm.runInContext(search,context);
context.searchFaq('сатып алуға');assert.deepEqual(found,['retail-buy']);
context.searchFaq('КУПИТЬ');assert.deepEqual(found,['retail-buy']);
context.searchFaq('abc-no-result');assert.deepEqual(found,[]);
context.searchFaq(' ');assert.equal(home,true);
console.log('Guide search: Kazakh, Russian, empty query and no matches passed');
