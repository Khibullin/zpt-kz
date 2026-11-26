(() => {
  const controls = document.getElementById('apify-export-controls');
  if (!controls) return;
  const sizeSelect = document.getElementById('apify-export-batch-size');
  const cards = document.getElementById('apify-export-cards');
  const error = document.getElementById('apify-export-error');
  const sizes = new Set([500, 1000, 1500, 2000]);
  const batchSize = () => sizes.has(Number(sizeSelect.value)) ? Number(sizeSelect.value) : 500;
  const selectedCities = () => [...controls.querySelectorAll('input[name="city"]:checked')].map(box => box.value);

  function configure(href) {
    const url = new URL(href, window.location.href);
    url.searchParams.set('batch_size', String(batchSize()));
    url.searchParams.set('cards', cards.value);
    url.searchParams.delete('city');
    selectedCities().forEach(city => url.searchParams.append('city', city));
    return url.href;
  }

  function updateLinks() {
    document.querySelectorAll('a[data-apify-export-link]').forEach(link => {
      link.href = configure(link.href);
    });
  }

  function rebuild() {
    document.querySelectorAll('[data-apify-batches]').forEach(container => {
      const count = Math.ceil(Number(container.dataset.queryCount) / batchSize());
      const primary = container.dataset.primary === '1';
      const links = document.createDocumentFragment();
      for (let batch = 1; batch <= count; batch++) {
        const url = new URL(container.dataset.exportUrl, window.location.href);
        url.searchParams.set('request_id', container.dataset.requestId);
        url.searchParams.set('batch', String(batch));
        const link = document.createElement('a');
        link.dataset.apifyExportLink = '';
        link.href = configure(url.href);
        link.textContent = `${primary ? '↓ ' : ''}Скачать JSON для Apify${count > 1 ? ` №${batch}` : ''}`;
        if (primary) link.className = 'button primary';
        links.append(link);
        if (!primary) links.append(document.createTextNode(' '));
      }
      container.replaceChildren(links);
      if (primary) document.querySelectorAll('[data-apify-file-count]').forEach(node => {
        node.textContent = `Файлов: ${count}`;
      });
    });
  }

  sizeSelect.addEventListener('change', () => {
    rebuild();
    const url = new URL(window.location.href);
    url.searchParams.set('batch_size', String(batchSize()));
    window.history.replaceState(null, '', url.href);
  });
  controls.addEventListener('change', updateLinks);
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-apify-export-link]');
    if (!link) return;
    if (!selectedCities().length) {
      event.preventDefault();
      error.hidden = false;
      controls.scrollIntoView({behavior: 'smooth', block: 'center'});
      return;
    }
    error.hidden = true;
    link.href = configure(link.href);
  });
  const requestSelect = document.getElementById('apify-export-request');
  const generate = document.getElementById('apify-generate');
  const status = document.getElementById('apify-generate-status');
  const files = document.getElementById('apify-generated-files');
  const draft = document.getElementById('omega-category-request-form');
  const currentRequest = document.querySelector('[data-apify-batches][data-primary="1"]')?.dataset.requestId || '';

  function captureSettings() {
    if (!draft) return;
    draft.querySelectorAll('[data-apify-setting]:not([name="apify_generate"])').forEach(input => input.remove());
    const values = [['apify_export_settings', '1'], ['apify_batch_size', String(batchSize())],
      ['apify_cards', cards.value], ...selectedCities().map(city => ['apify_city', city])];
    values.forEach(([name, value]) => {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = name; input.value = value;
      input.dataset.apifySetting = ''; draft.append(input);
    });
  }
  if (draft) draft.addEventListener('submit', captureSettings);
  requestSelect.addEventListener('change', () => {
    files.hidden = requestSelect.value !== currentRequest;
    status.textContent = 'Нажмите «Сформировать JSON для Apify».';
  });
  generate.addEventListener('click', () => {
    if (!selectedCities().length) {
      error.textContent = 'Выберите хотя бы один город.'; error.hidden = false; return;
    }
    error.hidden = true;
    const hasDraft = draft && draft.querySelector('[name="omega_category"]:checked');
    if (hasDraft) {
      if (Number(draft.querySelector('[name="omega_min_cost"]').value) < 2000) {
        status.textContent = 'Для Apify задайте закупочную цену от 2 000 ₸ в блоке №1.';
        draft.scrollIntoView({behavior: 'smooth', block: 'center'}); return;
      }
      const save = document.getElementById('omega-save-request');
      if (save.disabled) {
        status.textContent = 'Сначала нажмите «Выбрать товары», проверьте результат и сохраните запрос в блоке №1.';
        draft.scrollIntoView({behavior: 'smooth', block: 'center'});
        document.getElementById('omega-pick-choose').focus(); return;
      }
      captureSettings();
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = 'apify_generate'; input.value = '1';
      input.dataset.apifySetting = ''; draft.append(input);
      status.textContent = 'Сохраняем отбор и формируем JSON…';
      draft.requestSubmit(); return;
    }
    if (!requestSelect.value) {
      status.textContent = 'Выберите сохранённый запрос в списке выше либо выберите товары и сохраните новый запрос в блоке №1.';
      requestSelect.focus(); return;
    }
    const option = requestSelect.options[requestSelect.selectedIndex];
    if (Number(option.dataset.minCost) < 2000) {
      files.hidden = true;
      status.textContent = 'Этот запрос сохранён с порогом ниже 2 000 ₸. Создайте новый отбор от 2 000 ₸ в блоке №1; прежний запрос остаётся в истории.';
      return;
    }
    if (requestSelect.value === currentRequest) {
      rebuild(); updateLinks(); files.hidden = false;
      status.textContent = 'JSON подготовлены. Скачайте нужные партии ниже.';
      files.scrollIntoView({behavior: 'smooth', block: 'center'}); return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set('saved_request', requestSelect.value);
    url.searchParams.set('batch_size', String(batchSize()));
    url.searchParams.set('cards', cards.value);
    url.searchParams.delete('city');
    selectedCities().forEach(city => url.searchParams.append('city', city));
    url.hash = 'apify-step-title';
    window.location.assign(url.href);
  });
  updateLinks();
})();
