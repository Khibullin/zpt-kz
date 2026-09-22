(function () {
  const form = document.getElementById('home-parts-form');
  if (!form) return;

  const submitBtn = document.getElementById('home-parts-submit');
  const formError = document.getElementById('home-parts-form-error');
  const queryEl = document.getElementById('home-query');
  const brandEl = document.getElementById('home-brand');
  const brandIdEl = document.getElementById('home-brand-id');
  const brandSuggest = document.getElementById('home-brand-suggest');
  const modelEl = document.getElementById('home-model');
  const modelIdEl = document.getElementById('home-model-id');
  const modelSuggest = document.getElementById('home-model-suggest');
  const newRequestBtn = document.getElementById('home-parts-new');
  const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
  const KEY_STORAGE = 'zptHomePartsIdempotency';
  let memoryKey = '';
  let brandTimer = null;
  let modelTimer = null;
  let brandSuggestSeq = 0;
  let modelSuggestSeq = 0;
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

  function clearErrors() {
    form.querySelectorAll('[data-error-for]').forEach(function (el) {
      el.hidden = true;
      el.textContent = '';
    });
    if (formError) {
      formError.hidden = true;
      formError.textContent = '';
    }
  }

  function showFieldError(name, message) {
    const el = form.querySelector('[data-error-for="' + name + '"]');
    if (el) {
      el.textContent = message;
      el.hidden = !message;
    }
  }

  function closeSuggest(list) {
    if (!list) return;
    list.hidden = true;
    list.innerHTML = '';
  }

  function renderSuggest(list, items, onPick) {
    list.innerHTML = '';
    if (!items.length) {
      list.hidden = true;
      return;
    }
    items.forEach(function (item, index) {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = item.label || item.name;
      btn.setAttribute('role', 'option');
      btn.setAttribute('aria-selected', index === 0 ? 'true' : 'false');
      btn.addEventListener('mousedown', function (event) {
        event.preventDefault();
      });
      btn.addEventListener('click', function (event) {
        event.preventDefault();
        onPick(item);
      });
      btn.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        onPick(item);
      });
      li.appendChild(btn);
      list.appendChild(li);
    });
    list.hidden = false;
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

  function bindCombo(input, hidden, list, kind) {
    input.addEventListener('input', function () {
      hidden.value = '';
      const query = input.value.trim();
      if (kind === 'brand') {
        if (brandTimer) window.clearTimeout(brandTimer);
        modelEl.value = '';
        modelIdEl.value = '';
        closeSuggest(modelSuggest);
      } else if (modelTimer) {
        window.clearTimeout(modelTimer);
      }
      const seq = kind === 'brand' ? ++brandSuggestSeq : ++modelSuggestSeq;
      const timer = window.setTimeout(function () {
        if (!query && kind === 'brand') {
          closeSuggest(list);
          return;
        }
        const extra = kind === 'model'
          ? { brand_id: brandIdEl.value, brand: brandEl.value }
          : {};
        fetchSuggest(kind, query, extra).then(function (data) {
          if (kind === 'brand' && seq !== brandSuggestSeq) return;
          if (kind === 'model' && seq !== modelSuggestSeq) return;
          renderSuggest(list, data.items || [], function (item) {
            input.value = item.name;
            hidden.value = String(item.id || '');
            closeSuggest(list);
            if (kind === 'brand') {
              modelEl.value = '';
              modelIdEl.value = '';
              closeSuggest(modelSuggest);
              modelEl.focus();
            }
          });
        });
      }, 180);
      if (kind === 'brand') brandTimer = timer;
      else modelTimer = timer;
    });

    input.addEventListener('keydown', function (event) {
      const buttons = list.querySelectorAll('button');
      if (!buttons.length || list.hidden) return;
      let selected = list.querySelector('button[aria-selected="true"]');
      let index = Array.prototype.indexOf.call(buttons, selected);
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        index = Math.min(buttons.length - 1, index + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        index = Math.max(0, index - 1);
      } else if ((event.key === 'Enter' || event.key === ' ') && selected) {
        event.preventDefault();
        selected.click();
        return;
      } else if (event.key === 'Escape') {
        closeSuggest(list);
        return;
      } else {
        return;
      }
      buttons.forEach(function (btn, btnIndex) {
        btn.setAttribute('aria-selected', btnIndex === index ? 'true' : 'false');
      });
      buttons[index].focus();
      buttons[index].scrollIntoView({ block: 'nearest' });
    });

    input.addEventListener('blur', function () {
      window.setTimeout(function () {
        if (list.contains(document.activeElement)) return;
        closeSuggest(list);
      }, 180);
    });
  }

  bindCombo(brandEl, brandIdEl, brandSuggest, 'brand');
  bindCombo(modelEl, modelIdEl, modelSuggest, 'model');

  function applyGuideDraft() {
    var yearEl = document.getElementById('home-year');
    var vinEl = document.getElementById('home-vin');
    var phoneEl = document.getElementById('home-phone');
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
    if (queryEl && draft.query) queryEl.value = String(draft.query);
    if (brandEl && draft.brand) {
      brandEl.value = String(draft.brand);
      brandIdEl.value = '';
    }
    if (modelEl && draft.model) {
      modelEl.value = String(draft.model);
      modelIdEl.value = '';
    }
    if (yearEl && draft.year) yearEl.value = String(draft.year);
    if (vinEl) vinEl.value = vinEl.value;
    if (phoneEl && !phoneEl.value) {
      /* keep phone empty; never fill from the assistant */
    }
    form.hidden = false;
    if (typeof form.scrollIntoView === 'function') {
      form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (queryEl) queryEl.focus();
  }

  applyGuideDraft();

  function showNewRequestForm() {
    rotateIdempotencyKey();
    form.hidden = false;
    form.reset();
    brandIdEl.value = '';
    modelIdEl.value = '';
    clearErrors();
    if (queryEl) queryEl.focus();
  }

  if (newRequestBtn) {
    newRequestBtn.addEventListener('click', function () {
      showNewRequestForm();
    });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (submitting) return;
    clearErrors();
    submitting = true;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Отправляем…';
    }

    const formData = new FormData(form);
    const key = getIdempotencyKey();
    formData.set('idempotency_key', key);

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
      submitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Отправить запрос';
      }
    }).catch(function () {
      if (formError) {
        formError.textContent = 'Не удалось отправить запрос. Проверьте соединение и попробуйте ещё раз.';
        formError.hidden = false;
      }
      submitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Отправить запрос';
      }
    });
  });
})();
