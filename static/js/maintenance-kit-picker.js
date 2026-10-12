(() => {
  const fields = ['brand', 'model', 'year', 'engine'].map(
    (name) => document.getElementById(`kit-${name}`)
  );
  if (fields.some((field) => !field)) return;
  const form = fields[0].closest('form');
  fields.forEach((field, index) => {
    field.addEventListener('change', () => {
      fields.slice(index + 1).forEach((dependent) => {
        dependent.value = '';
        dependent.disabled = true;
      });
      form.requestSubmit();
    });
  });
})();
