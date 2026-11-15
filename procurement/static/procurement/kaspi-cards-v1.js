(() => {
  document.querySelectorAll('[data-batch-select]').forEach(select => select.addEventListener('change', () => select.form.requestSubmit()));
  document.querySelectorAll('[data-open-editor]').forEach(button => button.addEventListener('click', () => {
    const editor = document.getElementById('kc-editor');
    if (!editor) return;
    editor.open = true;
    editor.scrollIntoView({behavior: 'smooth', block: 'start'});
  }));
  const main = document.getElementById('kc-main-photo');
  const replace = document.querySelector('[data-replace-photo]');
  document.querySelectorAll('[data-gallery-src]').forEach(button => button.addEventListener('click', () => {
    if (main) main.src = button.dataset.gallerySrc;
    if (replace) replace.dataset.imageId = button.dataset.galleryId;
    const download = document.getElementById('kc-download-photo');
    if (download) download.href = button.dataset.gallerySrc + '&download_photo=1';
  }));
  const upload = document.getElementById('kc-photo-file');
  document.querySelectorAll('[data-add-photo],[data-replace-photo]').forEach(button => button.addEventListener('click', () => {
    if (!upload) return;
    document.getElementById('kc-replace-id').value = button.hasAttribute('data-replace-photo') ? button.dataset.imageId : '';
    upload.click();
  }));
  if (upload) upload.addEventListener('change', () => { if (upload.files.length) upload.form.requestSubmit(); });
  const payloadNode = document.getElementById('kc-payload');
  if (!payloadNode) return;
  const payload = JSON.parse(payloadNode.textContent);
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const fields = {
      title: [payload.title], description: [payload.description],
      attributes: Object.entries(payload.attributes).map(([key,value]) => key + ': ' + value),
      applicability: [payload.applicability], price: [payload.price ? payload.price + ' ₸' : ''],
      all: [payload.title, 'Бренд: ' + payload.brand, 'Артикул: ' + payload.article, payload.description,
        ...Object.entries(payload.attributes).map(([key, value]) => key + ': ' + value),
        payload.applicability, payload.price ? 'Цена: ' + payload.price + ' ₸' : '']
    };
    const lines = fields[button.dataset.copy] || [];
    const value = lines.filter(Boolean).join('\n\n');
    const status = document.getElementById('kc-copy-status');
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value);
      else {
        const area = document.createElement('textarea');
        area.value = value; document.body.appendChild(area); area.select();
        const copied = document.execCommand('copy'); area.remove();
        if (!copied) throw new Error('copy');
      }
      if (status) status.textContent = 'Материалы скопированы.';
    } catch (_) { if (status) status.textContent = 'Браузер не разрешил копирование. Скачайте комплект материалов.'; }
  }));
  document.querySelectorAll('[data-copy-value]').forEach(button => button.addEventListener('click', async () => {
    const status=document.getElementById('kc-copy-status');
    try { await navigator.clipboard.writeText(button.dataset.copyValue); if(status) status.textContent='Значение скопировано.'; }
    catch (_) { if(status) status.textContent='Не удалось скопировать. Выделите значение вручную.'; }
  }));
  const priceForm=document.querySelector('[data-price-form]');
  if (priceForm) {
    const amount=priceForm.querySelector('[name=amount]');
    const mode=priceForm.querySelector('[name=mode]');
    amount.addEventListener('input',()=>{mode.value='manual';});
    mode.addEventListener('change',()=>{ if(mode.value==='algorithm') amount.value=payload.recommended || ''; });
  }
  const progress=document.querySelector('[data-poll-jobs]');
  if (progress) {
    let attempts=0;
    let dirty=false;
    if(document.addEventListener) document.addEventListener('input',()=>{dirty=true;});
    const poll=async()=>{
      try {
        const url=new URL(location.href); url.searchParams.set('job_status','1');
        const response=await fetch(url,{credentials:'same-origin',headers:{'Accept':'application/json'}});
        if (!response.ok) throw new Error('status');
        const result=await response.json();
        if (result.jobs.length && result.jobs.every(job=>!['pending','running','submitted','awaiting_listing'].includes(job.status))) {if(dirty) progress.textContent='Обновление завершено. Сохраните свои изменения перед обновлением страницы.'; else location.reload();return;}
      } catch (_) { progress.textContent='Статус пока недоступен. Обновите страницу позже.'; }
      if (++attempts<18) setTimeout(poll,10000);
      else progress.textContent='Задача сохранена и продолжает выполняться в фоне. Обновите страницу для проверки результата.';
    };
    setTimeout(poll,10000);
  }
})();
