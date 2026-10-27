(function () {
  'use strict';
  let dialog, trigger;
  function close() { if (dialog) dialog.close(); if (trigger) trigger.focus(); }
  document.addEventListener('click', async function (event) {
    const link = event.target.closest('[data-price-inquiry]');
    if (event.target.closest('[data-price-close]')) { close(); return; }
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0 || !window.HTMLDialogElement) return;
    event.preventDefault(); trigger = link;
    if (link.dataset.loading) return;
    link.dataset.loading = '1';
    try {
      const response = await fetch(link.href, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok) throw new Error('load');
      const html = await response.text();
      if (!dialog) {
        dialog = document.createElement('dialog'); dialog.className = 'zpt-price-dialog';
        dialog.setAttribute('aria-labelledby', 'zpt-price-title'); document.body.appendChild(dialog);
        dialog.addEventListener('click', function (e) { if (e.target === dialog) close(); });
        dialog.addEventListener('close', function () { if (trigger) trigger.focus(); });
      }
      dialog.innerHTML = html; dialog.showModal();
    } catch (_) { window.location.assign(link.href); }
    finally { delete link.dataset.loading; }
  });
  document.addEventListener('submit', async function (event) {
    const form = event.target.closest('[data-price-form]'); if (!form) return;
    event.preventDefault();
    if (form.dataset.sending) return;
    const button = form.querySelector('[type=submit]'); const box = form.querySelector('[data-price-errors]');
    form.querySelectorAll('[data-price-error]').forEach(el => {el.textContent = '';}); box.textContent = '';
    form.dataset.sending = '1'; button.disabled = true; button.textContent = 'Отправляем…';
    try {
      const response = await fetch(form.action, {method: 'POST', credentials: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest'}, body: new FormData(form)});
      const data = await response.json();
      if (!response.ok || !data.success) {
        if (!data.errors) throw new Error('submit');
        for (const [name, errors] of Object.entries(data.errors)) {
          const field = form.querySelector('[data-price-error="' + name + '"]');
          (field || box).textContent = errors.map(item => item.message).join(' ');
        }
        box.textContent = box.textContent || 'Проверьте заполнение формы.'; return;
      }
      const container = form.closest('.zpt-price-form'); container.className = 'zpt-price-success';
      container.replaceChildren();
      const title = document.createElement('h2'); title.id = 'zpt-price-title'; title.textContent = 'Запрос принят';
      const message = document.createElement('p'); message.textContent = data.message;
      const done = document.createElement('button'); done.type = 'button'; done.className = 'zpt-price-submit';
      done.textContent = 'Готово'; done.addEventListener('click', function () { if (dialog && dialog.open) close(); else window.location.assign('/'); });
      container.append(title, message, done); done.focus();
    } catch (_) { box.textContent = 'Не удалось получить подтверждение. Повторите отправку — повторный запрос не создастся.'; }
    finally { delete form.dataset.sending; button.disabled = false; button.textContent = 'Отправить запрос'; }
  });
})();
