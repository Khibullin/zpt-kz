(function(){
'use strict';
const form=document.getElementById('selection-form');if(!form)return;
const status=document.getElementById('selection-status'),csrf=form.querySelector('[name=csrfmiddlewaretoken]').value;
let queue=Promise.resolve(),pending=0,failed=false;
function showCount(count){document.querySelectorAll('[data-selection-count]').forEach(el=>{el.textContent=count;});}
form.querySelectorAll('[name=seller_ids]').forEach(box=>box.addEventListener('change',()=>{
 const checked=box.checked;pending++;box.disabled=true;status.textContent='Сохраняем выбор…';
 queue=queue.then(async()=>{
  try{
   const body=new FormData();body.set('action','toggle');body.set('seller_id',box.value);body.set('checked',checked?'yes':'no');
   const response=await fetch(location.pathname+location.search,{method:'POST',body,credentials:'same-origin',headers:{'X-CSRFToken':csrf}});
   if(!response.ok){let data;try{data=await response.json();}catch(_){}throw Error(data?.error||'Не удалось сохранить выбор.');}
   const data=await response.json();showCount(data.selected_count);box.closest('[data-seller-row],tr').classList.toggle('mp-selected',checked);
  }catch(error){failed=true;box.checked=!checked;status.textContent=error.message+' Обновите страницу перед продолжением.';}
  finally{pending--;box.disabled=false;if(!pending&&!failed)status.textContent='Выбор сохранён';}
 });
}));
document.querySelectorAll('a[data-selection-navigation]').forEach(link=>link.addEventListener('click',async event=>{
 if(!pending&&!failed)return;event.preventDefault();await queue;if(!failed)location.href=link.href;
}));
document.querySelectorAll('form').forEach(target=>target.addEventListener('submit',async event=>{
 if(!pending&&!failed)return;event.preventDefault();const submitter=event.submitter;await queue;if(!failed)target.requestSubmit(submitter);
}));
window.addEventListener('beforeunload',event=>{if(pending){event.preventDefault();event.returnValue='';}});
})();
