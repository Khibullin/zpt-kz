const API = window.ZPT_CONFIG.apiBase.replace(/\/$/, '');
/* request-parts-form v5 — справочник марок/моделей из /api/ (core/vehicle_catalog.py) */
let transportType='car';
let countriesData=[];let brandsData=[];let modelsData=[];
const countryEl=document.getElementById('country');
const brandEl=document.getElementById('brand');
const modelEl=document.getElementById('model');
const categoryEl=document.getElementById('category');
const articleEl=document.getElementById('article');
const cityEl=document.getElementById('city');
const phoneEl=document.getElementById('phone');
const requestForm=document.getElementById('requestForm');
const photosEl=document.getElementById('photos');
const descriptionEl=document.getElementById('description');
const msg=document.getElementById('msg');
const submitBtn=document.getElementById('submitBtn');

function setMessage(text,type){msg.className='msg '+type;ZPTDom.setText(msg,text)}
function setRichMessage(html,type){msg.className='msg '+type;msg.innerHTML=html}
function clearMessage(){msg.className='msg';ZPTDom.setText(msg,'')}
function normalizePhone(v){return String(v||'').replace(/\D/g,'')}
async function apiGet(url){let r=await fetch(url);if(!r.ok){throw new Error('Ошибка загрузки данных')}return r.json()}
function getSelectedText(select){if(!select.value)return '';let opt=select.options[select.selectedIndex];return opt?opt.textContent.trim():''}
function toggleCitiesSelection(){
  let selected = document.querySelector(
    'input[name="search_scope"]:checked'
  )?.value;

  let box = document.getElementById('citiesSelection');

  if(!box){
    return;
  }

  box.style.display =
    selected === 'custom'
      ? 'block'
      : 'none';
}

function getSearchScope(){
  return document.querySelector(
    'input[name="search_scope"]:checked'
  )?.value || 'city';
}

function getSelectedCities(){
  return Array.from(
    document.querySelectorAll('#citiesSelection input[type="checkbox"]:checked')
  ).map(item => item.value);
}

function setTransport(type){
  transportType=type;
  carBtn.classList.toggle('active',type==='car');
  truckBtn.classList.toggle('active',type==='truck');
  ZPTDom.fillSelect(brandEl, [], 'Сначала выберите страну');
  ZPTDom.fillSelect(modelEl, [], 'Сначала выберите марку');
  loadCountries();
}

async function loadCategories(){
  try{
    let cats=await apiGet(API+'/part-categories/');
    ZPTDom.fillSelect(categoryEl, cats.map(c=>({id:c.name,name:c.name})), 'Выберите категорию', 'id', 'name');
  }catch(e){ZPTDom.fillSelect(categoryEl, [], 'Ошибка загрузки категорий')}
}

async function loadCountries(){
  try{
    countriesData=await apiGet(API+'/countries/');
    ZPTDom.fillSelect(countryEl, countriesData, 'Выберите страну');
  }catch(e){ZPTDom.fillSelect(countryEl, [], 'Ошибка загрузки стран')}
}

async function loadBrands(){
  let countryId=countryEl.value;
  ZPTDom.fillSelect(brandEl, [], 'Загрузка марок...');
  ZPTDom.fillSelect(modelEl, [], 'Сначала выберите марку');
  if(!countryId){ZPTDom.fillSelect(brandEl, [], 'Сначала выберите страну');return}
  try{
    brandsData=await apiGet(API+'/brands-by-country/?country_id='+encodeURIComponent(countryId)+'&transport_type='+encodeURIComponent(transportType));
    if(!brandsData.length){ZPTDom.fillSelect(brandEl, [], 'Марок нет в справочнике');return}
    ZPTDom.fillSelect(brandEl, brandsData, 'Выберите марку');
  }catch(e){ZPTDom.fillSelect(brandEl, [], 'Ошибка загрузки марок')}
}

async function loadModels(){
  let brandId=brandEl.value;
  ZPTDom.fillSelect(modelEl, [], 'Загрузка моделей...');
  if(!brandId){ZPTDom.fillSelect(modelEl, [], 'Сначала выберите марку');return}
  try{
    modelsData=await apiGet(API+'/models-by-brand/?brand_id='+encodeURIComponent(brandId)+'&transport_type='+encodeURIComponent(transportType));
    if(!modelsData.length){ZPTDom.fillSelect(modelEl, [], 'Моделей нет в справочнике');return}
    ZPTDom.fillSelect(modelEl, modelsData, 'Выберите модель');
  }catch(e){ZPTDom.fillSelect(modelEl, [], 'Ошибка загрузки моделей')}
}

function renderResult(data){
  let sellers = data.seller_notifications || [];
  let count = data.matches || sellers.length || 0;

  let strategyNote = '';

  if(data.strategy === 'fallback_kazakhstan'){
    strategyNote = `
<div style="
  margin-bottom:12px;
  padding:12px;
  border-radius:10px;
  background:#fff3cd;
  color:#7a5200;
">
  В вашем городе продавцы пока не найдены.<br>
  Мы автоматически расширили поиск на весь Казахстан.
</div>
`;
  }

  let html = `
${strategyNote}

<b>✅ Заявка отправлена ${count} продавцам</b><br><br>

Мы уже отправили вашу заявку продавцам запчастей.<br>
В ближайшее время они сами напишут вам в WhatsApp с предложениями.<br><br>

Обычно ответы приходят в течение 5–15 минут.<br><br>

Не нужно отправлять заявку повторно — продавцы уже её получили.<br><br>

Вы также можете написать продавцу сами, если хотите ускорить ответ.
`;
  if(data.photo_view_url){
    html += `<br><br><a href="${ZPTDom.escapeHtml(data.photo_view_url)}" style="color:#3478f6">Открыть страницу заявки</a>`;
  }

  if(data.upload_mode === 'json'){
    html += `<br><br><span style="color:#e65100">Браузер отправил устаревший запрос без файлов. Нажмите Ctrl+F5 и повторите отправку.</span>`;
  }else if(data.photos_received > 0 && !data.photos_saved){
    html += `<br><br><span style="color:#e65100">Фото не сохранились на сервере (${data.photos_received} шт. получено). Попробуйте другой формат (JPG/PNG).</span>`;
  }else if(data.photos_saved){
    html += `<br><br>Фото заявки: ${data.photos_saved} шт.`;
  }

  if(sellers.length){
    html += '<div class="result-card"><b>Продавцы получили заявку:</b>';

sellers.forEach((s)=>{

  let wa = s.buyer_wa_link || '#';

  html += `
  <div class="seller-row">

    <div>

      <b>${ZPTDom.escapeHtml(s.seller_name || 'Продавец')}</b><br>

      ${
        s.whatsapp_status === 'sent'
          ? '<span style="color:#2e7d32">✓ Заявка отправлена продавцу</span>'
          : '<span style="color:#c62828">Ошибка отправки WhatsApp</span>'
      }

      ${
        s.seller_catalog_url
          ? `<br><a href="${ZPTDom.escapeHtml(s.seller_catalog_url)}" style="color:#3478f6;text-decoration:none;font-size:14px">Открыть профиль продавца</a>`
          : ''
      }

    </div>

    <a
      class="wa-btn"
      href="${ZPTDom.escapeHtml(wa)}"
      target="_blank"
      rel="noopener"
    >
      WhatsApp
    </a>

  </div>
  `;
});
    html += '</div>';
  }

  setRichMessage(html,'success');
}

requestForm.addEventListener('submit',async function(e){
  e.preventDefault();clearMessage();
  let country=getSelectedText(countryEl);
  let brand=getSelectedText(brandEl);
  let model=getSelectedText(modelEl);
  let category=categoryEl.value;
  let article=articleEl.value.trim();
  let description=descriptionEl.value.trim();
  let city=cityEl.value;
  let searchScope=getSearchScope();
  let selectedCities=getSelectedCities();
  let phone=normalizePhone(phoneEl.value);

if(
  !country ||
  !brand ||
  !model ||
  !category ||
  !city ||
  !phone
){
  setMessage('Заполните обязательные поля.','error');
  return;
}

if(
  searchScope === 'custom' &&
  !selectedCities.length
){
  setMessage(
    'Выберите хотя бы один город для поиска продавцов.',
    'error'
  );
  return;
}

if(
  phone.length !== 11 ||
  !phone.startsWith('7')
){
  setMessage(
    'Введите номер WhatsApp корректно: номер должен начинаться с 7, например 77011234567',
    'error'
  );
  return;
}

  let formData=new FormData(requestForm);
  formData.set('transport_type',transportType);
  formData.set('country',country);
  formData.set('brand',brand);
  formData.set('model',model);
  formData.set('category',category);
  formData.set('article',article);
  formData.set('description',description);
  formData.set('city',city);
  formData.set('search_scope',searchScope);
  formData.set('phone',phone);
  formData.delete('selected_cities');
  selectedCities.forEach(function(cityName){
    formData.append('selected_cities',cityName);
  });

  let attachedPhotos=formData.getAll('photos');
  if(!attachedPhotos.length && photosEl && photosEl.files && photosEl.files.length){
    Array.from(photosEl.files).forEach(function(file){
      formData.append('photos',file);
    });
    attachedPhotos=formData.getAll('photos');
  }

  submitBtn.disabled=true;submitBtn.innerText='Отправляем...';
  try{
    let r=await fetch(API+'/create-request/',{method:'POST',body:formData});
    let raw=await r.text();
    let data;
    try{
      data=JSON.parse(raw);
    }catch(parseErr){
      setMessage('Ошибка отправки заявки. Сервер вернул неожиданный ответ.','error');
      return;
    }
    if(!r.ok || data.error){
      setMessage(data.error || 'Ошибка отправки заявки. Попробуйте ещё раз.','error');
      return;
    }
    renderResult(data);
    requestForm.reset();setTransport('car');await loadCategories();
  }catch(err){setMessage('Ошибка отправки заявки. Попробуйте ещё раз.','error')}
  finally{submitBtn.disabled=false;submitBtn.innerText='Отправить заявку'}
});

async function applyUrlParams(){

  const params = new URLSearchParams(
    window.location.search
  );

  const transport =
    params.get('transport');

  const brand =
    params.get('brand');

  const model =
    params.get('model');

  if(
    transport &&
    ['car','truck'].includes(transport)
  ){
    setTransport(transport);
  }

  await loadCountries();

  if(brand){

    for(const country of countriesData){

      countryEl.value = country.id;

      await loadBrands();

      const foundBrand =
        brandsData.find(
          b =>
            b.name.toLowerCase()
            === brand.toLowerCase()
        );

      if(foundBrand){

        brandEl.value = foundBrand.id;

        await loadModels();

        if(model){

          const foundModel =
            modelsData.find(
              m =>
                m.name.toLowerCase()
                === model.toLowerCase()
            );

          if(foundModel){
            modelEl.value =
              foundModel.id;
          }
        }

        break;
      }
    }
  }
}

countryEl.addEventListener('change',loadBrands);
brandEl.addEventListener('change',loadModels);
loadCategories();
applyUrlParams();
