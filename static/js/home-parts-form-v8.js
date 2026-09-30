(function () {
  const form = document.getElementById('home-parts-form');
  if (!form) return;

  const submitBtn = document.getElementById('home-parts-submit');
  const formError = document.getElementById('home-parts-form-error');
  const warningEl = document.getElementById('home-parts-warning');
  const queryEl = document.getElementById('home-query');
  const brandEl = document.getElementById('home-brand');
  const brandIdEl = document.getElementById('home-brand-id');
  const brandOpenBtn = document.getElementById('home-brand-open');
  const modelEl = document.getElementById('home-model');
  const modelIdEl = document.getElementById('home-model-id');
  const modelOpenBtn = document.getElementById('home-model-open');
  const vehiclePicker = document.getElementById('home-vehicle-picker');
  const vehiclePickerBackdrop = document.getElementById('home-vehicle-picker-backdrop');
  const vehiclePickerTitle = document.getElementById('home-vehicle-picker-title');
  const vehiclePickerHint = document.getElementById('home-vehicle-picker-hint');
  const vehiclePickerSearch = document.getElementById('home-vehicle-picker-search');
  const vehiclePickerResults = document.getElementById('home-vehicle-picker-results');
  const vehiclePickerStatus = document.getElementById('home-vehicle-picker-status');
  const vehiclePickerClose = document.getElementById('home-vehicle-picker-close');
  const vehiclePickerManual = document.getElementById('home-vehicle-picker-manual');
  const newRequestBtn = document.getElementById('home-parts-new');
  const categoryEl = document.getElementById('home-category');
  const cityEl = document.getElementById('home-city');
  const cityList = document.getElementById('home-city-list');
  const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
  const KEY_STORAGE = 'zptHomePartsIdempotency';
  let memoryKey = '';
  let pickerKind = '';
  let pickerTimer = null;
  let pickerSuggestSeq = 0;
  let pickerPreviousFocus = null;
  let submitting = false;

  function csrfToken() {
    return csrfInput ? csrfInput.value : '';
  }

  function newKey() {
    return (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : String(Date.now()) + '-' + Math.random().toString(16).slice(2);
  }

  function getIdempotencyKey() {
    if (memoryKey) return memoryKey;
    try {
      let key = sessionStorage.getItem(KEY_STORAGE);
      if (!key) {
        key = newKey();
        sessionStorage.setItem(KEY_STORAGE, key);
      }
      memoryKey = key;
      return key;
    } catch (err) {
      memoryKey = newKey();
      return memoryKey;
    }
  }

  function rotateIdempotencyKey() {
    memoryKey = '';
    try {
      sessionStorage.removeItem(KEY_STORAGE);
    } catch (err) {}
  }

  function hideWarning() {
    if (!warningEl) return;
    warningEl.hidden = true;
    warningEl.classList.remove('home-parts-rejection');
    warningEl.replaceChildren();
  }

  function clearErrors() {
    form.querySelectorAll('[data-error-for]').forEach(function (el) {
      el.hidden = true;
      el.textContent = '';
    });
    if (formError) {
      formError.hidden = true;
      formError.textContent = '';
    }
    hideWarning();
  }

  function warningButton(label, onClick) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', function () {
      onClick();
    });
    return button;
  }

  function showWarning(data) {
    if (!warningEl) return;
    warningEl.replaceChildren();
    const text = document.createElement('p');
    text.textContent = data.warning_message || '';
    warningEl.appendChild(text);
    const actions = document.createElement('div');
    actions.className = 'home-parts-warning-actions';
    if (data.warning_code === 'category_mismatch' && data.suggested_category && categoryEl) {
      actions.appendChild(warningButton(
        'Выбрать “' + data.suggested_category + '”',
        function () {
          const suggested = String(data.suggested_category);
          const options = Array.prototype.slice.call(categoryEl.options || []);
          const match = options.find(function (opt) {
            return opt.value === suggested || String(opt.textContent || '').trim() === suggested;
          });
          if (match) categoryEl.value = match.value;
          hideWarning();
          categoryEl.focus();
        }
      ));
    }
    actions.appendChild(warningButton('Исправить', function () {
      hideWarning();
      if (queryEl) queryEl.focus();
    }));
    actions.appendChild(warningButton('Всё равно отправить', function () {
      sendRequest(true);
    }));
    warningEl.appendChild(actions);
    warningEl.classList.remove('home-parts-rejection');
    warningEl.hidden = false;
  }

  function showRejection(data) {
    if (!warningEl) return;
    warningEl.replaceChildren();
    warningEl.classList.add('home-parts-rejection');
    const title = document.createElement('p');
    title.className = 'home-parts-rejection-title';
    title.textContent = 'Эта форма предназначена только для поиска и покупки автозапчастей.';
    warningEl.appendChild(title);
    const message = String(data.rejection_message || '');
    if (message && message !== title.textContent) {
      const extra = document.createElement('p');
      extra.textContent = message;
      warningEl.appendChild(extra);
    }
    const actions = document.createElement('div');
    actions.className = 'home-parts-warning-actions';
    actions.appendChild(warningButton('Исправить запрос', function () {
      hideWarning();
      if (queryEl) queryEl.focus();
    }));
    warningEl.appendChild(actions);
    warningEl.hidden = false;
    if (queryEl) queryEl.focus();
  }

  function showFieldError(name, message) {
    const el = form.querySelector('[data-error-for="' + name + '"]');
    if (el) {
      el.textContent = message;
      el.hidden = !message;
    }
  }

  function canonicalCity(value) {
    const text = String(value || '').trim().replace(/\s+/g, ' ');
    if (!text || !cityList) return '';
    const folded = text.toLocaleLowerCase('ru');
    let match = '';
    cityList.querySelectorAll('option').forEach(function (opt) {
      const name = String(opt.value || '').trim();
      if (name && name.toLocaleLowerCase('ru') === folded) {
        match = name;
      }
    });
    return match;
  }

  function stopSubmitWithCityError(message) {
    showFieldError('city', message);
    if (formError) {
      formError.textContent = message;
      formError.hidden = false;
    }
  }

  function currentTransportType() {
    const checked = form.querySelector('input[name="transport_type"]:checked');
    return checked ? checked.value : 'car';
  }

  function setTransportType(type) {
    if (type !== 'car' && type !== 'truck') return;
    const radio = form.querySelector('input[name="transport_type"][value="' + type + '"]');
    if (radio && !radio.checked) {
      radio.checked = true;
    }
  }

  function transportLabel(type) {
    if (type === 'truck') return 'Грузовые';
    if (type === 'car') return 'Легковые';
    return '';
  }

  function syncVehiclePickerState() {
    const hasBrand = Boolean(String(brandEl.value || '').trim());
    if (modelEl) {
      modelEl.disabled = !hasBrand;
      modelEl.placeholder = hasBrand ? 'Выберите модель' : 'Сначала выберите марку';
    }
    if (modelOpenBtn) {
      modelOpenBtn.disabled = !hasBrand;
    }
  }

  function clearModelSelection() {
    modelEl.value = '';
    modelIdEl.value = '';
    syncVehiclePickerState();
  }

  function clearVehicleSelection() {
    brandEl.value = '';
    brandIdEl.value = '';
    clearModelSelection();
  }

  function fetchSuggest(kind, query, extra) {
    const params = new URLSearchParams({ kind: kind, q: query });
    if (extra) {
      Object.keys(extra).forEach(function (key) {
        if (extra[key]) params.set(key, extra[key]);
      });
    }
    return fetch('/api/vehicle-suggest/?' + params.toString(), {
      headers: { 'Accept': 'application/json' },
    }).then(function (response) {
      if (!response.ok) return { items: [] };
      return response.json();
    }).catch(function () {
      return { items: [] };
    });
  }

  function setPickerStatus(text) {
    if (!vehiclePickerStatus) return;
    vehiclePickerStatus.textContent = text || '';
    vehiclePickerStatus.hidden = !text;
  }

  function clearPickerResults() {
    if (vehiclePickerResults) vehiclePickerResults.replaceChildren();
  }

  function pickerTarget(kind) {
    return kind === 'model'
      ? { input: modelEl, hidden: modelIdEl, open: modelOpenBtn }
      : { input: brandEl, hidden: brandIdEl, open: brandOpenBtn };
  }

  function pickerCopy(kind) {
    if (kind === 'model') {
      return {
        title: 'Выберите модель',
        hint: 'Показываем модели только выбранной марки.',
        placeholder: 'Например: CX-5, Camry, Tiggo 7',
      };
    }
    return {
      title: 'Выберите марку',
      hint: 'Можно писать по-русски или латиницей: Mazda, Мазда, Chery…',
      placeholder: 'Начните вводить марку',
    };
  }

  function closeVehiclePicker() {
    if (!vehiclePicker || vehiclePicker.hidden) return;
    vehiclePicker.hidden = true;
    vehiclePicker.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('home-vehicle-picker-open');
    pickerKind = '';
    if (pickerTimer) {
      window.clearTimeout(pickerTimer);
      pickerTimer = null;
    }
    clearPickerResults();
    setPickerStatus('');
    if (vehiclePickerManual) vehiclePickerManual.hidden = true;
    if (pickerPreviousFocus && typeof pickerPreviousFocus.focus === 'function') {
      pickerPreviousFocus.focus();
    }
    pickerPreviousFocus = null;
  }

  function selectVehicleValue(item) {
    const kind = pickerKind;
    const target = pickerTarget(kind);
    target.input.value = String(item.name || '').trim();
    target.hidden.value = String(item.id || '');

    if (kind === 'brand') {
      setTransportType(item.transport_type);
      clearModelSelection();
      closeVehiclePicker();
      syncVehiclePickerState();
      if (modelOpenBtn && !modelOpenBtn.disabled) modelOpenBtn.focus();
      return;
    }

    closeVehiclePicker();
  }

  function useManualPickerValue() {
    const query = String(vehiclePickerSearch ? vehiclePickerSearch.value : '').trim();
    if (!query || !pickerKind) return;
    const kind = pickerKind;
    const target = pickerTarget(kind);
    target.input.value = query;
    target.hidden.value = '';
    if (kind === 'brand') {
      clearModelSelection();
      syncVehiclePickerState();
    }
    closeVehiclePicker();
    if (kind === 'brand' && modelOpenBtn && !modelOpenBtn.disabled) {
      modelOpenBtn.focus();
    }
  }

  function renderPickerResults(items) {
    clearPickerResults();
    const query = String(vehiclePickerSearch ? vehiclePickerSearch.value : '').trim();
    if (vehiclePickerManual) {
      vehiclePickerManual.hidden = !query;
      vehiclePickerManual.textContent = query
        ? 'Не нашли? Использовать «' + query + '»'
        : '';
    }

    if (!items.length) {
      setPickerStatus(
        query
          ? 'Подходящих вариантов не найдено. Можно использовать введённое название.'
          : (pickerKind === 'model' ? 'Выберите модель из списка или начните вводить название.' : 'Начните вводить название марки.')
      );
      return;
    }

    setPickerStatus('');
    items.forEach(function (item) {
      const li = document.createElement('li');
      const button = document.createElement('button');
      const main = document.createElement('span');
      const meta = document.createElement('span');
      button.type = 'button';
      button.className = 'home-vehicle-picker-option';
      button.setAttribute('role', 'option');
      main.className = 'home-vehicle-picker-option-name';
      main.textContent = item.label || item.name || '';
      button.appendChild(main);

      const metaParts = [];
      if (pickerKind === 'brand') {
        const tLabel = transportLabel(item.transport_type);
        if (tLabel) metaParts.push(tLabel);
        if (item.country) metaParts.push(item.country);
      } else if (item.brand_name) {
        metaParts.push(item.brand_name);
      }
      if (metaParts.length) {
        meta.className = 'home-vehicle-picker-option-meta';
        meta.textContent = metaParts.join(' · ');
        button.appendChild(meta);
      }

      button.addEventListener('click', function () {
        selectVehicleValue(item);
      });
      li.appendChild(button);
      vehiclePickerResults.appendChild(li);
    });
  }

  function requestPickerSuggestions() {
    if (!pickerKind || !vehiclePickerSearch) return;
    const query = vehiclePickerSearch.value.trim();
    const seq = ++pickerSuggestSeq;
    if (pickerTimer) window.clearTimeout(pickerTimer);

    pickerTimer = window.setTimeout(function () {
      if (pickerKind === 'brand' && !query) {
        renderPickerResults([]);
        return;
      }

      const extra = {};
      if (pickerKind === 'model') {
        extra.brand_id = brandIdEl.value;
        extra.brand = brandEl.value;
        extra.transport_type = currentTransportType();
      }

      setPickerStatus('Ищем…');
      fetchSuggest(pickerKind, query, extra).then(function (data) {
        if (seq !== pickerSuggestSeq) return;
        renderPickerResults(data.items || []);
      });
    }, 140);
  }

  function openVehiclePicker(kind) {
    if (!vehiclePicker || !vehiclePickerSearch || !vehiclePickerResults) return;
    if (kind === 'model' && !String(brandEl.value || '').trim()) {
      if (brandOpenBtn) brandOpenBtn.focus();
      return;
    }

    pickerKind = kind;
    pickerPreviousFocus = document.activeElement;
    const copy = pickerCopy(kind);
    if (vehiclePickerTitle) vehiclePickerTitle.textContent = copy.title;
    if (vehiclePickerHint) vehiclePickerHint.textContent = copy.hint;
    vehiclePickerSearch.placeholder = copy.placeholder;
    vehiclePickerSearch.value = pickerTarget(kind).input.value || '';
    vehiclePicker.hidden = false;
    vehiclePicker.setAttribute('aria-hidden', 'false');
    document.body.classList.add('home-vehicle-picker-open');
    clearPickerResults();
    if (vehiclePickerManual) vehiclePickerManual.hidden = true;
    window.setTimeout(function () {
      vehiclePickerSearch.focus();
      vehiclePickerSearch.select();
      requestPickerSuggestions();
    }, 0);
  }

  if (brandEl) {
    brandEl.addEventListener('click', function () {
      openVehiclePicker('brand');
    });
    brandEl.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openVehiclePicker('brand');
      }
    });
  }
  if (brandOpenBtn) {
    brandOpenBtn.addEventListener('click', function () {
      openVehiclePicker('brand');
    });
  }
  if (modelEl) {
    modelEl.addEventListener('click', function () {
      if (!modelEl.disabled) openVehiclePicker('model');
    });
    modelEl.addEventListener('keydown', function (event) {
      if ((event.key === 'Enter' || event.key === ' ') && !modelEl.disabled) {
        event.preventDefault();
        openVehiclePicker('model');
      }
    });
  }
  if (modelOpenBtn) {
    modelOpenBtn.addEventListener('click', function () {
      if (!modelOpenBtn.disabled) openVehiclePicker('model');
    });
  }
  if (vehiclePickerSearch) {
    vehiclePickerSearch.addEventListener('input', requestPickerSuggestions);
    vehiclePickerSearch.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeVehiclePicker();
        return;
      }
      if (event.key !== 'ArrowDown') return;
      const first = vehiclePickerResults.querySelector('button');
      if (first) {
        event.preventDefault();
        first.focus();
      }
    });
  }
  if (vehiclePickerResults) {
    vehiclePickerResults.addEventListener('keydown', function (event) {
      const buttons = Array.prototype.slice.call(
        vehiclePickerResults.querySelectorAll('button')
      );
      if (!buttons.length) return;
      const index = buttons.indexOf(document.activeElement);
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        buttons[Math.min(buttons.length - 1, index + 1)].focus();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        if (index <= 0) vehiclePickerSearch.focus();
        else buttons[index - 1].focus();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closeVehiclePicker();
      }
    });
  }
  if (vehiclePickerClose) {
    vehiclePickerClose.addEventListener('click', closeVehiclePicker);
  }
  if (vehiclePickerBackdrop) {
    vehiclePickerBackdrop.addEventListener('click', closeVehiclePicker);
  }
  if (vehiclePickerManual) {
    vehiclePickerManual.addEventListener('click', useManualPickerValue);
  }
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && vehiclePicker && !vehiclePicker.hidden) {
      closeVehiclePicker();
    }
  });

  form.querySelectorAll('input[name="transport_type"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      clearVehicleSelection();
    });
  });

  syncVehiclePickerState();

  function applyGuideDraft() {
    var yearEl = document.getElementById('home-year');
    var vinEl = document.getElementById('home-vin');
    var extraEl = form.querySelector('.home-parts-extra');
    var draft;
    try {
      var raw = sessionStorage.getItem('zptGuideRequestDraft');
      if (!raw) return;
      sessionStorage.removeItem('zptGuideRequestDraft');
      draft = JSON.parse(raw);
    } catch (err) {
      return;
    }
    if (!draft || typeof draft !== 'object') return;

    function fillIfEmpty(el, value) {
      if (!el) return false;
      if (String(el.value || '').trim()) return false;
      var next = String(value || '').trim();
      if (!next) return false;
      el.value = next;
      return true;
    }

    fillIfEmpty(queryEl, draft.query);
    if (fillIfEmpty(brandEl, draft.brand) && brandIdEl) brandIdEl.value = '';
    if (fillIfEmpty(modelEl, draft.model) && modelIdEl) modelIdEl.value = '';
    var filledYear = fillIfEmpty(yearEl, draft.year);
    var filledVin = fillIfEmpty(vinEl, draft.vin);
    if ((filledYear || filledVin) && extraEl) extraEl.open = true;
    syncVehiclePickerState();
    form.hidden = false;
    if (typeof form.scrollIntoView === 'function') {
      form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (queryEl) queryEl.focus();
  }

  applyGuideDraft();

  window.ZPTHomePartsApplyGuideDraft = applyGuideDraft;

  function showNewRequestForm() {
    rotateIdempotencyKey();
    form.hidden = false;
    form.reset();
    brandIdEl.value = '';
    modelIdEl.value = '';
    closeVehiclePicker();
    syncVehiclePickerState();
    clearErrors();
    if (queryEl) queryEl.focus();
  }

  if (newRequestBtn) {
    newRequestBtn.addEventListener('click', function () {
      showNewRequestForm();
    });
  }

  function finishSubmitAttempt() {
    submitting = false;
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Отправить запрос';
    }
  }

  function sendRequest(confirmWarning) {
    if (submitting) return;
    clearErrors();
    if (categoryEl && !String(categoryEl.value || '').trim()) {
      showFieldError('category', 'Выберите категорию запчасти.');
      if (formError) {
        formError.textContent = 'Выберите категорию запчасти.';
        formError.hidden = false;
      }
      return;
    }
    if (cityEl) {
      const rawCity = String(cityEl.value || '').trim();
      if (!rawCity) {
        stopSubmitWithCityError('Укажите город.');
        return;
      }
      const cityCanonical = canonicalCity(cityEl.value);
      if (cityCanonical) {
        cityEl.value = cityCanonical;
      }
    }
    submitting = true;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Отправляем…';
    }

    const formData = new FormData(form);
    const key = getIdempotencyKey();
    formData.set('idempotency_key', key);
    if (confirmWarning) {
      formData.set('warning_confirmed', '1');
    }

    fetch(form.action, {
      method: 'POST',
      body: formData,
      headers: {
        'X-CSRFToken': csrfToken(),
        'Idempotency-Key': key,
        'X-Requested-With': 'XMLHttpRequest',
      },
      credentials: 'same-origin',
    }).then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok, status: response.status, data: data };
      }).catch(function () {
        return { ok: false, status: response.status, data: {} };
      });
    }).then(function (result) {
      if (result.ok && result.data && result.data.result_url) {
        rotateIdempotencyKey();
        window.location.assign(result.data.result_url);
        return;
      }
      const data = result.data || {};
      if (data.rejected) {
        finishSubmitAttempt();
        showRejection(data);
        return;
      }
      if (data.warning_required) {
        finishSubmitAttempt();
        showWarning(data);
        return;
      }
      const fields = data.fields || {};
      Object.keys(fields).forEach(function (name) {
        showFieldError(name, fields[name]);
      });
      if (formError) {
        formError.textContent = data.error || 'Не удалось отправить запрос. Проверьте поля и попробуйте ещё раз.';
        formError.hidden = false;
        if (result.status === 409 && data.can_retry_as_new) {
          const retry = document.createElement('button');
          retry.type = 'button';
          retry.className = 'btn-secondary';
          retry.textContent = 'Новый запрос';
          retry.addEventListener('click', function () {
            showNewRequestForm();
            retry.remove();
          });
          formError.appendChild(document.createTextNode(' '));
          formError.appendChild(retry);
        }
      }
      finishSubmitAttempt();
    }).catch(function () {
      if (formError) {
        formError.textContent = 'Не удалось отправить запрос. Проверьте соединение и попробуйте ещё раз.';
        formError.hidden = false;
      }
      finishSubmitAttempt();
    });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    sendRequest(false);
  });
})();
