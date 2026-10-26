document.getElementById('copy-message')?.addEventListener('click', async () => {
  const field = document.getElementById('seller-message');
  const status = document.getElementById('copy-status');
  try {
    await navigator.clipboard.writeText(field.value);
    status.textContent = 'Текст скопирован. Вставьте его в сообщение продавцу.';
  } catch {
    field.focus(); field.select();
    status.textContent = 'Текст выделен. Скопируйте его через меню или Ctrl+C.';
  }
});
