(() => {
  const updateCount = count => {
    if (!Number.isInteger(count) || count < 0) return;
    document.querySelectorAll('[data-basket-count]').forEach(node => {
      node.textContent = count;
    });
  };

  // A compact product card can add a selection without closing the catalog drawer.
  window.addEventListener('message', event => {
    const frame = document.getElementById('product-frame');
    if (event.origin === window.location.origin && frame &&
        event.source === frame.contentWindow && event.data?.type === 'procurement:basket-updated') {
      updateCount(event.data.basket_count);
    }
  });

  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('[data-selection-form]')) return;
    event.preventDefault();
    if (form.dataset.pending) return;
    const button = form.querySelector('button[type="submit"],button:not([type])');
    const status = form.querySelector('[data-selection-status]');
    form.dataset.pending = '1';
    button.disabled = true;
    status.classList.remove('danger');
    status.textContent = 'Добавляем…';
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Accept': 'application/json'},
        body: new FormData(form)
      });
      if (response.redirected || !response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('Не удалось добавить товар. Обновите страницу и проверьте вход в кабинет.');
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось добавить товар. Попробуйте ещё раз.');
      updateCount(data.basket_count);
      if (window.parent !== window) {
        window.parent.postMessage({type: 'procurement:basket-updated', basket_count: data.basket_count}, window.location.origin);
      }
      status.textContent = data.message;
    } catch (error) {
      status.classList.add('danger');
      status.textContent = error.message || 'Не удалось добавить товар. Попробуйте ещё раз.';
    } finally {
      button.disabled = false;
      delete form.dataset.pending;
    }
  });
})();
