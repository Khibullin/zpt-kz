// UI helpers only. Copying and previewing never mark a message as sent.
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy-target]');
  if (!button) return;
  const field = document.getElementById(button.dataset.copyTarget);
  const status = document.getElementById('copy-status');
  if (!field) return;
  try {
    await navigator.clipboard.writeText(field.value);
    if (status) status.textContent = 'Сообщение скопировано';
  } catch (_) {
    field.focus();
    field.select();
    if (status) status.textContent = 'Текст выделен — скопируйте его';
  }
});
document.addEventListener('input', (event) => {
  if (event.target.id !== 'id_text') return;
  const preview = document.getElementById('telegram-preview-text');
  if (preview) preview.textContent = event.target.value;
});

document.addEventListener('change', (event) => {
  if (!event.target.matches('.field-photo input[type=file]')) return;
  const status = event.target.closest('.field-photo').querySelector('.upload-name');
  if (status) status.textContent = Array.from(event.target.files || [], file => file.name).join(', ');
});

// Kazakhstan shop phone input. Only explicitly marked fields use this formatter.
(() => {
  function parts(value) {
    let digits = value.replace(/\D/g, '');
    const country = /^\s*\+7/.test(value) || (digits.length === 11 && /^[78]/.test(digits));
    if (country) digits = digits.slice(1);
    return {digits, country};
  }
  function formatted(digits) {
    let value = '+7';
    if (digits.length) value += ' (' + digits.slice(0, 3);
    if (digits.length >= 3) value += ')';
    if (digits.length > 3) value += ' ' + digits.slice(3, 6);
    if (digits.length > 6) value += '-' + digits.slice(6, 8);
    if (digits.length > 8) value += '-' + digits.slice(8);
    return value;
  }
  function warn(field, text) {
    const status = field.parentElement.querySelector('[data-phone-warning]');
    if (status) { status.textContent = text; status.hidden = !text; }
  }
  function update(field, caret) {
    const original = field.value;
    const {digits, country} = parts(original);
    const offset = caret === undefined ? field.selectionStart : caret;
    const before = original.slice(0, offset).replace(/\D/g, '').length;
    const nationalBefore = Math.max(0, before - (country ? 1 : 0));
    field.value = digits || document.activeElement === field ? formatted(digits) : '';
    field.setCustomValidity(digits.length === 10 ? '' : 'Введите 10 цифр номера после +7.');
    warn(field, digits.length > 10 ? 'Слишком много цифр. После +7 должно быть ровно 10 цифр.' : '');
    if (document.activeElement !== field) return;
    let target = field.value.length;
    if (offset < original.length) {
      target = 2;
      let count = 0;
      for (let i = 2; i < field.value.length; i++) {
        if (/\d/.test(field.value[i]) && ++count === nationalBefore) { target = i + 1; break; }
      }
    }
    field.setSelectionRange(target, target);
  }
  function init() {
    document.querySelectorAll('[data-kz-phone]').forEach(field => {
      const status = document.createElement('span');
      status.dataset.phoneWarning = '1';
      status.className = 'phone-warning';
      status.setAttribute('role', 'status');
      status.setAttribute('aria-live', 'polite');
      status.id = (field.id || 'shop-phone') + '-warning';
      status.hidden = true;
      field.after(status);
      field.setAttribute('aria-describedby', [field.getAttribute('aria-describedby'), status.id].filter(Boolean).join(' '));
      update(field);
      field.addEventListener('focus', () => update(field));
      field.addEventListener('input', () => update(field));
      field.addEventListener('blur', () => update(field));
      field.addEventListener('beforeinput', event => {
        if (event.inputType && event.inputType.startsWith('insert') && event.data && /^\d$/.test(event.data)) {
          const next = field.value.slice(0, field.selectionStart) + event.data + field.value.slice(field.selectionEnd);
          if (parts(next).digits.length > 10) {
            event.preventDefault();
            warn(field, 'Номер уже заполнен: после +7 — только 10 цифр. Лишние цифры не добавлены.');
            return;
          }
        }
        if (!['deleteContentBackward', 'deleteContentForward'].includes(event.inputType)) return;
        const start = field.selectionStart;
        if (start !== field.selectionEnd) return;
        const backwards = event.inputType === 'deleteContentBackward';
        let index = backwards ? start - 1 : start;
        if (index < 2) { event.preventDefault(); return; }
        if (index >= field.value.length || /\d/.test(field.value[index])) return;
        while (index >= 2 && index < field.value.length && !/\d/.test(field.value[index])) index += backwards ? -1 : 1;
        if (index < 2 || index >= field.value.length) return;
        event.preventDefault();
        field.value = field.value.slice(0, index) + field.value.slice(index + 1);
        update(field, index);
      });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

// Save buyer choices in place: submitting a checkbox must not navigate or scroll.
document.addEventListener('submit', async (event) => {
  const form = event.target;
  if (!form.matches('.sellers-panel .selection-toggle')) return;
  const checkbox = form.querySelector('input[type="checkbox"]');
  if (!checkbox) return;
  event.preventDefault();
  checkbox.disabled = true;
  let status = form.querySelector('[data-selection-status]');
  if (!status) {
    status = document.createElement('span');
    status.dataset.selectionStatus = '1';
    status.setAttribute('role', 'status');
    form.append(status);
  }
  status.textContent = '';
  try {
    const response = await fetch(form.action, {
      method: 'POST', body: new FormData(form), credentials: 'same-origin'
    });
    if (!response.ok) throw new Error('Selection save failed');
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    const saved = Array.from(page.querySelectorAll('.selection-toggle')).find(item =>
      item.getAttribute('action') === form.getAttribute('action'));
    const savedCheckbox = saved && saved.querySelector('input[type="checkbox"]');
    const counter = page.querySelector('[data-selection-next]');
    if (!savedCheckbox || !counter) throw new Error('Selection response missing');
    checkbox.checked = savedCheckbox.checked;
    document.querySelectorAll('[data-selection-next]').forEach(link => {
      link.textContent = counter.textContent;
    });
    status.textContent = Array.from(page.querySelectorAll('.selection-notice'))
      .map(item => item.textContent).join(' ');
  } catch (_) {
    checkbox.checked = !checkbox.checked;
    status.textContent = 'Не удалось сохранить выбор. Попробуйте ещё раз.';
  } finally {
    checkbox.disabled = false;
  }
});
