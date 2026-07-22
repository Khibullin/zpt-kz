let serviceType='sto';

const stoBtn=document.getElementById('stoBtn');
const detailingBtn=document.getElementById('detailingBtn');
const stoServices=document.getElementById('stoServices');
const detailingServices=document.getElementById('detailingServices');

const brandEl=document.getElementById('brand');
const modelEl=document.getElementById('model');

const cityEl=document.getElementById('city');
const districtEl=document.getElementById('district');

const phoneEl=document.getElementById('phone');
const descriptionEl=document.getElementById('description');

const msg=document.getElementById('msg');
const submitBtn=document.getElementById('submitBtn');

function setMessage(text,type){
  msg.className='msg '+type;
  ZPTDom.setText(msg,text);
}

function clearMessage(){
  msg.className='msg';
  ZPTDom.clearElement(msg);
}

function appendSpacer(container){
  container.appendChild(document.createElement('br'));
  container.appendChild(document.createElement('br'));
}

function appendTextBlock(container,text){
  if(!text){
    return;
  }
  appendSpacer(container);
  container.appendChild(document.createTextNode(text));
}

function appendLabeledLine(container,label,value){
  if(value === undefined || value === null || value === ''){
    return;
  }
  container.appendChild(document.createElement('br'));
  container.appendChild(document.createTextNode(label + value));
}

function renderSuccessResult(data){
  msg.className='msg success';
  ZPTDom.clearElement(msg);

  const title=document.createElement('strong');
  title.textContent=data.title || '✅ Заявка принята и отправлена подходящим исполнителям.';
  msg.appendChild(title);

  appendTextBlock(msg,'Что дальше?');
  appendTextBlock(msg,data.message);
  appendTextBlock(msg,data.timing_hint);

  const requestHeading=document.createElement('strong');
  appendSpacer(msg);
  requestHeading.textContent='Ваш запрос:';
  msg.appendChild(requestHeading);

  const services=Array.isArray(data.services) ? data.services.join(', ') : '';
  appendLabeledLine(msg,'Услуги: ',services);
  appendLabeledLine(msg,'Город: ',data.city);
  appendLabeledLine(msg,'Район: ',data.district);
  appendLabeledLine(msg,'Телефон: ',data.phone);
  appendLabeledLine(msg,'Описание: ',data.description);

  appendTextBlock(msg,data.catalog_hint);

  const link=document.createElement('a');
  link.href=data.result_url || ('/service-request/result/' + data.request_id + '/');
  link.className='service-result-link';
  link.textContent='Посмотреть исполнителей по заявке';
  appendSpacer(msg);
  msg.appendChild(link);
}

function normalizePhone(v){
  return String(v||'').replace(/\D/g,'');
}

function setServiceType(type){
  serviceType=type;

  stoBtn.classList.toggle('active',type==='sto');
  detailingBtn.classList.toggle('active',type==='detailing');

  stoServices.classList.toggle('hidden',type!=='sto');
  detailingServices.classList.toggle('hidden',type!=='detailing');

  document.querySelectorAll('input[type="checkbox"]').forEach(cb=>cb.checked=false);
}

function getSelectedServices(){
  const activeBlock = serviceType==='sto' ? stoServices : detailingServices;
  return Array.from(activeBlock.querySelectorAll('input[type="checkbox"]:checked')).map(cb=>cb.value);
}

const API = window.ZPT_CONFIG.serviceApiBase.replace(/\/$/, '');

document.getElementById('serviceForm').addEventListener('submit', async function(e){
  e.preventDefault();
  clearMessage();

  const services = getSelectedServices();

const payload = {
  service_type: serviceType,

  brand: brandEl.value.trim(),
  model: modelEl.value.trim(),

  services: services,

  city: cityEl.value,
  district: districtEl.value,

  phone: normalizePhone(phoneEl.value),

  description: descriptionEl.value.trim()
};

  if (!payload.services.length){
    setMessage('Выберите хотя бы одну услугу.','error');
    return;
  }

if (
  !payload.city ||
  !payload.district ||
  !payload.phone
){
  setMessage(
    'Заполните город, район и телефон / WhatsApp.',
    'error'
  );
  return;
}

  submitBtn.disabled = true;
  submitBtn.innerText = 'Отправляем...';

  try{
    const r = await fetch(API + '/create-service-request/',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    });

    const data = await r.json();

if(data.error){
  setMessage(data.error,'error');
  return;
}

if(!data.success){
  setMessage('Ошибка отправки заявки. Попробуйте ещё раз.','error');
  return;
}

renderSuccessResult(data);
document.getElementById('serviceForm').reset();
setServiceType('sto');

  }catch(err){
    setMessage('Ошибка отправки заявки. Попробуйте ещё раз.','error');
  }finally{
    submitBtn.disabled = false;
    submitBtn.innerText = 'Отправить заявку';
  }
});
