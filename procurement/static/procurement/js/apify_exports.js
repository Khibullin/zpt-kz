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
  updateLinks();
})();
