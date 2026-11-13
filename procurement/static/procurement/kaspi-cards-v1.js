(() => {
  document.querySelectorAll('[data-batch-select]').forEach(select => select.addEventListener('change', () => select.form.requestSubmit()));
  const back = document.querySelector('[data-kaspi-back]');
  if (back) back.addEventListener('click', event => {
    if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (window.navigation ? window.navigation.canGoBack : history.length > 1) { event.preventDefault(); history.back(); }
  });
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
    const lines = button.dataset.copy === 'description' ? [payload.description] : [
      payload.title, 'Бренд: ' + payload.brand, 'Артикул: ' + payload.article, payload.description,
      ...Object.entries(payload.attributes).map(([key, value]) => key + ': ' + value),
      payload.applicability, payload.price ? 'Цена: ' + payload.price + ' ₸' : ''
    ];
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
})();
