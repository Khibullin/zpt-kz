(function () {
  'use strict';
  function init() {
    const form = document.getElementById('apify-source-upload');
    if (!form) return;
    const button = document.getElementById('apify-source-button');
    const progress = document.getElementById('apify-source-progress');
    const fileInput = form.querySelector('input[name="apify_json"]');
    if (!button || !progress || !fileInput) return;
    let running = false;
    const BATCH = 500;
    function status(message, isError) {
      progress.textContent = message;
      progress.style.display = 'block';
      progress.style.width = '100%';
      progress.style.fontWeight = '600';
      progress.style.color = isError ? '#a02f2f' : '#075a83';
      progress.setAttribute('aria-live', 'polite');
    }
    function frame() { return new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 0))); }
    async function send(action, fields, token) {
      const data = new FormData();
      data.append('csrfmiddlewaretoken', token);
      data.append('action', action);
      Object.entries(fields).forEach(([key, value]) => data.append(key, String(value)));
      const response = await fetch(form.getAttribute('action') || location.pathname, {
        method: 'POST', credentials: 'same-origin', body: data,
        headers: {'X-Requested-With': 'XMLHttpRequest'}
      });
      const result = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error((result && result.error) || 'Ошибка сервера HTTP ' + response.status);
      }
      if (!result || typeof result !== 'object') {
        throw new Error('Сервер не вернул результат загрузки.');
      }
      return result;
    }
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      if (running) return;
      const file = fileInput.files && fileInput.files[0];
      if (!file) { status('Выберите JSON-файл.', true); return; }
      running = true;
      button.disabled = true;
      const originalLabel = button.textContent;
      button.textContent = 'Загрузка…';
      try {
        status('Читаю файл ' + file.name + ' (' + (file.size / 1048576).toFixed(1) + ' МБ)…');
        await frame();
        if (file.size > 200 * 1048576) throw new Error('Размер JSON превышает допустимые 200 МБ.');
        const bytes = await file.arrayBuffer();
        status('Файл прочитан. Проверяю содержимое…');
        await frame();
        const payload = JSON.parse(new TextDecoder('utf-8').decode(bytes).replace(/^\uFEFF/, ''));
        const rows = Array.isArray(payload) ? payload :
          [payload.entries, payload.items, payload.results, payload.data].find(Array.isArray);
        if (!rows || !rows.length || rows.length > 50000) {
          throw new Error('Ожидается JSON с 1–50 000 карточек Apify.');
        }
        if (!window.crypto || !window.crypto.subtle) {
          throw new Error('Браузер не поддерживает безопасную загрузку. Откройте сайт через HTTPS в Chrome.');
        }
        status('Найдено ' + rows.length + ' карточек. Проверяю повторную загрузку…');
        await frame();
        const hash = await crypto.subtle.digest('SHA-256', bytes);
        const digest = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
        const token = form.querySelector('[name=csrfmiddlewaretoken]').value;
        const init = await send('begin_apify_chunk_upload', {
          filename: file.name, total: rows.length, digest,
          request_id: document.getElementById('apify-export-request')?.value || ''
        }, token);
        if (!Number.isInteger(init.uploaded_batches) || !Number.isInteger(init.total_batches)) {
          throw new Error('Сервер не сообщил число сохранённых партий.');
        }
        for (let n = init.uploaded_batches; n < init.total_batches; n++) {
          status('Сохраняю карточки: партия ' + (n + 1) + ' из ' + init.total_batches +
            ' (' + Math.min((n + 1) * BATCH, rows.length) + ' / ' + rows.length + ')');
          await frame();
          await send('append_apify_chunk_upload', {
            session_id: init.session_id, batch: n,
            rows: JSON.stringify(rows.slice(n * BATCH, (n + 1) * BATCH))
          }, token);
        }
        status('Все ' + rows.length + ' карточек загружены. Открываю обработку…');
        location.reload();
      } catch (error) {
        status((error && error.message ? error.message : 'Неизвестная ошибка загрузки.') +
          ' Сохранённые партии не удалены; повторная загрузка продолжит их.', true);
        button.disabled = false;
        button.textContent = originalLabel;
      } finally {
        running = false;
      }
    });
    fileInput.addEventListener('change', function () {
      if (fileInput.files && fileInput.files[0]) {
        status('Файл выбран: ' + fileInput.files[0].name +
          '. Нажмите «Загрузить результаты».');
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}());
