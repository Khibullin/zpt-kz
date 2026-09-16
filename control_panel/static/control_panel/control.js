(function () {
  var toggle = document.getElementById('cp-menu-toggle');
  var sidebar = document.getElementById('cp-sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', function () {
      sidebar.classList.toggle('is-open');
    });
  }

  function setExpanded(id, open) {
    var panel = document.getElementById(id);
    if (!panel) return;
    panel.toggleAttribute('hidden', !open);
    var sourceRow = document.querySelector('[data-expand-row="' + id + '"]');
    if (sourceRow) sourceRow.classList.toggle('is-open', open);
  }

  function closeAllExpands(exceptId) {
    document.querySelectorAll('.cp-expand-row').forEach(function (panel) {
      if (exceptId && panel.id === exceptId) return;
      panel.setAttribute('hidden', '');
    });
    document.querySelectorAll('.cp-product-row.is-open').forEach(function (row) {
      if (exceptId && row.getAttribute('data-expand-row') === exceptId) return;
      row.classList.remove('is-open');
    });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-expand-toggle]');
    var row = event.target.closest('[data-expand-row]');
    var id = button
      ? button.getAttribute('data-expand-toggle')
      : row
        ? row.getAttribute('data-expand-row')
        : '';
    if (!id) return;
    if (!button && event.target.closest('a, button')) {
      event.stopPropagation();
      return;
    }
    var panel = document.getElementById(id);
    if (!panel) return;
    var willOpen = panel.hasAttribute('hidden');
    closeAllExpands(willOpen ? id : '');
    setExpanded(id, willOpen);
  });

  document.querySelectorAll('img.cp-thumb').forEach(function (img) {
    img.addEventListener('error', function () {
      var fallback = img.getAttribute('data-fallback') || '';
      var current = img.getAttribute('src') || '';
      if (fallback && current !== fallback) {
        img.setAttribute('src', fallback);
        return;
      }
      img.classList.add('is-missing');
      img.removeAttribute('src');
    });
  });
})();
