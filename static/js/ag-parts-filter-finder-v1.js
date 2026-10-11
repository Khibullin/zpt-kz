(() => {
  const brand = document.getElementById('ag-finder-brand');
  const model = document.getElementById('ag-finder-model');
  const year = document.getElementById('ag-finder-year');
  const engine = document.getElementById('ag-finder-engine');
  const submit = document.querySelector('.ag-finder__submit');
  const engineMapElement = document.getElementById('ag-finder-engine-map');
  const engineYearMapElement = document.getElementById('ag-finder-engine-year-map');
  if (!brand || !model || !year || !engine || !submit || !engineMapElement || !engineYearMapElement) return;

  const form = brand.closest('form');
  const enginesByModel = JSON.parse(engineMapElement.textContent || '{}');
  const enginesByModelAndYear = JSON.parse(engineYearMapElement.textContent || '{}');
  const modelOptions = Array.from(model.options).filter((option) => option.value);

  const updateSubmit = () => {
    submit.disabled = !brand.value;
  };

  const refreshEngines = (keepSelection) => {
    const previous = keepSelection ? engine.value : '';
    const engines = year.value
      ? ((enginesByModelAndYear[model.value] || {})[year.value] || [])
      : (enginesByModel[model.value] || []);
    engine.replaceChildren();

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = engines.length
      ? (year.value ? 'Все двигатели для выбранного года' : 'Все двигатели модели')
      : (year.value ? 'Двигатели для этого года не подтверждены' : 'Коды двигателей не указаны');
    engine.append(placeholder);

    for (const code of engines) {
      const option = document.createElement('option');
      option.value = code;
      option.textContent = code;
      engine.append(option);
    }

    engine.disabled = !model.value || engines.length === 0;
    engine.value = engines.includes(previous) ? previous : '';
    updateSubmit();
  };

  const refreshModels = (keepSelection) => {
    const previous = keepSelection ? model.value : '';
    for (const option of modelOptions) {
      const matches = option.dataset.brandId === brand.value;
      option.hidden = !matches;
      option.disabled = !matches;
    }
    model.disabled = !brand.value;
    model.value = modelOptions.some((option) => option.value === previous && !option.disabled)
      ? previous
      : '';
    refreshEngines(keepSelection);
  };

  brand.addEventListener('change', () => {
    year.value = '';
    refreshModels(false);
    form.requestSubmit();
  });
  model.addEventListener('change', () => {
    year.value = '';
    refreshEngines(false);
    form.requestSubmit();
  });
  year.addEventListener('change', () => {
    refreshEngines(false);
    form.requestSubmit();
  });
  engine.addEventListener('change', () => form.requestSubmit());
  refreshModels(true);
})();
