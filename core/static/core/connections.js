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
  function update(field, caret) {
    const original = field.value;
    const {digits, country} = parts(original);
    const offset = caret === undefined ? field.selectionStart : caret;
    const before = original.slice(0, offset).replace(/\D/g, '').length;
    const nationalBefore = Math.max(0, before - (country ? 1 : 0));
    field.value = digits || document.activeElement === field ? formatted(digits) : '';
    field.setCustomValidity(digits.length === 10 ? '' : 'Введите 10 цифр номера после +7.');
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
      update(field);
      field.addEventListener('focus', () => update(field));
      field.addEventListener('input', () => update(field));
      field.addEventListener('blur', () => update(field));
      field.addEventListener('beforeinput', event => {
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
