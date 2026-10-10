(() => {
  const brand = document.getElementById('ag-finder-brand');
  const model = document.getElementById('ag-finder-model');
  const engine = document.getElementById('ag-finder-engine');
  const submit = document.querySelector('.ag-finder__submit');
  const engineMapElement = document.getElementById('ag-finder-engine-map');
  if (!brand || !model || !engine || !submit || !engineMapElement) return;

  const form = brand.closest('form');
  const enginesByModel = JSON.parse(engineMapElement.textContent || '{}');
  const modelOptions = Array.from(model.options).filter((option) => option.value);

  const updateSubmit = () => {
    submit.disabled = !brand.value;
  };

  const refreshEngines = (keepSelection) => {
    const previous = keepSelection ? engine.value : '';
    const engines = enginesByModel[model.value] || [];
    engine.replaceChildren();

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = engines.length ? 'Все двигатели модели' : 'Коды двигателей не указаны';
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
    refreshModels(false);
    form.requestSubmit();
  });
  model.addEventListener('change', () => {
    refreshEngines(false);
    form.requestSubmit();
  });
  engine.addEventListener('change', () => form.requestSubmit());
  refreshModels(true);
})();
