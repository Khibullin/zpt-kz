(() => {
  const brand = document.getElementById('ag-finder-brand');
  const model = document.getElementById('ag-finder-model');
  const engine = document.getElementById('ag-finder-engine');
  const submit = document.querySelector('.ag-finder__submit');
  const engineMapElement = document.getElementById('ag-finder-engine-map');
  if (!brand || !model || !engine || !submit || !engineMapElement) return;

  const enginesByModel = JSON.parse(engineMapElement.textContent || '{}');
  const modelOptions = Array.from(model.options).filter((option) => option.value);

  const updateSubmit = () => {
    const hasVehicle = Boolean(brand.value && model.value);
    const engines = enginesByModel[model.value] || [];
    submit.disabled = !hasVehicle || (engines.length > 0 && !engine.value);
  };

  const refreshEngines = (keepSelection) => {
    const previous = keepSelection ? engine.value : '';
    const engines = enginesByModel[model.value] || [];
    engine.replaceChildren();

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = !model.value
      ? 'Сначала выберите модель'
      : engines.length
        ? 'Выберите двигатель'
        : 'Код двигателя не указан';
    engine.append(placeholder);

    for (const code of engines) {
      const option = document.createElement('option');
      option.value = code;
      option.textContent = code;
      engine.append(option);
    }

    engine.disabled = !model.value || engines.length === 0;
    engine.required = engines.length > 0;
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

  brand.addEventListener('change', () => refreshModels(false));
  model.addEventListener('change', () => refreshEngines(false));
  engine.addEventListener('change', updateSubmit);
  refreshModels(true);
})();
