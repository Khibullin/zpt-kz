(() => {
  const all = document.getElementById('kp-select-all');
  const choices = Array.from(document.querySelectorAll('input[name="selected"][form="kp-prepare"]'));
  if (all) {
    all.addEventListener('change', () => choices.forEach(input => { input.checked = all.checked; }));
    choices.forEach(input => input.addEventListener('change', () => {
      const count = choices.filter(choice => choice.checked).length;
      all.checked = choices.length > 0 && count === choices.length;
      all.indeterminate = count > 0 && count < choices.length;
    }));
  }
  const search = document.getElementById('kp-batch-search');
  if (search) search.addEventListener('input', () => {
    const value = search.value.trim().toLocaleLowerCase('ru');
    document.querySelectorAll('.kp-batch').forEach(batch => {
      batch.hidden = !batch.textContent.toLocaleLowerCase('ru').includes(value);
    });
  });
  const form = document.getElementById('kp-prepare');
  if (form) form.addEventListener('submit', event => {
    if (!choices.some(input => input.checked)) {
      event.preventDefault();
      const first = choices[0];
      if (first) first.focus();
      const message = document.querySelector('.kp-caption');
      if (message) { message.textContent = 'Отметьте галочками товары для подготовки.'; message.setAttribute('role', 'alert'); }
    }
  });
})();
