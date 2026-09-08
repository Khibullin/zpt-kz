(function () {
  var form = document.getElementById('simple-mailing-form');
  if (!form) {
    return;
  }

  var CONTROL_ONLY = 'control_only';
  var PREVIEW_LABEL_CONTROL = 'Показать контрольных получателей';
  var PREVIEW_LABEL_SELLERS = 'Показать продавцов';
  var PREVIEW_LABEL_DEFAULT = 'Показать количество';

  var allBrands = document.getElementById('all-brands-checkbox');
  var brandCheckboxes = Array.prototype.slice.call(document.querySelectorAll('.brand-checkbox'));
  var brandSearch = document.getElementById('brand-search');
  var brandSearchEmpty = document.getElementById('brand-search-empty');
  var brandOptions = Array.prototype.slice.call(document.querySelectorAll('.brand-option'));
  var recipientRadios = Array.prototype.slice.call(
    document.querySelectorAll('[data-recipient-type-radio]')
  );
  var recipientScopeRadios = Array.prototype.slice.call(
    document.querySelectorAll('[data-recipient-scope-radio]')
  );
  var brandSelectionCard = document.getElementById('brand-selection-card');
  var brandFieldset = document.getElementById('brand-selection-fieldset');
  var controlBrandsHint = document.getElementById('control-brands-hint');
  var controlScopeHint = document.getElementById('control-scope-hint');
  var previewButton = document.getElementById('preview-button');
  var continueButton = document.getElementById('continue-button');
  var countDisplay = document.getElementById('recipient-count-display');
  var ordinaryCountDisplay = document.getElementById('ordinary-count-display');
  var controlCountDisplay = document.getElementById('control-count-display');
  var selectedCountDisplay = document.getElementById('selected-brands-count');
  var hasFreshResult = form.getAttribute('data-has-fresh-result') === 'true';
  var marketplaceLocked = form.getAttribute('data-marketplace-locked') === 'true';

  function selectedBrandCount() {
    if (marketplaceLocked || (allBrands && allBrands.checked)) {
      return 'Все марки';
    }
    var count = brandCheckboxes.filter(function (checkbox) {
      return checkbox.checked;
    }).length;
    return String(count);
  }

  function selectedRecipientType() {
    var checked = recipientRadios.find(function (radio) {
      return radio.checked;
    });
    return checked ? checked.value : '';
  }

  function isControlOnlyScope() {
    var checked = recipientScopeRadios.find(function (radio) {
      return radio.checked;
    });
    return checked && checked.value === CONTROL_ONLY;
  }

  function previewButtonLabel() {
    if (isControlOnlyScope()) {
      return PREVIEW_LABEL_CONTROL;
    }
    if (selectedRecipientType() === 'sellers') {
      return PREVIEW_LABEL_SELLERS;
    }
    return PREVIEW_LABEL_DEFAULT;
  }

  function hasValidSelection() {
    if (isControlOnlyScope()) {
      return true;
    }
    if (marketplaceLocked) {
      return true;
    }
    if (allBrands && allBrands.checked) {
      return true;
    }
    return brandCheckboxes.some(function (checkbox) {
      return checkbox.checked;
    });
  }

  function invalidateResult() {
    hasFreshResult = false;
    if (countDisplay) {
      countDisplay.textContent = '—';
    }
    if (ordinaryCountDisplay) {
      ordinaryCountDisplay.textContent = '—';
    }
    if (controlCountDisplay) {
      controlCountDisplay.textContent = '—';
    }
    if (continueButton) {
      continueButton.disabled = true;
    }
  }

  function clearBrandSelection() {
    if (allBrands) {
      allBrands.checked = false;
    }
    brandCheckboxes.forEach(function (checkbox) {
      checkbox.checked = false;
    });
  }

  function syncBrandDisabledState() {
    var controlOnly = isControlOnlyScope();
    if (brandFieldset) {
      brandFieldset.disabled = controlOnly;
    }
    if (allBrands) {
      allBrands.disabled = controlOnly || marketplaceLocked;
    }
    if (brandSearch) {
      brandSearch.disabled = controlOnly;
    }
    var disableConcrete = controlOnly || (allBrands && allBrands.checked);
    brandCheckboxes.forEach(function (checkbox) {
      if (checkbox.hasAttribute('data-perma-disabled')) {
        return;
      }
      checkbox.disabled = disableConcrete;
      if (!controlOnly && allBrands && allBrands.checked) {
        checkbox.checked = false;
      }
    });
  }

  function syncUiState() {
    var controlOnly = isControlOnlyScope();
    if (brandSelectionCard) {
      brandSelectionCard.classList.toggle('simple-mailing__card--muted', controlOnly);
    }
    if (controlBrandsHint) {
      controlBrandsHint.hidden = !controlOnly;
    }
    if (controlScopeHint) {
      controlScopeHint.hidden = !controlOnly;
    }
    if (selectedCountDisplay) {
      selectedCountDisplay.textContent = controlOnly ? '—' : selectedBrandCount();
    }
    if (previewButton) {
      previewButton.disabled = !hasValidSelection();
      previewButton.textContent = previewButtonLabel();
    }
    if (!hasFreshResult && continueButton) {
      continueButton.disabled = true;
    }
  }

  function onSelectionChanged() {
    invalidateResult();
    syncBrandDisabledState();
    syncUiState();
  }

  function onScopeChanged() {
    if (isControlOnlyScope()) {
      clearBrandSelection();
    } else if (marketplaceLocked && allBrands) {
      allBrands.checked = true;
    }
    onSelectionChanged();
  }

  if (allBrands) {
    allBrands.addEventListener('change', onSelectionChanged);
  }

  brandCheckboxes.forEach(function (checkbox) {
    checkbox.addEventListener('change', onSelectionChanged);
  });

  function normalizeSearchQuery(value) {
    return (value || '').trim().toLowerCase();
  }

  function filterBrandCards() {
    var query = normalizeSearchQuery(brandSearch ? brandSearch.value : '');
    var visibleCount = 0;
    brandOptions.forEach(function (label) {
      var searchText = label.getAttribute('data-brand-search') || '';
      var matches = !query || searchText.indexOf(query) !== -1;
      label.classList.toggle('is-search-hidden', !matches);
      if (matches) {
        visibleCount += 1;
      }
    });
    if (brandSearchEmpty) {
      brandSearchEmpty.hidden = !(query && visibleCount === 0);
    }
  }

  if (brandSearch) {
    brandSearch.addEventListener('input', filterBrandCards);
    brandSearch.addEventListener('search', filterBrandCards);
  }

  recipientScopeRadios.forEach(function (radio) {
    radio.addEventListener('change', onScopeChanged);
  });

  recipientRadios.forEach(function (radio) {
    radio.addEventListener('change', function () {
      if (!radio.checked) {
        return;
      }
      var url = new URL(window.location.href);
      url.searchParams.set('recipient_type', radio.value);
      window.location.href = url.toString();
    });
  });

  syncBrandDisabledState();
  syncUiState();
})();
