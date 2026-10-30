const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/js/zpt-pwa-install-v2.js','utf8');
const navigation=fs.readFileSync('static/interface/navigation.js','utf8');
const translations=JSON.parse(navigation.match(/const translations = (\{.*?\});/)[1]);
const functions=source.slice(source.indexOf('  function isStandalone()'),source.indexOf('  function wasDismissedThisVisit()'));
function browser(agent,platform='',touch=0,standalone=false){
 const c=vm.createContext({window:{navigator:{userAgent:agent,platform,maxTouchPoints:touch,standalone},matchMedia:()=>({matches:false})}});
 vm.runInContext(functions,c);return c;
}
for(const [agent,platform,touch,kind] of [
 ['Mozilla Android Chrome/120','Linux',0,'android'],
 ['Mozilla iPhone Safari/605','iPhone',1,'ios'],
 ['Mozilla Safari/605','MacIntel',5,'ios'],
 ['Mozilla Android Instagram','Linux',0,'webview'],
 ['Mozilla Chrome/120','Linux',0,'desktop'],
 ['Mozilla Firefox/120','Linux',0,'desktop']]){
 const c=browser(agent,platform,touch);assert.equal(c.detectedKind(),kind);
 const ru=c.instructionsFor(kind);assert.ok(ru.length>0);assert.ok(translations[ru]);
}
assert.equal(browser('Chrome','Linux',0,true).detectedKind(),'standalone');
console.log('PWA: Android, iPhone, iPad, embedded browser, desktop, standalone and Kazakh instructions passed');
