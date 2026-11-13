(() => {
  const method = document.getElementById('id_method');
  const value = document.getElementById('id_value');
  const example = document.getElementById('pricing-example');
  if (!method || !value || !example) return;
  const labels = {
    minimum_minus_amount: 'Сумма снижения, ₸',
    minimum_minus_percent: 'Процент снижения, %',
    cost_plus_percent: 'Процент наценки, %'
  };
  const fmt = number => number.toLocaleString('ru-RU', {maximumFractionDigits: 2});
  const update = () => {
    const label = document.querySelector('label[for="id_value"]');
    if (label) label.textContent = labels[method.value] + ':';
    const raw = value.value.trim().replace(',', '.');
    const amount = Number(raw);
    if (!raw || !Number.isFinite(amount) || amount < 0 || (method.value === 'minimum_minus_percent' && amount >= 100)) {
      example.textContent = 'Введите допустимую сумму или процент для выбранного способа.';
      return;
    }
    const isCost = method.value === 'cost_plus_percent';
    const target = isCost ? 10000 * (1 + amount / 100) : method.value === 'minimum_minus_amount' ? 10000 - amount : 10000 * (1 - amount / 100);
    example.textContent = 'Учебный пример: ' + (isCost ? 'закупочная цена' : 'минимальная цена конкурентов') + ' — 10 000 ₸. ' +
      (isCost ? 'Наценка' : 'Снижение') + ' — ' + fmt(amount) + (method.value === 'minimum_minus_amount' ? ' ₸' : '%') +
      '. Расчётная цена — ' + fmt(Math.ceil(target)) + ' ₸. Итоговая рекомендация учитывает комиссию, расходы и минимальную прибыль по конкретному товару.';
  };
  method.addEventListener('change', update);
  value.addEventListener('input', update);
  update();
})();
