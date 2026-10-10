(() => {
  const brand = document.getElementById('ag-finder-brand');
  const model = document.getElementById('ag-finder-model');
  const submit = document.querySelector('.ag-finder__submit');
  if (!brand || !model || !submit) return;

  const options = Array.from(model.options).filter((option) => option.value);

  const refreshModels = (keepSelection) => {
    const previous = keepSelection ? model.value : '';
    for (const option of options) {
      const matches = option.dataset.brandId === brand.value;
      option.hidden = !matches;
      option.disabled = !matches;
    }
    model.disabled = !brand.value;
    model.value = options.some((option) => option.value === previous && !option.disabled)
      ? previous
      : '';
    submit.disabled = !(brand.value && model.value);
  };

  brand.addEventListener('change', () => refreshModels(false));
  model.addEventListener('change', () => {
    submit.disabled = !(brand.value && model.value);
  });
  refreshModels(true);
})();
