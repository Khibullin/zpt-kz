(function(){
'use strict';
const form=document.getElementById('request-filter-form');if(!form)return;
const initial=JSON.parse(document.getElementById('request-filter-data').textContent);
const catalog=JSON.parse(document.getElementById('request-filter-catalog').textContent);
const chosen=new Map((initial.brands||[]).map(b=>[b.id,{...b,model_ids:new Set(b.model_ids)}]));
const categories=new Set(initial.category_ids||[]);
const $=id=>document.getElementById(id);
const mode=name=>form.querySelector('input[name="'+name+'"]:checked')?.value;
const transports=()=>Array.from(form.querySelectorAll('[name=transport]:checked'),x=>x.value);
const activeBrands=()=>catalog.brands.filter(b=>chosen.has(b.id)&&transports().includes(b.transport_type));
function node(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;}
function checkbox(text,checked,fn){const label=node('label'),input=node('input');input.type='checkbox';input.checked=checked;input.addEventListener('change',()=>fn(input.checked));label.append(input,document.createTextNode(text));return label;}
form.querySelectorAll('[name=transport]').forEach(e=>{e.checked=initial.transport_types.includes(e.value);e.addEventListener('change',()=>{renderBrands();renderModels();summary();});});
form.querySelector('[name=brands_mode][value="'+(initial.all_brands?'all':'selected')+'"]').checked=true;
form.querySelector('[name=categories_mode][value="'+(initial.all_categories?'all':'selected')+'"]').checked=true;
function brandLabel(b){return b.name+' · '+(b.transport_type==='truck'?'грузовые':'легковые');}
function renderBrands(){
 const list=$('brand-list');list.replaceChildren();const q=$('brand-search').value.trim().toLocaleLowerCase();
 catalog.brands.filter(b=>transports().includes(b.transport_type)&&b.name.toLocaleLowerCase().includes(q)).forEach(b=>{
 list.append(checkbox(brandLabel(b),chosen.has(b.id),checked=>{if(checked)chosen.set(b.id,{id:b.id,all_models:true,model_ids:new Set()});else chosen.delete(b.id);renderModels();summary();}));
 });
 if(!list.children.length)list.append(node('p',transports().length?'Марки не найдены.':'Сначала выберите тип транспорта.'));
}
function renderModels(){
 const list=$('model-list');list.replaceChildren();
 if(mode('brands_mode')==='all'){list.append(node('p','Все модели всех марок'));return;}
 if(!activeBrands().length){list.append(node('p','Сначала выберите марки автомобилей.'));return;}
 activeBrands().forEach(b=>{
 const state=chosen.get(b.id),box=node('div');box.className='rf-model';box.append(node('strong',brandLabel(b)));
 const details=node('details'),heading=node('summary','Выбрать модели'),options=node('div');options.className='rf-options';
 box.append(checkbox('Все модели',state.all_models,checked=>{state.all_models=checked;details.hidden=checked;details.open=!checked;summary();}));
 details.hidden=state.all_models;details.open=!state.all_models;details.append(heading);
 const search=node('input');search.type='search';search.placeholder='Поиск модели';search.setAttribute('aria-label','Поиск модели '+b.name);details.append(search,options);
 function draw(){options.replaceChildren();const q=search.value.trim().toLocaleLowerCase();catalog.models.filter(m=>m.brand_id===b.id&&m.name.toLocaleLowerCase().includes(q)).forEach(m=>options.append(checkbox(m.name,state.model_ids.has(m.id),checked=>{if(checked)state.model_ids.add(m.id);else state.model_ids.delete(m.id);summary();})));if(!options.children.length)options.append(node('p','Модели не найдены.'));}
 search.addEventListener('input',draw);draw();box.append(details);list.append(box);
 });
}
catalog.categories.forEach(c=>$('category-list').append(checkbox(c.name,categories.has(c.id),checked=>{if(checked)categories.add(c.id);else categories.delete(c.id);summary();})));
function summary(){
 $('brand-picker').hidden=mode('brands_mode')==='all';$('category-list').hidden=mode('categories_mode')==='all';
 const brands=mode('brands_mode')==='all'?'Все марки и модели':activeBrands().map(b=>{const s=chosen.get(b.id);return b.name+': '+(s.all_models?'все модели':catalog.models.filter(m=>m.brand_id===b.id&&s.model_ids.has(m.id)).map(m=>m.name).join(', ')||'модели не выбраны');}).join('; ')||'Марки не выбраны';
 const cats=mode('categories_mode')==='all'?'Все категории':catalog.categories.filter(c=>categories.has(c.id)).map(c=>c.name).join(', ')||'Категории не выбраны';
 $('filter-summary').textContent=[transports().map(t=>t==='car'?'Легковые':'Грузовые').join(', ')||'Тип транспорта не выбран',brands,cats].join(' · ');
}
form.querySelectorAll('[name=brands_mode],[name=categories_mode]').forEach(e=>e.addEventListener('change',()=>{renderModels();summary();}));
$('brand-search').addEventListener('input',renderBrands);
form.addEventListener('submit',event=>{
 if($('filter-save').disabled){event.preventDefault();return;}
 const data={transport_types:transports(),all_brands:mode('brands_mode')==='all',all_categories:mode('categories_mode')==='all',category_ids:Array.from(categories),brands:activeBrands().map(b=>{const s=chosen.get(b.id);return {id:b.id,all_models:s.all_models,model_ids:Array.from(s.model_ids)};})};
 let error='';if(!data.transport_types.length)error='Выберите тип транспорта.';else if(!data.all_brands&&!data.brands.length)error='Выберите хотя бы одну марку.';else if(!data.all_brands&&data.brands.some(b=>!b.all_models&&!b.model_ids.length))error='Выберите модели или отметьте «Все модели».';else if(!data.all_categories&&!data.category_ids.length)error='Выберите хотя бы одну категорию.';
 if(error){event.preventDefault();$('filter-error').textContent=error;$('filter-error').hidden=false;$('filter-error').scrollIntoView({block:'center'});return;}
 $('filter-payload').value=JSON.stringify(data);$('filter-save').disabled=true;$('filter-save').textContent='Сохраняем…';
});
window.addEventListener('pageshow',()=>{$('filter-save').disabled=false;$('filter-save').textContent='Сохранить настройки';});
renderBrands();renderModels();summary();
})();
