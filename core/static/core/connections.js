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
