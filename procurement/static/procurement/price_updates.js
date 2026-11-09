(() => {
  const rows = [...document.querySelectorAll('[data-price-update]')];
  const token = document.querySelector('[name=csrfmiddlewaretoken]').value;
  const resume = document.querySelector('[data-price-resume]');
  let busy = false;
  async function run() {
    if (busy) return;
    busy = true;
    resume.hidden = true;
    try {
      for (const row of rows) {
        while (row.dataset.priceUpdate) {
          const response = await fetch(row.dataset.priceUpdate, {
            method: 'POST', headers: {'X-CSRFToken': token, 'Accept': 'application/json'}, credentials: 'same-origin'
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Не удалось продолжить.');
          row.querySelector('[data-progress]').textContent = `${data.status === 'done' ? 'Каталог обновлён' : 'Обрабатывается'} · ${data.processed} / ${data.total}`;
          const s = data.summary;
          row.querySelector('[data-summary]').textContent = `Новых: ${s.new}; изменено: ${s.changed}; без изменений: ${s.unchanged}; снято с наличия: ${s.unavailable}; очередь Kaspi: ${s.queued}; использовано соответствий: ${s.reused}; конфликтов прайса: ${s.conflicts}`;
          if (data.status === 'done') delete row.dataset.priceUpdate;
        }
      }
      if (rows.length) window.location.reload();
    } catch (error) {
      resume.hidden = false;
      resume.textContent = 'Продолжить обработку';
      let notice = document.querySelector('[data-price-error]');
      if (!notice) { notice = document.createElement('p'); notice.dataset.priceError = ''; notice.setAttribute('role', 'alert'); resume.before(notice); }
      notice.textContent = 'Обработка приостановлена. Прогресс сохранён. ' + error.message;
    } finally { busy = false; }
  }
  resume.addEventListener('click', run);
  run();
})();
