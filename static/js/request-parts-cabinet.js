const API = window.ZPT_CONFIG.apiBase.replace(/\/$/, '');
let currentPeriod='7d';
let currentStatus='all';
let allRequests=[];
let sellerProfileData=null;
let profileEditMode=false;
let dictionariesLoaded=false;
let countriesDict=[];
let categoriesDict=[];
let brandsDict=[];

function normalizePhone(v){return String(v||'').replace(/\D/g,'')}
function showLogin(){loginBox.classList.remove('hidden');cabinetApp.classList.add('hidden')}
function showCabinet(){loginBox.classList.add('hidden');cabinetApp.classList.remove('hidden')}
function labelStatus(s){if(s==='Новая'||s==='prepared')return 'Новая';if(s==='Отправлена'||s==='sent')return 'Отправлена';if(s==='Просмотрена'||s==='viewed')return 'Просмотрена';if(s==='В работе'||s==='contacted')return 'Связался';if(s==='Закрыта'||s==='done')return 'Отказ';return s||'Новая'}
function statusClass(s){let t=labelStatus(s);if(t==='Новая')return 'badge-new';if(t==='Отправлена')return 'badge-sent';if(t==='Просмотрена')return 'badge-viewed';if(t==='Связался')return 'badge-contacted';if(t==='Отказ')return 'badge-done';return 'badge-new'}
function statusKey(s){let t=labelStatus(s);if(t==='Новая')return 'new';if(t==='Отправлена')return 'sent';if(t==='Просмотрена')return 'viewed';if(t==='Связался')return 'contacted';if(t==='Отказ')return 'done';return 'new'}
function escHtml(v){return String(v ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]))}
function ids(arr){return (arr||[]).map(x=>Number(x.id)).filter(Boolean)}

async function apiGet(url){let r=await fetch(url,{credentials:'include'});if(!r.ok)throw new Error('api error');return r.json()}

async function sellerLogin(){
  loginError.innerText='';
  let whatsapp=normalizePhone(login_whatsapp.value.trim());
  let password=login_password.value;
  try{
    let r=await fetch(`${API}/seller-login/`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({whatsapp,password})});
    let data=await r.json();
    if(data.error){loginError.innerText=data.error;return}
    await loadProfile();
    await loadRequests();
  }catch(e){loginError.innerText='Ошибка входа. Попробуйте ещё раз.'}
}

async function loadProfile(){
  try{
    let r=await fetch(`${API}/seller-profile/`,{credentials:'include'});
    let data=await r.json();
    if(data.error){showLogin();return}
    showCabinet();
    renderProfile(data);
  }catch(e){showLogin()}
}

function renderProfile(data){
  sellerProfileData=data;
  profileEditMode=false;
  let editBtn=document.getElementById('editProfileBtn');
  if(editBtn) editBtn.classList.remove('hidden');

  profileBox.innerHTML=`
    <div class="info-box"><b>Название магазина</b><div>${escHtml(data.name||'-')}</div></div>
    <div class="info-box"><b>WhatsApp</b><div>${escHtml(data.whatsapp||'-')}</div></div>
    <div class="info-box"><b>Город</b><div>${escHtml(data.city||data.market_location||'-')}</div></div>
    <div class="info-box"><b>Тип транспорта</b><div>${data.transport_type==='truck'?'Грузовые':'Легковые'}</div></div>
    <div class="info-box"><b>Получает заявки</b><div>${data.receive_requests?'Да':'Нет'}</div></div>
    <div class="info-box"><b>Статус</b><div>${data.is_paused?'Пауза':'Активен'}</div></div>`;

  let cats=(data.all_categories?'Все категории':(data.selected_categories||[]).map(x=>x.name).join(', ')||'-');
  let countries=(data.all_countries?'Все страны':(data.selected_countries||[]).map(x=>x.name).join(', ')||'-');
  let brands=(data.all_brands?'Все марки':(data.selected_brands||[]).map(x=>x.name).join(', ')||'-');
  let models=(data.all_models?'Все модели':(data.selected_models||[]).map(x=>x.name).join(', ')||'-');

  settingsBox.className='profile-grid';
  settingsBox.innerHTML=`
    <div class="info-box"><b>Страны производителей</b><div>${escHtml(countries)}</div></div>
    <div class="info-box"><b>Категории</b><div>${escHtml(cats)}</div></div>
    <div class="info-box"><b>Марки</b><div>${escHtml(brands)}</div></div>
    <div class="info-box"><b>Модели</b><div>${escHtml(models)}</div></div>`;
}

async function loadEditDictionaries(){
  if(dictionariesLoaded)return;
  settingsBox.className='settings-grid';
  settingsBox.innerHTML='<div class="loading">Загрузка справочников...</div>';

  let countries=await apiGet(`${API}/countries/`);
  let categories=await apiGet(`${API}/part-categories/`);

  let transport=sellerProfileData?.transport_type || 'car';
  let brandLists=await Promise.all(
    countries.map(c=>apiGet(`${API}/brands-by-country/?country_id=${c.id}&transport_type=${transport}`).catch(()=>[]))
  );

  let brands=[];
  brandLists.forEach(list=>{(list||[]).forEach(b=>brands.push(b))});
  let seen={};
  brands=brands.filter(b=>{if(seen[b.id])return false;seen[b.id]=true;return true}).sort((a,b)=>String(a.name).localeCompare(String(b.name),'ru'));

  countriesDict=countries;
  categoriesDict=categories;
  brandsDict=brands;
  dictionariesLoaded=true;
}

function checkboxList(items,type,selectedIds,allFlag){
  let selected=new Set((selectedIds||[]).map(Number));
  return `
    <div class="checkbox-list">
      ${(items||[]).map(item=>`
        <label class="checkbox-item" title="${escHtml(item.name)}">
          <input type="checkbox" data-type="${type}" value="${item.id}" ${allFlag||selected.has(Number(item.id))?'checked':''} ${allFlag?'disabled':''}>
          <span>${escHtml(item.name)}</span>
        </label>`).join('')}
    </div>`;
}

function allCheckbox(type,label,checked){
  return `<label class="checkbox-item checkbox-all"><input type="checkbox" id="all_${type}" ${checked?'checked':''} onchange="toggleAllGroup('${type}')"><span>${label}</span></label>`;
}

function toggleAllGroup(type){
  let checked=document.getElementById(`all_${type}`).checked;
  document.querySelectorAll(`input[data-type="${type}"]`).forEach(cb=>{cb.checked=checked;cb.disabled=checked});
}

function renderSettingsEdit(){
  let data=sellerProfileData;
  settingsBox.className='settings-grid';
  settingsBox.innerHTML=`
    <div class="settings-card">
      <h3>Страны производителей</h3>
      ${checkboxList(countriesDict,'countries',ids(data.selected_countries),data.all_countries)}
      ${allCheckbox('countries','Все страны',data.all_countries)}
    </div>
    <div class="settings-card">
      <h3>Категории</h3>
      ${checkboxList(categoriesDict,'categories',ids(data.selected_categories),data.all_categories)}
      ${allCheckbox('categories','Все категории',data.all_categories)}
    </div>
    <div class="settings-card">
      <h3>Марки автомобилей</h3>
      ${checkboxList(brandsDict,'brands',ids(data.selected_brands),data.all_brands)}
      ${allCheckbox('brands','Все марки',data.all_brands)}
    </div>
    <div class="settings-card">
      <h3>Модели автомобилей</h3>
      <div class="checkbox-list">
        ${(data.selected_models||[]).map(m=>`
          <label class="checkbox-item" title="${escHtml(m.name)}">
            <input type="checkbox" data-type="models" value="${m.id}" checked ${data.all_models?'disabled':''}>
            <span>${escHtml(m.name)}</span>
          </label>`).join('') || '<div class="muted">Конкретные модели не выбраны.</div>'}
      </div>
      ${allCheckbox('models','Все модели',data.all_models)}
      <div class="settings-note">Чтобы кабинет открывался быстро, все модели не загружаются сразу. Конкретные модели можно уточнять после выбора марок отдельным шагом.</div>
    </div>`;
}

async function enableEditProfile(){
  if(!sellerProfileData || profileEditMode)return;
  profileEditMode=true;

  profileBox.innerHTML=`
    <div class="info-box"><b>Название магазина</b><input id="edit_name" class="profile-input" value="${escHtml(sellerProfileData.name||'')}"></div>
    <div class="info-box"><b>WhatsApp</b><input id="edit_whatsapp" class="profile-input" value="${escHtml(sellerProfileData.whatsapp||'')}"></div>
    <div class="info-box"><b>Город</b><input id="edit_city" class="profile-input" value="${escHtml(sellerProfileData.city||sellerProfileData.market_location||'')}"></div>
    <div class="info-box"><b>Тип транспорта</b><select id="edit_transport_type" class="profile-input"><option value="car" ${sellerProfileData.transport_type==='truck'?'':'selected'}>Легковые</option><option value="truck" ${sellerProfileData.transport_type==='truck'?'selected':''}>Грузовые</option></select></div>
    <div class="info-box"><b>Получает заявки</b><label class="checkbox-item"><input id="edit_receive_requests" type="checkbox" ${sellerProfileData.receive_requests?'checked':''}><span>Получать заявки</span></label></div>
    <div class="info-box"><b>Статус</b><label class="checkbox-item"><input id="edit_is_paused" type="checkbox" ${sellerProfileData.is_paused?'checked':''}><span>Поставить на паузу</span></label></div>
    <div class="profile-actions"><button class="btn btn-blue" type="button" onclick="saveProfile()">Сохранить</button><button class="btn btn-gray" type="button" onclick="cancelEditProfile()">Отмена</button></div>`;

  let editBtn=document.getElementById('editProfileBtn');
  if(editBtn)editBtn.classList.add('hidden');

  try{
    await loadEditDictionaries();
    renderSettingsEdit();
  }catch(e){
    settingsBox.innerHTML='<p class="error">Не удалось загрузить справочники.</p>';
  }
}

function cancelEditProfile(){
  if(sellerProfileData)renderProfile(sellerProfileData);
}

function checkedIds(type){return Array.from(document.querySelectorAll(`input[data-type="${type}"]:checked`)).map(x=>Number(x.value)).filter(Boolean)}

async function saveProfile(){
  let payload={
    name:document.getElementById('edit_name').value.trim(),
    whatsapp:normalizePhone(document.getElementById('edit_whatsapp').value),
    city:document.getElementById('edit_city').value.trim(),
    transport_type:document.getElementById('edit_transport_type').value,
    receive_requests:document.getElementById('edit_receive_requests').checked,
    is_paused:document.getElementById('edit_is_paused').checked,
    all_countries:document.getElementById('all_countries')?.checked || false,
    all_categories:document.getElementById('all_categories')?.checked || false,
    all_brands:document.getElementById('all_brands')?.checked || false,
    all_models:document.getElementById('all_models')?.checked || false,
    selected_country_ids:checkedIds('countries'),
    selected_category_ids:checkedIds('categories'),
    selected_brand_ids:checkedIds('brands'),
    selected_model_ids:checkedIds('models')
  };

  try{
    let r=await fetch(`${API}/update-seller-profile/`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    let data=await r.json();
    if(data.error){alert(data.error);return}
    await loadProfile();
    alert('Профиль сохранён');
  }catch(e){alert('Ошибка сохранения профиля')}
}

async function loadRequests(){
  try{
    requestsContent.innerHTML='Загрузка...';
    let r=await fetch(`${API}/seller-requests/?period=${currentPeriod}`,{credentials:'include'});
    let data=await r.json();
    if(data.error){showLogin();return}
    allRequests=data.requests||[];
    renderRequests();
  }catch(e){requestsContent.innerHTML='<p class="error">Ошибка загрузки заявок.</p>'}
}

function renderRequests(){
  let filtered=allRequests.filter(x=>currentStatus==='all'||statusKey(x.match_status)===currentStatus);
  countAll.innerText=allRequests.length;
  countNew.innerText=allRequests.filter(x=>statusKey(x.match_status)==='new'||statusKey(x.match_status)==='sent').length;
  countPeriod.innerText=filtered.length;
  visibleCounter.innerText=`${filtered.length} заявок`;
  if(!filtered.length){requestsContent.innerHTML='<p>Заявок по выбранному фильтру нет.</p>';return}
  let html='';
  filtered.forEach(x=>{
    let phone=normalizePhone(x.phone);
    let auto=`${x.brand||'-'} ${x.model||''}`.trim();
    html+=`<div class="request-card">
      <div class="request-title"><h3><span class="badge ${statusClass(x.match_status)}">${labelStatus(x.match_status)}</span> Заявка №${x.id}</h3><span class="muted">${x.created_at||''}</span></div>
      <div class="request-body"><div>${x.city||'-'} • ${auto}</div><div>Категория: ${x.category||'-'}</div><div class="request-description">${escHtml(x.description||'-')}</div></div>
      <div class="actions">${phone?`<a class="btn btn-green" target="_blank" href="https://wa.me/${phone}">Написать клиенту</a>`:''}<button class="btn btn-blue" onclick="setMatchStatus(${x.match_id},'contacted')">Связался</button><button class="btn btn-red" onclick="setMatchStatus(${x.match_id},'done')">Отказ</button></div>
    </div>`;
  });
  requestsContent.innerHTML=html;
}

async function setMatchStatus(matchId,status){
  try{
    await fetch(`${API}/update-match-status/`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({match_id:matchId,status})});
    await loadRequests();
  }catch(e){alert('Не удалось изменить статус')}
}
function setPeriod(p){currentPeriod=p;document.querySelectorAll('.period-pill').forEach(b=>b.classList.toggle('active',b.dataset.period===p));loadRequests()}
function setStatusFilter(s){currentStatus=s;document.querySelectorAll('.status-pill').forEach(b=>b.classList.toggle('active',b.dataset.status===s));renderRequests()}
function showTab(tab){
  let isRequests=tab==='requests';
  requestsTab.classList.toggle('hidden',!isRequests);profileTab.classList.toggle('hidden',isRequests);
  tabRequests.classList.toggle('active',isRequests);tabProfile.classList.toggle('active',!isRequests);
  pageTitle.innerText=isRequests?'Кабинет продавца':'Мой профиль';
  pageSubtitle.innerText=isRequests?'Здесь отображаются заявки покупателей, которые подходят вашему магазину.':'Проверьте данные вашего магазина и настройки автозапчастей.';
}
async function sellerLogout(){
  await fetch(`${API}/seller-logout/`,{
    method:'POST',
    credentials:'include'
  });

  window.location.href = '/request-parts/';
}

function showToast(text,type='success'){
  let old=document.getElementById('toastMessage');
  if(old) old.remove();

  let toast=document.createElement('div');
  toast.id='toastMessage';
  toast.style.position='fixed';
  toast.style.right='20px';
  toast.style.bottom='20px';
  toast.style.zIndex='99999';
  toast.style.padding='14px 18px';
  toast.style.borderRadius='12px';
  toast.style.fontWeight='700';
  toast.style.fontSize='14px';
  toast.style.color='#fff';
  toast.style.boxShadow='0 10px 30px rgba(0,0,0,.15)';
  toast.style.transition='all .25s ease';
  toast.style.background=type==='error' ? '#d92027' : '#22c55e';
  toast.innerText=text;
  document.body.appendChild(toast);

  setTimeout(()=>{toast.style.opacity='0';toast.style.transform='translateY(10px)';},2200);
  setTimeout(()=>{toast.remove();},2600);
}

function togglePasswordVisibility(){
  let show=document.getElementById('show_passwords')?.checked;
  document.querySelectorAll('.password-field').forEach(input=>{
    input.type=show ? 'text' : 'password';
  });
}

async function changeSellerPassword(){
  let oldPassword=document.getElementById('old_password').value;
  let newPassword=document.getElementById('new_password').value;
  let newPasswordConfirm=document.getElementById('new_password_confirm').value;
  let btn=document.getElementById('changePasswordBtn');

  if(!oldPassword || !newPassword || !newPasswordConfirm){
    showToast('Заполните все поля пароля', 'error');
    return;
  }

  if(newPassword.length < 6){
    showToast('Новый пароль должен быть не короче 6 символов', 'error');
    return;
  }

  if(newPassword !== newPasswordConfirm){
    showToast('Новые пароли не совпадают', 'error');
    return;
  }

  try{
    if(btn){btn.disabled=true;btn.innerText='Сохраняем...';}

    let r=await fetch(`${API}/change-seller-password/`,{
      method:'POST',
      credentials:'include',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        old_password:oldPassword,
        new_password:newPassword,
        new_password_confirm:newPasswordConfirm
      })
    });

    let data=await r.json();

    if(data.error){
      showToast(data.error, 'error');
      return;
    }

    document.getElementById('old_password').value='';
    document.getElementById('new_password').value='';
    document.getElementById('new_password_confirm').value='';
    document.getElementById('show_passwords').checked=false;
    togglePasswordVisibility();

    showToast('Пароль изменён');
  }catch(e){
    showToast('Ошибка смены пароля', 'error');
  }finally{
    if(btn){btn.disabled=false;btn.innerText='Сохранить пароль';}
  }
}

loadProfile().then(()=>loadRequests());
