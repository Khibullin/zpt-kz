(function () {
  var form = document.querySelector('[data-kit-select]');
  if (!form) return;

  var boxes = Array.prototype.slice.call(form.querySelectorAll('[data-kit-item]'));
  var totalNode = form.querySelector('[data-kit-total]');
  var submit = form.querySelector('[data-kit-submit]');
  var note = document.querySelector('[data-kit-cover-note]');
  var orderableCount = boxes.length;

  function formatTenge(value) {
    return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₸';
  }

  function refresh() {
    var selected = 0;
    var total = 0;
    boxes.forEach(function (box) {
      if (!box.checked) return;
      selected += 1;
      var raw = String(box.getAttribute('data-subtotal') || '0').replace(/\s/g, '').replace(',', '');
      total += Number(raw) || 0;
    });
    if (totalNode) {
      totalNode.textContent = selected ? ('Итого: ' + formatTenge(total)) : 'Итого: выберите позиции';
    }
    if (submit && !submit.hasAttribute('data-kit-locked')) {
      submit.disabled = selected === 0;
    }
    if (note) {
      var partial = selected < orderableCount;
      note.hidden = !partial;
    }
  }

  boxes.forEach(function (box) {
    box.addEventListener('change', refresh);
  });
  form.addEventListener('submit', function (event) {
    var selected = boxes.some(function (box) { return box.checked; });
    if (!selected) {
      event.preventDefault();
      refresh();
    }
  });
  refresh();
})();
