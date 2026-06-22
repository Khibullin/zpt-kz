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
  ZPTDom.setText(msg,'');
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

setMessage(`
<b>✅ Заявка принята и отправлена подходящим исполнителям.</b>

<br><br>

<b>Что дальше?</b>

<br><br>

Ничего делать не нужно —
СТО и мастера сами напишут вам в WhatsApp
с ценой, сроками и условиями.

<br><br>

Обычно первые ответы приходят
в течение 5–15 минут.

<br><br>

<b>Ваш запрос:</b><br>

Услуги: ${services.join(', ')}<br>
Город: ${payload.city}<br>
Район: ${payload.district}<br>
Телефон: ${payload.phone}<br>

${payload.description
  ? `Описание: ${payload.description}<br>`
  : ''
}

<br>

Если хотите самостоятельно посмотреть исполнителей —
можете открыть каталог.

<br><br>

<a
  href="/service-request/result/${data.request_id}/"
  style="
    display:inline-block;
    background:#3478f6;
    color:#fff;
    text-decoration:none;
    padding:12px 18px;
    border-radius:10px;
    font-weight:bold;
  "
>
  Посмотреть исполнителей по заявке
</a>
`,'success');
document.getElementById('serviceForm').reset();
setServiceType('sto');

  }catch(err){
    setMessage('Ошибка отправки заявки. Попробуйте ещё раз.','error');
  }finally{
    submitBtn.disabled = false;
    submitBtn.innerText = 'Отправить заявку';
  }
});
