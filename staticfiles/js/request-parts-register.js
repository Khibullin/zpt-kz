const API = window.ZPT_CONFIG.apiBase.replace(/\/$/, '');
const cities=['Алматы','Астана','Шымкент','Караганда','Актобе','Атырау','Актау','Павлодар','Усть-Каменогорск','Семей','Костанай','Петропавловск','Кокшетау','Тараз','Туркестан','Кызылорда','Уральск','Талдыкорган'];
const el=id=>document.getElementById(id);
function setMessage(text,type){el('msg').className='msg '+type;el('msg').innerText=text;}
function normalizePhone(v){return (v||'').replace(/\D/g,'');}
function checkedIds(cls){return [...document.querySelectorAll('.'+cls+':checked')].map(x=>parseInt(x.value));}
function renderChecks(box,data,cls){
  const container=el(box);
  ZPTDom.clearElement(container);
  if(!data||!data.length){
    const hint=document.createElement('div');
    hint.className='hint';
    hint.textContent='Нет данных';
    container.appendChild(hint);
    return;
  }
  ZPTDom.renderCheckboxLabels(container,data,{inputClass:cls});
}
async function getJson(url){let r=await fetch(url);return await r.json();}
async function loadBase(){ZPTDom.fillSelectFromStrings(el('city'),cities,'Выберите город');let countries=await getJson(API+'/countries/');let cats=await getJson(API+'/part-categories/');renderChecks('countriesBox',countries,'countrycheck');renderChecks('categoriesBox',cats,'catcheck');document.querySelectorAll('.countrycheck').forEach(x=>x.onchange=loadBrands);await loadBrands();}
async function loadBrands(){let tt=el('transport_type').value;let countryIds=checkedIds('countrycheck');let brands=[];if(el('all_countries').checked||countryIds.length===0){brands=await getJson(API+'/brands-by-country/?transport_type='+tt);}else{for(let id of countryIds){let d=await getJson(API+'/brands-by-country/?country_id='+id+'&transport_type='+tt);brands=brands.concat(d);}}let map=new Map();brands.forEach(b=>map.set(b.id,b));renderChecks('brandsBox',[...map.values()],'brandcheck');document.querySelectorAll('.brandcheck').forEach(x=>x.onchange=loadModels);await loadModels();}
async function loadModels(){let tt=el('transport_type').value;let ids=checkedIds('brandcheck');let models=[];if(ids.length===0||el('all_brands').checked){renderChecks('modelsBox',[],'modelcheck');return;}for(let id of ids){let d=await getJson(API+'/models-by-brand/?brand_id='+id+'&transport_type='+tt);models=models.concat(d);}let map=new Map();models.forEach(m=>map.set(m.id,m));renderChecks('modelsBox',[...map.values()],'modelcheck');}
el('all_countries').onchange=()=>{el('countriesBox').classList.toggle('disabled',el('all_countries').checked);loadBrands();};el('all_brands').onchange=()=>{el('brandsBox').classList.toggle('disabled',el('all_brands').checked);loadModels();};el('all_models').onchange=()=>el('modelsBox').classList.toggle('disabled',el('all_models').checked);el('all_categories').onchange=()=>el('categoriesBox').classList.toggle('disabled',el('all_categories').checked);el('transport_type').onchange=loadBrands;el('showPass').onclick=()=>{let show=el('password').type==='password';el('password').type=show?'text':'password';el('password2').type=show?'text':'password';el('showPass').innerText=show?'Скрыть':'Показать';};
el('sellerForm').addEventListener('submit',async e=>{e.preventDefault();let password=el('password').value;let password2=el('password2').value;if(password.length<6){setMessage('Пароль минимум 6 символов','error');return;}if(password!==password2){setMessage('Пароли не совпадают','error');return;}let payload={name:el('name').value.trim(),whatsapp:normalizePhone(el('whatsapp').value),password,password_confirm:password2,transport_type:el('transport_type').value,city:el('city').value,market_location:el('market_location').value,selected_category_ids:checkedIds('catcheck'),selected_country_ids:checkedIds('countrycheck'),selected_brand_ids:checkedIds('brandcheck'),selected_model_ids:checkedIds('modelcheck'),all_categories:el('all_categories').checked,all_countries:el('all_countries').checked,all_brands:el('all_brands').checked,all_models:el('all_models').checked};let r=await fetch(API+'/create-seller/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),credentials:'include'});let data=await r.json();if(data.error){setMessage(data.error,'error');return;}setMessage('Регистрация успешна. Теперь можно войти в кабинет продавца.','success');});
loadBase();
