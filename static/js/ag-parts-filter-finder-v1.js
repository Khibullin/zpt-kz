(() => {
  const brand = document.getElementById('ag-finder-brand');
  const model = document.getElementById('ag-finder-model');
  const year = document.getElementById('ag-finder-year');
  const engine = document.getElementById('ag-finder-engine');
  const submit = document.querySelector('.ag-finder__submit');
  if (!brand || !model || !year || !engine || !submit) return;

  const form = brand.closest('form');
  const modelOptions = Array.from(model.options).filter((option) => option.value);

  const updateSubmit = () => {
    submit.disabled = !brand.value;
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
  };

  brand.addEventListener('change', () => {
    year.value = '';
    engine.value = '';
    refreshModels(false);
    form.requestSubmit();
  });
  model.addEventListener('change', () => {
    year.value = '';
    engine.value = '';
    form.requestSubmit();
  });
  year.addEventListener('change', () => {
    engine.value = '';
    form.requestSubmit();
  });
  engine.addEventListener('change', () => form.requestSubmit());

  refreshModels(true);
  updateSubmit();
})();
