(function () {
  var picker = document.getElementById('seller-picker');
  if (!picker) {
    return;
  }

  var selectFirstN = parseInt(picker.getAttribute('data-select-first-n') || '5', 10) || 5;
  var searchInput = document.getElementById('seller-search');
  var consentFilter = document.getElementById('seller-consent-filter');
  var selectedCount = document.getElementById('seller-selected-count');
  var consentSummary = document.getElementById('seller-consent-summary');
  var continueButton = document.getElementById('continue-button');
  var selectFirstButton = document.getElementById('select-first-sellers');
  var clearButton = document.getElementById('clear-seller-selection');
  var rows = Array.prototype.slice.call(picker.querySelectorAll('.seller-picker-row'));

  function normalize(value) {
    return (value || '').toString().trim().toLowerCase();
  }

  function rowVisible(row) {
    var query = normalize(searchInput ? searchInput.value : '');
    var consent = consentFilter ? consentFilter.value : '';
    var searchText = row.getAttribute('data-search') || '';
    var rowConsent = row.getAttribute('data-consent') || '';
    var matchesSearch = !query || searchText.indexOf(query) !== -1;
    var matchesConsent = true;
    if (consent === 'granted') {
      matchesConsent = rowConsent === 'granted';
    } else if (consent === 'revoked') {
      matchesConsent = rowConsent === 'revoked';
    } else if (consent === 'not_recorded') {
      matchesConsent = rowConsent !== 'granted' && rowConsent !== 'revoked';
    }
    return matchesSearch && matchesConsent;
  }

  function applyFilters() {
    rows.forEach(function (row) {
      var visible = rowVisible(row);
      row.hidden = !visible;
      row.classList.toggle('is-search-hidden', !visible);
    });
  }

  function selectedCheckboxes() {
    return rows
      .map(function (row) {
        return row.querySelector('.seller-picker-checkbox');
      })
      .filter(function (checkbox) {
        return checkbox && checkbox.checked;
      });
  }

  function updateSummary() {
    var selectedRows = rows.filter(function (row) {
      var checkbox = row.querySelector('.seller-picker-checkbox');
      return checkbox && checkbox.checked;
    });
    var selected = selectedRows.length;
    var allowed = 0;
    var notRecorded = 0;
    var revoked = 0;
    selectedRows.forEach(function (row) {
      var consent = row.getAttribute('data-consent') || '';
      if (row.getAttribute('data-eligible') === '1') {
        allowed += 1;
      }
      if (consent === 'revoked') {
        revoked += 1;
      } else if (consent !== 'granted') {
        notRecorded += 1;
      }
    });
    if (selectedCount) {
      selectedCount.textContent = 'Выбрано: ' + selected;
    }
    if (consentSummary) {
      consentSummary.textContent =
        'WhatsApp разрешён: ' + allowed + '. ' +
        'Не подтверждено: ' + notRecorded + '. ' +
        'Отключено: ' + revoked + '.';
    }
    if (continueButton) {
      continueButton.disabled = selected === 0;
    }
  }

  function rowSelectable(row) {
    var checkbox = row.querySelector('.seller-picker-checkbox');
    if (!checkbox || checkbox.disabled) {
      return false;
    }
    return row.getAttribute('data-selectable') === '1';
  }

  function visibleSelectableRows() {
    return rows.filter(function (row) {
      return !row.hidden && rowSelectable(row);
    });
  }

  if (selectFirstButton) {
    selectFirstButton.addEventListener('click', function () {
      rows.forEach(function (row) {
        var checkbox = row.querySelector('.seller-picker-checkbox');
        if (checkbox) {
          checkbox.checked = false;
        }
      });
      var selectable = visibleSelectableRows();
      selectable.slice(0, selectFirstN).forEach(function (row) {
        var checkbox = row.querySelector('.seller-picker-checkbox');
        if (checkbox && !checkbox.disabled) {
          checkbox.checked = true;
        }
      });
      updateSummary();
    });
  }

  if (clearButton) {
    clearButton.addEventListener('click', function () {
      rows.forEach(function (row) {
        var checkbox = row.querySelector('.seller-picker-checkbox');
        if (checkbox) {
          checkbox.checked = false;
        }
      });
      updateSummary();
    });
  }

  rows.forEach(function (row) {
    var checkbox = row.querySelector('.seller-picker-checkbox');
    if (checkbox) {
      checkbox.addEventListener('change', updateSummary);
    }
  });

  if (searchInput) {
    searchInput.addEventListener('input', function () {
      applyFilters();
    });
    searchInput.addEventListener('search', function () {
      applyFilters();
    });
  }
  if (consentFilter) {
    consentFilter.addEventListener('change', applyFilters);
  }

  picker.querySelectorAll('.seller-copy-consent-link').forEach(function (button) {
    button.addEventListener('click', function () {
      var url = button.getAttribute('data-consent-url') || '';
      if (!url) {
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(function () {
          button.textContent = 'Ссылка скопирована';
        }).catch(function () {
          window.prompt('Скопируйте ссылку подтверждения', url);
        });
      } else {
        window.prompt('Скопируйте ссылку подтверждения', url);
      }
    });
  });

  applyFilters();
  updateSummary();
})();
